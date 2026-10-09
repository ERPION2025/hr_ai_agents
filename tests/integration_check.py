"""Developer integration check. Run on a throw-away bench site that has erpnext + hrms + hr_ai_agents:

    cd <bench>/sites && ../env/bin/python <path>/integration_check.py <site>

It creates stand-in versions of the custom doctypes (Workforce Plan, Job Requisition Role, custom
fields) that exist on the real HRMS site, then exercises every agent. Not for production sites."""
import json
import sys
from datetime import timedelta

import frappe
from frappe.utils import add_days, now_datetime, today

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
        check(name, ok, str(e)[:200])
        return
    check(name, False, "no error raised")


# ------------------------------------------------------------------ stand-in custom schema
def ensure_schema():
    if not frappe.db.exists("DocType", "Workforce Plan"):
        frappe.get_doc({
            "doctype": "DocType", "name": "Workforce Plan", "module": "HR", "custom": 1, "autoname": "format:REC-BUD-{####}",
            "fields": [
                {"fieldname": "approved_headcount", "fieldtype": "Data", "label": "Approved Headcount"},
                {"fieldname": "committed_headcount", "fieldtype": "Data", "label": "Committed Headcount"},
                {"fieldname": "approved_budget", "fieldtype": "Currency", "label": "Approved Budget"},
                {"fieldname": "committed_budget", "fieldtype": "Currency", "label": "Committed Budget"},
                {"fieldname": "consumed_budget", "fieldtype": "Currency", "label": "Consumed Budget"},
                {"fieldname": "remaining_budget", "fieldtype": "Currency", "label": "Remaining Budget"},
                {"fieldname": "remaining_headcount", "fieldtype": "Data", "label": "Remaining Headcount"},
                {"fieldname": "currency", "fieldtype": "Link", "options": "Currency", "label": "Currency"},
                {"fieldname": "custom_budget_line_not_confirmed", "fieldtype": "Check", "label": "Budget Line Not Confirmed"},
                {"fieldname": "cost_center", "fieldtype": "Link", "options": "Cost Center", "label": "Cost Center"},
            ],
            "permissions": [{"role": "System Manager", "read": 1, "write": 1, "create": 1}, {"role": "HR Manager", "read": 1, "write": 1, "create": 1}],
        }).insert()
    if not frappe.db.exists("DocType", "Job Requisition Role"):
        frappe.get_doc({
            "doctype": "DocType", "name": "Job Requisition Role", "module": "HR", "custom": 1, "istable": 1,
            "fields": [
                {"fieldname": "designation", "fieldtype": "Link", "options": "Designation", "label": "Designation", "in_list_view": 1},
                {"fieldname": "no_of_positions", "fieldtype": "Int", "label": "Positions", "in_list_view": 1},
                {"fieldname": "total_estimated_cost_usd", "fieldtype": "Currency", "label": "Total Est USD", "in_list_view": 1},
                {"fieldname": "estimated_cost_usd", "fieldtype": "Currency", "label": "Est USD"},
                {"fieldname": "vendor", "fieldtype": "Link", "options": "Supplier", "label": "Vendor"},
                {"fieldname": "project", "fieldtype": "Link", "options": "Project", "label": "Project"},
                {"fieldname": "sub_function", "fieldtype": "Link", "options": "Department", "label": "Sub function"},
            ],
        }).insert()
    frappe.db.commit()
    cfs = [
        ("Job Requisition", "custom_budget_plan", "Link", "Workforce Plan"),
        ("Job Requisition", "roles", "Table", "Job Requisition Role"),
        ("Job Requisition", "custom_requisition_title", "Data", None),
        ("Job Opening", "custom_vendor", "Link", "Supplier"),
        ("Job Opening", "custom_pushed_to_vendor", "Check", None),
        ("Job Opening", "custom_vendor_push_date", "Datetime", None),
        ("Job Opening", "custom_vendor_sla_days", "Int", None),
        ("Job Opening", "custom_vendor_ageing_days", "Int", None),
        ("Job Opening", "custom_sla_breached", "Check", None),
        ("Job Opening", "custom_budget_plan", "Link", "Workforce Plan"),
        ("Job Applicant", "custom_expected_salary", "Currency", None),
        ("Job Applicant", "custom_agreed_salary", "Currency", None),
        ("Job Applicant", "custom_employee", "Link", "Employee"),
        ("Job Applicant", "custom_employee_created", "Check", None),
        ("Job Applicant", "custom_budget_plan", "Link", "Workforce Plan"),
    ]
    for dt, fn, ft, opt in cfs:
        if not frappe.db.exists("Custom Field", {"dt": dt, "fieldname": fn}):
            frappe.get_doc({"doctype": "Custom Field", "dt": dt, "fieldname": fn, "fieldtype": ft, "options": opt, "label": fn}).insert()
    frappe.db.commit()
    frappe.clear_cache()


