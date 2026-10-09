"""Atlas: system-manager reporting agent. Every figure is a database query saved as the report's
data snapshot; the model (when allowed) only writes the narrative, which is rejected if it
contains a number that is not in the snapshot."""
import json
import re

import frappe
from frappe.utils import add_days, cint, flt, getdate, now_datetime, today

from hr_ai_agents.hr_ai_agents.agents import sla as sla_mod
from hr_ai_agents.hr_ai_agents.runtime import core

AGENT = "Atlas"
STALE_DAYS = 7

PLAN_FIELDS = [
    "name", "workflow_state", "fiscal_year", "department", "currency", "custom_project_name",
    "approved_headcount", "committed_headcount", "consumed_headcount", "remaining_headcount",
    "approved_budget", "committed_budget", "consumed_budget", "remaining_budget",
    "custom_headcount_utilization_pct", "custom_budget_utilization_pct", "custom_budget_line_not_confirmed", "modified",
]
REQ_FIELDS = ["name", "custom_requisition_title", "workflow_state", "status", "posting_date", "expected_by", "department", "custom_budget_plan", "modified"]
OPEN_FIELDS = [
    "name", "job_title", "status", "job_requisition", "vacancies", "custom_vendor", "custom_pushed_to_vendor",
    "custom_vendor_ageing_days", "custom_vendor_sla_days", "custom_sla_breached",
]
APPROVAL_DOCTYPES = ["Workforce Plan", "Job Requisition", "CXO Hiring Approval", "Probation Review", "Leave Application", "Employee Exit"]
CLOSED_WORDS = ("closed", "cancel", "reject", "fill", "complete", "approved")


def _fields(doctype, wanted):
    meta = frappe.get_meta(doctype)
    return [f for f in wanted if f == "name" or f == "modified" or meta.has_field(f)]


def _days_since(dt):
    return (getdate(today()) - getdate(dt)).days if dt else None


def _table_field(parent_dt, child_dt):
    for df in frappe.get_meta(parent_dt).get_table_fields():
        if df.options == child_dt:
            return df.fieldname
    return None


# ------------------------------------------------------------------ data
def requisition_block(run, req):
    req = frappe._dict(req)
    out = {k: req.get(k) for k in REQ_FIELDS if k in req}
    out["days_since_last_change"] = _days_since(req.get("modified"))
    out["stale"] = bool(out["days_since_last_change"] is not None and out["days_since_last_change"] >= STALE_DAYS and not any(w in (req.get("workflow_state") or "").lower() for w in CLOSED_WORDS))
    out["overdue_vs_expected_by"] = bool(req.get("expected_by") and getdate(req.expected_by) < getdate(today()) and not any(w in (req.get("status") or req.get("workflow_state") or "").lower() for w in CLOSED_WORDS))
    # roles
    roles = []
    if run.can("Job Requisition Role", "read") or run.can("Job Requisition", "read"):
        roles = frappe.get_all(
            "Job Requisition Role",
            filters={"parent": req.name, "parenttype": "Job Requisition"},
            fields=_fields("Job Requisition Role", ["designation", "no_of_positions", "total_estimated_cost_usd", "estimated_cost_usd", "vendor"]),
        )
    out["roles"] = len(roles)
    out["positions"] = sum(cint(r.get("no_of_positions")) for r in roles)
    out["estimated_cost_usd"] = round(sum(flt(r.get("total_estimated_cost_usd") or r.get("estimated_cost_usd")) for r in roles), 2)
    # openings
    openings = []
    if run.can("Job Opening", "read"):
        openings = frappe.get_all("Job Opening", filters={"job_requisition": req.name}, fields=_fields("Job Opening", OPEN_FIELDS))
    out["openings"] = [
        {k: o.get(k) for k in ("name", "job_title", "status", "vacancies", "custom_vendor", "custom_vendor_ageing_days", "custom_vendor_sla_days", "custom_sla_breached") if k in o}
        for o in openings
    ]
    out["vendor_sla_breached"] = sum(1 for o in openings if o.get("custom_sla_breached"))
    # applicants
    by_status, scores = {}, {}
    if openings and run.can("Job Applicant", "read"):
        names = [o.name for o in openings]
        for r in frappe.get_all("Job Applicant", filters={"job_title": ("in", names)}, fields=["status", "count(name) as n"], group_by="status"):
            by_status[r.status or "Unset"] = cint(r.n)
        if run.can("Applicant ATS Result", "read"):
            for r in frappe.get_all("Applicant ATS Result", filters={"is_latest": 1, "job_opening": ("in", names)}, fields=["avg(score) as avg_score", "count(name) as n"]):
                scores = {"scored": cint(r.n), "average": round(flt(r.avg_score), 1) if r.n else None}
    out["applicants_by_status"] = by_status
    out["applicants_total"] = sum(by_status.values())
    out["ats"] = scores
    return out


