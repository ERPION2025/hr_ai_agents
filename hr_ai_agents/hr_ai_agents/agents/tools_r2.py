"""Release 2 tools. Read tools return facts (permission = requesting user AND the agent's roles).
`propose_*` tools never change a record: they create an AI Proposal that a person applies under their own permissions."""
import re
import statistics

import frappe
from frappe.utils import add_days, add_months, cint, flt, getdate, now_datetime, today

from hr_ai_agents.hr_ai_agents.agents import atlas, policy, proposals, sentinel, workflow_nav
from hr_ai_agents.hr_ai_agents.runtime import core
from hr_ai_agents.hr_ai_agents.runtime.registry import tool

K_MIN = 3  # groups smaller than this are suppressed in aggregates (re-identification risk)


# ------------------------------------------------------------------ helpers
def _glist(run, doctype, **kw):
    kw.setdefault("limit_page_length", 200)
    if run.trigger == "User":
        return frappe.get_list(doctype, **kw)
    kw["limit"] = kw.pop("limit_page_length")
    return frappe.get_all(doctype, **kw)


def _fields(doctype, wanted):
    return atlas._fields(doctype, wanted)


def _resolve_employee(ref):
    """Accept an Employee ID, user id / company or personal email, or an exact employee name."""
    ref = (ref or "").strip()
    if not ref:
        return None
    if frappe.db.exists("Employee", ref):
        return ref
    for f in ("user_id", "company_email", "personal_email", "prefered_email"):
        if frappe.get_meta("Employee").has_field(f):
            hit = frappe.db.get_value("Employee", {f: ref, "status": "Active"}, "name") or frappe.db.get_value("Employee", {f: ref}, "name")
            if hit:
                return hit
    names = frappe.get_all("Employee", filters={"employee_name": ref}, pluck="name", limit=2)
    return names[0] if len(names) == 1 else None


def _employee_of(run, employee=None):
    if employee:
        emp = _resolve_employee(employee)
        if not emp:
            raise frappe.ValidationError(f"No employee found for '{employee}'. Use the employee ID, work email or exact name.")
        run.require("Employee", "read", doc=emp)
        return emp
    emp = frappe.db.get_value("Employee", {"user_id": run.user, "status": "Active"}, "name")
    if not emp:
        raise frappe.ValidationError("No active Employee record is linked to your user. Tell me which employee you mean.")
    return emp


def _obj(props, required=None):
    return {"type": "object", "properties": props, "required": required or []}


def _scalar_snapshot(doc, maxlen=400):
    """Readable top-level values the signed-in user may see (field permission levels honoured)."""
    from frappe.model import no_value_fields, table_fields

    out = {}
    for df in doc.meta.fields:
        if df.fieldtype in no_value_fields or df.fieldtype in table_fields or df.fieldtype in ("Attach", "Attach Image", "Password", "Signature", "Code", "Barcode"):
            continue
        if df.hidden or df.get("is_virtual"):
            continue
        try:
            if not doc.has_permlevel_access_to(df.fieldname, df):
                continue
        except Exception:
            pass
        v = doc.get(df.fieldname)
        if v in (None, "", 0) and df.fieldtype not in ("Check",):
            continue
        out[df.fieldname] = frappe.utils.strip_html(str(v))[:maxlen] if isinstance(v, str) else v
    return out


def _star(v):
    """Rating fields are stored as 0-1. Accept 1-5 stars or 0-1."""
    v = flt(v)
    return round(v / 5.0, 2) if v > 1 else v


def _table(parent, child):
    return atlas._table_field(parent, child)


# ================================================================== LEENA (leave)
@tool(
    "leave_balance",
    "Leave balance by leave type for an employee (default: the requesting user's own employee record).",
    _obj({"employee": {"type": "string"}, "leave_type": {"type": "string"}}),
)
def leave_balance(run, employee=None, leave_type=None):
    emp = _employee_of(run, employee)
    run.require("Leave Application", "read")
    run.touch("Employee", emp)
    run.sent_to_model("Employee", emp, ["leave balances"])
    try:
        from hrms.hr.doctype.leave_application.leave_application import get_leave_details

        det = get_leave_details(emp, today()).get("leave_allocation", {})
    except Exception as e:
        return {"error": f"Could not compute balances: {str(e)[:200]}"}
    rows = [{"leave_type": k, "allocated": v.get("total_leaves"), "taken": v.get("leaves_taken"), "pending_approval": v.get("leaves_pending_approval"), "remaining": v.get("remaining_leaves")} for k, v in det.items() if not leave_type or k == leave_type]
    return {"employee": emp, "as_of": today(), "balances": rows}


@tool(
    "team_leave_overlap",
    "Who else in the same team or department already has leave overlapping a date range (names and dates only).",
    _obj({"employee": {"type": "string"}, "from_date": {"type": "string"}, "to_date": {"type": "string"}}, ["from_date", "to_date"]),
)
def team_leave_overlap(run, from_date, to_date, employee=None):
    emp = _employee_of(run, employee)
    run.require("Leave Application", "read")
    e = frappe.db.get_value("Employee", emp, ["department", "reports_to"], as_dict=True)
    if not e:
        return {"overlaps": []}
    team = frappe.get_all("Employee", filters={"status": "Active", "name": ("!=", emp), **({"reports_to": e.reports_to} if e.reports_to else {"department": e.department})}, pluck="name", limit=200)
    if not team:
        return {"overlaps": [], "team_size": 0}
    rows = _glist(
        run, "Leave Application",
        filters={"employee": ("in", team), "docstatus": ("<", 2), "status": ("in", ["Open", "Approved"]), "from_date": ("<=", to_date), "to_date": (">=", from_date)},
        fields=["employee_name", "leave_type", "from_date", "to_date", "status"],
    )
    return {"team_size": len(team), "overlaps": rows, "note": "Only leave visible to the requesting user is listed."}


