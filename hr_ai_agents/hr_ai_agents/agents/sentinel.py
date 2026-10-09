"""Sentinel: read-only anomaly detection and document-completeness review.
Writes AI Finding records only (never edits the source document). Findings are de-duplicated by
fingerprint and automatically marked 'Resolved (auto)' when the issue disappears."""
import hashlib
import json
import re

import frappe
from frappe.utils import cint, flt, getdate, now_datetime, today

from hr_ai_agents.hr_ai_agents.runtime import core

AGENT = "Sentinel"
DEFAULT_DOCTYPES = [
    "Workforce Plan", "Job Requisition", "Job Opening", "Job Applicant", "CXO Hiring Approval",
    "Employee", "Probation Review", "Leave Application", "Employee Exit",
]
PROBATION_OPEN = ("Draft", "Manager Review", "HR Review")


def F(rule, ftype, severity, message, impact="", evidence="", fix="", field=""):
    return {"rule": rule, "finding_type": ftype, "severity": severity, "message": message[:480],
            "impact": impact, "evidence": evidence, "suggested_fix": fix, "field_name": field}


def _empty(v):
    return v is None or v == "" or v == [] or (isinstance(v, str) and not v.strip())


# ---------------------------------------------------------------- completeness rules
def completeness(doc):
    out = []
    state = doc.get("workflow_state")
    meta = frappe.get_meta(doc.doctype)
    rules = frappe.get_all(
        "Completeness Rule", filters={"enabled": 1},
        fields=["name", "reference_doctype", "field_name", "label", "severity", "impact", "applies_when_state"],
    )
    for r in rules:
        states = [x.strip() for x in (r.applies_when_state or "").split("\n") if x.strip()]
        if states and state not in states:
            continue
        label = r.label or r.field_name
        if r.reference_doctype == doc.doctype:
            if not meta.has_field(r.field_name):
                continue
            if _empty(doc.get(r.field_name)):
                out.append(F(f"completeness:{r.name}", "Missing field", r.severity, f"{label} is empty on {doc.doctype} {doc.name}", r.impact or "", "", f"Fill in {label}", r.field_name))
        else:  # child-table rule
            for df in meta.get_table_fields():
                if df.options != r.reference_doctype or not frappe.get_meta(r.reference_doctype).has_field(r.field_name):
                    continue
                for row in doc.get(df.fieldname) or []:
                    if _empty(row.get(r.field_name)):
                        out.append(F(f"completeness:{r.name}", "Missing field", r.severity, f"{label} is empty in row {row.idx} of {df.label or df.fieldname} on {doc.name}", r.impact or "", f"row {row.idx}", f"Fill in {label}", f"{df.fieldname}.{r.field_name}.{row.idx}"))
    return out


