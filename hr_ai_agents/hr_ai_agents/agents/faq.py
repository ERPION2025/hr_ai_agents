"""Knowledge base: approved FAQs and quick actions, served without the model (zero tokens).

FAQs are matched by keywords (no embeddings, nothing sent anywhere). Only FAQs an HR Manager approved are served, only to users
who hold one of the FAQ's roles, and only while the policy they came from is unchanged. Candidates are created from helpful
ratings, never from answers that touched employee or applicant records, and wait for HR approval.
Quick actions run a read-only agent tool as the asking user and render the result as a table, so data is always fresh and
access-driven; their answers are never stored as FAQs."""
import hashlib
import json
import math
import re

import frappe

WORD = re.compile(r"[a-z0-9]+")
# negations are deliberately NOT stop words: "can I carry forward" and "can I not carry forward" must differ
STOP = set("the a an of to and or in on for is are be by with as at it this that from your you i we can may must shall will do does did my me how what when where which who".split())
SERVE_AT = 0.8      # weighted keyword overlap needed to answer straight from an FAQ
SUGGEST_AT = 0.3    # overlap needed to suggest it
MIN_ANSWER, MAX_ANSWER = 40, 6000
PII_TOKEN = re.compile(r"\[[A-Z_]+_\d+\]")
RECORD_ID = re.compile(r"\b[A-Z]{2,}(?:-[A-Z0-9]+)*-\d{3,}\b|\bHR-[A-Z]+-\d+\b")
NO_CANDIDATE_AGENTS = {"Auditor"}


def _stem(w):
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def toks(text):
    return [_stem(w) for w in WORD.findall((text or "").lower()) if w not in STOP and len(w) > 1]


def key(text):
    """Order-insensitive fingerprint of the content words."""
    return hashlib.sha1(" ".join(sorted(set(toks(text)))).encode()).hexdigest()


# ------------------------------------------------------------------ matching
def _index(agent, user):
    """Approved FAQs this user may see for this agent (role visibility), with their wording variants."""
    rows = frappe.get_all(
        "AI FAQ", filters={"status": "Approved"},
        fields=["name", "question", "variants", "answer", "agent", "source_policy", "source_policy_version", "approved_by", "last_verified", "hits", "question_key"],
    )
    rows = [r for r in rows if not r.agent or r.agent == agent]
    if not rows:
        return []
    roles = set(frappe.get_roles(user))
    allowed = {}
    for r in frappe.get_all("AI Visible Role", filters={"parenttype": "AI FAQ", "parent": ("in", [r.name for r in rows])}, fields=["parent", "role"]):
        allowed.setdefault(r.parent, set()).add(r.role)
    out = []
    for r in rows:
        if not (allowed.get(r.name, set()) & roles):
            continue
        if _stale(r):
            continue
        phrases = [r.question] + [v for v in (r.variants or "").splitlines() if v.strip()]
        r["phrases"] = [(p, set(toks(p)), key(p)) for p in phrases]
        out.append(r)
    return out


def _stale(r):
    """A policy-sourced FAQ stops being served when the policy version changed or the policy was deactivated."""
    if not r.source_policy:
        return False
    cur = frappe.db.get_value("AI Policy Document", r.source_policy, ["version", "active"], as_dict=True)
    if cur and cur.active and (cur.version or "") == (r.source_policy_version or ""):
        return False
    frappe.db.set_value("AI FAQ", r.name, "status", "Needs review", update_modified=False)
    return True


def find(agent, text, user=None, limit=3):
    """-> {"serve": faq-or-None, "suggest": [faq...]}. Pure keyword matching; no model, no network."""
    user = user or frappe.session.user
    q = set(toks(text))
    if not q or len((text or "").strip()) < 4:
        return {"serve": None, "suggest": []}
    index = _index(agent, user)
    if not index:
        return {"serve": None, "suggest": []}
    n = len(index)
    df = {}
    for r in index:
        for t in set().union(*[p[1] for p in r["phrases"]]):
            df[t] = df.get(t, 0) + 1
    idf = lambda t: math.log(1 + n / (df.get(t, 0) + 0.5))  # noqa: E731
    qk = key(text)
    scored = []
    for r in index:
        best, exact = 0.0, False
        for _p, ts, k in r["phrases"]:
            if k == qk:
                best, exact = 1.0, True
                break
            inter, union = q & ts, q | ts
            if inter:
                s = sum(idf(t) for t in inter) / (sum(idf(t) for t in union) or 1)
                best = max(best, s)
        if best >= SUGGEST_AT:
            scored.append((best, exact, r))
    scored.sort(key=lambda x: (-x[0], -(x[2].hits or 0)))
    serve = scored[0][2] if scored and (scored[0][1] or (scored[0][0] >= SERVE_AT and len(q) >= 2)) else None
    return {"serve": serve, "suggest": [{"name": r.name, "question": r.question, "score": round(s, 2)} for s, _e, r in scored[:limit]]}


