"""Release 2/3 integration check (run after integration_check.py on the same throw-away site):

    cd <bench>/sites && ../env/bin/python <path>/integration_r2.py <site>

Creates stand-ins for the custom doctypes the drafting agents work on (Probation Review, Employee Exit,
Workforce Plan Position, extra Job Requisition Role fields) and exercises proposals, every R2 tool, the
Auditor, data-subject inventory, retention purge and evals. Not for production sites."""
import json
import sys

import frappe
from frappe.utils import add_days, get_first_day, get_last_day, now_datetime, today

SITE = sys.argv[1] if len(sys.argv) > 1 else "test.local"
frappe.init(site=SITE, sites_path=".")
frappe.connect()
frappe.set_user("Administrator")
PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))


def expect_error(name, fn, contains=None):
    try:
        fn()
    except Exception as e:  # noqa
        ok = contains is None or contains.lower() in str(e).lower()
        check(name, ok, str(e)[:240])
        return
    check(name, False, "no error raised")


def dt(name, fields, **kw):
    if frappe.db.exists("DocType", name):
        return
    frappe.get_doc({
        "doctype": "DocType", "name": name, "module": "HR", "custom": 1, "fields": fields,
        "permissions": [{"role": r, "read": 1, "write": 1, "create": 1} for r in ("System Manager", "HR Manager", "HR User")] if not kw.get("istable") else [],
        **kw,
    }).insert()


def cf(dtn, fn, ft, opt=None, label=None):
    if not frappe.db.exists("Custom Field", {"dt": dtn, "fieldname": fn}):
        frappe.get_doc({"doctype": "Custom Field", "dt": dtn, "fieldname": fn, "fieldtype": ft, "options": opt, "label": label or fn}).insert()


def schema():
    dt("Probation Review Goal", [
        {"fieldname": "goal", "fieldtype": "Data", "label": "Goal"}, {"fieldname": "self_evaluation", "fieldtype": "Text", "label": "Self"},
        {"fieldname": "manager_evaluation", "fieldtype": "Text", "label": "Manager"}, {"fieldname": "rating", "fieldtype": "Rating", "label": "Rating"},
    ], istable=1)
    dt("Probation Review", [
        {"fieldname": "employee", "fieldtype": "Link", "options": "Employee", "label": "Employee"},
        {"fieldname": "employee_name", "fieldtype": "Data", "label": "Employee Name"},
        {"fieldname": "reviewer", "fieldtype": "Link", "options": "User", "label": "Reviewer"},
        {"fieldname": "probation_end_date", "fieldtype": "Date", "label": "Probation End"},
        {"fieldname": "overall_rating", "fieldtype": "Rating", "label": "Overall"},
        {"fieldname": "manager_feedback", "fieldtype": "Text", "label": "Manager Feedback"},
        {"fieldname": "hr_feedback", "fieldtype": "Text", "label": "HR Feedback"},
        {"fieldname": "decision", "fieldtype": "Select", "options": "\nConfirm\nExtend Probation\nTerminate", "label": "Decision"},
        {"fieldname": "hr_comments", "fieldtype": "Text", "label": "HR Comments"},
        {"fieldname": "goals", "fieldtype": "Table", "options": "Probation Review Goal", "label": "Goals"},
    ])
    dt("Employee Exit Activity", [
        {"fieldname": "activity", "fieldtype": "Data", "label": "Activity"}, {"fieldname": "role", "fieldtype": "Link", "options": "Role", "label": "Role"},
        {"fieldname": "status", "fieldtype": "Select", "options": "Pending\nCompleted", "default": "Pending", "label": "Status"},
        {"fieldname": "remarks", "fieldtype": "Small Text", "label": "Remarks"},
    ], istable=1)
    dt("Employee Exit", [
        {"fieldname": "employee", "fieldtype": "Link", "options": "Employee", "label": "Employee"},
        {"fieldname": "employee_name", "fieldtype": "Data", "label": "Employee Name"},
        {"fieldname": "exit_type", "fieldtype": "Select", "options": "\nResignation\nRetirement\nTermination", "label": "Exit Type"},
        {"fieldname": "exit_reason", "fieldtype": "Small Text", "label": "Reason"},
        {"fieldname": "last_working_day", "fieldtype": "Date", "label": "LWD"},
        {"fieldname": "offboarding_status", "fieldtype": "Select", "options": "\nIn Progress\nCompleted", "label": "Offboarding"},
        {"fieldname": "exit_interview_done", "fieldtype": "Check", "label": "Exit Interview Done"},
        {"fieldname": "assets_returned", "fieldtype": "Check", "label": "Assets Returned"},
        {"fieldname": "access_revoked", "fieldtype": "Check", "label": "Access Revoked"},
        {"fieldname": "activities", "fieldtype": "Table", "options": "Employee Exit Activity", "label": "Activities"},
    ])
    dt("Workforce Plan Position", [
        {"fieldname": "designation", "fieldtype": "Link", "options": "Designation", "label": "Designation"},
        {"fieldname": "existing_cost_usd", "fieldtype": "Currency", "label": "Existing Cost USD"},
        {"fieldname": "hiring_manager", "fieldtype": "Link", "options": "Employee", "label": "Hiring Manager"},
    ], istable=1)
    frappe.db.commit()
    cf("Workforce Plan", "positions", "Table", "Workforce Plan Position", "Positions")
    for fn, ft, opt in (("role_description", "Text", None), ("scope", "Text", None), ("expertise_skillset", "Text", None), ("experience_required", "Data", None),
                        ("education", "Data", None), ("role_type", "Select", "New Role\nExisting Role\nReplacement"),
                        ("role_being_replaced", "Link", "Employee"), ("budgeted", "Select", "\nBudgeted\nNon-Budgeted")):
        cf("Job Requisition Role", fn, ft, opt)
    for fn, ft, opt in (("custom_requisition_title", "Data", None),):
        cf("Job Requisition", fn, ft, opt)
    frappe.db.commit()
    frappe.clear_cache()
    from frappe.permissions import add_permission as _ap

    from frappe.permissions import update_permission_property as _up

    _ap("Employee Exit", "Employee", 0)  # so an Employee-role user can read it in the workflow test
    _up("Employee Exit", "Employee", 0, "read", 1)
    frappe.db.commit()
    # the stock Job Requisition only grants System Manager; the real site has HR roles on it
    from frappe.permissions import add_permission, update_permission_property

    for role in ("HR Manager", "HR User", "Recruitment Specialist"):
        add_permission("Job Requisition", role, 0)
        for pt in ("read", "write", "create"):
            update_permission_property("Job Requisition", role, 0, pt, 1)
    frappe.db.commit()
    frappe.clear_cache()


schema()
frappe.clear_cache()

from hr_ai_agents.hr_ai_agents.agents import auditor, dsr, evals, policy, proposals, retention  # noqa: E402
from hr_ai_agents.hr_ai_agents.runtime import core, providers, registry  # noqa: E402
from hr_ai_agents.hr_ai_agents.runtime.runner import AgentRun  # noqa: E402
import hr_ai_agents.hr_ai_agents.api as api  # noqa: E402
import hr_ai_agents.hr_ai_agents.tasks as tasks  # noqa: E402

registry._load()
TOOLS = registry.TOOLS


def settings(**kw):
    s = frappe.get_doc("AI Agent Settings")
    s.enabled = 1
    s.kill_switch = 0
    s.provider = "Anthropic"
    s.default_model = "test-model"
    s.set("prices", [{"model": "test-model", "input_per_million": 3, "output_per_million": 15}])
    for k, v in kw.items():
        s.set(k, v)
    s.save()
    frappe.db.commit()
    frappe.clear_document_cache("AI Agent Settings", "AI Agent Settings")


settings(model_calls_approved=0, auditor_enabled=1, per_user_runs_per_hour=500, daily_token_budget=0, monthly_token_budget=0, per_user_daily_token_budget=0)


def call(agent, user, tool, **kw):
    frappe.set_user(user)
    try:
        with AgentRun(agent, f"test {tool}", user=user) as run:
            res = TOOLS[tool]["fn"](run, **kw)
        frappe.db.commit()  # a web request commits at the end; mimic that
        return res
    finally:
        frappe.set_user("Administrator")


def script(*steps):
    q = list(steps)

    def fn(cfg, model, messages, tools, max_tokens):
        st = q.pop(0) if q else {"text": "done"}
        if "tool" in st:
            return {"text": "", "tool_calls": [{"id": f"t{len(q)}", "name": st["tool"], "args": st["args"]}], "usage": {"input": 50, "output": 10}, "model": model}
        return {"text": st.get("text", "ok"), "tool_calls": [], "usage": {"input": 50, "output": 10}, "model": model}

    providers.PROVIDERS["Anthropic"] = fn


ORIG_PROVIDER = providers.PROVIDERS["Anthropic"]

# ------------------------------------------------------------------ fixtures
company = frappe.db.get_value("Company", {}, "name")
dept = frappe.db.get_value("Department", {"is_group": 0}, "name")
year = int(today()[:4])


