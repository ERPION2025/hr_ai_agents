"""Auditor: reviews the agents' own logs for policy breaches and builds the monthly compliance pack.
Read-only on logs; writes only AI Finding and AI Compliance Pack."""
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime

import frappe
from frappe.utils import add_days, add_to_date, add_months, cint, flt, get_first_day, get_last_day, getdate, now_datetime, today

from hr_ai_agents.hr_ai_agents.agents import sla
from hr_ai_agents.hr_ai_agents.runtime import core

AGENT = "Auditor"
CHAINED = ("AI Agent Run Log", "AI Security Log", "AI Data Access Log")
AUDITOR_ROLES = ["AI Compliance Auditor", "AI Agent Manager", "System Manager"]


def verify_chain(doctype="AI Agent Run Log"):
    """Recompute the hash chain of an append-only log and report the first mismatch."""
    if doctype not in CHAINED:
        frappe.throw("Not a chained log")
    mod = frappe.scrub(doctype)
    hashed = frappe.get_attr(f"hr_ai_agents.hr_ai_agents.doctype.{mod}.{mod}.HASHED")
    rows = frappe.get_all(doctype, fields=["name", "prev_hash", "row_hash"] + hashed, order_by="creation asc, name asc", limit=200000)
    prev, bad = "GENESIS", None
    for r in rows:
        payload = json.dumps({f: str(r.get(f) or "") for f in hashed}, sort_keys=True)
        calc = hashlib.sha256((prev + payload).encode()).hexdigest()
        if r.prev_hash != prev or r.row_hash != calc:
            bad = r.name
            break
        prev = r.row_hash
    return {"log": doctype, "ok": bad is None, "checked": len(rows), "first_mismatch": bad}


def _local_hour(dt, s):
    from zoneinfo import ZoneInfo

    try:
        sys_tz = ZoneInfo(frappe.utils.get_system_timezone())
        return dt.replace(tzinfo=sys_tz).astimezone(ZoneInfo(s.timezone or "Asia/Qatar")).hour
    except Exception:
        return dt.hour


def _in_window(hour, start, end):
    return (hour >= start or hour < end) if start > end else (start <= hour < end)


def _finding(rule, who, severity, message, impact, evidence, fix, ref=None):
    return {"rule": rule, "who": who, "severity": severity, "message": message, "impact": impact, "evidence": evidence, "fix": fix, "ref": ref}