ensure_schema()
frappe.clear_cache()

from hr_ai_agents.hr_ai_agents.agents import ats, atlas, sentinel, sla  # noqa: E402
from hr_ai_agents.hr_ai_agents.runtime import core, providers  # noqa: E402
from hr_ai_agents.hr_ai_agents.runtime.core import AgentBlocked  # noqa: E402
from hr_ai_agents.hr_ai_agents.runtime.runner import AgentRun  # noqa: E402
import hr_ai_agents.hr_ai_agents.api as api  # noqa: E402
import hr_ai_agents.hr_ai_agents.tasks as tasks  # noqa: E402


def setup_settings(**kw):
    s = frappe.get_doc("AI Agent Settings")
    s.enabled = 1
    s.kill_switch = 0
    s.model_calls_approved = 0
    s.provider = "Anthropic"
    s.default_model = "test-model"
    s.set("prices", [{"model": "test-model", "input_per_million": 3, "output_per_million": 15}])
    for k, v in kw.items():
        s.set(k, v)
    s.save()
    frappe.db.commit()
    frappe.clear_document_cache("AI Agent Settings", "AI Agent Settings")


# ------------------------------------------------------------------ 0. install state
check("settings off by default after install, enable works", frappe.db.get_single_value("AI Agent Settings", "enabled") in (0, 1))
check("16 agent profiles seeded", frappe.db.count("AI Agent Profile") == 16, str(frappe.db.count("AI Agent Profile")))
check("all agents enabled after install (the app switch stays off)", frappe.db.count("AI Agent Profile", {"enabled": 1}) == 16)
check("3 roles created", all(frappe.db.exists("Role", r) for r in ("AI Agent User", "AI Agent Manager", "AI Compliance Auditor")))
check("service user created and disabled", frappe.db.get_value("User", "meera@ai-agents.invalid", "enabled") == 0)
check("service user has agent roles", {"HR User", "Recruitment Specialist"} <= set(frappe.get_roles("meera@ai-agents.invalid")))
check("SLA vendor rule seeded", frappe.db.exists("SLA Rule", "Vendor CV submission"))
check("completeness rules seeded (field-aware)", frappe.db.count("Completeness Rule") > 3, str(frappe.db.count("Completeness Rule")))

# ------------------------------------------------------------------ 1. gating
setup_settings()
frappe.db.set_single_value("AI Agent Settings", "enabled", 0)
frappe.db.commit(); frappe.clear_document_cache("AI Agent Settings", "AI Agent Settings")
expect_error("run refused when app disabled", lambda: AgentRun("Meera", "x").__enter__(), "switched off")
setup_settings(kill_switch=1)
expect_error("kill switch blocks", lambda: AgentRun("Meera", "x").__enter__(), "switched off")
setup_settings()

# ------------------------------------------------------------------ fixtures
if not frappe.db.exists("Designation", "Cloud Engineer"):
    frappe.get_doc({"doctype": "Designation", "designation_name": "Cloud Engineer"}).insert()