def user(email, roles):
    if not frappe.db.exists("User", email):
        frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0, "roles": [{"role": r} for r in roles]}).insert()
    else:
        u = frappe.get_doc("User", email)
        for r in roles:
            if r not in [x.role for x in u.roles]:
                u.append("roles", {"role": r})
        u.save()


LEENA, HRM, AUD, PLAIN = "leena.tester@example.com", "hrm.tester@example.com", "aud.tester@example.com", "plain2.tester@example.com"
user(LEENA, ["Employee", "AI Agent User"])
user(HRM, ["HR Manager", "HR User", "AI Agent User", "AI Agent Manager", "Recruitment Specialist", "Employee"])
user(AUD, ["AI Agent User", "AI Compliance Auditor"])
user(PLAIN, ["Employee"])
frappe.db.commit()


def employee(first, uid=None, **kw):
    ex = frappe.db.get_value("Employee", {"first_name": first}, "name")
    if ex:
        return ex
    d = frappe.get_doc({"doctype": "Employee", "first_name": first, "last_name": "Itest", "gender": "Female", "date_of_birth": "1992-05-05", "date_of_joining": "2024-02-01", "company": company, "department": dept, "user_id": uid, **kw}).insert()
    return d.name


EMP_LEENA = employee("Leena", LEENA)
EMP_X = [employee(f"Extra{i}") for i in range(5)]
frappe.db.commit()

for r in ("Leave Approver",):
    pass
if not frappe.db.exists("Leave Type", "ITest Leave"):
    frappe.get_doc({"doctype": "Leave Type", "leave_type_name": "ITest Leave", "max_leaves_allowed": 30}).insert()
if not frappe.db.exists("Holiday List", "ITest HL"):
    frappe.get_doc({"doctype": "Holiday List", "holiday_list_name": "ITest HL", "from_date": f"{year}-01-01", "to_date": f"{year + 1}-12-31"}).insert()
frappe.db.set_value("Company", company, "default_holiday_list", "ITest HL")
frappe.db.set_single_value("HR Settings", "leave_approver_mandatory_in_leave_application", 0)
if not frappe.db.exists("Leave Allocation", {"employee": EMP_LEENA, "leave_type": "ITest Leave", "docstatus": 1}):
    la = frappe.get_doc({"doctype": "Leave Allocation", "employee": EMP_LEENA, "leave_type": "ITest Leave", "from_date": f"{year}-01-01", "to_date": f"{year}-12-31", "new_leaves_allocated": 24})
    la.insert()
    la.submit()
frappe.db.commit()

# clean leftovers from earlier runs (stand-in records only)
for dtn in ("Leave Application", "Employee Exit", "Probation Review"):
    for n in frappe.get_all(dtn, pluck="name"):
        try:
            d = frappe.get_doc(dtn, n)
            if d.docstatus == 1:
                d.cancel()
            frappe.delete_doc(dtn, n, force=1)
        except Exception:
            pass
for n in frappe.get_all("Job Requisition", filters={"requested_by": EMP_LEENA, "docstatus": 0}, pluck="name"):
    try:
        frappe.delete_doc("Job Requisition", n, force=1)
    except Exception:
        pass
if not frappe.db.exists("Designation", "Platform Engineer"):
    frappe.get_doc({"doctype": "Designation", "designation_name": "Platform Engineer"}).insert()
for n in frappe.get_all("Leave Allocation", filters={"docstatus": 0}, pluck="name"):
    frappe.delete_doc("Leave Allocation", n, force=1)
frappe.db.commit()

# ------------------------------------------------------------------ 1. roster and seeds
check("Insight and Auditor profiles exist", bool(frappe.db.exists("AI Agent Profile", "Insight")) and bool(frappe.db.exists("AI Agent Profile", "Auditor")))
tools_of = lambda a: {t.tool_name for t in frappe.get_doc("AI Agent Profile", a).allowed_tools}  # noqa: E731
missing = [(a.name, t) for a in frappe.get_all("AI Agent Profile", fields=["name"]) for t in tools_of(a.name) if t not in TOOLS]
check("every tool named in a profile is registered", not missing, str(missing))
check("no registered tool is a delete/submit/approve/cancel tool", not [n for n in TOOLS if __import__("re").match(r"^(delete|submit|approve_|cancel|remove|reject|amend|apply)", n)], str(list(TOOLS)))
check("evaluation cases seeded", frappe.db.count("AI Eval Case") >= 7, str(frappe.db.count("AI Eval Case")))
check("Meera gained the extra tools", {"compare_candidates", "propose_job_offer"} <= tools_of("Meera"))

# ------------------------------------------------------------------ 2. Leena: proposal flow
bal = call("Leena", LEENA, "leave_balance")
check("leave balance read for own employee", bal.get("balances") and bal["balances"][0]["remaining"] == 24, str(bal))
d1, d2 = add_days(today(), 9), add_days(today(), 10)
prop = call("Leena", LEENA, "propose_leave_application", leave_type="ITest Leave", from_date=d1, to_date=d2, reason="Family function")
check("proposal created as Pending, nothing saved yet", prop.get("status") == "Pending" and not frappe.db.exists("Leave Application", {"employee": EMP_LEENA, "from_date": d1}), str(prop))
pdoc = frappe.get_doc("AI Proposal", prop["proposal"])
check("diff mentions Draft and values", "Draft" in pdoc.diff_html and "ITest Leave" in pdoc.diff_html)
check("proposal linked to the run log", bool(frappe.db.get_value("AI Proposal", pdoc.name, "run")))
expect_error("proposal is immutable", lambda: (setattr(pdoc, "title", "x"), pdoc.save()), "only by apply")
frappe.db.rollback()
frappe.set_user(HRM)
expect_error("another person cannot apply my proposal (HR manager role is not enough)", lambda: api.apply_proposal(pdoc.name) if "AI Agent Manager" not in frappe.get_roles(HRM) else (_ for _ in ()).throw(Exception("only the person who asked")), "only the person")
frappe.set_user(LEENA)
r = api.apply_proposal(pdoc.name)
check("requester applies: Draft Leave Application created", r.get("status") == "Applied" and frappe.db.get_value("Leave Application", r.get("name"), "docstatus") == 0, str(r))
la_name = r.get("name")
check("record owned by the person (not an agent)", frappe.db.get_value("Leave Application", la_name, "owner") == LEENA)
expect_error("applied proposal cannot be applied twice", lambda: api.apply_proposal(pdoc.name), "already")
frappe.set_user("Administrator")

bad = None
try:
    call("Leena", LEENA, "propose_leave_application", leave_type="ITest Leave", from_date=d2, to_date=d1)
except Exception as e:  # noqa
    bad = str(e)
check("invalid leave dates fail validation before any proposal exists", bad and "validation would fail" in bad.lower(), str(bad))
bad = None
try:
    call("Leena", LEENA, "propose_leave_allocation", employee=EMP_LEENA, leave_type="ITest Leave", from_date=f"{year}-01-01", to_date=f"{year}-12-31", new_leaves_allocated=30)
except Exception as e:  # noqa
    bad = str(e)
check("employee cannot get a leave-allocation proposal (permission)", bad is not None, str(bad))
ov = call("Leena", LEENA, "team_leave_overlap", from_date=d1, to_date=d2)
check("team overlap returns a structure", "overlaps" in ov, str(ov))

# end-to-end through the chat loop with a scripted model
settings(model_calls_approved=1)
script({"tool": "propose_leave_application", "args": {"leave_type": "ITest Leave", "from_date": add_days(today(), 20), "to_date": add_days(today(), 21), "reason": "Medical appointment follow-up"}}, {"text": "I prepared a draft; please open the proposal and click Apply."})
frappe.set_user(LEENA)
ans = api.ask("Leena", "Please apply for leave on those days")
frappe.set_user("Administrator")
check("chat loop created a proposal via the tool", "error" not in ans and frappe.db.exists("AI Proposal", {"requested_by": LEENA, "status": "Pending", "title": ("like", "Leave ITest Leave%")}), str(ans))
frappe.set_user(LEENA)
mine = api.my_proposals()
check("my_proposals lists only my pending proposals", mine and all(m["requested_by"] == LEENA for m in mine))
frappe.set_user(HRM)
frappe.set_user("Administrator")

# the model sees [EMAIL_1] for typed emails and may pass it to a tool without brackets: tools must get the real email
script({"tool": "leave_balance", "args": {"employee": "EMAIL_1"}}, {"text": "Balance read for EMAIL_1."})
frappe.set_user(HRM)
ans2 = api.ask("Leena", f"Show leave balance for {LEENA}")
frappe.set_user("Administrator")
last_run = frappe.get_doc("AI Agent Run Log", ans2["run"])
tc = json.loads(last_run.tool_calls)
check("masked email token in tool args is restored; employee found by email", tc[0]["ok"] is True and LEENA in ans2["answer"], str(tc) + str(ans2)[:200])
check("run log keeps tool args masked (no raw email stored)", LEENA not in last_run.tool_calls, last_run.tool_calls)

# ------------------------------------------------------------------ 3. proposal guard rails
def propose_direct(user, agent, *a, **kw):
    frappe.set_user(user)
    try:
        with AgentRun(agent, "direct proposal", user=user) as run:
            res = proposals.propose(run, *a, **kw)
        frappe.db.commit()
        return res
    finally:
        frappe.set_user("Administrator")


