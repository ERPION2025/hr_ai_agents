"""Pure-python redaction helpers (no frappe import, unit-testable).

Two jobs:
1. mask()/unmask(): replace emails, phone numbers and long ID-like numbers by tokens before text
   goes to a model, and restore them when rendering to an authorised user.
2. strip_protected(): remove lines of a CV that carry protected attributes, and replace the
   candidate's own name, so the ATS never scores on them.
"""
import hashlib
import re

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().\-]{7,}\d)(?!\w)")
LONG_ID_RE = re.compile(r"\b\d{9,}\b")
URL_RE = re.compile(r"https?://\S+")

PROTECTED_LINE_RE = re.compile(
    r"^\s*(?:date of birth|dob|d\.o\.b|birth\s*date|gender|sex|age|nationality|marital status|"
    r"religion|caste|father'?s name|mother'?s name|spouse|passport(?: no\.?| number)?|"
    r"national id|qid|aadhaar|aadhar|pan\b|visa status|permanent address|current address|address)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)

INJECTION_RE = re.compile(
    r"(ignore (?:all |any )?(?:previous|prior|above) (?:instructions|prompts)|disregard (?:the )?(?:above|previous)|"
    r"system prompt|you are (?:now )?an? (?:ai|assistant|language model)|score (?:this|me) (?:100|highly|10/10)|"
    r"rate this candidate (?:as )?(?:excellent|perfect)|do not (?:flag|reject))",
    re.IGNORECASE,
)


def mask(text, token_map=None):
    """Return (masked_text, token_map). token_map maps token -> original."""
    token_map = {} if token_map is None else token_map
    reverse = {v: k for k, v in token_map.items()}
    counters = {"EMAIL": 0, "PHONE": 0, "ID": 0, "URL": 0}

    def _sub(kind):
        def inner(m):
            original = m.group(0)
            if original in reverse:
                return reverse[original]
            counters[kind] += 1
            token = f"[{kind}_{len(token_map) + 1}]"
            token_map[token] = original
            reverse[original] = token
            return token

        return inner

    out = text or ""
    out = EMAIL_RE.sub(_sub("EMAIL"), out)
    out = URL_RE.sub(_sub("URL"), out)
    out = LONG_ID_RE.sub(_sub("ID"), out)
    out = PHONE_RE.sub(_sub("PHONE"), out)
    return out, token_map


def unmask(text, token_map):
    """Restore originals. Models often drop the square brackets (EMAIL_1), so both forms are accepted."""
    out = text or ""
    for token, original in sorted((token_map or {}).items(), key=lambda kv: -len(kv[0])):
        bare = token.strip("[]")
        out = re.sub(r"\[?" + re.escape(bare) + r"\b\]?", lambda m, o=original: o, out)
    return out


def unmask_obj(obj, token_map):
    """Recursively restore tokens inside tool arguments (strings in dicts/lists)."""
    if isinstance(obj, str):
        return unmask(obj, token_map)
    if isinstance(obj, list):
        return [unmask_obj(x, token_map) for x in obj]
    if isinstance(obj, dict):
        return {k: unmask_obj(v, token_map) for k, v in obj.items()}
    return obj


def strip_protected(text, names=None):
    """Remove protected-attribute lines and replace the candidate's own name parts."""
    out = PROTECTED_LINE_RE.sub("", text or "")
    for n in sorted({n.strip() for n in (names or []) if n and len(n.strip()) > 1}, key=len, reverse=True):
        out = re.sub(re.escape(n), "[CANDIDATE]", out, flags=re.IGNORECASE)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out


def injection_flags(text):
    return sorted({m.group(0).lower() for m in INJECTION_RE.finditer(text or "")})


def sha256(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()