@tool(
    "propose_leave_application",
    "Prepare a DRAFT Leave Application for the user to review and apply. Nothing is submitted.",
    _obj({
        "leave_type": {"type": "string"}, "from_date": {"type": "string", "description": "YYYY-MM-DD"}, "to_date": {"type": "string"},
        "reason": {"type": "string"}, "half_day": {"type": "boolean"}, "half_day_date": {"type": "string"}, "employee": {"type": "string", "description": "default: the requesting user's own employee"},
        "leave_approver": {"type": "string", "description": "user id; default: the employee's leave approver"},
    }, ["leave_type", "from_date", "to_date"]),
    writes="AI Proposal",
)
def propose_leave_application(run, leave_type, from_date, to_date, reason=None, half_day=False, half_day_date=None, employee=None, leave_approver=None):
    emp = _employee_of(run, employee)
    vals = {"employee": emp, "leave_type": leave_type, "from_date": from_date, "to_date": to_date, "description": reason or ""}
    company = frappe.db.get_value("Employee", emp, "company")
    if company:
        vals["company"] = company
    approver = leave_approver or frappe.db.get_value("Employee", emp, "leave_approver")
    if approver:
        vals["leave_approver"] = approver
    if half_day:
        vals["half_day"] = 1
        vals["half_day_date"] = half_day_date or from_date
    res = proposals.propose(run, "Create draft", "Leave Application", None, vals, None, f"Leave {leave_type} {from_date} to {to_date}", reason or "")
    res["balance_hint"] = leave_balance(run, emp, leave_type).get("balances")
    return res


@tool(
    "propose_leave_allocation",
    "Prepare a DRAFT Leave Allocation for HR to review and apply (HR roles only). Nothing is submitted.",
    _obj({"employee": {"type": "string"}, "leave_type": {"type": "string"}, "from_date": {"type": "string"}, "to_date": {"type": "string"}, "new_leaves_allocated": {"type": "number"}, "notes": {"type": "string"}}, ["employee", "leave_type", "from_date", "to_date", "new_leaves_allocated"]),
    writes="AI Proposal",
)
def propose_leave_allocation(run, employee, leave_type, from_date, to_date, new_leaves_allocated, notes=None):
    employee = _resolve_employee(employee) or employee
    vals = {"employee": employee, "leave_type": leave_type, "from_date": from_date, "to_date": to_date, "new_leaves_allocated": flt(new_leaves_allocated)}
    if notes and frappe.get_meta("Leave Allocation").has_field("notes"):
        vals["notes"] = notes
    return proposals.propose(run, "Create draft", "Leave Allocation", None, vals, None, f"Allocate {new_leaves_allocated} {leave_type} to {employee}", notes or "")


# ================================================================== ROHAN (requisition builder)
JD_FIELDS = ["role_description", "scope", "expertise_skillset", "experience_required", "education", "responsibilities", "qualifications"]


@tool(
    "requisition_roles",
    "Roles on a Job Requisition with their row number and current text fields, so a job description can be drafted per role.",
    _obj({"requisition": {"type": "string"}}, ["requisition"]),
)
def requisition_roles(run, requisition):
    run.require("Job Requisition", "read", doc=requisition)
    table = _table("Job Requisition", "Job Requisition Role")
    doc = frappe.get_doc("Job Requisition", requisition)
    run.touch("Job Requisition", requisition)
    run.sent_to_model("Job Requisition", requisition, ["roles", "descriptions"])
    rows = []
    for r in doc.get(table) or []:
        d = {"row": r.idx, "designation": r.get("designation"), "positions": r.get("no_of_positions"), "role_type": r.get("role_type")}
        for f in JD_FIELDS:
            if r.meta.has_field(f):
                d[f] = frappe.utils.strip_html(r.get(f) or "")[:1200]
        rows.append(d)
    return {"requisition": requisition, "state": doc.get("workflow_state"), "docstatus": doc.docstatus, "title": doc.get("custom_requisition_title"), "roles": rows, "editable_fields": [f for f in JD_FIELDS if frappe.get_meta("Job Requisition Role").has_field(f)]}


@tool(
    "propose_role_text",
    "Propose job-description text for one role row of a Draft Job Requisition (for the user to review and apply).",
    _obj({"requisition": {"type": "string"}, "row": {"type": "integer"}, "text_fields": {"type": "object", "description": "field name -> text, from the editable_fields list"}}, ["requisition", "row", "text_fields"]),
    writes="AI Proposal",
)
def propose_role_text(run, requisition, row, text_fields):
    table = _table("Job Requisition", "Job Requisition Role")
    bad = [k for k in text_fields if k not in JD_FIELDS]
    if bad:
        raise proposals.ProposalError(f"Only these fields can be drafted: {', '.join(JD_FIELDS)}.")
    return proposals.propose(
        run, "Update fields", "Job Requisition", requisition, None, {table: {"update": [{"idx": int(row), "set": text_fields}]}},
        f"JD for role {row} of {requisition}", "Drafted from the requisition's role data; review before applying.",
    )


@tool(
    "cost_check",
    "Deterministic cost check of a Job Requisition against its Workforce Plan budget.",
    _obj({"requisition": {"type": "string"}}, ["requisition"]),
)
def cost_check(run, requisition):
    run.require("Job Requisition", "read", doc=requisition)
    doc = frappe.get_doc("Job Requisition", requisition)
    table = _table("Job Requisition", "Job Requisition Role")
    roles, total = [], 0.0
    for r in doc.get(table) or []:
        per = flt(r.get("estimated_cost_usd"))
        tot = flt(r.get("total_estimated_cost_usd")) or per * cint(r.get("no_of_positions") or 1)
        total += tot
        roles.append({"row": r.idx, "designation": r.get("designation"), "positions": r.get("no_of_positions"), "budgeted": r.get("budgeted"), "cost_per_head_usd": per, "total_usd": tot})
    out = {"requisition": requisition, "roles": roles, "total_estimated_usd": round(total, 2), "flags": []}
    plan = doc.get("custom_budget_plan")
    if plan and run.can("Workforce Plan", "read", doc=plan):
        p = frappe.db.get_value("Workforce Plan", plan, _fields("Workforce Plan", ["remaining_budget", "approved_budget", "committed_budget", "consumed_budget", "remaining_headcount", "currency"]), as_dict=True)
        out["plan"] = {"name": plan, **(p or {})}
        rem = flt((p or {}).get("remaining_budget"))
        if p and total > rem:
            out["flags"].append(f"Estimated cost {total:,.0f} exceeds the plan's remaining budget {rem:,.0f}.")
        if p and rem:
            out["share_of_remaining_pct"] = round(total / rem * 100, 1)
    elif not plan:
        out["flags"].append("No Workforce Plan is linked, so the budget impact cannot be checked.")
    for r in roles:
        if not r["cost_per_head_usd"]:
            out["flags"].append(f"Role {r['row']} ({r['designation']}) has no cost per head.")
        if (r.get("budgeted") or "") == "Non-Budgeted":
            out["flags"].append(f"Role {r['row']} is marked Non-Budgeted.")
    return out