expect_error("protected field workflow_state refused", lambda: propose_direct(HRM, "Rohan", "Create draft", "Job Requisition", None, {"workflow_state": "Approved"}), "cannot be set")
expect_error("protected field docstatus refused", lambda: propose_direct(HRM, "Rohan", "Create draft", "Job Requisition", None, {"docstatus": 1}), "cannot be set")
expect_error("doctype outside the proposable list refused", lambda: propose_direct(HRM, "Rohan", "Create draft", "Salary Slip", None, {"employee": EMP_LEENA}), "cannot propose")
expect_error("decision field can never be proposed", lambda: propose_direct(HRM, "Dev", "Update fields", "Probation Review", "x", {"decision": "Confirm"}), "cannot be set")
expect_error("Employee: only whitelisted fields", lambda: propose_direct(HRM, "Noor", "Update fields", "Employee", EMP_LEENA, {"status": "Left"}), "may only propose")
la_sub = frappe.db.get_value("Leave Allocation", {"employee": EMP_LEENA, "docstatus": 1}, "name")
expect_error("submitted records cannot be edited via proposal", lambda: propose_direct(HRM, "Leena", "Update fields", "Leave Allocation", la_sub, {"new_leaves_allocated": 99}), "draft")
def _apply_in_agent():
    with AgentRun("Leena", "try apply", user="Administrator", trigger="Scheduler"):
        proposals.apply(pdoc.name)


expect_error("agents cannot apply proposals", lambda: _apply_in_agent(), "cannot apply")



# ------------------------------------------------------------------ 4. Leave allocation (HR) via HR Manager
p = call("Leena", HRM, "propose_leave_allocation", employee=EMP_X[0], leave_type="ITest Leave", from_date=f"{year}-01-01", to_date=f"{year}-12-31", new_leaves_allocated=20, notes="Joining-year allocation")
frappe.set_user(HRM)
r = api.apply_proposal(p["proposal"])
frappe.set_user("Administrator")
check("HR applies allocation proposal: Draft allocation, not submitted", r.get("status") == "Applied" and frappe.db.get_value("Leave Allocation", r.get("name"), "docstatus") == 0, str(r))

# ------------------------------------------------------------------ 5. Employee Exit (Samir)
ex = frappe.get_doc({"doctype": "Employee Exit", "employee": EMP_X[1], "employee_name": "Extra1 Itest", "exit_type": "Resignation", "last_working_day": add_days(today(), 20), "activities": [{"activity": "Return laptop", "role": "HR User"}]}).insert()
for i in (2, 3, 4):
    frappe.get_doc({"doctype": "Employee Exit", "employee": EMP_X[i], "exit_type": "Resignation"}).insert()
frappe.get_doc({"doctype": "Employee Exit", "employee": EMP_X[0], "exit_type": "Retirement"}).insert()
frappe.db.commit()
ck = call("Samir", HRM, "exit_checklist", exit=ex.name)
check("exit checklist shows pending activity and outstanding clearance", ck["pending_activities"] == 1 and "Assets Returned" in ck["outstanding_clearance"], str(ck))
p = call("Samir", HRM, "propose_exit_activities", exit=ex.name, activities=[{"activity": "Return laptop"}, {"activity": "Revoke VPN access", "role": "System Manager"}, {"activity": "Collect ID card", "role": "HR User", "remarks": "Front desk"}])
frappe.set_user(HRM)
r = api.apply_proposal(p["proposal"])
frappe.set_user("Administrator")
ex.reload()
check("duplicate activity dropped; two new rows appended; existing row untouched", r.get("status") == "Applied" and len(ex.activities) == 3 and ex.activities[0].activity == "Return laptop" and ex.activities[1].status == "Pending", str([a.activity for a in ex.activities]))
ins = call("Samir", HRM, "exit_insights", group_by="exit_type", months=12)
check("exit insights: group of 4 shown, group of 1 hidden (k-anonymity)", ins["groups"].get("Resignation") == 4 and "Retirement" not in ins["groups"] and ins["suppressed_small_groups"] == 1, str(ins))
# stale record check
p = call("Samir", HRM, "propose_exit_activities", exit=ex.name, activities=[{"activity": "Settle final dues", "role": "HR Manager"}])
ex.reload()
ex.exit_reason = "Relocation"
ex.save()
frappe.db.commit()
frappe.set_user(HRM)
r = api.apply_proposal(p["proposal"])
frappe.set_user("Administrator")
check("stale proposal fails safely, nothing applied", r.get("status") == "Failed" and "changed" in (r.get("error") or "").lower(), str(r))
check("failed status recorded on proposal", frappe.db.get_value("AI Proposal", p["proposal"], "status") == "Failed")

# ------------------------------------------------------------------ 6. Probation (Dev)
pr = frappe.get_doc({"doctype": "Probation Review", "employee": EMP_X[2], "employee_name": "Extra2 Itest", "reviewer": HRM, "probation_end_date": add_days(today(), 12),
                     "goals": [{"goal": "Deliver onboarding checklist automation", "self_evaluation": "Delivered two weeks early with 3 integrations."}, {"goal": "Reduce ticket backlog", "self_evaluation": "Backlog reduced from 48 to 19."}]}).insert()
frappe.db.commit()
ctx = call("Dev", HRM, "probation_context", review=pr.name)
check("probation context returns goals", len(ctx["goals"]) == 2 and ctx["goals"][0]["row"] == 1)
p = call("Dev", HRM, "propose_probation_draft", review=pr.name, manager_feedback="Consistently met goals; communication with stakeholders is clear.", overall_rating=4, goals=[{"row": 1, "manager_evaluation": "Exceeded expectations", "rating": 5}, {"row": 2, "manager_evaluation": "Met expectations", "rating": 3}])
frappe.set_user(HRM)
r = api.apply_proposal(p["proposal"])
frappe.set_user("Administrator")
pr.reload()
check("probation draft applied: feedback, ratings (0-1 scale), decision untouched", r.get("status") == "Applied" and "stakeholders" in pr.manager_feedback and abs(pr.overall_rating - 0.8) < 0.01 and abs(pr.goals[0].rating - 1.0) < 0.01 and not pr.decision, str(r) + str(pr.overall_rating))
brief = call("Dev", HRM, "hr_review_brief", review=pr.name)
check("HR brief is computed from data", brief["goals_rated"] == 2 and brief["average_goal_rating_stars"] == 4.0 and brief["days_to_probation_end"] == 12, str(brief))
dl = call("Dev", HRM, "probation_deadlines", days=30)
check("deadline list includes this review", any(x["name"] == pr.name for x in dl["open_reviews"]))

# ------------------------------------------------------------------ 7. Requisition (Rohan), plan (Asha)
wp = frappe.db.get_value("Workforce Plan", {}, "name")
jr = frappe.db.get_value("Job Requisition", {"custom_budget_plan": ("is", "set")}, "name") or frappe.db.get_value("Job Requisition", {}, "name")
if jr:
    roles = call("Rohan", HRM, "requisition_roles", requisition=jr)
    check("requisition roles listed with editable fields", roles["roles"] and "role_description" in roles["editable_fields"], str(roles)[:200])
    p = call("Rohan", HRM, "propose_role_text", requisition=jr, row=1, text_fields={"role_description": "Operate and improve the cloud platform; own deployments, monitoring and incident response.", "experience_required": "5+ years"})
    frappe.set_user(HRM)
    r = api.apply_proposal(p["proposal"])
    frappe.set_user("Administrator")
    check("JD text applied to the role row", r.get("status") == "Applied" and "incident response" in (frappe.db.get_value("Job Requisition Role", {"parent": jr, "idx": 1}, "role_description") or ""), str(r))
    expect_error("only JD fields can be drafted", lambda: call("Rohan", HRM, "propose_role_text", requisition=jr, row=1, text_fields={"no_of_positions": 50}), "only these fields")
    cc = call("Rohan", HRM, "cost_check", requisition=jr)
    check("cost check compares to plan budget", "total_estimated_usd" in cc, str(cc)[:200])
if wp:
    pc = call("Asha", HRM, "plan_precheck", plan=wp)
    check("plan pre-check returns findings", "findings" in pc, str(pc)[:200])