# ---------------------------------------------------------------- built-in checks
def chk_workforce_plan(doc):
    out = []
    cur = doc.get("currency") or ""
    if flt(doc.get("remaining_budget")) < 0:
        out.append(F("wp-negative-budget", "Salary or budget", "High", f"Remaining budget is negative ({flt(doc.remaining_budget):,.0f} {cur})", "Spend is committed beyond the approved budget.", f"approved {flt(doc.approved_budget):,.0f}, committed {flt(doc.committed_budget):,.0f}, consumed {flt(doc.consumed_budget):,.0f}", "Review committed positions or request a budget revision", "remaining_budget"))
    if flt(doc.get("approved_budget")) > 0 and flt(doc.get("committed_budget")) > flt(doc.approved_budget):
        out.append(F("wp-committed-over-approved", "Salary or budget", "High", f"Committed budget {flt(doc.committed_budget):,.0f} exceeds approved {flt(doc.approved_budget):,.0f}", "Hiring may proceed on unapproved funds.", "", "Reconcile committed budget against requisitions", "committed_budget"))
    if cint(doc.get("approved_headcount") or 0) and cint(doc.get("committed_headcount") or 0) > cint(doc.approved_headcount):
        out.append(F("wp-headcount-over", "Anomaly", "High", f"Committed headcount {doc.committed_headcount} exceeds approved {doc.approved_headcount}", "More roles are in flight than approved.", "", "Review requisitions against the plan", "committed_headcount"))
    if doc.get("custom_budget_line_not_confirmed"):
        out.append(F("wp-budget-line-unconfirmed", "Anomaly", "Medium", "Budget line is not confirmed", "Budget tracking and posting rely on a confirmed budget line.", doc.get("custom_best_known_budget_line") or "", "Confirm the budget line with Finance", "budget_line"))
    # committed vs sum of requisition role costs (surfaces double counting)
    if doc.get("currency") in (None, "", "USD") and frappe.get_meta("Job Requisition").has_field("custom_budget_plan"):
        reqs = frappe.get_all("Job Requisition", filters={"custom_budget_plan": doc.name, "docstatus": ("<", 2)}, pluck="name")
        if reqs:
            total = frappe.db.sql(
                "select coalesce(sum(coalesce(total_estimated_cost_usd,0)),0) from `tabJob Requisition Role` where parent in %s and parenttype='Job Requisition'",
                (tuple(reqs),),
            )[0][0]
            total = flt(total)
            committed = flt(doc.get("committed_budget"))
            if total > 0 and committed > 0 and abs(committed - total) / total > 0.10:
                out.append(F("wp-committed-vs-requisitions", "Salary or budget", "Medium", f"Committed budget {committed:,.0f} differs from requisition role totals {total:,.0f}", "Possible double counting or stale committed figure; budget utilisation reports may be wrong.", f"{len(reqs)} requisition(s) linked", "Compare the plan's committed figure with its requisitions", "committed_budget"))
    return out


def chk_job_requisition(doc):
    out, seen = [], {}
    tf = next((df.fieldname for df in frappe.get_meta("Job Requisition").get_table_fields() if df.options == "Job Requisition Role"), None)
    for row in (doc.get(tf) or []) if tf else []:
        key = (row.get("designation"), row.get("project"), row.get("sub_function"), row.get("vendor"))
        if key in seen and row.get("designation"):
            out.append(F("jr-duplicate-role", "Duplicate", "Medium", f"Rows {seen[key]} and {row.idx} look like the same role ({row.designation})", "Duplicate roles inflate headcount and cost.", "", "Merge the rows or change the role details", "roles"))
        seen[key] = row.idx
        if flt(row.get("lower_salary_range")) and flt(row.get("max_salary_range")) and flt(row.lower_salary_range) > flt(row.max_salary_range):
            out.append(F("jr-salary-range-inverted", "Salary or budget", "Medium", f"Row {row.idx}: lower salary range is above the maximum", "Cost estimates and ATS salary checks use this band.", "", "Correct the salary range", "roles"))
        if cint(row.get("no_of_positions")) and flt(row.get("estimated_cost_usd")) <= 0 and flt(row.get("total_estimated_cost_usd") or 0) <= 0:
            out.append(F("jr-zero-cost", "Salary or budget", "Medium", f"Row {row.idx} ({row.get('designation')}) has positions but no estimated cost", "Budget impact will be understated.", "", "Fetch or enter the estimated cost", "roles"))
    return out


def chk_job_opening(doc):
    out = []
    if doc.get("custom_pushed_to_vendor") and not doc.get("custom_vendor"):
        out.append(F("jo-pushed-no-vendor", "Integrity", "Medium", "Opening is marked pushed to a vendor but has no vendor", "Vendor SLA cannot be tracked.", "", "Select the vendor", "custom_vendor"))
    if doc.get("custom_sla_breached"):
        out.append(F("jo-vendor-sla-breached", "Workflow", "Medium", f"Vendor SLA breached ({doc.get('custom_vendor_ageing_days')} of {doc.get('custom_vendor_sla_days')} days)", "Hiring timeline at risk.", "", "Chase the vendor or reallocate", "custom_sla_breached"))
    if flt(doc.get("lower_range")) and flt(doc.get("upper_range")) and flt(doc.lower_range) > flt(doc.upper_range):
        out.append(F("jo-range-inverted", "Salary or budget", "Medium", "Salary range lower bound is above the upper bound", "Applicant salary checks use this band.", "", "Correct the range", "lower_range"))
    return out