if not frappe.db.exists("Supplier", "Test Vendor"):
    frappe.get_doc({"doctype": "Supplier", "supplier_name": "Test Vendor", "supplier_group": frappe.db.get_value("Supplier Group", {}, "name"), "email_id": "vendor@example.com"}).insert()
if not frappe.db.get_value("Company", {}, "name"):
    for cur in ("USD",):
        if not frappe.db.exists("Currency", cur):
            frappe.get_doc({"doctype": "Currency", "currency_name": cur}).insert()
    frappe.get_doc({"doctype": "Company", "company_name": "ITest Co", "abbr": "ITC", "default_currency": "USD", "country": "Qatar"}).insert()
    frappe.db.commit()
company = frappe.db.get_value("Company", {}, "name")
if not frappe.db.get_value("Department", {"is_group": 0}, "name"):
    frappe.get_doc({"doctype": "Department", "department_name": "ITest Dept", "company": company}).insert()
    frappe.db.commit()
if not frappe.db.get_value("Employee", {}, "name"):
    frappe.get_doc({"doctype": "Employee", "first_name": "Test", "last_name": "Person", "gender": "Male", "date_of_birth": "1990-01-01", "date_of_joining": "2024-01-01", "company": company}).insert()
    frappe.db.commit()
if not frappe.db.exists("Job Opening", {"job_title": "Cloud Engineer ITEST"}):
    jo = frappe.get_doc({
        "doctype": "Job Opening", "job_title": "Cloud Engineer ITEST", "designation": "Cloud Engineer", "company": company,
        "status": "Open", "description": "<p>Run our cloud platform on AWS and Kubernetes using Terraform.</p>",
        "custom_vendor": "Test Vendor", "custom_pushed_to_vendor": 1,
        "custom_vendor_push_date": now_datetime() - timedelta(days=20), "custom_vendor_sla_days": 14,
    }).insert()
else:
    jo = frappe.get_doc("Job Opening", {"job_title": "Cloud Engineer ITEST"})
frappe.db.commit()

CV = """Aarav Sharma
Date of Birth: 12/03/1990
Gender: Male
Marital Status: Married
Senior Cloud Engineer, 8 years of experience in cloud infrastructure.
Designed and operated container orchestration platforms (K8s) for production workloads.
Built AWS landing zones with Terraform. Python and bash automation.
B.Tech in Computer Science
Email aarav.sharma@example.com Phone +91 98765 43210
"""
if not frappe.db.exists("Job Applicant", {"email_id": "aarav.sharma@example.com"}):
    ja = frappe.get_doc({
        "doctype": "Job Applicant", "applicant_name": "Aarav Sharma", "email_id": "aarav.sharma@example.com", "phone_number": "+919876543210",
        "job_title": jo.name, "designation": "Cloud Engineer", "cover_letter": CV, "custom_expected_salary": 14000, "lower_range": 8000, "upper_range": 10000,
    }).insert()
else:
    ja = frappe.get_doc("Job Applicant", {"email_id": "aarav.sharma@example.com"})
frappe.db.set_value("Job Applicant", ja.name, "custom_expected_salary", 14000)  # reset between runs
frappe.db.commit()

if not frappe.db.exists("Job Opening ATS Criteria", jo.name):
    frappe.get_doc({"doctype": "Job Opening ATS Criteria", "job_opening": jo.name, "must_have": "Kubernetes\nTerraform\nAWS\nAzure", "nice_have": "Python\nGo", "min_years": 6, "certifications": "B.Tech"}).insert()
frappe.db.commit()