def record_hit(name):
    frappe.db.sql("update `tabAI FAQ` set hits = coalesce(hits,0) + 1 where name = %s", (name,))


def served_payload(r):
    return {"name": r.name, "question": r.question, "verified_on": str(r.last_verified or ""), "approved_by": frappe.utils.get_fullname(r.approved_by) if r.approved_by else ""}


def listing(agent, user=None, limit=8):
    """Quick-question chips: approved FAQs the user may see, most used first."""
    idx = _index(agent, user or frappe.session.user)
    idx.sort(key=lambda r: -(r.hits or 0))
    return [{"name": r.name, "question": r.question} for r in idx[:limit]]


# ------------------------------------------------------------------ feedback -> candidates and quality
def on_feedback(run, rating, user):
    """Thumbs on a served FAQ adjust its counters; a thumbs-up on an eligible model answer proposes a candidate FAQ."""
    row = frappe.db.get_value("AI Agent Run Log", run, ["agent", "prompt", "response", "records_touched", "context", "outcome", "tool_calls"], as_dict=True)
    if not row:
        return None
    ctx = {}
    try:
        ctx = json.loads(row.context or "{}")
    except ValueError:
        pass
    if ctx.get("served_from") == "faq" and ctx.get("faq"):
        col = "helpful" if rating == "Up" else "not_helpful"
        frappe.db.sql(f"update `tabAI FAQ` set {col} = coalesce({col},0) + 1 where name = %s", (ctx["faq"],))
        h, nh = frappe.db.get_value("AI FAQ", ctx["faq"], ["helpful", "not_helpful"]) or (0, 0)
        if (nh or 0) >= 3 and (nh or 0) > (h or 0):
            frappe.db.set_value("AI FAQ", ctx["faq"], "status", "Needs review", update_modified=False)
        return None
    if rating != "Up" or ctx.get("served_from"):
        return None
    return maybe_candidate(run, row)


def eligible(row, run):
    """May this model answer become a shared FAQ candidate? Never if it read records, mentions record ids or personal tokens."""
    if row.outcome != "Success" or row.agent in NO_CANDIDATE_AGENTS:
        return False
    p, r = row.prompt or "", row.response or ""
    if p.startswith("sha256:") or not (MIN_ANSWER <= len(r) <= MAX_ANSWER) or len(p) > 400:
        return False
    if PII_TOKEN.search(p) or PII_TOKEN.search(r) or RECORD_ID.search(p) or RECORD_ID.search(r):
        return False
    touched = (row.records_touched or "").strip()
    if touched not in ("", "[]", "null"):
        return False
    if frappe.db.exists("AI Data Access Log", {"run": run}):
        return False
    return True


def maybe_candidate(run, row=None):
    row = row or frappe.db.get_value("AI Agent Run Log", run, ["agent", "prompt", "response", "records_touched", "context", "outcome"], as_dict=True)
    if not row or not eligible(row, run):
        return None
    k = key(row.prompt)
    ex = frappe.db.get_value("AI FAQ", {"question_key": k, "agent": ("in", [row.agent, ""])}, ["name", "status"], as_dict=True)
    if ex:
        if ex.status == "Candidate":
            frappe.db.sql("update `tabAI FAQ` set asked_count = coalesce(asked_count,0) + 1 where name = %s", (ex.name,))
        return None
    doc = frappe.get_doc({"doctype": "AI FAQ", "question": row.prompt.strip(), "answer": row.response.strip(), "agent": row.agent, "status": "Candidate", "source_run": run, "asked_count": 1})
    doc.flags.ignore_permissions = True
    doc.insert()
    frappe.db.set_value("AI FAQ", doc.name, "owner", "Administrator", update_modified=False)  # de-identified: HR sees the text, not who rated it
    return doc.name


# ------------------------------------------------------------------ quick actions
def quick_actions(agent, user=None):
    user = user or frappe.session.user
    roles = set(frappe.get_roles(user))
    rows = frappe.get_all("AI Quick Action", filters={"enabled": 1}, fields=["name", "title", "agent", "description", "parameters"])
    rows = [r for r in rows if r.agent == agent]
    if not rows:
        return []
    allowed = {}
    for r in frappe.get_all("AI Visible Role", filters={"parenttype": "AI Quick Action", "parent": ("in", [r.name for r in rows])}, fields=["parent", "role"]):
        allowed.setdefault(r.parent, set()).add(r.role)
    out = []
    for r in rows:
        if allowed.get(r.name, set()) & roles:
            try:
                params = json.loads(r.parameters or "[]")
            except ValueError:
                params = []
            out.append({"name": r.name, "title": r.title, "description": r.description or "", "parameters": params})
    return out