def plan_block(run, plan):
    plan = frappe._dict(plan)
    out = {k: plan.get(k) for k in PLAN_FIELDS if k in plan}
    out["days_since_last_change"] = _days_since(plan.get("modified"))
    reqs = []
    if run.can("Job Requisition", "read"):
        reqs = frappe.get_all(
            "Job Requisition", filters={"custom_budget_plan": plan.name, "docstatus": ("<", 2)}, fields=_fields("Job Requisition", REQ_FIELDS), order_by="modified desc", limit=50
        )
    out["requisitions"] = [requisition_block(run, r) for r in reqs]
    out["requisition_cost_usd"] = round(sum(r["estimated_cost_usd"] for r in out["requisitions"]), 2)
    applicants = {}
    if run.can("Job Applicant", "read") and frappe.get_meta("Job Applicant").has_field("custom_budget_plan"):
        for r in frappe.get_all("Job Applicant", filters={"custom_budget_plan": plan.name}, fields=["status", "count(name) as n"], group_by="status"):
            applicants[r.status or "Unset"] = cint(r.n)
        out["joined"] = frappe.db.count("Job Applicant", {"custom_budget_plan": plan.name, "custom_employee_created": 1}) if frappe.get_meta("Job Applicant").has_field("custom_employee_created") else None
    out["applicants_by_status"] = applicants
    return out


def org_block(run):
    out = {"pending_by_state": {}, "sla": {}, "findings_open": {}, "agent_usage_today": {}}
    for dt in APPROVAL_DOCTYPES:
        if not frappe.db.exists("DocType", dt) or not frappe.get_meta(dt).has_field("workflow_state") or not run.can(dt, "read"):
            continue
        rows = frappe.get_all(dt, filters={"docstatus": ("<", 2)}, fields=["workflow_state", "count(name) as n"], group_by="workflow_state")
        out["pending_by_state"][dt] = {(r.workflow_state or "No state"): cint(r.n) for r in rows}
    for r in frappe.get_all("SLA Alert Log", filters={"status": ("!=", "Dismissed")}, fields=["level", "status", "count(name) as n"], group_by="level, status"):
        out["sla"][f"{r.level} / {r.status}"] = cint(r.n)
    out["sla_new_today"] = frappe.db.count("SLA Alert Log", {"creation": (">=", today())})
    for r in frappe.get_all("AI Finding", filters={"status": ("in", ["Open", "Acknowledged"])}, fields=["severity", "count(name) as n"], group_by="severity"):
        out["findings_open"][r.severity] = cint(r.n)
    u = frappe.db.sql(
        "select count(*), coalesce(sum(input_tokens+output_tokens),0), coalesce(sum(cost),0) from `tabAI Agent Run Log` where creation >= %s",
        (today(),),
    )[0]
    out["agent_usage_today"] = {"runs": cint(u[0]), "tokens": cint(u[1]), "cost": round(flt(u[2]), 4)}
    return out


def collect_snapshot(run, subject_doctype=None, subject_name=None):
    s = core.settings()
    snap = {"generated_at": str(now_datetime()), "report_date": today(), "scope": "all" if not subject_name else f"{subject_doctype} {subject_name}"}
    if subject_doctype == "Job Requisition" and subject_name:
        run.require("Job Requisition", "read", doc=subject_name)
        r = frappe.get_all("Job Requisition", filters={"name": subject_name}, fields=_fields("Job Requisition", REQ_FIELDS))
        if not r:
            frappe.throw(f"Job Requisition {subject_name} not found")
        snap["requisitions"] = [requisition_block(run, r[0])]
        plan = r[0].get("custom_budget_plan")
        snap["plans"] = []
        if plan and run.can("Workforce Plan", "read"):
            p = frappe.get_all("Workforce Plan", filters={"name": plan}, fields=_fields("Workforce Plan", PLAN_FIELDS))
            if p:
                pb = plan_block(run, p[0])
                pb["requisitions"] = [x for x in pb["requisitions"] if x["name"] == subject_name]
                snap["plans"] = [pb]
        run.touch("Job Requisition", subject_name)
    else:
        run.require("Workforce Plan", "read")
        filters = {"docstatus": ("<", 2)}
        if subject_doctype == "Workforce Plan" and subject_name:
            filters["name"] = subject_name
        plans = frappe.get_all(
            "Workforce Plan", filters=filters, fields=_fields("Workforce Plan", PLAN_FIELDS), order_by="modified desc", limit=cint(s.atlas_plan_limit) or 50
        )
        snap["plans"] = [plan_block(run, p) for p in plans]
        for p in plans:
            run.touch("Workforce Plan", p.name)
    snap["org"] = org_block(run)
    snap["attention"] = attention_items(snap)
    return snap