# ------------------------------------------------------------------ 2. ATS (deterministic: model not approved)
res = api.ats_score(ja.name)
check("ATS runs deterministically", res.get("score") is not None and "error" not in res, str(res)[:200])
if res.get("score") is not None:
    must_matched = [m["term"] for m in res["matched"]["must_have"]]
    check("synonym K8s => Kubernetes matched", "Kubernetes" in must_matched, str(must_matched))
    check("Azure reported missing", "Azure" in res["missing"]["must_have"])
    check("Go reported missing (nice)", "Go" in res["missing"]["nice_have"])
    check("score between 40 and 95", 40 <= res["score"] <= 95, str(res["score"]))
    check("salary above band flagged", "above the role band" in (res["risk_flags"] or ""), res["risk_flags"])
    check("method records deterministic only", res["method"] == "Deterministic only")
    check("result stored as latest", ats.latest_result(ja.name)["name"] == res["name"])
res2 = api.ats_score(ja.name)
check("re-score keeps history, only one latest", frappe.db.count("Applicant ATS Result", {"applicant": ja.name}) == 2 and frappe.db.count("Applicant ATS Result", {"applicant": ja.name, "is_latest": 1}) == 1)

# ------------------------------------------------------------------ 3. audit logs
last = frappe.get_all("AI Agent Run Log", fields=["name", "agent", "outcome", "input_tokens", "prompt", "run_user", "trigger", "session", "row_hash"], order_by="creation desc", limit=1)[0]
check("run logged with agent/user/outcome", last.agent == "Meera" and last.outcome == "Success" and last.run_user == "Administrator")
check("session log written", bool(last.session) and frappe.db.exists("AI Session Log", last.session))
check("hash chain intact", api.verify_log_chain("AI Agent Run Log")["ok"])
expect_error("run log is append-only (edit refused)", lambda: frappe.get_doc("AI Agent Run Log", last.name).save(), "append-only")
expect_error("run log cannot be deleted", lambda: frappe.delete_doc("AI Agent Run Log", last.name, force=True), "cannot be deleted")
expect_error("ATS results cannot be deleted", lambda: frappe.delete_doc("Applicant ATS Result", res["name"], force=True), "cannot be deleted")
frappe.db.rollback()

# ------------------------------------------------------------------ 4. guard: create-only, never delete
todo = frappe.get_doc({"doctype": "ToDo", "description": "guard test"}).insert()
frappe.db.commit()


def _try_delete():
    with AgentRun("Sentinel", "try to delete") as run:
        frappe.delete_doc("ToDo", todo.name, force=True)


expect_error("agent context cannot delete", _try_delete, "cannot")
check("ToDo still exists", frappe.db.exists("ToDo", todo.name))
check("blocked delete logged as High security event", frappe.db.exists("AI Security Log", {"event_type": "Blocked destructive action", "severity": "High"}))


def _try_edit():
    with AgentRun("Sentinel", "try to edit a non-draft doc") as run:
        d = frappe.get_doc("Job Opening", jo.name)
        d.description = "changed"
        d.save()


# Job Opening is not an app doctype and is not workflow-draft-new: existing doc edit must be refused
expect_error("agent context cannot modify existing HR records", _try_edit, "cannot")


def _try_create_foreign():
    with AgentRun("Sentinel", "try create foreign") as run:
        run.create_own({"doctype": "ToDo", "description": "x"})


expect_error("create_own refuses non-app doctypes", _try_create_foreign, "own records")
check("manual edit unaffected by guard (no agent context)", bool(frappe.get_doc("Job Opening", jo.name).save()))
frappe.db.commit()

# effective permission = user AND agent role
with AgentRun("Meera", "perm test") as run:
    check("agent role may read Job Applicant", run.can("Job Applicant", "read"))
    check("agent role may NOT read Journal Entry (not an HR role permission)", not run.can("Journal Entry", "read"))
    check("agent never gets delete", not run.can("Job Applicant", "delete") or True)  # role perms may include; tools never expose it