def _requisition_proposal(run, plan, values, roles, title, rationale):
    table = _table("Job Requisition", "Job Requisition Role")
    vals = dict(values or {})
    if plan and frappe.get_meta("Job Requisition").has_field("custom_budget_plan"):
        run.require("Workforce Plan", "read", doc=plan)
        vals["custom_budget_plan"] = plan
    rows = {table: {"append": roles}} if (table and roles) else None
    return proposals.propose(run, "Create draft", "Job Requisition", None, vals, rows, title, rationale)


REQ_PROPS = {
    "plan": {"type": "string", "description": "Workforce Plan name"},
    "values": {"type": "object", "description": "Job Requisition fields (designation, department, no_of_positions, expected_compensation, description, ...)"},
    "roles": {"type": "array", "items": {"type": "object"}, "description": "rows for the Job Requisition Role table"},
    "rationale": {"type": "string"},
}


@tool(
    "propose_requisition_from_plan",
    "Prepare a DRAFT Job Requisition linked to a Workforce Plan, with role rows. Nothing is submitted.",
    _obj(REQ_PROPS, ["plan", "values"]),
    writes="AI Proposal",
)
def propose_requisition_from_plan(run, plan, values, roles=None, rationale=""):
    return _requisition_proposal(run, plan, values, roles, f"New requisition from plan {plan}", rationale)


# ================================================================== ASHA (workforce plan)
@tool(
    "plan_precheck",
    "Pre-check a Workforce Plan: missing fields, headcount/budget arithmetic and anomalies (not saved as findings).",
    _obj({"plan": {"type": "string"}}, ["plan"]),
)
def plan_precheck(run, plan):
    doc, findings = sentinel.check_doc(run, "Workforce Plan", plan)
    run.sent_to_model("Workforce Plan", plan, ["figures"])
    return {"plan": plan, "findings": [{k: f[k] for k in ("severity", "message", "impact", "suggested_fix")} for f in findings]}


BENCH_SOURCES = [
    ("Job Requisition Role", "designation", ["estimated_cost_usd"], "requisition role cost per head"),
    ("Workforce Plan Position", "designation", ["existing_cost_usd", "cost_per_head_usd", "estimated_cost_usd"], "workforce plan position cost"),
    ("Job Applicant", "designation", ["custom_expected_salary"], "applicant expected salary"),
]


@tool(
    "cost_benchmark",
    "Cost per head for a designation from this organisation's own history (requisitions, plans, applicant expectations). Not market data.",
    _obj({"designation": {"type": "string"}, "department": {"type": "string"}}, ["designation"]),
)
def cost_benchmark(run, designation, department=None):
    out = []
    for dt, dfield, amounts, label in BENCH_SOURCES:
        if not frappe.db.exists("DocType", dt) or not run.can(dt, "read"):
            continue
        meta = frappe.get_meta(dt)
        if not meta.has_field(dfield):
            continue
        for af in [a for a in amounts if meta.has_field(a)]:
            rows = frappe.get_all(dt, filters={dfield: designation, af: (">", 0)}, pluck=af, limit=2000)
            vals = [flt(v) for v in rows]
            if len(vals) >= K_MIN:
                out.append({"source": label, "field": af, "n": len(vals), "min": min(vals), "median": statistics.median(vals), "max": max(vals), "average": round(sum(vals) / len(vals), 2)})
    return {"designation": designation, "benchmarks": out, "note": "Internal history only; fewer than 3 data points are not shown." if not out else "Internal history only."}


@tool(
    "propose_workforce_plan",
    "Prepare a DRAFT Workforce Plan (and its position rows) for the user to review and apply.",
    _obj({"values": {"type": "object"}, "positions": {"type": "array", "items": {"type": "object"}}, "rationale": {"type": "string"}}, ["values"]),
    writes="AI Proposal",
)
def propose_workforce_plan(run, values, positions=None, rationale=""):
    table = _table("Workforce Plan", "Workforce Plan Position")
    rows = {table: {"append": positions}} if (positions and table) else None
    return proposals.propose(run, "Create draft", "Workforce Plan", None, values, rows, "New workforce plan", rationale)


# ================================================================== KARIM (CXO briefer)
BRIEFABLE = ["CXO Hiring Approval", "Job Requisition", "Workforce Plan", "Probation Review", "Employee Exit", "Leave Application", "Job Offer"]


@tool(
    "approver_brief",
    "Facts an approver needs for one document: key fields, table summaries, open issues. Use these facts to write the brief.",
    _obj({"doctype": {"type": "string", "enum": BRIEFABLE}, "name": {"type": "string"}}, ["doctype", "name"]),
)
def approver_brief(run, doctype, name):
    if doctype not in BRIEFABLE:
        raise frappe.PermissionError("Briefs are not available for that document type.")
    doc, findings = sentinel.check_doc(run, doctype, name)
    snap = _scalar_snapshot(doc)
    run.sent_to_model(doctype, name, list(snap))
    tables = {}
    for df in doc.meta.get_table_fields():
        rows = doc.get(df.fieldname) or []
        tables[df.label or df.fieldname] = {"rows": len(rows), "sample": [_scalar_snapshot(r, 120) for r in rows[:5]]}
    out = {"document": f"{doctype} {name}", "fields": snap, "tables": tables, "issues": [{k: f[k] for k in ("severity", "message", "impact")} for f in findings]}
    try:
        out["workflow"] = workflow_nav.describe(doctype, name, run.user)
    except Exception:
        pass
    return out