def detect(hours=24):
    """Pure read of the logs. Returns a list of finding dicts."""
    s = core.settings()
    since = add_to_date(now_datetime(), hours=-hours)
    out = []
    max_runs = cint(s.aud_max_runs_per_user_day) or 50
    deny_thr = cint(s.aud_denials_threshold) or 5
    a_start = sla._t(s.aud_after_hours_start, datetime.strptime("21:00", "%H:%M").time()).hour
    a_end = sla._t(s.aud_after_hours_end, datetime.strptime("05:00", "%H:%M").time()).hour

    ev = frappe.get_all("AI Security Log", filters={"creation": (">=", since)}, fields=["name", "event_type", "severity", "event_user", "agent", "tool", "detail"], limit=5000)
    by = defaultdict(list)
    for e in ev:
        by[e.event_type].append(e)

    for u, rows in _group(by.get("Out-of-role request", []), "event_user").items():
        out.append(_finding("aud-out-of-role", u, "High" if len(rows) >= 3 else "Medium", f"{u} tried to use AI agents without the AI Agent User role ({len(rows)} time(s))", "Someone without access is attempting to use AI features.", rows[0].detail, "Confirm whether the user should have access; check for shared credentials.", ("AI Security Log", rows[0].name)))
    for u, rows in _group(by.get("Permission denied", []), "event_user").items():
        if len(rows) >= deny_thr:
            out.append(_finding("aud-repeated-denials", u, "Medium", f"{u} was refused data {len(rows)} times in {hours}h (limit {deny_thr})", "Repeated attempts to read records outside their permission may indicate probing.", "; ".join(sorted({r.detail[:120] for r in rows})[:3]), "Review the requests in the AI Agent Run Log for this user.", ("AI Security Log", rows[0].name)))
    for (u, a), rows in _group2(by.get("Blocked destructive action", []), "event_user", "agent").items():
        out.append(_finding("aud-destructive", f"{u}/{a}", "High", f"{a} (asked by {u}) tried to delete, submit, cancel or edit an existing record {len(rows)} time(s); the guard blocked it", "An agent attempted an action it is not allowed to take. Nothing was changed.", rows[0].detail, "Review the run log for the prompt; if the prompt was injected content, tighten the source data.", ("AI Security Log", rows[0].name)))
    for et in ("Unknown tool requested", "Write outside app doctypes refused"):
        for (u, a), rows in _group2(by.get(et, []), "event_user", "agent").items():
            out.append(_finding("aud-" + et.lower().replace(" ", "-"), f"{u}/{a}", "High", f"{et}: {a} / {u} ({len(rows)})", "A model tried to use a capability that is not registered.", rows[0].detail, "Review the run log; this can indicate prompt injection.", ("AI Security Log", rows[0].name)))
    for u, rows in _group(by.get("Budget or rate limit block", []), "event_user").items():
        if len(rows) >= 3:
            out.append(_finding("aud-limit-blocks", u, "Low", f"{u} hit budget or rate limits {len(rows)} times", "Possible automated or excessive use.", rows[0].detail, "Check whether the limits suit this user's work.", ("AI Security Log", rows[0].name)))

    runs = frappe.get_all("AI Agent Run Log", filters={"creation": (">=", since), "trigger": "User"}, fields=["name", "run_user", "agent", "creation", "outcome", "cost"], limit=20000)
    per_user = Counter(r.run_user for r in runs)
    for u, n in per_user.items():
        if n > max_runs:
            out.append(_finding("aud-high-volume", u, "Medium", f"{u} made {n} agent requests in {hours}h (limit {max_runs})", "Unusually high use may indicate scripted or bulk extraction of data.", "", "Ask the user about the use case or lower their rate limit.", ("AI Agent Run Log", next(r.name for r in runs if r.run_user == u))))
    after = defaultdict(list)
    for r in runs:
        if _in_window(_local_hour(r.creation, s), a_start, a_end):
            after[r.run_user].append(r)
    for u, rows in after.items():
        out.append(_finding("aud-after-hours", u, "Medium" if len(rows) >= 5 else "Low", f"{u} used AI agents outside working hours ({len(rows)} request(s))", "After-hours access is reviewed as part of access monitoring.", ", ".join(sorted({r.agent for r in rows})), "Confirm it was expected (for example a different time zone).", ("AI Agent Run Log", rows[0].name)))
    errs = [r for r in runs if r.outcome == "Error"]
    if len(errs) >= 5:
        out.append(_finding("aud-error-rate", "system", "Low", f"{len(errs)} agent runs failed in {hours}h", "Repeated failures reduce reliability and may hide misuse.", "", "Check Error Log and provider status."))

    dal = frappe.db.sql("select access_user, count(distinct concat(reference_doctype, '|', reference_name)) from `tabAI Data Access Log` where creation >= %s group by access_user", (since,))
    for u, n in dal:
        if cint(n) > max_runs * 4:
            out.append(_finding("aud-data-volume", u, "Medium", f"Records of {n} different documents reached the model for {u} in {hours}h", "Large volumes of personal data sent to the provider.", "", "Review the Data Access Log for this user."))

    today_cost = sum(flt(r.cost) for r in frappe.get_all("AI Agent Run Log", filters={"creation": (">=", since)}, fields=["cost"], limit=20000))
    prev = frappe.db.sql("select coalesce(sum(cost),0) from `tabAI Agent Run Log` where creation >= %s and creation < %s", (add_days(since, -7), since))[0][0]
    avg = flt(prev) / 7
    if today_cost > 1 and avg and today_cost > 3 * avg:
        out.append(_finding("aud-cost-spike", "system", "Medium", f"Model cost in the last {hours}h ({today_cost:,.2f}) is over 3x the 7-day daily average ({avg:,.2f})", "Unexpected spending.", "", "Check the AI Usage by User report."))

    for dt in CHAINED:
        v = verify_chain(dt)
        if not v["ok"]:
            out.append(_finding("aud-chain-broken", dt, "High", f"Integrity check failed for {dt}: first mismatch at {v['first_mismatch']}", "An audit record was changed or removed outside the application.", json.dumps(v), "Treat as a security incident: preserve the database and investigate.", (dt, v["first_mismatch"])))
    return out


def _group(rows, key):
    d = defaultdict(list)
    for r in rows:
        d[r.get(key) or "unknown"].append(r)
    return d


def _group2(rows, k1, k2):
    d = defaultdict(list)
    for r in rows:
        d[(r.get(k1) or "unknown", r.get(k2) or "unknown")].append(r)
    return d


def save(run, findings):
    day = today()
    saved = []
    now = now_datetime()
    for f in findings:
        fp = hashlib.sha1(f"{f['rule']}|{f['who']}|{day}".encode()).hexdigest()
        if frappe.db.exists("AI Finding", {"fingerprint": fp}):
            frappe.db.set_value("AI Finding", {"fingerprint": fp}, "last_seen", now, update_modified=False)
            continue
        ref = f.get("ref") or (None, None)
        d = run.create_own({
            "doctype": "AI Finding", "message": f["message"][:140], "severity": f["severity"], "status": "Open", "finding_type": "Agent policy",
            "reference_doctype": ref[0], "reference_name": ref[1], "impact": f["impact"], "evidence": f["evidence"], "suggested_fix": f["fix"],
            "rule": f["rule"], "detected_by": AGENT, "detected_on": now, "last_seen": now, "fingerprint": fp,
        })
        saved.append(d.name)
    return saved


def daily_scan(run, hours=24):
    fs = detect(hours)
    saved = save(run, fs)
    run.response = f"{len(fs)} observation(s), {len(saved)} new finding(s)."
    return fs