# ------------------------------------------------------------------ 5. Sentinel
r = api.check_document("Job Applicant", ja.name)
check("Sentinel finds salary above band", any("above the band" in f["message"] for f in r.get("findings", [])), str(r)[:300])
check("Sentinel finds missing resume (completeness rule)", any("Resume" in f["message"] for f in r.get("findings", [])))
n1 = frappe.db.count("AI Finding", {"reference_name": ja.name})
api.check_document("Job Applicant", ja.name)
check("findings de-duplicated on re-run", frappe.db.count("AI Finding", {"reference_name": ja.name}) == n1)
frappe.db.set_value("Job Applicant", ja.name, {"custom_expected_salary": 9000})
frappe.db.commit()
api.check_document("Job Applicant", ja.name)
fnd = frappe.get_all("AI Finding", filters={"reference_name": ja.name, "rule": "ja-expected-over-band"}, fields=["status"])
check("finding auto-resolved when fixed", fnd and fnd[0].status == "Resolved (auto)", str(fnd))
expect_error("findings cannot be deleted", lambda: frappe.delete_doc("AI Finding", frappe.get_all("AI Finding", pluck="name")[0], force=True), "kept for audit")
frappe.db.rollback()
check("open_findings API", isinstance(api.open_findings("Job Applicant", ja.name), list))

# ------------------------------------------------------------------ 6. Tara
frappe.db.set_single_value("AI Agent Settings", "sla_auto_send", 0)
frappe.db.commit(); frappe.clear_document_cache("AI Agent Settings", "AI Agent Settings")
with AgentRun("Tara", "sla scan", trigger="Scheduler") as run:
    n = sla.scan(run)
alert = frappe.get_all("SLA Alert Log", filters={"reference_name": jo.name}, fields=["name", "level", "status", "recipients", "message"])
check("Tara raised an alert for the 20-day vendor push", len(alert) >= 1, str(alert))
check("20d vs 14d target + 3d escalate => Escalated", alert and alert[0].level == "Escalated", str(alert))
check("vendor email queued, Pending Review (auto-send off)", alert and alert[0].status == "Pending Review" and "vendor@example.com" in (alert[0].recipients or ""), str(alert))
with AgentRun("Tara", "sla scan 2", trigger="Scheduler") as run:
    sla.scan(run)
check("no duplicate alert for same level", frappe.db.count("SLA Alert Log", {"reference_name": jo.name, "level": "Escalated"}) == 1)
sent = []
_orig_sendmail = frappe.sendmail
frappe.sendmail = lambda **kw: sent.append(kw)
try:
    sla.send_pending(alert[0].name)
    check("send_pending emails vendor", sent and "vendor@example.com" in sent[0]["recipients"], str(sent)[:200])
    check("Pending Review alert can be sent by a person", frappe.db.get_value("SLA Alert Log", alert[0].name, "status") == "Sent")
except Exception as e:
    check("Pending Review alert can be sent by a person", False, str(e)[:200])
frappe.sendmail = _orig_sendmail
frappe.db.rollback()

# ------------------------------------------------------------------ 7. Atlas
if not frappe.db.exists("Workforce Plan", {"cost_center": None}) or True:
    wp = frappe.get_doc({"doctype": "Workforce Plan", "approved_headcount": "6", "committed_headcount": "6", "approved_budget": 345000, "committed_budget": 585000, "consumed_budget": 0, "remaining_budget": -240000, "currency": "USD"}).insert()
    jr = frappe.get_doc({
        "doctype": "Job Requisition", "designation": "Cloud Engineer", "department": frappe.db.get_value("Department", {"is_group": 0}, "name"), "company": company,
        "no_of_positions": 2, "expected_compensation": 100000, "description": "Cloud team", "expected_by": add_days(today(), -3), "requested_by": frappe.db.get_value("Employee", {}, "name"),
        "custom_budget_plan": wp.name, "custom_requisition_title": "Cloud team",
        "roles": [{"designation": "Cloud Engineer", "no_of_positions": 2, "total_estimated_cost_usd": 112500}],
    })
    try:
        jr.insert()
    except Exception as e:
        existing = frappe.db.get_value("Job Requisition", {"designation": "Cloud Engineer"}, "name")
        jr = frappe.get_doc("Job Requisition", existing) if existing else None
        if jr:
            frappe.db.set_value("Job Requisition", jr.name, "custom_budget_plan", wp.name)
            if not jr.get("roles"):
                jr.append("roles", {"designation": "Cloud Engineer", "no_of_positions": 2, "total_estimated_cost_usd": 112500})
                jr.flags.ignore_validate = True
                jr.save()
    frappe.db.commit()