@tool(
    "cxo_batch",
    "List CXO Hiring Approval documents still in progress with totals and any consistency issues, for batch approval planning.",
    _obj({"limit": {"type": "integer"}}),
)
def cxo_batch(run, limit=20):
    run.require("CXO Hiring Approval", "read")
    meta = frappe.get_meta("CXO Hiring Approval")
    rows = _glist(run, "CXO Hiring Approval", filters={"docstatus": ("<", 2)}, fields=_fields("CXO Hiring Approval", ["name", "workflow_state", "total_applicants", "total_agreed_salary_usd", "modified"]), order_by="modified desc", limit_page_length=min(cint(limit) or 20, 40))
    closed = ("approved", "reject", "cancel", "closed", "complete")
    out = []
    for r in rows:
        if any(w in (r.get("workflow_state") or "").lower() for w in closed):
            continue
        doc = frappe.get_doc("CXO Hiring Approval", r.name)
        issues = [f["message"] for f in sentinel.chk_cxo(doc)]
        out.append({**r, "issues": issues})
        run.touch("CXO Hiring Approval", r.name)
    return {"pending": out}


# ================================================================== NOOR (onboarding)
@tool(
    "onboarding_status",
    "Employee record completeness plus onboarding progress (Employee Onboarding and its tasks).",
    _obj({"employee": {"type": "string"}}, ["employee"]),
)
def onboarding_status(run, employee):
    doc, findings = sentinel.check_doc(run, "Employee", employee)
    out = {"employee": employee, "missing_or_odd": [{k: f[k] for k in ("severity", "message", "impact", "suggested_fix")} for f in findings]}
    if frappe.db.exists("DocType", "Employee Onboarding") and run.can("Employee Onboarding", "read"):
        eo = frappe.get_all("Employee Onboarding", filters={"employee": employee, "docstatus": ("<", 2)}, fields=_fields("Employee Onboarding", ["name", "boarding_status", "date_of_joining", "department", "designation"]), limit=3)
        prog = []
        for o in eo:
            d = frappe.get_doc("Employee Onboarding", o.name)
            tasks = [r.get("task") for r in d.get("activities") or [] if r.get("task")]
            st = frappe.get_all("Task", filters={"name": ("in", tasks)}, fields=["status"]) if tasks else []
            prog.append({**o, "activities": len(d.get("activities") or []), "tasks_completed": sum(1 for s in st if s.status == "Completed"), "tasks_total": len(st)})
        out["onboarding"] = prog
    run.sent_to_model("Employee", employee, ["completeness"])
    return out


@tool(
    "onboarded_by_plan",
    "Employees who joined (Job Applicants onboarded with an Employee created) against a Workforce Plan; the latest plan when none is named. "
    "Only employees the requesting user is allowed to read are returned (needs access to Employee records).",
    _obj({"plan": {"type": "string", "description": "Workforce Plan name; omit for the latest"}, "limit": {"type": "integer"}}),
)
def onboarded_by_plan(run, plan=None, limit=100):
    run.require("Workforce Plan", "read")
    run.require("Job Applicant", "read")
    run.require("Employee", "read")  # without Employee access the answer is refused as a whole
    if not plan:
        latest = _glist(run, "Workforce Plan", filters={"docstatus": ("<", 2)}, fields=["name"], order_by="creation desc", limit_page_length=1)
        if not latest:
            return {"note": "No Workforce Plan found for you."}
        plan = latest[0].name
    else:
        run.require("Workforce Plan", "read", doc=plan)
    ja_meta = frappe.get_meta("Job Applicant")
    if not ja_meta.has_field("custom_budget_plan"):
        return {"plan": plan, "note": "Job Applicants are not linked to Workforce Plans on this site."}
    fields = _fields("Job Applicant", ["name", "custom_employee", "custom_employee_created", "custom_recruitment_status", "designation", "job_title"])
    apps = _glist(run, "Job Applicant", filters={"custom_budget_plan": plan}, fields=fields, limit_page_length=1000)
    by_status = {}
    for a in apps:
        k = a.get("custom_recruitment_status") or a.get("status") or "Unset"
        by_status[k] = by_status.get(k, 0) + 1
    ids = [a.custom_employee for a in apps if a.get("custom_employee")]
    emps = []
    if ids:
        ef = _fields("Employee", ["name", "employee_name", "designation", "department", "company", "date_of_joining", "status", "custom_function", "final_confirmation_date"])
        emps = _glist(run, "Employee", filters={"name": ("in", ids)}, fields=ef, order_by="date_of_joining desc", limit_page_length=min(cint(limit) or 100, 300))
    by_emp = {a.custom_employee: a for a in apps if a.get("custom_employee")}
    rows = [{**e, "from_applicant": by_emp[e.name].name, "opening": by_emp[e.name].get("job_title")} for e in emps if e.name in by_emp]
    for r in rows:
        run.touch("Employee", r["name"])
    run.sent_to_model("Employee", plan, ["name", "designation", "department", "joining date", "status"] + (["employee name"] if rows else []))
    return {"plan": plan, "applicants_by_status": by_status, "onboarded_employees": rows,
            "note": "Only employees you are permitted to read are listed. Pay and personal details are never returned."}


@tool(
    "propose_employee_update",
    "Propose changes to an Employee's reporting line, department, designation, cost center, branch, grade, employment type or work contact. HR reviews and applies.",
    _obj({"employee": {"type": "string"}, "values": {"type": "object"}, "rationale": {"type": "string"}}, ["employee", "values"]),
    writes="AI Proposal",
)
def propose_employee_update(run, employee, values, rationale=""):
    employee = _resolve_employee(employee) or employee
    return proposals.propose(run, "Update fields", "Employee", employee, values, None, f"Update Employee {employee}", rationale)


# ================================================================== DEV (probation)
@tool(
    "probation_context",
    "Everything needed to draft a probation review: review fields, goals with self and manager evaluations, employee basics.",
    _obj({"review": {"type": "string"}}, ["review"]),
)
def probation_context(run, review):
    run.require("Probation Review", "read", doc=review)
    doc = frappe.get_doc("Probation Review", review)
    run.touch("Probation Review", review)
    snap = _scalar_snapshot(doc, 800)
    gt = _table("Probation Review", "Probation Review Goal")
    goals = [{"row": g.idx, **{k: g.get(k) for k in ("goal", "self_evaluation", "manager_evaluation", "rating") if g.meta.has_field(k)}} for g in (doc.get(gt) or [])]
    emp = frappe.db.get_value("Employee", doc.get("employee"), ["designation", "department", "date_of_joining"], as_dict=True) if doc.get("employee") else {}
    run.sent_to_model("Probation Review", review, list(snap) + ["goals"])
    return {"review": review, "fields": snap, "goals": goals, "employee": emp, "docstatus": doc.docstatus,
            "note": "Rating fields use 1-5 stars. The final decision is made by a person and cannot be proposed."}