def run_quick_action(name, params=None, user=None):
    """Run the recipe as the asking user. Returns {"answer": markdown, "run": id, ...}. Never uses the model."""
    from hr_ai_agents.hr_ai_agents.runtime import registry
    from hr_ai_agents.hr_ai_agents.runtime.runner import AgentRun

    user = user or frappe.session.user
    qa = frappe.get_doc("AI Quick Action", name)
    if not qa.enabled or qa.name not in {a["name"] for a in quick_actions(qa.agent, user)}:
        raise frappe.PermissionError("This quick action is not available to you.")
    registry._load()
    tool = registry.TOOLS.get(qa.tool_name)
    if not tool or tool.get("writes"):
        raise frappe.ValidationError("This quick action is not configured correctly.")
    declared = {p.get("name") for p in json.loads(qa.parameters or "[]") if p.get("name")}
    args = json.loads(qa.arguments or "{}")
    for k, v in (params or {}).items():
        if k in declared and v not in (None, ""):
            args[k] = v
    denied = None
    with AgentRun(qa.agent, f"[Quick action] {qa.title}", user=user, context={"served_from": "quick_action", "quick_action": qa.name, "args": args}) as run:
        if qa.tool_name not in [t.tool_name for t in (run.agent.get("allowed_tools") or [])]:
            denied = f"{qa.agent} does not have the {qa.tool_name} tool."
        else:
            try:
                result = tool["fn"](run, **args)
                run.tool_calls.append({"tool": qa.tool_name, "args": args, "ok": True})
                run.response = render(result)
            except (frappe.PermissionError, frappe.ValidationError) as e:
                denied = str(e) or "You do not have access to this information."
                run.tool_calls.append({"tool": qa.tool_name, "args": args, "ok": False, "error": denied[:300]})
                frappe.clear_messages()
        if denied:
            run.outcome, run.error, run.response = "Blocked", denied[:300], denied
    if denied:
        return {"error": denied, "run": run.run_id}
    return {"answer": run.response, "run": run.run_id, "tokens": 0, "model": None, "served_from": "quick_action", "title": qa.title}


# ------------------------------------------------------------------ plain rendering (no model)
def _label(k):
    return str(k).replace("_", " ").strip().capitalize()


def _cell(v):
    if v is None or v == "":
        return "-"
    if isinstance(v, (dict, list)):
        return ", ".join(f"{_label(k)}: {_cell(x)}" for k, x in v.items()) if isinstance(v, dict) else ", ".join(str(_cell(x)) for x in v)
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    return str(v).replace("|", "/").replace("\n", " ")


def _table(rows):
    cols = []
    for r in rows[:200]:
        for k in r:
            if k not in cols and len(cols) < 10:
                cols.append(k)
    out = ["| " + " | ".join(_label(c) for c in cols) + " |", "|" + "---|" * len(cols)]
    for r in rows[:200]:
        out.append("| " + " | ".join(_cell(r.get(c)) for c in cols) + " |")
    if len(rows) > 200:
        out.append(f"\n_Showing 200 of {len(rows)} rows._")
    return "\n".join(out)


def render(result):
    """Result of a read tool -> Markdown: scalars as lines, lists of records as tables."""
    if isinstance(result, list):
        if not result:
            return "Nothing to show."
        return _table(result) if all(isinstance(x, dict) for x in result) else "\n".join(f"- {_cell(x)}" for x in result)
    if not isinstance(result, dict):
        return str(result)
    parts, notes = [], []
    for k, v in result.items():
        if k in ("note", "notes"):
            notes.append(str(v))
        elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            parts.append(f"**{_label(k)}** ({len(v)})\n\n{_table(v)}")
        elif isinstance(v, list) and not v:
            parts.append(f"**{_label(k)}:** none")
        elif isinstance(v, list):
            parts.append(f"**{_label(k)}:** " + _cell(v))
        elif isinstance(v, dict) and v and all(isinstance(x, dict) for x in v.values()):
            parts.append(f"**{_label(k)}**\n\n" + _table([{"name": a, **b} for a, b in v.items()]))
        else:
            parts.append(f"**{_label(k)}:** {_cell(v)}")
    text = "\n\n".join(parts) or "Nothing to show."
    if notes:
        text += "\n\n_" + " ".join(notes) + "_"
    return text[:60000]