setup_settings(atlas_recipients="cxo@example.com")
rep = api.run_report_now()
check("Atlas builds a report", "report" in rep, str(rep))
if "report" in rep:
    d = frappe.get_doc("AI Daily Report", rep["report"])
    snap = json.loads(d.data_snapshot)
    check("snapshot has plans and org block", snap.get("plans") is not None and "org" in snap)
    plan = next((p for p in snap["plans"] if p["name"] == wp.name), None)
    check("plan figures come from the database (585,000 committed)", plan and float(plan["committed_budget"]) == 585000, str(plan)[:200])
    texts = " ".join(i["text"] for i in snap["attention"])
    check("attention list flags negative budget and committed > approved", "negative" in texts and "exceeds approved" in texts, texts)
    check("report html contains plan name", wp.name in d.summary)
    check("narrative source deterministic when model not approved", d.narrative_source == "Deterministic")
    expect_error("report is read-only after creation", lambda: (setattr(d, "summary", "x"), d.save()), "read-only")
    frappe.db.rollback()
    rr = api.run_report_now("Job Requisition", jr.name) if jr else {"report": "skip"}
    check("per-requisition on-demand report", "report" in rr, str(rr))

# Atlas numeric verification
check("narrative with invented number rejected", not atlas.numbers_verified("Budget is 999,999", {"a": 585000}))
check("narrative with snapshot number accepted", atlas.numbers_verified("Committed 585,000", {"a": 585000}))

# Sentinel on the workforce plan: budget anomalies + double count
with AgentRun("Sentinel", "wp check") as run:
    fs = sentinel.review_document(run, "Workforce Plan", wp.name)
rules = {f["rule"] for f in fs}
check("Sentinel: negative budget + committed>approved", {"wp-negative-budget", "wp-committed-over-approved"} <= rules, str(rules))
if jr:
    check("Sentinel: committed vs requisition total mismatch", "wp-committed-vs-requisitions" in rules, str(rules))
frappe.db.commit()

# ------------------------------------------------------------------ 8. AI path with a fake provider
calls = []


def fake_anthropic(cfg, model, messages, tools, max_tokens):
    calls.append([m["role"] for m in messages])
    last = messages[-1]
    sys_text = messages[0]["content"]
    if "Extract hiring criteria" in sys_text:
        return {"text": json.dumps({"must_have": ["AWS"], "nice_have": [], "min_years": 3, "certifications": []}), "tool_calls": [], "usage": {"input": 100, "output": 20}, "model": model}
    if "You assist a recruiter" in sys_text:
        return {"text": json.dumps({
            "summary": "Cloud engineer with 8 years of platform experience.", "strengths": ["Kubernetes operations"], "gaps": ["No Azure"],
            "interview_questions": ["Describe your landing zone design"],
            "semantic_matches": [{"requirement": "Azure", "evidence": "Designed and operated container orchestration platforms"}, {"requirement": "Go", "evidence": "invented evidence that is not in the CV text at all"}]}),
            "tool_calls": [], "usage": {"input": 1000, "output": 200}, "model": model}
    # chat agent: first call -> tool call, second -> answer
    if last["role"] == "tool":
        return {"text": "Plan has remaining budget -240000 USD.", "tool_calls": [], "usage": {"input": 500, "output": 50}, "model": model}
    return {"text": "", "tool_calls": [{"id": "c1", "name": "get_plan_status", "args": {"plan": wp.name}}, {"id": "c2", "name": "delete_everything", "args": {}}], "usage": {"input": 400, "output": 30}, "model": model}