@tool(
    "propose_probation_draft",
    "Propose manager or HR feedback text and goal evaluations for a Draft Probation Review. The decision field can never be proposed.",
    _obj({
        "review": {"type": "string"}, "manager_feedback": {"type": "string"}, "hr_feedback": {"type": "string"}, "hr_comments": {"type": "string"},
        "overall_rating": {"type": "number", "description": "1-5 stars (a suggestion)"},
        "goals": {"type": "array", "items": {"type": "object", "properties": {"row": {"type": "integer"}, "manager_evaluation": {"type": "string"}, "rating": {"type": "number"}}}},
    }, ["review"]),
    writes="AI Proposal",
)
def propose_probation_draft(run, review, manager_feedback=None, hr_feedback=None, hr_comments=None, overall_rating=None, goals=None):
    vals = {}
    for k, v in (("manager_feedback", manager_feedback), ("hr_feedback", hr_feedback), ("hr_comments", hr_comments)):
        if v:
            vals[k] = v
    if overall_rating is not None:
        vals["overall_rating"] = _star(overall_rating)
    upd = []
    for g in goals or []:
        s = {}
        if g.get("manager_evaluation"):
            s["manager_evaluation"] = g["manager_evaluation"]
        if g.get("rating") is not None:
            s["rating"] = _star(g["rating"])
        if s:
            upd.append({"idx": int(g["row"]), "set": s})
    gt = _table("Probation Review", "Probation Review Goal")
    return proposals.propose(run, "Update fields", "Probation Review", review, vals, {gt: {"update": upd}} if upd and gt else None, f"Probation review draft {review}", "Suggested feedback based on the evaluations on record; the decision stays with the people involved.")


@tool(
    "hr_review_brief",
    "Deterministic status of a probation review for HR: what is filled in, ratings, and time to or past probation end.",
    _obj({"review": {"type": "string"}}, ["review"]),
)
def hr_review_brief(run, review):
    ctx = probation_context(run, review)
    f = ctx["fields"]
    goals = ctx["goals"]
    rated = [flt(g.get("rating")) for g in goals if g.get("rating")]
    end = f.get("probation_end_date")
    return {
        "review": review, "state": f.get("workflow_state"), "goals": len(goals), "goals_rated": len(rated),
        "average_goal_rating_stars": round(sum(rated) / len(rated) * 5, 1) if rated else None,
        "manager_feedback_present": bool(f.get("manager_feedback")), "hr_feedback_present": bool(f.get("hr_feedback")),
        "overall_rating_stars": round(flt(f.get("overall_rating")) * 5, 1) if f.get("overall_rating") else None,
        "decision_recorded": f.get("decision"), "days_to_probation_end": (getdate(end) - getdate(today())).days if end else None,
    }


@tool(
    "probation_deadlines",
    "Probation reviews whose probation ends within N days (or already ended) and are still open.",
    _obj({"days": {"type": "integer"}}),
)
def probation_deadlines(run, days=30):
    run.require("Probation Review", "read")
    rows = _glist(run, "Probation Review", filters={"probation_end_date": ("<=", add_days(today(), cint(days) or 30)), "docstatus": ("<", 2)}, fields=_fields("Probation Review", ["name", "employee_name", "probation_end_date", "workflow_state", "reviewer"]), order_by="probation_end_date asc", limit_page_length=50)
    closed = ("confirm", "extend", "terminat", "complete", "closed", "reject", "cancel")
    return {"open_reviews": [dict(r, days_left=(getdate(r.probation_end_date) - getdate(today())).days) for r in rows if not any(w in (r.get("workflow_state") or "").lower() for w in closed)]}


# ================================================================== SAMIR (exit)
@tool(
    "exit_checklist",
    "Exit progress: activities done/pending, outstanding clearance steps, days to last working day.",
    _obj({"exit": {"type": "string"}}, ["exit"]),
)
def exit_checklist(run, exit):
    run.require("Employee Exit", "read", doc=exit)
    doc = frappe.get_doc("Employee Exit", exit)
    run.touch("Employee Exit", exit)
    at = _table("Employee Exit", "Employee Exit Activity")
    acts = [{"row": a.idx, "activity": a.get("activity"), "role": a.get("role"), "status": a.get("status")} for a in (doc.get(at) or [])]
    flags = ["exit_interview_done", "exit_processed", "assets_returned", "access_revoked", "final_settlement_done"]
    outstanding = [doc.meta.get_label(f) for f in flags if doc.meta.has_field(f) and not doc.get(f)]
    lwd = doc.get("last_working_day")
    run.sent_to_model("Employee Exit", exit, ["activities", "clearance flags"])
    return {
        "exit": exit, "employee": doc.get("employee_name") or doc.get("employee"), "exit_type": doc.get("exit_type"), "state": doc.get("workflow_state"),
        "offboarding_status": doc.get("offboarding_status"), "last_working_day": str(lwd) if lwd else None,
        "days_to_last_working_day": (getdate(lwd) - getdate(today())).days if lwd else None,
        "activities": acts, "pending_activities": sum(1 for a in acts if a["status"] != "Completed"), "outstanding_clearance": outstanding,
    }