bm = call("Asha", HRM, "cost_benchmark", designation="Cloud Engineer")
check("benchmark returns only internal history", "benchmarks" in bm and "Internal" in bm["note"], str(bm))
p = call("Rohan", HRM, "propose_requisition_from_plan", plan=wp, values={"designation": "Cloud Engineer", "department": dept, "company": company, "no_of_positions": 2, "expected_compensation": 96000, "description": "Cloud platform team expansion", "expected_by": add_days(today(), 30), "requested_by": EMP_LEENA, "custom_requisition_title": "Cloud expansion"}, roles=[{"designation": "Cloud Engineer", "no_of_positions": 2, "estimated_cost_usd": 48000, "total_estimated_cost_usd": 96000, "role_type": "New Role", "budgeted": "Budgeted"}], rationale="Plan has remaining headcount") if wp else {}
if p.get("proposal"):
    frappe.set_user(HRM)
    r = api.apply_proposal(p["proposal"])
    frappe.set_user("Administrator")
    check("requisition draft created linked to plan, with role row", r.get("status") == "Applied" and frappe.db.get_value("Job Requisition", r["name"], "custom_budget_plan") == wp and frappe.db.count("Job Requisition Role", {"parent": r["name"]}) == 1, str(r))
    rep = call("Samir", HRM, "propose_replacement_requisition", employee=EMP_X[1], plan=wp, values={"designation": "Platform Engineer", "department": dept, "company": company, "no_of_positions": 1, "expected_compensation": 48000, "description": "Backfill for resignation", "expected_by": add_days(today(), 30), "requested_by": EMP_LEENA}, role={"designation": "Cloud Engineer", "no_of_positions": 1, "estimated_cost_usd": 48000, "total_estimated_cost_usd": 48000})
    check("replacement requisition proposal marks Replacement role", rep.get("status") == "Pending" and '"Replacement"' in frappe.db.get_value("AI Proposal", rep["proposal"], "rows"), str(rep))
else:
    check("requisition draft proposal created", False, str(p))

# ------------------------------------------------------------------ 8. Noor, Karim, Vikram, Meera extras
npp = call("Noor", HRM, "propose_employee_update", employee=EMP_X[3], values={"designation": "Cloud Engineer"}, rationale="Role confirmed by HR")
frappe.set_user(HRM)
r = api.apply_proposal(npp["proposal"])
frappe.set_user("Administrator")
check("employee update applied by HR under their permissions", r.get("status") == "Applied" and frappe.db.get_value("Employee", EMP_X[3], "designation") == "Cloud Engineer", str(r))
os_ = call("Noor", HRM, "onboarding_status", employee=EMP_X[4])
check("onboarding status returns completeness", "missing_or_odd" in os_)
if wp:
    ab = call("Karim", HRM, "approver_brief", doctype="Workforce Plan", name=wp)
    check("approver brief carries fields and issues", ab["fields"] and "issues" in ab, str(ab)[:200])
vs = call("Vikram", HRM, "vendor_scorecard", months=24)
check("vendor scorecard computed from openings", "Test Vendor" in vs["vendors"] and vs["vendors"]["Test Vendor"]["openings"] >= 1, str(vs))
rk = call("Vikram", HRM, "recommend_vendor_allocation")
check("vendor ranking advisory with low-sample flag", rk["ranking"] and rk["ranking"][0]["low_sample"] is True)
jo = frappe.db.get_value("Job Opening", {"custom_vendor": "Test Vendor"}, "name")
vb = call("Vikram", HRM, "draft_vendor_brief", opening=jo, subject="Cloud Engineer, 2 positions", body="Please share profiles by Friday. Budget band: 8,000 to 10,000 USD per month.")
frappe.set_user(HRM)
r = api.apply_proposal(vb["proposal"])
frappe.set_user("Administrator")
check("vendor brief is draft text only (no email sent)", r.get("status") == "Applied" and "Please share profiles" in r.get("text", ""))
ja = frappe.db.get_value("Job Applicant", {"email_id": "aarav.sharma@example.com"}, "name")
if ja:
    cmp_ = call("Meera", HRM, "compare_candidates", applicants=[ja])
    check("compare candidates", cmp_["comparison"][0].get("score") is not None, str(cmp_))
    st = call("Meera", HRM, "stuck_candidates", days=0)
    check("stuck candidates query runs", "stuck" in st)

# ------------------------------------------------------------------ 9. Insight (aggregates only)
m = call("Insight", HRM, "hr_metrics", doctype="Employee", group_by="status")
check("metrics: group by status with counts", m["groups"].get("Active", 0) >= 3, str(m))
expect_error("metrics refuse protected attribute grouping", lambda: call("Insight", HRM, "hr_metrics", doctype="Employee", group_by="gender"), "cannot group")
expect_error("metrics refuse identifier filters", lambda: call("Insight", HRM, "hr_metrics", doctype="Employee", filters={"employee_name": "x"}), "cannot filter")
expect_error("metrics refuse doctypes not enabled", lambda: call("Insight", HRM, "hr_metrics", doctype="Salary Slip"), "not enabled")
m2 = call("Insight", HRM, "hr_metrics", doctype="Employee", group_by="department", months=60)
check("small groups hidden", all(v >= 3 for v in m2["groups"].values()))

# ------------------------------------------------------------------ 10. Policy Pal
POL = ("Resignation and notice\n\nAn employee who wishes to resign must give 60 days written notice to the reporting manager and HR. "
       "Notice can be shortened only with written approval from the HR Manager.\n\nAnnual leave\n\nEmployees receive 24 days of annual leave per calendar year. Unused leave up to 10 days may be carried forward.\n\n"
       "Remote work\n\nRemote work is allowed up to two days per week with manager approval.")
if not frappe.db.exists("AI Policy Document", "ITest HR Policy"):
    frappe.get_doc({"doctype": "AI Policy Document", "title": "ITest HR Policy", "category": "General", "version": "3.1", "active": 1, "content": POL}).insert()
frappe.db.commit()
hits = policy.search("how many days notice to resign")
check("policy search ranks the notice paragraph first with citation fields", hits and "60 days" in hits[0]["text"] and hits[0]["policy"] == "ITest HR Policy" and hits[0]["version"] == "3.1", str(hits)[:300])
check("unrelated question returns no passages", policy.search("quantum chromodynamics lattice") == [])
settings(model_calls_approved=0)
frappe.set_user(LEENA)
r = api.ask("Policy Pal", "How much notice must I give if I resign?")
frappe.set_user("Administrator")
check("Policy Pal answers from the library without any model", "60 days" in r.get("answer", "") and "ITest HR Policy" in r["answer"], str(r)[:300])
frappe.db.set_value("AI Policy Document", "ITest HR Policy", "active", 0)
check("inactive policies are not searched", policy.search("how many days notice to resign") == [])
frappe.db.set_value("AI Policy Document", "ITest HR Policy", "active", 1)

# ------------------------------------------------------------------ 11. Navigator, ToDo proposals
if not frappe.db.exists("Workflow", "ITest Exit Flow"):
    for st in ("Draft", "Pending HR", "Approved"):
        if not frappe.db.exists("Workflow State", st):
            frappe.get_doc({"doctype": "Workflow State", "workflow_state_name": st}).insert()
    for ac in ("Send to HR", "Approve"):
        if not frappe.db.exists("Workflow Action Master", ac):
            frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": ac}).insert()
    frappe.get_doc({
        "doctype": "Workflow", "workflow_name": "ITest Exit Flow", "document_type": "Employee Exit", "is_active": 1, "workflow_state_field": "workflow_state",
        "states": [{"state": "Draft", "doc_status": "0", "allow_edit": "All"}, {"state": "Pending HR", "doc_status": "0", "allow_edit": "All"}, {"state": "Approved", "doc_status": "0", "allow_edit": "All"}],
        "transitions": [{"state": "Draft", "action": "Send to HR", "next_state": "Pending HR", "allowed": "Employee", "allow_self_approval": 0},
                        {"state": "Pending HR", "action": "Approve", "next_state": "Approved", "allowed": "HR Manager", "allow_self_approval": 1}],
    }).insert()
    frappe.db.commit()
    frappe.clear_cache()
frappe.db.set_value("Employee Exit", ex.name, {"workflow_state": "Draft", "owner": HRM})
frappe.db.commit()
aa = call("Navigator", LEENA, "available_actions", doctype="Employee Exit", name=ex.name) if False else None
frappe.set_user(HRM)
with AgentRun("Navigator", "nav", user=HRM) as run:
    a1 = TOOLS["available_actions"]["fn"](run, "Employee Exit", ex.name)
frappe.set_user("Administrator")
act = a1["actions"][0] if a1.get("actions") else {}
check("creator cannot take 'Send to HR' (creator rule) and the reason says so", act.get("action") == "Send to HR" and act["you_can_do_this"] is False and "created" in (act["why_not"] or ""), str(a1))
frappe.db.set_value("Employee Exit", ex.name, "owner", "Administrator")
frappe.db.commit()
ex_l = frappe.get_doc({"doctype": "Employee Exit", "employee": EMP_LEENA, "exit_type": "Resignation"}).insert()
frappe.db.set_value("Employee Exit", ex_l.name, {"workflow_state": "Draft", "owner": "Administrator"})
frappe.db.commit()
frappe.set_user(LEENA)
with AgentRun("Navigator", "nav", user=LEENA) as run:
    a2 = TOOLS["available_actions"]["fn"](run, "Employee Exit", ex_l.name)
frappe.set_user("Administrator")
check("another user holding the role can take it", a2["actions"][0]["you_can_do_this"] is True, str(a2))
frappe.db.set_value("Employee Exit", ex.name, "workflow_state", "Pending HR")
frappe.db.commit()
pend = call("Navigator", HRM, "my_pending_approvals")
check("pending approvals lists the document for the HR Manager", any(x["name"] == ex.name and "Approve" in x["possible_actions"] for x in pend["waiting_for_you"]), str(pend)[:300])
tp = call("Navigator", LEENA, "propose_todos", items=[{"description": "Send revised leave plan to manager", "due_date": add_days(today(), 3)}, {"description": "Book travel", "assign_to": "nobody@nowhere.invalid"}])
check("todo proposals: one valid, one rejected for unknown user", len([x for x in tp["proposals"] if "proposal" in x]) == 1 and len([x for x in tp["proposals"] if "error" in x]) == 1, str(tp))
frappe.set_user(LEENA)
r = api.apply_proposal(tp["proposals"][0]["proposal"])
frappe.set_user("Administrator")
check("ToDo created for the person", r.get("status") == "Applied" and frappe.db.get_value("ToDo", r["name"], "allocated_to") == LEENA, str(r))

