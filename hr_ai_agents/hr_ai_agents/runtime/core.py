"""Settings access, gating, budgets, session + audit logging for every agent run."""
import hashlib
import json
from datetime import timedelta

import frappe
from frappe.utils import add_to_date, cint, flt, get_datetime, now_datetime

SETTINGS = "AI Agent Settings"


class AgentBlocked(frappe.ValidationError):
    """Raised when a run is refused (disabled, budget, permission...). Message is user-safe."""


def settings():
    return frappe.get_cached_doc(SETTINGS)


def is_enabled():
    s = settings()
    return bool(s.enabled) and not s.kill_switch


def user_opted_out(user):
    return bool(frappe.db.get_value("AI User Preference", user, "opted_out"))


def user_can_use(user=None):
    user = user or frappe.session.user
    if user == "Administrator":
        return True
    return "AI Agent User" in frappe.get_roles(user) or "AI Agent Manager" in frappe.get_roles(user)


def provider_cfg():
    s = settings()
    if s.provider == "Azure OpenAI":
        return {
            "endpoint": s.azure_endpoint,
            "api_key": s.get_password("azure_api_key", raise_exception=False),
            "api_version": s.azure_api_version,
            "timeout": cint(s.request_timeout) or 60,
        }
    return {
        "api_key": s.get_password("anthropic_api_key", raise_exception=False),
        "workspace_id": (s.get("anthropic_workspace_id") or "").strip(),
        "timeout": cint(s.request_timeout) or 60,
        "temperature": 0.2,
    }


def price_for(model):
    for row in settings().get("prices") or []:
        if (row.model or "").strip().lower() == (model or "").strip().lower():
            return flt(row.input_per_million), flt(row.output_per_million)
    return 0.0, 0.0


def cost_of(model, in_tokens, out_tokens):
    pin, pout = price_for(model)
    return round(in_tokens * pin / 1e6 + out_tokens * pout / 1e6, 6)


# ---------------- budgets / limits ----------------
def _tokens_since(start, agent=None, user=None):
    cond, vals = ["creation >= %(start)s"], {"start": start}
    if agent:
        cond.append("agent = %(agent)s")
        vals["agent"] = agent
    if user:
        cond.append("run_user = %(user)s")
        vals["user"] = user
    row = frappe.db.sql(
        f"select coalesce(sum(input_tokens + output_tokens), 0) from `tabAI Agent Run Log` where {' and '.join(cond)}",
        vals,
    )
    return cint(row[0][0]) if row else 0


def check_limits(agent, user):
    """Raises AgentBlocked when a budget or rate limit is hit. Returns 'downgrade' when the
    configured over-budget action is to switch to the fallback model."""
    s = settings()
    now = now_datetime()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)
    action = "ok"
    checks = [
        ("daily token budget", cint(s.daily_token_budget), lambda: _tokens_since(day_start)),
        ("monthly token budget", cint(s.monthly_token_budget), lambda: _tokens_since(month_start)),
        (
            f"{agent.name} monthly token budget",
            cint(agent.monthly_token_budget),
            lambda: _tokens_since(month_start, agent=agent.name),
        ),
        (
            "your daily token budget",
            cint(s.per_user_daily_token_budget),
            lambda: _tokens_since(day_start, user=user),
        ),
    ]
    for label, limit, used_fn in checks:
        if limit and used_fn() >= limit:
            if (s.over_budget_action or "Block") == "Use fallback model" and s.fallback_model:
                action = "downgrade"
            else:
                raise AgentBlocked(f"The {label} has been reached. Ask an AI Agent Manager to raise it in AI Agent Settings.")

    per_hour = cint(s.per_user_runs_per_hour)
    if per_hour:
        n = frappe.db.count(
            "AI Agent Run Log", {"run_user": user, "creation": (">=", add_to_date(now, hours=-1))}
        )
        if n >= per_hour:
            raise AgentBlocked("Hourly request limit reached. Please try again later.")
    per_day = cint(agent.max_runs_per_day)
    if per_day:
        n = frappe.db.count("AI Agent Run Log", {"agent": agent.name, "creation": (">=", day_start)})
        if n >= per_day:
            raise AgentBlocked(f"{agent.name} has reached today's run limit.")
    return action


# ---------------- hash chain ----------------
def _last_hash(doctype):
    row = frappe.db.sql(f"select row_hash from `tab{doctype}` order by creation desc, name desc limit 1")
    return (row[0][0] if row and row[0][0] else "GENESIS")


def stamp_hash(doc, fields):
    """Called from before_insert of append-only logs. row_hash = sha256(prev_hash + canonical fields)."""
    prev = _last_hash(doc.doctype)
    payload = json.dumps({f: str(doc.get(f) or "") for f in fields}, sort_keys=True)
    doc.prev_hash = prev
    doc.row_hash = hashlib.sha256((prev + payload).encode()).hexdigest()


# ---------------- security events ----------------
def security_event(event_type, detail="", severity="Medium", user=None, agent=None, tool=None, run=None):
    try:
        doc = frappe.get_doc(
            {
                "doctype": "AI Security Log",
                "event_type": event_type,
                "severity": severity,
                "event_user": user or frappe.session.user,
                "agent": agent,
                "tool": tool,
                "run": run,
                "detail": (detail or "")[:4000],
            }
        )
        doc.insert(ignore_permissions=True)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AI Security Log write failed")


# ---------------- sessions ----------------
def get_session(user, trigger="User"):
    """One AI Session Log per user per 30 minutes of activity (scheduler runs get their own)."""
    ip = getattr(frappe.local, "request_ip", None) or ""
    ua = ""
    try:
        ua = (frappe.request.headers.get("User-Agent") or "")[:300] if frappe.request else ""
    except Exception:
        pass
    sid = ""
    try:
        sid = hashlib.sha256((frappe.session.sid or "").encode()).hexdigest()[:24]
    except Exception:
        pass
    cutoff = add_to_date(now_datetime(), minutes=-30)
    filters = {"session_user": user, "trigger": trigger, "last_active": (">=", cutoff)}
    if sid and trigger == "User":
        filters["sid_hash"] = sid
    name = frappe.db.get_value("AI Session Log", filters, "name", order_by="last_active desc")
    if name:
        frappe.db.set_value(
            "AI Session Log", name, {"last_active": now_datetime()}, update_modified=False
        )
        return name
    doc = frappe.get_doc(
        {
            "doctype": "AI Session Log",
            "session_user": user,
            "trigger": trigger,
            "sid_hash": sid,
            "ip_address": ip,
            "user_agent": ua,
            "started": now_datetime(),
            "last_active": now_datetime(),
            "run_count": 0,
        }
    )
    doc.insert(ignore_permissions=True)
    return doc.name


def bump_session(session, tokens=0):
    if not session:
        return
    frappe.db.sql(
        "update `tabAI Session Log` set run_count = run_count + 1, last_active = %s where name = %s",
        (now_datetime(), session),
    )