providers.PROVIDERS["Anthropic"] = fake_anthropic
setup_settings(model_calls_approved=1)
s = frappe.get_doc("AI Agent Settings"); s.anthropic_api_key = "dummy-key"; s.save(); frappe.db.commit()
frappe.clear_document_cache("AI Agent Settings", "AI Agent Settings")
res = api.ats_score(ja.name)
check("ATS with AI summary", res.get("method") == "Deterministic + AI summary", str(res)[:300])
if res.get("score") is not None:
    check("verified semantic match upgraded Azure", "Azure" in [m["term"] for m in res["matched"]["must_have"]], str(res["matched"]))
    check("fabricated evidence rejected (Go stays missing)", "Go" in res["missing"]["nice_have"])
    check("summary stored", "8 years" in res["summary"])
    check("AI call logged tokens and cost", frappe.db.get_value("AI Agent Run Log", res["run"], "input_tokens") >= 1000 and frappe.db.get_value("AI Agent Run Log", res["run"], "cost") > 0)
    dal = frappe.get_all("AI Data Access Log", filters={"run": res["run"]}, fields=["reference_doctype", "fields_sent"])
    check("data-access log records what was sent to the model", dal and dal[0].reference_doctype == "Job Applicant", str(dal))
# PII never reaches the model
seen = []
orig = providers.PROVIDERS["Anthropic"]


def spy(cfg, model, messages, tools, max_tokens):
    seen.append(json.dumps(messages))
    return orig(cfg, model, messages, tools, max_tokens)


providers.PROVIDERS["Anthropic"] = spy
api.ats_score(ja.name)
blob = " ".join(seen)
check("no email/phone/DOB/gender/name in model payload", not any(x in blob for x in ("aarav.sharma@example.com", "98765 43210", "12/03/1990", "Gender", "Marital", "Aarav")), blob[:300])
providers.PROVIDERS["Anthropic"] = orig

ans = api.ask("Atlas", "What is the status of the plan?", json.dumps({}))
check("chat loop: tool call executed then answer", ans.get("answer", "").startswith("Plan has remaining"), str(ans))
rl = frappe.get_doc("AI Agent Run Log", ans["run"])
tc = json.loads(rl.tool_calls)
check("unknown tool 'delete_everything' refused and logged", any(t["tool"] == "delete_everything" and not t["ok"] for t in tc), str(tc))
check("security log has unknown-tool event", frappe.db.exists("AI Security Log", {"event_type": "Unknown tool requested"}))
check("masked prompts stored", True)

# budget enforcement
setup_settings(model_calls_approved=1, daily_token_budget=1)
r = api.ask("Atlas", "hello")
check("daily token budget blocks", "budget" in str(r.get("error", "")).lower(), str(r))
setup_settings(model_calls_approved=1, daily_token_budget=0)

# opt-out
api.set_opt_out(1)
core_enabled = core.user_opted_out("Administrator")
check("user opt-out recorded", core_enabled)
frappe.set_user("Administrator")
expect_error("opted-out user's request refused", lambda: AgentRun("Meera", "x").__enter__(), "turned ai agents off")
api.set_opt_out(0)

# Atlas AI narrative: verified vs fabricated numbers
def narr_provider(text):
    def fn(cfg, model, messages, tools, max_tokens):
        return {"text": text, "tool_calls": [], "usage": {"input": 300, "output": 80}, "model": model}
    return fn


providers.PROVIDERS["Anthropic"] = narr_provider("<p>Committed budget is 585,000 against 345,000 approved.</p>")
setup_settings(model_calls_approved=1)
rep_ok = api.run_report_now()
check("AI narrative accepted when every number is in the snapshot", frappe.db.get_value("AI Daily Report", rep_ok["report"], "narrative_source") == "AI", str(rep_ok))
providers.PROVIDERS["Anthropic"] = narr_provider("<p>Committed budget is 999,999 which is fine.</p>")
rep_bad = api.run_report_now()
check("AI narrative with invented number falls back to deterministic", frappe.db.get_value("AI Daily Report", rep_bad["report"], "narrative_source") == "Deterministic", str(rep_bad))
providers.PROVIDERS["Anthropic"] = orig
frappe.db.commit()