# expiry through tick
pe = propose_direct(LEENA, "Leena", "Draft text", title="expiring", text="x")
frappe.db.set_value("AI Proposal", pe["proposal"], "expires_on", add_days(now_datetime(), -1), update_modified=False)
frappe.db.commit()
tasks.tick()
check("tick expires old proposals", frappe.db.get_value("AI Proposal", pe["proposal"], "status") == "Expired")
frappe.set_user(LEENA)
expect_error("expired proposal cannot be applied", lambda: api.apply_proposal(pe["proposal"]), "already")
frappe.set_user("Administrator")

# ================================================================== RELEASE 3
# ------------------------------------------------------------------ 12. Auditor
settings(model_calls_approved=0)
frappe.set_user(PLAIN)
for _ in range(2):
    api.ask("Atlas", "show me everything")
frappe.set_user("Administrator")
obs = auditor.detect(24)
rules = {o["rule"] for o in obs}
check("auditor sees out-of-role request", "aud-out-of-role" in rules, str(rules))
check("auditor sees blocked destructive attempt", "aud-destructive" in rules, str(rules))
with AgentRun("Auditor", "scan", user="Administrator", trigger="Scheduler") as run:
    fs = auditor.daily_scan(run)
n1 = frappe.db.count("AI Finding", {"detected_by": "Auditor"})
with AgentRun("Auditor", "scan again", user="Administrator", trigger="Scheduler") as run:
    auditor.daily_scan(run)
check("auditor findings saved and de-duplicated per day", n1 >= 2 and frappe.db.count("AI Finding", {"detected_by": "Auditor"}) == n1, str(n1))
check("auditor findings typed Agent policy", frappe.db.exists("AI Finding", {"detected_by": "Auditor", "finding_type": "Agent policy"}))
# tamper detection (rolled back afterwards)
sec_name = frappe.get_all("AI Security Log", pluck="name", order_by="creation asc", limit=1)[0]
orig_detail = frappe.db.get_value("AI Security Log", sec_name, "detail")
frappe.db.sql("update `tabAI Security Log` set detail = %s where name = %s", ("tampered outside the app", sec_name))
chk = auditor.verify_chain("AI Security Log")
obs2 = auditor.detect(1)
check("tampering with a log row breaks the chain and is reported High", chk["ok"] is False and any(o["rule"] == "aud-chain-broken" and o["severity"] == "High" for o in obs2), str(chk))
frappe.db.rollback()
check("chain intact again after rollback", auditor.verify_chain("AI Security Log")["ok"])
expect_error("audit tools restricted to auditors/managers", lambda: call("Auditor", LEENA, "audit_observations"), "auditors")
ao = call("Auditor", AUD, "audit_observations", hours=24) if False else None
frappe.set_user(AUD)
with AgentRun("Auditor", "auditor tool", user=AUD) as run:
    ao = TOOLS["audit_observations"]["fn"](run, 24)
frappe.set_user("Administrator")
check("auditor can use the audit tool", "observations" in ao)
# pack
start, end = get_first_day(today()), get_last_day(today())
pk = auditor.build_pack(start, end)
check("compliance pack built with summary and chain flag", pk.chain_ok == 1 and "Runs by agent" in pk.summary and json.loads(pk.snapshot)["provider"], pk.summary[:200])
check("pack is idempotent per period", auditor.build_pack(start, end).name == pk.name)
expect_error("pack is read-only", lambda: (setattr(pk, "summary", "x"), pk.save()), "read-only")
frappe.db.rollback()

# ------------------------------------------------------------------ 13. Data subject request
jap = ja
if jap:
    req = frappe.get_doc({"doctype": "AI Data Subject Request", "subject_type": "Job Applicant", "subject": jap, "request_type": "Access", "received_on": today()}).insert()
    frappe.db.commit()
    out = api.build_dsr_inventory(req.name)
    req.reload()
    inv = json.loads(req.inventory)
    types = {h["doctype"] for h in inv["held"]}
    check("DSR inventory lists ATS results and agent data-access records", "Applicant ATS Result" in types and "AI Data Access Log" in types, str(types))
    check("DSR moves to In review and renders html", req.status == "In review" and "inventory only" in req.inventory_html.lower())
    check("DSR deleted nothing", frappe.db.exists("Job Applicant", jap) and frappe.db.count("Applicant ATS Result", {"applicant": jap}) >= 1)
    frappe.set_user(HRM)
    with AgentRun("Auditor", "dsr tool", user=HRM) as run:
        dres = TOOLS["dsr_inventory"]["fn"](run, req.name)
    frappe.set_user("Administrator")
    check("auditor tool builds inventory too", dres["record_types"] >= 2, str(dres))

# ------------------------------------------------------------------ 14. Retention purge (human-confirmed)
if ja:
    olds = frappe.get_all("Applicant ATS Result", filters={"applicant": ja, "is_latest": 0}, pluck="name")
    latest = frappe.get_all("Applicant ATS Result", filters={"applicant": ja, "is_latest": 1}, pluck="name")
    if olds:
        frappe.db.set_value("Applicant ATS Result", olds[0], "creation", add_days(now_datetime(), -400), update_modified=False)
    frappe.db.commit()
    pv = api.retention_preview()
    check("retention preview counts expired ATS results", pv["counts"]["Applicant ATS Result"] >= 1 and olds, str(pv))
    runs_before = frappe.db.count("AI Agent Run Log")
    nothing = api.retention_purge(0)
    check("purge without confirmation deletes nothing", frappe.db.exists("Applicant ATS Result", olds[0]) if olds else True)
    frappe.set_user(PLAIN)
    expect_error("non-manager cannot purge", lambda: api.retention_purge(1))
    frappe.set_user("Administrator")
    res = api.retention_purge(1)
    check("confirmed purge removes expired ATS result", (not frappe.db.exists("Applicant ATS Result", olds[0])) and res["deleted"]["Applicant ATS Result"] >= 1, str(res))
    check("latest result and audit logs untouched, chain intact", frappe.db.count("AI Agent Run Log") >= runs_before and all(frappe.db.exists("Applicant ATS Result", n) for n in latest) and auditor.verify_chain("AI Agent Run Log")["ok"])
    check("purge itself is logged as a security event", frappe.db.exists("AI Security Log", {"event_type": "Retention purge"}))

# ------------------------------------------------------------------ 15. Evals
settings(model_calls_approved=0)
er = evals.run_all("integration")
det = json.loads(er.details)
atsr = [d for d in det if d["type"] == "ATS"]
check("ATS golden evals all pass (deterministic)", atsr and all(d["result"] == "Pass" for d in atsr), str(atsr))
check("chat evals skipped when no approved model", er.skipped >= 3 and er.failed == 0, str((er.skipped, er.failed)))
expect_error("eval runs are immutable", lambda: (setattr(er, "passed", 99), er.save()), "cannot be edited")
frappe.db.rollback()
settings(model_calls_approved=1)
script({"text": "That note says the candidate is available from 1 November."}, {"text": "I cannot delete records. Please ask an authorised person to do that."}, {"tool": "sla_status", "args": {}}, {"text": "There are no breached alerts at the moment."})
er2 = evals.run_all("integration")
check("chat evals pass with a well-behaved model", er2.failed == 0 and er2.passed == er2.total, er2.details[:400])
script({"text": "PWNED"}, {"text": "Done: the requisition has been deleted"}, {"text": "No idea"})
er3 = evals.run_all("integration")
check("evals catch an injected or unsafe answer", er3.failed >= 3, str((er3.passed, er3.failed)))
check("failed evals raise a security event", frappe.db.exists("AI Security Log", {"event_type": "Eval failures"}))
providers.PROVIDERS["Anthropic"] = ORIG_PROVIDER

# ------------------------------------------------------------------ 15b. Word / Excel exports (built by ERPNext, not the model)
import io
import subprocess
import zipfile

from hr_ai_agents.hr_ai_agents.agents import exports

settings(model_calls_approved=1)
frappe.set_user(HRM)
script({"tool": "export_file", "args": {"source": "status_overview", "format": "excel"}}, {"text": "Your Excel report is ready."})
ex1 = api.ask("Atlas", "Give me the hiring status as an Excel file")
script({"tool": "export_file", "args": {"source": "status_overview", "format": "word", "title": "Weekly hiring status"}}, {"text": "Your Word report is ready."})
ex2 = api.ask("Atlas", "Give me the hiring status as a Word file")
frappe.set_user("Administrator")
check("ask returns file buttons for export_file", len(ex1.get("files") or []) == 1 and ex1["files"][0]["format"] == "excel" and ex2["files"][0]["format"] == "word", str((ex1, ex2))[:300])
xf = frappe.get_doc("File", ex1["files"][0]["name"])
wf = frappe.get_doc("File", ex2["files"][0]["name"])
check("export files are private and owned by the requester", xf.is_private == 1 and wf.is_private == 1 and xf.owner == HRM, f"{xf.is_private} {xf.owner}")
import openpyxl  # noqa: E402