def attention_items(snap):
    items = []
    for p in snap.get("plans", []):
        if flt(p.get("remaining_budget")) < 0:
            items.append({"severity": "High", "text": f"Workforce Plan {p['name']}: remaining budget is negative ({flt(p.get('remaining_budget')):,.0f} {p.get('currency') or ''})."})
        if flt(p.get("committed_budget")) > flt(p.get("approved_budget")) > 0:
            items.append({"severity": "High", "text": f"Workforce Plan {p['name']}: committed budget {flt(p.get('committed_budget')):,.0f} exceeds approved {flt(p.get('approved_budget')):,.0f}."})
        if p.get("custom_budget_line_not_confirmed"):
            items.append({"severity": "Medium", "text": f"Workforce Plan {p['name']}: budget line not confirmed."})
        for r in p.get("requisitions", []):
            items.extend(_req_attention(r))
    for r in snap.get("requisitions", []):
        items.extend(_req_attention(r))
    seen, out = set(), []
    for i in items:
        if i["text"] not in seen:
            seen.add(i["text"])
            out.append(i)
    order = {"High": 0, "Medium": 1, "Low": 2}
    return sorted(out, key=lambda i: order[i["severity"]])[:15]


def _req_attention(r):
    items = []
    if r.get("stale"):
        items.append({"severity": "Medium", "text": f"Requisition {r['name']} has been in '{r.get('workflow_state')}' for {r['days_since_last_change']} days."})
    if r.get("overdue_vs_expected_by"):
        items.append({"severity": "Medium", "text": f"Requisition {r['name']} is past its expected-by date ({r.get('expected_by')})."})
    if r.get("vendor_sla_breached"):
        items.append({"severity": "High", "text": f"Requisition {r['name']}: {r['vendor_sla_breached']} opening(s) with vendor SLA breached."})
    return items


# ------------------------------------------------------------------ narrative
def _num_tokens(text):
    return {t.replace(",", "").rstrip(".").lstrip("0") or "0" for t in re.findall(r"\d[\d,]*\.?\d*", text or "")}


def numbers_verified(narrative_text, snapshot):
    allowed = _num_tokens(json.dumps(snapshot, default=str))
    # also allow numbers derivable by simple rounding of snapshot values
    for t in list(allowed):
        try:
            v = float(t)
            allowed.add(str(int(round(v))))
            allowed.add(f"{v:.1f}".rstrip("0").rstrip("."))
        except ValueError:
            pass
    return all(t in allowed for t in _num_tokens(narrative_text))


def deterministic_narrative(snap):
    org = snap["org"]
    plans = snap.get("plans", [])
    reqs = sum(len(p.get("requisitions", [])) for p in plans) or len(snap.get("requisitions", []))
    lines = [f"<p>{len(plans)} workforce plan(s) and {reqs} requisition(s) in scope on {snap['report_date']}.</p>"]
    u = org["agent_usage_today"]
    lines.append(f"<p>SLA alerts raised today: {org.get('sla_new_today', 0)}. Open findings: " + (", ".join(f"{k} {v}" for k, v in org["findings_open"].items()) or "none") + f". AI usage today: {u['runs']} run(s), {u['tokens']} token(s).</p>")
    att = snap.get("attention", [])
    if att:
        lines.append("<p><b>Needs attention today</b></p><ul>" + "".join(f"<li>[{i['severity']}] {frappe.utils.escape_html(i['text'])}</li>" for i in att[:10]) + "</ul>")
    else:
        lines.append("<p>Nothing needs attention today.</p>")
    return "".join(lines)


def ai_narrative(run, snap):
    if not run.model_ready():
        return None
    compact = json.dumps({k: snap[k] for k in ("report_date", "scope", "plans", "org", "attention")}, default=str)[:30000]
    res = run.call_model(
        [
            {"role": "system", "content": (
                "You write the top-level EOD HR hiring report for executives. Use ONLY facts and numbers present in the JSON. "
                "Do not compute new totals, percentages or averages. Do not use numbered lists. Output HTML fragments only (<p>, <ul>, <li>, <b>). "
                "Structure: <p> executive summary (max 120 words), then <p><b>Needs attention today</b></p> and a <ul> of at most 6 items with owner role if obvious."
            )},
            {"role": "user", "content": compact},
        ],
        max_tokens=700,
    )
    text = res["text"].strip()
    if not text or not numbers_verified(re.sub(r"<[^>]+>", " ", text), snap):
        return None
    return text