def chk_job_applicant(doc):
    out = []
    upper, lower = flt(doc.get("upper_range")), flt(doc.get("lower_range"))
    exp, agreed = flt(doc.get("custom_expected_salary")), flt(doc.get("custom_agreed_salary"))
    if upper and exp > upper:
        out.append(F("ja-expected-over-band", "Salary or budget", "Medium", f"Expected salary {exp:,.0f} is above the band upper limit {upper:,.0f}", "May breach the approved cost for the role.", "", "Review before shortlisting", "custom_expected_salary"))
    if upper and agreed and (agreed > upper or (lower and agreed < lower)):
        out.append(F("ja-agreed-outside-band", "Salary or budget", "High", f"Agreed salary {agreed:,.0f} is outside the band {lower:,.0f}-{upper:,.0f}", "Direct budget impact; needs approval on record.", "", "Confirm approval for the exception", "custom_agreed_salary"))
    if doc.get("custom_employee_created") and not doc.get("custom_employee"):
        out.append(F("ja-employee-flag-no-link", "Integrity", "Medium", "Marked 'employee created' but no Employee is linked", "Onboarding and headcount tracking will not match.", "", "Link the Employee or clear the flag", "custom_employee"))
    return out


def chk_cxo(doc):
    out = []
    summary = frappe.utils.strip_html(doc.get("workforce_plan_summary") or "")
    if flt(doc.get("total_agreed_salary_usd")) > 0 and re.search(r"\$\s*0(?:\.0+)?(?![\d,])", summary):
        out.append(F("cxo-summary-zero", "Anomaly", "Medium", f"Summary text shows $0 while total agreed salary is {flt(doc.total_agreed_salary_usd):,.0f} USD", "Approvers may read a wrong budget impact.", "", "Regenerate or correct the summary", "workforce_plan_summary"))
    if cint(doc.get("total_applicants")) != len(doc.get("applicants") or []):
        out.append(F("cxo-applicant-count", "Integrity", "Low", f"Total applicants {doc.total_applicants} does not match rows ({len(doc.get('applicants') or [])})", "Approval pack may be inconsistent.", "", "Save the document to recalculate", "total_applicants"))
    return out


def chk_probation(doc):
    out = []
    end = doc.get("probation_end_date")
    if end and getdate(end) < getdate(today()) and (doc.get("workflow_state") in PROBATION_OPEN):
        days = (getdate(today()) - getdate(end)).days
        out.append(F("pr-overdue", "Workflow", "High" if days > 14 else "Medium", f"Probation ended {days} day(s) ago and the review is still in '{doc.workflow_state}'", "Employee confirmation or extension is overdue.", "", "Complete the review", "probation_end_date"))
    return out


def chk_leave(doc):
    out = []
    if doc.get("employee") and doc.get("leave_type") and doc.get("from_date") and doc.get("to_date"):
        ok = frappe.db.exists("Leave Allocation", {"employee": doc.employee, "leave_type": doc.leave_type, "docstatus": 1, "from_date": ("<=", doc.from_date), "to_date": (">=", doc.to_date)})
        if not ok:
            out.append(F("la-no-allocation", "Integrity", "High", f"No submitted Leave Allocation covers {doc.leave_type} for {doc.from_date} to {doc.to_date}", "The application cannot be approved or will drive a negative balance.", "", "Ask HR to create the allocation", "leave_type"))
    return out


def chk_employee(doc):
    out = []
    if doc.get("date_of_joining") and getdate(doc.date_of_joining) > getdate(today()) and doc.get("status") == "Active":
        out.append(F("emp-future-joining", "Integrity", "Low", "Active employee has a future date of joining", "Payroll and leave accruals start from the joining date.", "", "Confirm the joining date", "date_of_joining"))
    return out