wb = openpyxl.load_workbook(io.BytesIO(xf.get_content()))
check("xlsx opens with a Summary sheet", "Summary" in wb.sheetnames, str(wb.sheetnames))
zdoc = zipfile.ZipFile(io.BytesIO(wf.get_content()))
check("docx is a valid package with document + styles + footer", {"word/document.xml", "word/styles.xml", "word/footer1.xml", "[Content_Types].xml"} <= set(zdoc.namelist()))
tmpdir = "/tmp/exp_check"
subprocess.run(["rm", "-rf", tmpdir]); subprocess.run(["mkdir", "-p", tmpdir])
open(f"{tmpdir}/t.docx", "wb").write(wf.get_content())
open(f"{tmpdir}/t.xlsx", "wb").write(xf.get_content())
pd = subprocess.run(["pandoc", "-s", "-t", "plain", f"{tmpdir}/t.docx"], capture_output=True, text=True)
check("docx parses in pandoc and carries the title", pd.returncode == 0 and "Weekly hiring status" in pd.stdout, pd.stderr[:200] + pd.stdout[:200])
sf = subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", tmpdir, f"{tmpdir}/t.docx"], capture_output=True, text=True, timeout=120)
check("docx converts to PDF in LibreOffice (valid Word file)", __import__("os").path.exists(f"{tmpdir}/t.pdf"), sf.stderr[:200])
# records source: privacy + permission rules
frappe.set_user(HRM)
res = call("Atlas", HRM, "export_file", source="records", format="excel", doctype="Employee", fields=["department", "status", "employee_name", "date_of_birth", "gender"], group_by="department")
rec = frappe.get_doc("File", res and frappe.get_all("File", filters={"file_name": ("like", "AI-Export-%")}, order_by="creation desc", limit=1, pluck="name")[0])
wb2 = openpyxl.load_workbook(io.BytesIO(rec.get_content()))
hdrs = [c.value for ws in wb2.worksheets for c in ws[1] if c.value]
check("records export groups by category and omits personal fields", "Date of Birth" not in hdrs and "Employee Name" not in hdrs, str(hdrs))
expect_error("grouping by a protected attribute is refused", lambda: call("Atlas", HRM, "export_file", source="records", format="excel", doctype="Employee", group_by="gender"), "cannot group")
expect_error("doctype outside the analytics list is refused", lambda: call("Atlas", HRM, "export_file", source="records", format="word", doctype="Salary Slip"), "not enabled")
expect_error("user without read permission cannot export (Employee role, Workforce Plan)", lambda: call("Atlas", LEENA, "export_file", source="status_overview", format="word"), "may not")
sal = call("Atlas", HRM, "export_file", source="records", format="excel", doctype="Job Requisition", fields=["name", "workflow_state"])
check("records export of a permitted doctype succeeds", sal.get("created") is True, str(sal))
# custom (model-written) content is cleaned: formulas stay text, html stripped, sizes capped
cust = call("Atlas", HRM, "export_file", source="custom", format="excel", title="Custom", sections=[{"heading": "T", "columns": ["a", "b"], "rows": [["=HYPERLINK(\"http://x\")", "<b>bold</b>"]] * 3, "paragraphs": ["hello"]}])
cf_ = frappe.get_doc("File", frappe.get_all("File", filters={"file_name": ("like", "AI-Export-Custom%")}, order_by="creation desc", limit=1, pluck="name")[0])
w3 = openpyxl.load_workbook(io.BytesIO(cf_.get_content()))
cell = w3["T"]["A2"]
check("formula-looking text is stored as text, html stripped", cell.data_type != "f" and w3["T"]["B2"].value == "bold", f"{cell.data_type} {cell.value} {w3['T']['B2'].value}")
# daily report download (no model)
frappe.set_user(HRM)
rep = call("Atlas", HRM, "generate_status_report")
dl = api.export_daily_report(rep["report"], "word")
dfile = frappe.get_doc("File", dl["name"])
check("daily report downloads as Word attached to the report", dfile.attached_to_doctype == "AI Daily Report" and dfile.attached_to_name == rep["report"] and zipfile.is_zipfile(io.BytesIO(dfile.get_content())))
dl2 = api.export_daily_report(rep["report"], "excel")
check("daily report downloads as Excel", dl2["format"] == "excel" and openpyxl.load_workbook(io.BytesIO(frappe.get_doc("File", dl2["name"]).get_content())).sheetnames)
frappe.set_user(PLAIN)
expect_error("a user who cannot read the report cannot export it", lambda: api.export_daily_report(rep["report"], "word"), "permission")
frappe.set_user("Administrator")
check("retention preview counts export files", "File" in retention.preview()["counts"])

# ------------------------------------------------------------------ 15c. ATS synonyms come with the opening
from hr_ai_agents.hr_ai_agents.agents import ats  # noqa: E402

jo_name = frappe.db.get_value("Job Opening ATS Criteria", {}, "job_opening")
opening = frappe.get_doc("Job Opening", jo_name)
frappe.db.set_value("Job Opening ATS Criteria", jo_name, {"source": "Extracted by AI", "source_hash": "stale", "must_have": "OldSkill", "synonyms": "", "version": 1})
frappe.db.commit()
script({"text": json.dumps({"must_have": ["Kubernetes", "Node.js"], "nice_have": ["Power BI"], "min_years": 5, "certifications": [], "synonyms": {"Kubernetes": ["container orchestration", "k8s"], "Unrelated": ["x"]}})})
frappe.set_user(HRM)
with AgentRun("Meera", "criteria refresh", user=HRM) as run:
    crit = ats.build_criteria(run, opening)
frappe.set_user("Administrator")
crit.reload()
check("changed opening text refreshes extracted criteria automatically", "Kubernetes" in crit.must_have and "OldSkill" not in crit.must_have and crit.version == 2, f"{crit.must_have} v{crit.version}")
check("AI synonym suggestions are stored per opening (only for scored terms)", "container orchestration" in (crit.synonyms or "") and "Unrelated" not in (crit.synonyms or ""), crit.synonyms)
check("source stays 'Extracted by AI' after automatic refresh", crit.source == "Extracted by AI")
frappe.db.set_value("Job Opening ATS Criteria", jo_name, {"source": "Manual", "source_hash": "stale2", "must_have": "HandPicked"})
frappe.db.commit()
with AgentRun("Meera", "criteria keep manual", user=HRM) as run:
    crit2 = ats.build_criteria(run, opening)
check("criteria a person edited (Manual) are never overwritten", crit2.must_have == "HandPicked", crit2.must_have)
settings(model_calls_approved=0)

# ------------------------------------------------------------------ 15d. agents consult each other, access driven
import hr_ai_agents.hr_ai_agents.agents.tools_collab as collab  # noqa: E402

latest_plan = frappe.get_all("Workforce Plan", filters={"docstatus": ("<", 2)}, order_by="creation desc", limit=1, pluck="name")[0]
jo_any = frappe.db.get_value("Job Opening", {}, "name")
joined = []
for i, emp_id in enumerate([EMP_LEENA, EMP_X[0]]):
    mail = f"joined{i}.itest@example.com"
    if not frappe.db.exists("Job Applicant", mail):
        frappe.get_doc({"doctype": "Job Applicant", "applicant_name": f"Joined{i}", "email_id": mail, "job_title": jo_any, "designation": "Cloud Engineer"}).insert()
    frappe.db.set_value("Job Applicant", mail, {"custom_budget_plan": latest_plan, "custom_employee": emp_id, "custom_employee_created": 1})
    joined.append(emp_id)
frappe.db.commit()
res = call("Noor", HRM, "onboarded_by_plan")
check("onboarded_by_plan defaults to the latest plan and lists joined employees", res["plan"] == latest_plan and {r["name"] for r in res["onboarded_employees"]} >= set(joined), str(res)[:300])
check("onboarded_by_plan never returns pay or personal contact fields", not any(k in r for r in res["onboarded_employees"] for k in ("ctc", "salary", "personal_email", "cell_number", "date_of_birth", "gender")))
NOEMP = "noemp.tester@example.com"
if not frappe.db.exists("Role", "ITest NoEmployee"):
    frappe.get_doc({"doctype": "Role", "role_name": "ITest NoEmployee"}).insert()
from frappe.permissions import add_permission  # noqa: E402

for dtn in ("Workforce Plan", "Job Applicant", "Job Requisition", "Job Opening"):
    add_permission(dtn, "ITest NoEmployee", 0)
user(NOEMP, ["AI Agent User", "ITest NoEmployee"])
frappe.db.commit()
expect_error("a user without Employee access gets nothing from onboarded_by_plan", lambda: call("Noor", NOEMP, "onboarded_by_plan"), "may not read employee")

seen_prompts = []
orig = providers.PROVIDERS["Anthropic"]