# ------------------------------------------------------------------ monthly pack
def pack_snapshot(start, end):
    s = core.settings()
    st, en = f"{start} 00:00:00", f"{end} 23:59:59"
    q = lambda sql, v=(): frappe.db.sql(sql, (st, en) + tuple(v), as_dict=True)  # noqa: E731
    runs = q("select agent, outcome, count(*) n, coalesce(sum(input_tokens+output_tokens),0) tokens, coalesce(sum(cost),0) cost from `tabAI Agent Run Log` where creation between %s and %s group by agent, outcome")
    users = frappe.db.sql("select count(distinct run_user) from `tabAI Agent Run Log` where creation between %s and %s and trigger_ = 'User'".replace("trigger_", "`trigger`"), (st, en))[0][0]
    sec = q("select event_type, severity, count(*) n from `tabAI Security Log` where creation between %s and %s group by event_type, severity")
    fnd = q("select finding_type, severity, status, count(*) n from `tabAI Finding` where creation between %s and %s group by finding_type, severity, status")
    prop = q("select status, count(*) n from `tabAI Proposal` where creation between %s and %s group by status")
    dsr = q("select request_type, status, count(*) n from `tabAI Data Subject Request` where creation between %s and %s group by request_type, status")
    dal = frappe.db.sql("select count(*) from `tabAI Data Access Log` where creation between %s and %s", (st, en))[0][0]
    chains = [verify_chain(d) for d in CHAINED]
    agents = frappe.get_all("AI Agent Profile", fields=["name", "enabled", "autonomy", "role", "lawful_basis"], order_by="name")
    last_eval = frappe.get_all("AI Eval Run", fields=["name", "run_on", "total", "passed", "failed", "skipped"], order_by="creation desc", limit=1)
    return {
        "period": [str(start), str(end)], "provider": s.provider, "model_calls_approved": bool(s.model_calls_approved), "store_prompts": s.store_prompts,
        "retention_months": {"logs": s.log_retention_months, "content": s.content_retention_months}, "runs": runs, "distinct_users": users,
        "security_events": sec, "findings": fnd, "proposals": prop, "data_subject_requests": dsr, "data_access_rows": dal, "chains": chains,
        "agents": agents, "last_eval": last_eval[0] if last_eval else None,
    }


def _table(rows, cols):
    if not rows:
        return "<p><i>None</i></p>"
    h = "<table class='table table-bordered'><tr>" + "".join(f"<th>{c}</th>" for c in cols) + "</tr>"
    for r in rows:
        h += "<tr>" + "".join(f"<td>{frappe.utils.escape_html(str(r.get(c, '')))}</td>" for c in cols) + "</tr>"
    return h + "</table>"


def build_pack(start=None, end=None):
    """Previous calendar month by default. One pack per month."""
    if not start:
        first_this = get_first_day(today())
        end = add_days(first_this, -1)
        start = get_first_day(end)
    if frappe.db.exists("AI Compliance Pack", {"period_start": start, "period_end": end}):
        return frappe.get_doc("AI Compliance Pack", {"period_start": start, "period_end": end})
    snap = pack_snapshot(start, end)
    total = sum(r["n"] for r in snap["runs"])
    cost = sum(flt(r["cost"]) for r in snap["runs"])
    html = (
        f"<h4>AI agents: monthly governance pack {start} to {end}</h4>"
        f"<p>Provider: <b>{snap['provider']}</b>. Data transfer to provider approved: <b>{'Yes' if snap['model_calls_approved'] else 'No'}</b>. "
        f"Prompt storage: <b>{snap['store_prompts']}</b>. Retention (months): logs {snap['retention_months']['logs']}, content {snap['retention_months']['content']}.</p>"
        f"<p>Agent requests: <b>{total}</b> by <b>{snap['distinct_users']}</b> user(s); model cost <b>{cost:,.2f}</b>; "
        f"rows in the data-access log: <b>{snap['data_access_rows']}</b>.</p>"
        "<h5>Runs by agent and outcome</h5>" + _table(snap["runs"], ["agent", "outcome", "n", "tokens", "cost"])
        + "<h5>Security events</h5>" + _table(snap["security_events"], ["event_type", "severity", "n"])
        + "<h5>Findings raised</h5>" + _table(snap["findings"], ["finding_type", "severity", "status", "n"])
        + "<h5>Proposals</h5>" + _table(snap["proposals"], ["status", "n"])
        + "<h5>Data subject requests</h5>" + _table(snap["data_subject_requests"], ["request_type", "status", "n"])
        + "<h5>Log integrity</h5>" + _table(snap["chains"], ["log", "ok", "checked", "first_mismatch"])
        + "<h5>Agent roster</h5>" + _table(snap["agents"], ["name", "enabled", "autonomy", "role", "lawful_basis"])
    )
    doc = frappe.get_doc({
        "doctype": "AI Compliance Pack", "period_start": start, "period_end": end, "chain_ok": 1 if all(c["ok"] for c in snap["chains"]) else 0,
        "summary": html, "snapshot": json.dumps(snap, default=str, indent=1),
    })
    doc.insert(ignore_permissions=True)
    return doc