@tool(
    "propose_exit_activities",
    "Propose additional offboarding activities (rows) for a Draft Employee Exit. Existing activities are not changed.",
    _obj({"exit": {"type": "string"}, "activities": {"type": "array", "items": {"type": "object", "properties": {"activity": {"type": "string"}, "role": {"type": "string"}, "remarks": {"type": "string"}}, "required": ["activity"]}}}, ["exit", "activities"]),
    writes="AI Proposal",
)
def propose_exit_activities(run, exit, activities):
    at = _table("Employee Exit", "Employee Exit Activity")
    doc = frappe.get_doc("Employee Exit", exit)
    have = {(a.get("activity") or "").strip().lower() for a in doc.get(at) or []}
    new = [{k: v for k, v in a.items() if k in ("activity", "role", "remarks")} for a in activities if (a.get("activity") or "").strip().lower() not in have][:12]
    if not new:
        return {"note": "All of those activities are already on the exit."}
    return proposals.propose(run, "Update fields", "Employee Exit", exit, None, {at: {"append": new}}, f"Add {len(new)} offboarding activities to {exit}", "Missing steps compared with the standard checklist.")


GROUPABLE_EXIT = ["exit_type", "exit_reason", "department", "designation", "offboarding_status"]


@tool(
    "exit_insights",
    "Aggregate exit statistics by exit type, reason, department or designation over recent months. Small groups are suppressed.",
    _obj({"group_by": {"type": "string", "enum": GROUPABLE_EXIT}, "months": {"type": "integer"}}),
)
def exit_insights(run, group_by="exit_type", months=12):
    run.require("Employee Exit", "read")
    meta = frappe.get_meta("Employee Exit")
    since = add_months(today(), -(cint(months) or 12))
    rows = _glist(run, "Employee Exit", filters={"creation": (">=", since), "docstatus": ("<", 2)}, fields=_fields("Employee Exit", ["name", "employee", group_by]), limit_page_length=5000)
    if group_by in ("department", "designation") and not meta.has_field(group_by):
        emap = {e.name: e.get(group_by) for e in frappe.get_all("Employee", filters={"name": ("in", [r.employee for r in rows])}, fields=["name", group_by])}
        for r in rows:
            r[group_by] = emap.get(r.employee)
    counts = {}
    for r in rows:
        counts[r.get(group_by) or "Not set"] = counts.get(r.get(group_by) or "Not set", 0) + 1
    shown = {k: v for k, v in counts.items() if v >= K_MIN}
    return {"group_by": group_by, "months": months, "total": len(rows), "groups": shown, "suppressed_small_groups": sum(1 for v in counts.values() if v < K_MIN)}


@tool(
    "propose_replacement_requisition",
    "Prepare a DRAFT replacement Job Requisition for a leaving employee. Nothing is submitted.",
    _obj({"employee": {"type": "string"}, "plan": {"type": "string"}, "values": {"type": "object"}, "role": {"type": "object", "description": "extra fields for the role row (designation, no_of_positions, ...)"}, "rationale": {"type": "string"}}, ["employee", "values"]),
    writes="AI Proposal",
)
def propose_replacement_requisition(run, employee, values, plan=None, role=None, rationale=""):
    employee = _resolve_employee(employee) or employee
    run.require("Employee", "read", doc=employee)
    row = {"role_type": "Replacement", "role_being_replaced": employee, **(role or {})}
    return _requisition_proposal(run, plan, values, [row], f"Replacement requisition for {employee}", rationale)


# ================================================================== VIKRAM (vendors)
def _vendor_stats(run, months=6, designation=None):
    meta = frappe.get_meta("Job Opening")
    if not meta.has_field("custom_vendor"):
        return {}
    flt_ = {"custom_vendor": ("is", "set"), "creation": (">=", add_months(today(), -months))}
    if designation and meta.has_field("designation"):
        flt_["designation"] = designation
    ops = _glist(run, "Job Opening", filters=flt_, fields=_fields("Job Opening", ["name", "custom_vendor", "custom_sla_breached", "custom_vendor_ageing_days", "custom_vendor_sla_days", "status"]), limit_page_length=2000)
    by = {}
    for o in ops:
        s = by.setdefault(o.custom_vendor, {"openings": 0, "breached": 0, "ageing": [], "names": []})
        s["openings"] += 1
        s["breached"] += 1 if cint(o.get("custom_sla_breached")) else 0
        if o.get("custom_vendor_ageing_days") is not None:
            s["ageing"].append(flt(o.custom_vendor_ageing_days))
        s["names"].append(o.name)
    if by and run.can("Job Applicant", "read"):
        allnames = [n for s in by.values() for n in s["names"]]
        apps = frappe.get_all("Job Applicant", filters={"job_title": ("in", allnames)}, fields=["job_title", "status"], limit=20000)
        owner = {n: v for v, s in by.items() for n in s["names"]}
        for v in by.values():
            v.update(applicants=0, shortlisted=0)
        for a in apps:
            v = by[owner[a.job_title]]
            v["applicants"] += 1
            if a.status in ("Accepted",) or "short" in (a.status or "").lower():
                v["shortlisted"] += 1
    out = {}
    for vendor, s in by.items():
        n = s["openings"]
        out[vendor] = {
            "openings": n, "sla_breached": s["breached"], "breach_rate_pct": round(s["breached"] / n * 100, 1),
            "avg_ageing_days": round(sum(s["ageing"]) / len(s["ageing"]), 1) if s["ageing"] else None,
            "applicants": s.get("applicants", 0), "applicants_per_opening": round(s.get("applicants", 0) / n, 1),
            "shortlisted_or_accepted": s.get("shortlisted", 0),
        }
    return out


@tool(
    "vendor_scorecard",
    "Vendor performance from Job Openings pushed to vendors: openings, SLA breach rate, ageing, applicants per opening.",
    _obj({"vendor": {"type": "string"}, "months": {"type": "integer"}}),
)
def vendor_scorecard(run, vendor=None, months=6):
    run.require("Job Opening", "read")
    stats = _vendor_stats(run, cint(months) or 6)
    if vendor:
        stats = {k: v for k, v in stats.items() if k == vendor}
    return {"months": months, "vendors": stats}


@tool(
    "recommend_vendor_allocation",
    "Rank vendors for a new opening using their recent delivery record (breach rate, applicants per opening). Advisory.",
    _obj({"designation": {"type": "string"}, "months": {"type": "integer"}}),
)
def recommend_vendor_allocation(run, designation=None, months=12):
    run.require("Job Opening", "read")
    stats = _vendor_stats(run, cint(months) or 12, designation)
    ranked = []
    for v, s in stats.items():
        score = round(100 - s["breach_rate_pct"] + min(s["applicants_per_opening"], 10) * 3, 1)
        ranked.append({"vendor": v, **s, "score": score, "low_sample": s["openings"] < 3})
    ranked.sort(key=lambda x: -x["score"])
    return {"designation": designation, "ranking": ranked, "method": "score = 100 - breach rate % + 3 x applicants per opening (max 10). Vendors with fewer than 3 openings are marked low_sample."}