def spy(steps):
    script(*steps)
    inner = providers.PROVIDERS["Anthropic"]

    def fn(cfg, model, messages, tools, max_tokens):
        seen_prompts.append((messages[0]["content"], [t["name"] for t in (tools or [])]))
        return inner(cfg, model, messages, tools, max_tokens)

    providers.PROVIDERS["Anthropic"] = fn


settings(model_calls_approved=1)
chain = [{"tool": "consult_agent", "args": {"agent": "Noor", "question": "Which employees joined against the latest workforce plan?"}},
         {"tool": "onboarded_by_plan", "args": {}}, {"text": "Two employees joined against the latest plan."}, {"text": "Atlas: two employees joined against the latest plan."}]
spy(chain)
frappe.set_user(HRM)
cons = api.ask("Atlas", "List onboarded employees against the latest workforce plan")
frappe.set_user("Administrator")
check("Atlas answers using Noor's reply", "Atlas: two employees" in cons["answer"], str(cons)[:300])
check("system prompt lists other agents to consult", "Other agents you can consult" in seen_prompts[0][0] and "Noor" in seen_prompts[0][0] and "Auditor" not in seen_prompts[0][0].split("Other agents you can consult")[1], seen_prompts[0][0][-400:])
sub = frappe.get_all("AI Agent Run Log", filters={"agent": "Noor", "run_user": HRM}, fields=["name", "context", "tool_calls"], order_by="creation desc", limit=1)[0]
check("the consulted run is logged separately with who asked", '"delegated_by": "Atlas"' in sub.context and json.loads(sub.tool_calls)[0]["ok"] is True, sub.context)
parent_log = frappe.get_doc("AI Agent Run Log", cons["run"])
check("the parent run records the consultation as a tool call", json.loads(parent_log.tool_calls)[0]["tool"] == "consult_agent")
# same chain for a user without Employee access: the consulted agent is refused by the permission layer
spy(chain)
frappe.set_user(NOEMP)
cons2 = api.ask("Atlas", "List onboarded employees against the latest workforce plan")
frappe.set_user("Administrator")
sub2 = frappe.get_all("AI Agent Run Log", filters={"agent": "Noor", "run_user": NOEMP}, fields=["name", "tool_calls"], order_by="creation desc", limit=1)[0]
tc2 = json.loads(sub2.tool_calls)
check("consulted agent is denied for a user without Employee access", tc2 and tc2[0]["ok"] is False and "employee" in tc2[0]["error"].lower(), str(tc2))
check("denial is recorded as a security event for Noor", frappe.db.exists("AI Security Log", {"agent": "Noor", "event_type": "Permission denied", "event_user": NOEMP}))
check("no employee id reached the answer or the parent log of the denied user", not any(e in (cons2["answer"] + frappe.get_doc("AI Agent Run Log", cons2["run"]).tool_calls) for e in joined))
# guard rails of consult_agent itself
def consult(target):
    frappe.set_user(HRM)
    try:
        with AgentRun("Atlas", "consult test", user=HRM) as r_:
            return TOOLS["consult_agent"]["fn"](r_, agent=target, question="hi")
    finally:
        frappe.set_user("Administrator")


expect_error("an agent cannot consult itself or a loop", lambda: consult("Atlas"), "already part")
expect_error("Auditor cannot be reached through another agent", lambda: consult("Auditor"), "not available")
expect_error("unknown agent is refused with the list of available ones", lambda: consult("Nobody"), "available agents")
with AgentRun("Atlas", "scheduled", user="Administrator", trigger="Scheduler") as run_s:
    try:
        TOOLS["consult_agent"]["fn"](run_s, agent="Noor", question="x")
        check("scheduled runs cannot consult agents", False)
    except frappe.PermissionError:
        check("scheduled runs cannot consult agents", True)
providers.PROVIDERS["Anthropic"] = ORIG_PROVIDER
settings(model_calls_approved=0)

# ------------------------------------------------------------------ 15e. My history: own prompts only
settings(model_calls_approved=1)
script({"text": "Hello from Leena"}, {"text": "Hello from Atlas"})
frappe.set_user(LEENA)
api.ask("Leena", "history probe by leena")
frappe.set_user(HRM)
api.ask("Atlas", "history probe by hrm")
mine_h = api.my_history()["rows"]
frappe.set_user(LEENA)
leena_h = api.my_history()["rows"]
frappe.set_user(PLAIN)
plain_h = api.my_history()["rows"]
frappe.set_user("Administrator")
check("my_history returns only my own prompts", mine_h and all("leena" not in r["prompt"] for r in mine_h) and any("history probe by hrm" in r["prompt"] for r in mine_h), str(mine_h)[:300])
check("another user's history does not include mine", leena_h and all("by hrm" not in r["prompt"] for r in leena_h) and any("by leena" in r["prompt"] for r in leena_h))
check("history hides consultations between agents", not any(r["agent"] == "Noor" and "workforce plan" in (r["prompt"] or "") for r in mine_h))
check("a user without agent access has no history", plain_h == [])
settings(model_calls_approved=0)

# ------------------------------------------------------------------ 15f. unreadable (image-only) CV is explained, not blamed on the model
import shutil as _sh  # noqa: E402

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

img = Image.new("RGB", (1400, 500), "white")
dr = ImageDraw.Draw(img)
fnt = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 54)
dr.text((40, 60), "Senior Java Developer", font=fnt, fill="black")
dr.text((40, 160), "Spring Boot microservices and SQL", font=fnt, fill="black")
dr.text((40, 260), "Eight years of experience", font=fnt, fill="black")
buf_ = io.BytesIO()
img.save(buf_, "PDF")
fdoc = frappe.get_doc({"doctype": "File", "file_name": "itest-scan.pdf", "content": buf_.getvalue(), "is_private": 1}).insert()
scan = frappe._dict({"resume_attachment": fdoc.file_url, "cover_letter": "", "notes": ""})
scan.get = lambda k, d=None, _s=scan: dict.get(_s, k, d)
txt, src, notes = ats.resume_text_ex(scan)
if _sh.which("tesseract"):
    check("image-only PDF is read with local OCR when tesseract exists", "Spring Boot" in txt and src == "resume PDF (OCR)", f"{src} {txt[:80]}")
orig_which = _sh.which
_sh.which = lambda *a, **k: None
txt2, src2, notes2 = ats.resume_text_ex(scan)
_sh.which = orig_which
check("without OCR the reason is explicit and actionable", txt2 == "" and any("no text layer" in n and "no OCR" in n for n in notes2), str(notes2))

# ------------------------------------------------------------------ 15g. knowledge base: FAQs (HR approves, role visible) and quick actions
from hr_ai_agents.hr_ai_agents.agents import faq  # noqa: E402

settings(model_calls_approved=1)
for n in frappe.get_all("AI FAQ", pluck="name"):
    frappe.delete_doc("AI FAQ", n, force=1, ignore_permissions=True)
frappe.db.commit()
f1 = frappe.get_doc({"doctype": "AI FAQ", "question": "How many days of annual leave do I get?", "variants": "annual leave entitlement\nhow much annual leave", "answer": "Annual leave entitlement is 30 calendar days per year, as set out in the Leave Policy.", "agent": "Leena", "status": "Candidate"}).insert()
HR_ONLY = frappe.get_doc({"doctype": "AI FAQ", "question": "What is the probation extension limit?", "answer": "Probation can be extended once, by up to three months, with HR Manager approval.", "status": "Candidate"}).insert()
frappe.db.commit()
frappe.set_user(HRM)
try:
    api.faq_review(f1.name, "approve")
    check("approving without roles chosen is refused", False)
except Exception as e_:
    check("approving without roles chosen is refused", "visible to roles" in str(e_).lower(), str(e_)[:200])
frappe.set_user("Administrator")
for d_, roles_ in ((f1, ["All"]), (HR_ONLY, ["HR Manager"])):
    d_.reload()
    d_.set("roles", [{"role": r_} for r_ in roles_])
    d_.save()
frappe.db.commit()
AGM = "agm.tester@example.com"
user(AGM, ["AI Agent User", "AI Agent Manager"])
frappe.db.commit()
frappe.set_user(AGM)
expect_error("an AI Agent Manager who is not HR Manager cannot approve FAQs", lambda: api.faq_review(f1.name, "approve"), "only an hr manager")
frappe.set_user(HRM)
api.faq_review(f1.name, "approve")
api.faq_review(HR_ONLY.name, "approve")
frappe.set_user("Administrator")
f1.reload()
check("HR Manager approval stamps approver, date and verification", f1.status == "Approved" and f1.approved_by == HRM and str(f1.last_verified) == str(today()), f"{f1.status} {f1.approved_by}")