BUILTIN = {
    "Workforce Plan": [chk_workforce_plan], "Job Requisition": [chk_job_requisition], "Job Opening": [chk_job_opening],
    "Job Applicant": [chk_job_applicant], "CXO Hiring Approval": [chk_cxo], "Probation Review": [chk_probation],
    "Leave Application": [chk_leave], "Employee": [chk_employee],
}


# ---------------------------------------------------------------- engine
def fingerprint(doctype, name, f):
    return hashlib.sha1(f"{f['rule']}|{doctype}|{name}|{f.get('field_name','')}".encode()).hexdigest()


def check_doc(run, doctype, name):
    run.require(doctype, "read", doc=name)
    doc = frappe.get_doc(doctype, name)
    run.touch(doctype, name)
    findings = []
    for fn in BUILTIN.get(doctype, []):
        try:
            findings.extend(fn(doc))
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Sentinel check {fn.__name__}")
    findings.extend(completeness(doc))
    return doc, findings


def save_findings(run, doctype, name, findings, owner=None):
    now = now_datetime()
    live = set()
    saved = []
    for f in findings:
        fp = fingerprint(doctype, name, f)
        live.add(fp)
        existing = frappe.db.get_value("AI Finding", {"fingerprint": fp}, ["name", "status"], as_dict=True)
        if existing:
            upd = {"last_seen": now, "evidence": f["evidence"], "message": f["message"]}
            if existing.status == "Resolved (auto)":
                upd["status"] = "Open"
            frappe.db.set_value("AI Finding", existing.name, upd, update_modified=False)
            saved.append(existing.name)
        else:
            d = run.create_own({
                "doctype": "AI Finding", "message": f["message"], "severity": f["severity"], "status": "Open",
                "finding_type": f["finding_type"], "reference_doctype": doctype, "reference_name": name,
                "field_name": f["field_name"], "owner_user": owner, "impact": f["impact"], "evidence": f["evidence"],
                "suggested_fix": f["suggested_fix"], "rule": f["rule"], "detected_by": AGENT, "detected_on": now,
                "last_seen": now, "fingerprint": fp,
            })
            saved.append(d.name)
    # auto-resolve what is no longer true
    for r in frappe.get_all("AI Finding", filters={"reference_doctype": doctype, "reference_name": name, "status": ("in", ["Open", "Acknowledged"]), "detected_by": AGENT}, fields=["name", "fingerprint"]):
        if r.fingerprint not in live:
            frappe.db.set_value("AI Finding", r.name, {"status": "Resolved (auto)", "last_seen": now}, update_modified=False)
    return saved


def review_document(run, doctype, name):
    doc, findings = check_doc(run, doctype, name)
    save_findings(run, doctype, name, findings, owner=doc.get("owner"))
    run.response = f"{len(findings)} finding(s) on {doctype} {name}."
    return findings


def nightly_scan(run):
    s = core.settings()
    lookback = cint(s.sentinel_lookback_days) or 90
    cutoff = frappe.utils.add_days(today(), -lookback)
    doctypes = [x.strip() for x in (s.sentinel_doctypes or "").split("\n") if x.strip()] or DEFAULT_DOCTYPES
    total = 0
    for dt in doctypes:
        if not frappe.db.exists("DocType", dt) or not run.can(dt, "read"):
            continue
        names = frappe.get_all(dt, filters={"modified": (">=", cutoff), "docstatus": ("<", 2)}, pluck="name", limit=500, order_by="modified desc")
        for n in names:
            try:
                doc, findings = check_doc(run, dt, n)
                save_findings(run, dt, n, findings, owner=doc.get("owner"))
                total += len(findings)
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"Sentinel scan {dt} {n}")
    run.response = f"Nightly scan complete: {total} finding(s) across {len(doctypes)} doctype(s)."
    return total