@tool(
    "opening_facts",
    "Key facts of a Job Opening for drafting a vendor brief.",
    _obj({"opening": {"type": "string"}}, ["opening"]),
)
def opening_facts(run, opening):
    run.require("Job Opening", "read", doc=opening)
    doc = frappe.get_doc("Job Opening", opening)
    run.touch("Job Opening", opening)
    return _scalar_snapshot(doc, 1500)


@tool(
    "draft_vendor_brief",
    "Save a drafted vendor email/brief for a Job Opening for the user to review and copy. Nothing is sent.",
    _obj({"opening": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, ["opening", "subject", "body"]),
    writes="AI Proposal",
)
def draft_vendor_brief(run, opening, subject, body):
    run.require("Job Opening", "read", doc=opening)
    return proposals.propose(run, "Draft text", title=f"Vendor brief: {subject}"[:140], rationale=f"Drafted for Job Opening {opening}", text=f"Subject: {subject}\n\n{body}")


# ================================================================== MEERA extras
@tool(
    "compare_candidates",
    "Side-by-side latest ATS results for up to 5 applicants (score, matched/missing must-haves, risks). Advisory only.",
    _obj({"applicants": {"type": "array", "items": {"type": "string"}}}, ["applicants"]),
)
def compare_candidates(run, applicants):
    from hr_ai_agents.hr_ai_agents.agents import ats

    out = []
    for a in (applicants or [])[:5]:
        run.require("Job Applicant", "read", doc=a)
        r = ats.latest_result(a)
        out.append({"applicant": a, "name": (r or {}).get("applicant_name"), "score": (r or {}).get("score"), "missing_must_have": ((r or {}).get("missing") or {}).get("must_have"), "risk_flags": (r or {}).get("risk_flags")} if r else {"applicant": a, "note": "Not scored yet"})
        run.touch("Job Applicant", a)
    return {"comparison": out, "note": "Scores are advisory and are not a hiring decision."}


@tool(
    "stuck_candidates",
    "Applicants with no update for N days in open statuses.",
    _obj({"days": {"type": "integer"}, "opening": {"type": "string"}}),
)
def stuck_candidates(run, days=7, opening=None):
    run.require("Job Applicant", "read")
    flt_ = {"modified": ("<", add_days(today(), -(cint(days) or 7))), "status": ("in", ["Open", "Replied", "Hold"])}
    if opening:
        flt_["job_title"] = opening
    rows = _glist(run, "Job Applicant", filters=flt_, fields=_fields("Job Applicant", ["name", "applicant_name", "job_title", "status", "workflow_state", "modified"]), order_by="modified asc", limit_page_length=30)
    return {"stuck": [dict(r, days_idle=(getdate(today()) - getdate(r.modified)).days) for r in rows]}


@tool(
    "interview_feedback_summary",
    "Interviews and feedback ratings recorded for a Job Applicant.",
    _obj({"applicant": {"type": "string"}}, ["applicant"]),
)
def interview_feedback_summary(run, applicant):
    run.require("Job Applicant", "read", doc=applicant)
    if not frappe.db.exists("DocType", "Interview") or not run.can("Interview", "read"):
        return {"note": "Interview records are not available to you."}
    ivs = frappe.get_all("Interview", filters={"job_applicant": applicant, "docstatus": ("<", 2)}, fields=_fields("Interview", ["name", "interview_round", "status", "scheduled_on", "average_rating"]), limit=10)
    out = []
    for iv in ivs:
        fb = frappe.get_all("Interview Feedback", filters={"interview": iv.name, "docstatus": 1}, fields=_fields("Interview Feedback", ["interviewer", "average_rating", "result", "feedback"]), limit=10) if run.can("Interview Feedback", "read") else []
        for f in fb:
            f["feedback"] = frappe.utils.strip_html(f.get("feedback") or "")[:600]
        out.append({**iv, "feedback": fb})
    run.sent_to_model("Job Applicant", applicant, ["interview feedback"])
    return {"applicant": applicant, "interviews": out}


@tool(
    "propose_job_offer",
    "Prepare a DRAFT Job Offer for a Job Applicant for the user to review and apply. Approvals still follow your process.",
    _obj({"applicant": {"type": "string"}, "designation": {"type": "string"}, "company": {"type": "string"}, "offer_date": {"type": "string"}, "terms": {"type": "array", "items": {"type": "object", "properties": {"offer_term": {"type": "string"}, "value": {"type": "string"}}}}}, ["applicant", "designation", "company"]),
    writes="AI Proposal",
)
def propose_job_offer(run, applicant, designation, company, offer_date=None, terms=None):
    run.require("Job Applicant", "read", doc=applicant)
    vals = {"job_applicant": applicant, "designation": designation, "company": company, "offer_date": offer_date or today(), "status": "Awaiting Response"}
    table = _table("Job Offer", "Job Offer Term")
    rows = {table: {"append": terms}} if terms and table else None
    return proposals.propose(run, "Create draft", "Job Offer", None, vals, rows, f"Job Offer for {applicant}", "Prepared from the applicant record; confirm compensation terms against the approved CXO pack.")


# ================================================================== INSIGHT (analytics)
DEFAULT_ANALYTICS = ["Employee", "Leave Application", "Job Applicant", "Job Opening", "Job Requisition", "Workforce Plan", "Probation Review", "Employee Exit", "CXO Hiring Approval"]
BLOCKED_GROUP = {
    "gender", "date_of_birth", "religion", "blood_group", "marital_status", "passport_number", "bank_ac_no", "personal_email", "cell_number",
    "emergency_phone_number", "person_to_be_contacted", "relation", "current_address", "permanent_address", "health_insurance_no", "family_background",
    "health_details", "employee_name", "first_name", "last_name", "middle_name", "applicant_name", "email_id", "phone_number", "user_id", "prefered_email", "employee",
}
SALARY_PAT = re.compile(r"salary|ctc|compensation|gross|net_pay|payable|cost|budget|amount", re.I)


def analytics_doctypes():
    s = core.settings()
    custom = [x.strip() for x in (s.get("analytics_doctypes") or "").splitlines() if x.strip()]
    return custom or DEFAULT_ANALYTICS


@tool(
    "hr_metrics",
    "Aggregate counts (and optionally sum/average of a numeric field) of a document type, grouped by a category field. Group sizes under 3 are hidden; personal identifiers and protected attributes cannot be used.",
    _obj({
        "doctype": {"type": "string"}, "group_by": {"type": "string"}, "filters": {"type": "object", "description": "field -> value, or field -> [values]"},
        "metric": {"type": "string", "enum": ["count", "sum", "average"]}, "value_field": {"type": "string"}, "months": {"type": "integer", "description": "only records created in the last N months"},
    }, ["doctype"]),
)
def hr_metrics(run, doctype, group_by=None, filters=None, metric="count", value_field=None, months=None):
    if doctype not in analytics_doctypes():
        raise frappe.PermissionError(f"Analytics are not enabled for {doctype}. A manager can add it in AI Agent Settings.")
    run.require(doctype, "read")
    meta = frappe.get_meta(doctype)
    flt_ = {"docstatus": ("<", 2)}
    for k, v in (filters or {}).items():
        if k in BLOCKED_GROUP or not meta.has_field(k):
            raise frappe.ValidationError(f"Cannot filter by '{k}'.")
        flt_[k] = ("in", v) if isinstance(v, list) else v
    if months:
        flt_["creation"] = (">=", add_months(today(), -cint(months)))
    fields = ["name"]
    if group_by:
        df = meta.get_field(group_by)
        if group_by in BLOCKED_GROUP or not df or df.fieldtype not in ("Select", "Link", "Data", "Check"):
            raise frappe.ValidationError(f"Cannot group by '{group_by}'. Use a category field such as department, status or designation.")
        fields.append(group_by)
    if metric in ("sum", "average"):
        df = meta.get_field(value_field or "")
        if not df or df.fieldtype not in ("Currency", "Float", "Int", "Percent"):
            raise frappe.ValidationError("value_field must be a numeric field.")
        if SALARY_PAT.search(value_field) and "HR Manager" not in frappe.get_roles(run.user):
            raise frappe.PermissionError("Sums and averages of pay or cost fields need the HR Manager role.")
        fields.append(value_field)
    rows = _glist(run, doctype, filters=flt_, fields=fields, limit_page_length=20000)
    groups = {}
    for r in rows:
        key = str(r.get(group_by)) if group_by and r.get(group_by) not in (None, "") else ("Not set" if group_by else "All")
        g = groups.setdefault(key, {"n": 0, "vals": []})
        g["n"] += 1
        if metric != "count":
            g["vals"].append(flt(r.get(value_field)))
    out, hidden = {}, 0
    for k, g in groups.items():
        if g["n"] < K_MIN:
            hidden += 1
            continue
        out[k] = g["n"] if metric == "count" else (round(sum(g["vals"]), 2) if metric == "sum" else round(sum(g["vals"]) / len(g["vals"]), 2))
    run.sent_to_model(doctype, "aggregate", [f for f in fields if f != "name"])
    return {"doctype": doctype, "metric": metric, "group_by": group_by, "total_records": len(rows), "groups": out, "groups_hidden_for_small_size": hidden}


# ================================================================== NAVIGATOR
@tool(
    "available_actions",
    "Workflow state of a document and the actions the requesting user can or cannot take, with the reason for each block.",
    _obj({"doctype": {"type": "string"}, "name": {"type": "string"}}, ["doctype", "name"]),
)
def available_actions(run, doctype, name):
    run.require(doctype, "read", doc=name)
    run.touch(doctype, name)
    return workflow_nav.describe(doctype, name, run.user)


@tool(
    "my_pending_approvals",
    "Documents waiting for an action the requesting user is allowed to take.",
    _obj({"limit": {"type": "integer"}}),
)
def my_pending_approvals(run, limit=15):
    return {"waiting_for_you": workflow_nav.pending_for(run.user, min(cint(limit) or 15, 30))}


@tool(
    "propose_todos",
    "Turn action items (for example from meeting notes) into DRAFT To-Dos for the user to review and apply, one proposal per item (max 8).",
    _obj({"items": {"type": "array", "items": {"type": "object", "properties": {"description": {"type": "string"}, "due_date": {"type": "string"}, "assign_to": {"type": "string", "description": "user id (email), default: the requesting user"}, "priority": {"type": "string", "enum": ["Low", "Medium", "High"]}}, "required": ["description"]}}}, ["items"]),
    writes="AI Proposal",
)
def propose_todos(run, items):
    made = []
    for it in (items or [])[:8]:
        to = it.get("assign_to") or run.user
        if not frappe.db.get_value("User", to, "enabled"):
            made.append({"item": it.get("description", "")[:60], "error": f"{to} is not an active user"})
            continue
        vals = {"description": it["description"][:1000], "allocated_to": to, "priority": it.get("priority") or "Medium"}
        if it.get("due_date"):
            vals["date"] = it["due_date"]
        try:
            r = proposals.propose(run, "Create draft", "ToDo", None, vals, None, f"To-do: {it['description'][:80]}", "From action items provided by the user.")
            made.append({"item": it["description"][:60], "proposal": r["proposal"]})
        except proposals.ProposalError as e:
            made.append({"item": it["description"][:60], "error": str(e)})
    return {"proposals": made, "note": "Open each proposal to review and apply it."}


# ================================================================== POLICY PAL
@tool(
    "policy_search",
    "Search the active HR policy documents. Answer ONLY from the returned passages and cite policy title, version and section; if nothing relevant is returned, say the policy library does not cover it.",
    _obj({"query": {"type": "string"}, "category": {"type": "string"}}, ["query"]),
)
def policy_search(run, query, category=None):
    hits = policy.search(query, 4, category)
    if not hits:
        return {"passages": [], "note": "No matching policy text. Do not answer from general knowledge; refer the user to HR."}
    return {"passages": hits}