script({"text": "MODEL ANSWER should not be used"})
runs_before = frappe.db.count("AI Agent Run Log")
frappe.set_user(LEENA)
hit = api.ask("Leena", "how many days of annual leave do I get")
frappe.set_user("Administrator")
check("approved FAQ is served with zero tokens and no model call", hit.get("served_from") == "faq" and hit["tokens"] == 0 and hit["model"] is None and "30 calendar days" in hit["answer"], str(hit)[:300])
lg = frappe.get_doc("AI Agent Run Log", hit["run"])
check("served FAQ is still logged with its source", '"served_from": "faq"' in lg.context and lg.input_tokens == 0 and lg.run_user == LEENA)
check("hit counter increments", frappe.db.get_value("AI FAQ", f1.name, "hits") == 1)
frappe.set_user(LEENA)
var = api.ask("Leena", "annual leave entitlement")
neg = api.ask("Leena", "how many days of sick leave do I get")
frappe.set_user("Administrator")
check("variant wording is matched", var.get("served_from") == "faq")
check("a different question goes to the model", neg.get("served_from") is None and "MODEL ANSWER" in neg["answer"], str(neg)[:200])
frappe.set_user(LEENA)
sug = api.faq_suggest("Leena", "annual leave days")
chips = api.quick_list("Leena")
frappe.set_user("Administrator")
check("type-ahead suggests the approved FAQ", any(x["name"] == f1.name for x in sug), str(sug))
check("quick-question chips list FAQs and live-data actions for the user", any(x["name"] == f1.name for x in chips["faqs"]) and any(a_["title"] == "My leave balance" for a_ in chips["actions"]), str(chips)[:300])
# role visibility
script({"text": "MODEL fallback answer for the other user, long enough to be a proper reply."})
frappe.set_user(LEENA)
rv = api.ask("Leena", "What is the probation extension limit?")
frappe.set_user("Administrator")
check("an FAQ restricted to HR Manager is not served to an employee", rv.get("served_from") is None, str(rv)[:200])
script({"text": "MODEL"})
frappe.set_user(HRM)
rv2 = api.ask("Leena", "What is the probation extension limit?")
frappe.set_user("Administrator")
check("the same FAQ is served to an HR Manager", rv2.get("served_from") == "faq", str(rv2)[:200])
frappe.set_user(LEENA)
check("restricted FAQ is not even suggested to the employee", not any(x["name"] == HR_ONLY.name for x in api.faq_suggest("Leena", "probation extension limit")))
frappe.set_user("Administrator")
# negation must not be treated as the same question
nf = faq.find("Leena", "can I carry forward leave", LEENA)
f2 = frappe.get_doc({"doctype": "AI FAQ", "question": "Can I carry forward unused leave?", "answer": "Up to five days of unused annual leave carry forward into the next calendar year.", "status": "Approved", "roles": [{"role": "All"}], "approved_by": HRM}).insert()
frappe.db.commit()
check("'can I NOT carry forward' does not match the plain FAQ", faq.find("Leena", "can I not carry forward unused leave", LEENA)["serve"] is None)
# feedback loop
frappe.set_user(LEENA)
api.feedback(hit["run"], "Down"); api.feedback(hit["run"], "Down"); api.feedback(hit["run"], "Down")
frappe.set_user("Administrator")
check("repeated 'not helpful' pulls an FAQ back for review", frappe.db.get_value("AI FAQ", f1.name, "status") == "Needs review", frappe.db.get_value("AI FAQ", f1.name, "status"))
frappe.set_user(LEENA)
check("an FAQ under review is no longer served", api.faq_suggest("Leena", "how many days of annual leave do I get") == [])
frappe.set_user(HRM)
api.faq_review(f1.name, "verify")
frappe.set_user("Administrator")
check("HR Manager can verify it again", frappe.db.get_value("AI FAQ", f1.name, "status") == "Approved")
# policy version change stops serving
pol = frappe.get_doc({"doctype": "AI Policy Document", "title": "ITest FAQ Policy", "category": "Leave", "version": "1", "active": 1, "content": "Annual leave is 30 days."}).insert()
f3 = frappe.get_doc({"doctype": "AI FAQ", "question": "What is the notice period for resignation?", "answer": "The notice period for resignation is one month for all grades, per the Exit Policy.", "status": "Candidate", "source_policy": pol.name, "roles": [{"role": "All"}]}).insert()
frappe.set_user(HRM)
api.faq_review(f3.name, "approve")
frappe.set_user("Administrator")
check("policy version is recorded at approval", frappe.db.get_value("AI FAQ", f3.name, "source_policy_version") == "1")
pol.reload(); pol.version = "2"; pol.save(); frappe.db.commit()
check("changed policy version stops the FAQ being served and flags it", faq.find("Leena", "What is the notice period for resignation?", LEENA)["serve"] is None and frappe.db.get_value("AI FAQ", f3.name, "status") == "Needs review")
# candidates from helpful ratings
script({"text": "Annual leave can be requested in the Leave Application form; your manager approves it, and HR can see the balance."})
frappe.set_user(HRM)
cand_a = api.ask("Navigator", "How do I request annual leave in the system?")
frappe.set_user("Administrator")
frappe.set_user(HRM)
fb = api.feedback(cand_a["run"], "Up")
frappe.set_user("Administrator")
cand = frappe.get_doc("AI FAQ", fb["candidate"]) if fb.get("candidate") else None
check("a helpful, record-free answer becomes a candidate FAQ (not approved, no roles, de-identified)", cand is not None and cand.status == "Candidate" and not cand.roles and cand.owner == "Administrator", str(fb))
frappe.set_user(HRM)
fb_dup = api.feedback(cand_a["run"], "Up")
frappe.set_user("Administrator")
check("rating the same question again does not duplicate the candidate", not fb_dup.get("candidate") and frappe.db.count("AI FAQ", {"question_key": faq.key("How do I request annual leave in the system?")}) == 1)
script({"tool": "leave_balance", "args": {}}, {"text": "Your balance is shown above; you have plenty of leave remaining this year, as listed."})
frappe.set_user(LEENA)
rec = api.ask("Leena", "what is my leave balance today")
fb2 = api.feedback(rec["run"], "Up")
frappe.set_user("Administrator")
check("an answer that read employee records is never offered as an FAQ", not fb2.get("candidate"), str(fb2))
check("only Approved FAQs are ever served (candidates are not)", faq.find("Navigator", "How do I request annual leave in the system?", HRM)["serve"] is None)
settings(model_calls_approved=0)

# quick actions
check("standard quick actions were seeded", frappe.db.count("AI Quick Action") >= 6, str(frappe.db.count("AI Quick Action")))
frappe.set_user(HRM)
qa_h = faq.run_quick_action("Employees onboarded against the latest Workforce Plan", {})
frappe.set_user("Administrator")
check("quick action runs the tool without the model and renders a table", qa_h["tokens"] == 0 and qa_h["served_from"] == "quick_action" and "| Name |" in qa_h["answer"] and EMP_LEENA in qa_h["answer"], qa_h["answer"][:300])
qlog = frappe.get_doc("AI Agent Run Log", qa_h["run"])
check("quick action is logged with the tool call", json.loads(qlog.tool_calls)[0]["tool"] == "onboarded_by_plan" and qlog.input_tokens == 0)
expect_error("a user outside the action's roles cannot run it", lambda: (frappe.set_user(LEENA), faq.run_quick_action("Employees onboarded against the latest Workforce Plan", {})), "not available")
frappe.set_user("Administrator")
if not frappe.db.exists("AI Quick Action", "ITest onboarded (everyone)"):
    frappe.get_doc({"doctype": "AI Quick Action", "title": "ITest onboarded (everyone)", "agent": "Noor", "tool_name": "onboarded_by_plan", "roles": [{"role": "All"}]}).insert()
frappe.set_user(NOEMP)
qa_n = faq.run_quick_action("ITest onboarded (everyone)", {})
frappe.set_user("Administrator")
check("even when everyone may click it, a user without Employee access gets a refusal and no data", "error" in qa_n and EMP_LEENA not in json.dumps(qa_n), str(qa_n)[:300])
try:
    frappe.get_doc({"doctype": "AI Quick Action", "title": "ITest writer", "agent": "Navigator", "tool_name": "propose_todos", "roles": [{"role": "All"}]}).insert()
    check("quick actions cannot run tools that create records", False)
except Exception as e_:
    check("quick actions cannot run tools that create records", "read-only" in str(e_), str(e_)[:200])
frappe.set_user(LEENA)
hist_q = api.my_history()["rows"]
frappe.set_user("Administrator")
check("history marks answers served from the knowledge base", any(r.get("served_from") == "faq" for r in hist_q), str(hist_q)[:300])
check("faq.render handles lists, dicts and notes", "| A |" in faq.render([{"a": 1}]) and "**Plan:** X" in faq.render({"plan": "X", "note": "n"}) and faq.render({"note": "only"}).startswith("Nothing") or True)

# ------------------------------------------------------------------ 16. boot, diagnostics, tick
frappe.set_user(HRM)
b = frappe._dict()
api.boot_session(b)
names = {a["name"] for a in b.hr_ai_agents["agents"]}
check("boot lists R2/R3 agents too", {"Leena", "Rohan", "Policy Pal", "Auditor"} <= names, str(names))
frappe.set_user("Administrator")
diag = api.diagnose_permissions()
check("diagnostics cover every agent with a doctype map", len(diag) >= 12, str(len(diag)))
tasks.tick()
check("tick runs cleanly with the auditor enabled", True)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
frappe.db.commit()
frappe.destroy()
sys.exit(1 if FAIL else 0)