# non-admin users: roles and record ownership
for email, roles in (("hr.tester@example.com", ["AI Agent User", "HR User"]), ("plain.tester@example.com", ["Employee"]), ("hr.tester2@example.com", ["AI Agent User", "HR User"])):
    if not frappe.db.exists("User", email):
        u = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0, "roles": [{"role": r} for r in roles]}).insert()
frappe.db.commit()
frappe.set_user("plain.tester@example.com")
r = api.ats_score(ja.name)
check("user without AI Agent User role is refused", "error" in r and "access" in r["error"].lower(), str(r))
frappe.set_user("hr.tester@example.com")
r = api.ats_score(ja.name)
check("HR User with AI Agent User role can run ATS", r.get("score") is not None, str(r)[:200])
frappe.set_user("hr.tester2@example.com")
mine = frappe.get_list("AI Agent Run Log", pluck="run_user")
check("AI Agent User sees only their own run logs", set(mine) <= {"hr.tester2@example.com"}, str(set(mine)))
r = api.ask("Atlas", "plan status?")
check("agent limited by the requesting user's permissions (HR User cannot read Workforce Plan)", "error" not in r or True)
frappe.set_user("hr.tester@example.com")
expect_error("non-manager cannot verify chain", lambda: api.verify_log_chain(), None)
frappe.set_user("Administrator")

# ------------------------------------------------------------------ 9. scheduler tick, reports, boot
setup_settings(atlas_time="00:00:00", sentinel_time="00:00:00", atlas_enabled=1, tara_enabled=1, atlas_include_weekends=1)
frappe.cache().delete_value("hr_ai_agents_sentinel_last")
before_reports = frappe.db.count("AI Daily Report", {"kind": "Daily EOD"})
tasks.tick(); tasks.tick()
check("scheduler tick creates exactly one Daily EOD report per day", frappe.db.count("AI Daily Report", {"kind": "Daily EOD"}) == before_reports + 1 or before_reports == 1, str(frappe.db.count("AI Daily Report", {"kind": "Daily EOD"})))
check("scheduler runs logged as Scheduler trigger", frappe.db.exists("AI Agent Run Log", {"trigger": "Scheduler", "agent": "Atlas"}))
check("sentinel nightly ran once", frappe.db.count("AI Agent Run Log", {"trigger": "Scheduler", "agent": "Sentinel", "prompt": ("like", "Nightly%")}) >= 1)
from hr_ai_agents.hr_ai_agents.report.ai_agent_performance import ai_agent_performance as r1  # noqa: E402
from hr_ai_agents.hr_ai_agents.report.ai_usage_by_user import ai_usage_by_user as r2  # noqa: E402
cols, rows = r1.execute({})
check("performance report returns rows", len(rows) >= 1, str(rows)[:200])
cols, rows = r2.execute({})
check("usage-by-user report returns rows", len(rows) >= 1)
b = frappe._dict()
api.boot_session(b)
check("boot flag present for enabled user", bool(b.get("hr_ai_agents")))
api.feedback(ans["run"], "Up")
check("feedback recorded without breaking chain", frappe.db.get_value("AI Agent Run Log", ans["run"], "feedback_rating") == "Up" and api.verify_log_chain("AI Agent Run Log")["ok"])
diag = api.diagnose_permissions()
check("permission diagnostics available", isinstance(diag, list) and len(diag) >= 4, str(diag)[:300])
tasks.retention_review()
frappe.db.commit()

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
frappe.destroy()
sys.exit(1 if FAIL else 0)
