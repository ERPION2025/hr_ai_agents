"""Provider adapter. Internal message format (OpenAI-like):

  {"role": "system"|"user", "content": str}
  {"role": "assistant", "content": str, "tool_calls": [{"id", "name", "args": dict}]}
  {"role": "tool", "tool_call_id": str, "content": str}

Every provider returns the same shape:
  {"text": str, "tool_calls": [{"id","name","args"}], "usage": {"input": int, "output": int}, "model": str}
"""
import json
import time

import requests


class ProviderError(Exception):
    pass


# ---------- payload converters (pure, unit-tested) ----------
def to_openai_tools(tools):
    return [
        {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["schema"]}}
        for t in tools
    ]


def to_anthropic_tools(tools):
    return [{"name": t["name"], "description": t["description"], "input_schema": t["schema"]} for t in tools]


def messages_to_openai(messages):
    out = []
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            out.append(
                {
                    "role": "assistant",
                    "content": m.get("content") or None,
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": json.dumps(tc["args"])},
                        }
                        for tc in m["tool_calls"]
                    ],
                }
            )
        else:
            out.append(dict(m))
    return out


def messages_to_anthropic(messages):
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    out = []
    for m in messages:
        role = m["role"]
        if role == "system":
            continue
        if role == "user":
            out.append({"role": "user", "content": m["content"]})
        elif role == "assistant":
            blocks = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m.get("tool_calls", []):
                blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["args"]})
            out.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
            prev = out[-1] if out else None
            if (
                prev
                and prev["role"] == "user"
                and isinstance(prev["content"], list)
                and prev["content"]
                and prev["content"][0].get("type") == "tool_result"
            ):
                prev["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return system, out


def parse_openai_response(data):
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    calls = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {}
        calls.append({"id": tc.get("id"), "name": fn.get("name"), "args": args})
    usage = data.get("usage") or {}
    return {
        "text": msg.get("content") or "",
        "tool_calls": calls,
        "usage": {"input": usage.get("prompt_tokens", 0), "output": usage.get("completion_tokens", 0)},
        "model": data.get("model"),
    }


def parse_anthropic_response(data):
    text, calls = [], []
    for b in data.get("content") or []:
        if b.get("type") == "text":
            text.append(b.get("text", ""))
        elif b.get("type") == "tool_use":
            calls.append({"id": b.get("id"), "name": b.get("name"), "args": b.get("input") or {}})
    usage = data.get("usage") or {}
    return {
        "text": "".join(text),
        "tool_calls": calls,
        "usage": {"input": usage.get("input_tokens", 0), "output": usage.get("output_tokens", 0)},
        "model": data.get("model"),
    }


# ---------- HTTP ----------
def _post(url, headers, body, timeout):
    last = None
    for attempt in range(3):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=timeout)
        except requests.RequestException as e:
            last = f"network error: {e}"
        else:
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code not in (408, 409, 429, 500, 502, 503, 504):
                break
        time.sleep(1.5 * (attempt + 1))
    raise ProviderError(last or "unknown provider error")


def call_azure(cfg, model, messages, tools, max_tokens):
    """cfg: dict(endpoint, api_key, api_version, timeout). model = Azure deployment name."""
    url = (
        f"{cfg['endpoint'].rstrip('/')}/openai/deployments/{model}/chat/completions"
        f"?api-version={cfg.get('api_version') or '2024-10-21'}"
    )
    body = {"messages": messages_to_openai(messages), "max_completion_tokens": max_tokens}
    if tools:
        body["tools"] = to_openai_tools(tools)
    data = _post(url, {"api-key": cfg["api_key"], "Content-Type": "application/json"}, body, cfg.get("timeout", 60))
    out = parse_openai_response(data)
    out["model"] = model
    return out


def call_anthropic(cfg, model, messages, tools, max_tokens):
    system, msgs = messages_to_anthropic(messages)
    body = {"model": model, "max_tokens": max_tokens, "messages": msgs, "temperature": cfg.get("temperature", 0.2)}
    if system:
        body["system"] = system
    if tools:
        body["tools"] = to_anthropic_tools(tools)
    headers = {
        "x-api-key": cfg["api_key"],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    if cfg.get("workspace_id"):
        headers["anthropic-workspace-id"] = cfg["workspace_id"]
    data = _post("https://api.anthropic.com/v1/messages", headers, body, cfg.get("timeout", 60))
    return parse_anthropic_response(data)


PROVIDERS = {"Azure OpenAI": call_azure, "Anthropic": call_anthropic}


def chat(provider, cfg, model, messages, tools=None, max_tokens=1500, fallback_model=None):
    fn = PROVIDERS.get(provider)
    if not fn:
        raise ProviderError(f"Unknown provider '{provider}'")
    if not cfg.get("api_key"):
        raise ProviderError(f"No API key configured for {provider}")
    try:
        return fn(cfg, model, messages, tools or [], max_tokens)
    except ProviderError:
        if fallback_model and fallback_model != model:
            return fn(cfg, fallback_model, messages, tools or [], max_tokens)
        raise