def _fmt_status(d):
    return ", ".join(f"{k}: {v}" for k, v in (d or {}).items()) or "-"


def render_html(snap, narrative):
    esc = frappe.utils.escape_html
    h = [f"<h2>HR Hiring Progress Report: {esc(snap['report_date'])}</h2>", narrative]
    for p in snap.get("plans", []):
        h.append(
            f"<h3>Workforce Plan {esc(p['name'])} <small>({esc(p.get('workflow_state') or '')})</small></h3>"
            f"<p>Headcount approved {p.get('approved_headcount')}, committed {p.get('committed_headcount')}, consumed {p.get('consumed_headcount')}, remaining {p.get('remaining_headcount')}. "
            f"Budget approved {flt(p.get('approved_budget')):,.0f}, committed {flt(p.get('committed_budget')):,.0f}, consumed {flt(p.get('consumed_budget')):,.0f}, remaining {flt(p.get('remaining_budget')):,.0f} {esc(p.get('currency') or '')}. "
            f"Applicants: {esc(_fmt_status(p.get('applicants_by_status')))}.</p>"
        )
        if p.get("requisitions"):
            h.append("<table border='1' cellpadding='4' style='border-collapse:collapse;width:100%;font-size:11px'><tr><th>Requisition</th><th>State</th><th>Positions</th><th>Est. cost USD</th><th>Openings</th><th>Applicants</th><th>ATS avg</th><th>Days since change</th></tr>")
            for r in p["requisitions"]:
                h.append(
                    f"<tr><td>{esc(r['name'])}<br>{esc(r.get('custom_requisition_title') or '')}</td><td>{esc(r.get('workflow_state') or '')}</td>"
                    f"<td>{r['positions']}</td><td>{r['estimated_cost_usd']:,.0f}</td><td>{len(r['openings'])}"
                    + (f" ({r['vendor_sla_breached']} SLA breached)" if r["vendor_sla_breached"] else "")
                    + f"</td><td>{esc(_fmt_status(r['applicants_by_status']))}</td><td>{(r.get('ats') or {}).get('average') or '-'}</td><td>{r.get('days_since_last_change')}</td></tr>"
                )
            h.append("</table>")
    for r in snap.get("requisitions", []):
        if not snap.get("plans"):
            h.append(f"<h3>Requisition {esc(r['name'])}: {esc(r.get('workflow_state') or '')}</h3><p>Positions {r['positions']}, estimated cost USD {r['estimated_cost_usd']:,.0f}. Applicants: {esc(_fmt_status(r['applicants_by_status']))}.</p>")
    org = snap["org"]
    h.append("<h3>Pending items by workflow state</h3><ul>" + "".join(f"<li>{esc(dt)}: {esc(_fmt_status(v))}</li>" for dt, v in org["pending_by_state"].items()) + "</ul>")
    h.append("<p style='font-size:10px;color:#666'>Figures are database queries saved with this report as its data snapshot. Narrative text is checked against that snapshot. Generated by Atlas (AI agent).</p>")
    return "\n".join(h)


# ------------------------------------------------------------------ build + deliver
def build_report(run, kind="On demand", subject_doctype=None, subject_name=None, email=False):
    s = core.settings()
    snap = collect_snapshot(run, subject_doctype, subject_name)
    narrative, source, model = None, "Deterministic", None
    try:
        narrative = ai_narrative(run, snap)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Atlas narrative")
    if narrative:
        source, model = "AI", run.model_used
    else:
        narrative = deterministic_narrative(snap)
    html = render_html(snap, narrative)
    doc = run.create_own(
        {
            "doctype": "AI Daily Report", "report_date": today(), "kind": kind,
            "subject_doctype": subject_doctype if subject_name else None, "subject_name": subject_name or None,
            "summary": html, "data_snapshot": json.dumps(snap, default=str), "figures_verified": 1,
            "narrative_source": source, "model": model,
        }
    )
    recipients = [x.strip() for x in (s.atlas_recipients or "").replace("\n", ",").split(",") if x.strip()]
    if email and recipients:
        try:
            from frappe.utils.pdf import get_pdf

            pdf = get_pdf(f"<html><body style='font-family:Arial'>{html}</body></html>")
            frappe.sendmail(
                recipients=recipients, subject=f"HR hiring progress report {today()}", message=html,
                attachments=[{"fname": f"HR-Report-{today()}.pdf", "fcontent": pdf}], now=False,
            )
            frappe.flags.ai_log_system_update = True
            frappe.db.set_value("AI Daily Report", doc.name, "emailed_to", ", ".join(recipients), update_modified=False)
            frappe.flags.ai_log_system_update = False
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Atlas email")
    run.response = f"Report {doc.name} created ({source} narrative)."
    return doc
