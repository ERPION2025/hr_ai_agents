import json

import frappe

AI_ROLES = ["AI Agent User", "AI Agent Manager", "AI Compliance Auditor"]
# roles referenced by DocType permissions; created only if the site lacks them
REFERENCED_ROLES = ["HR Manager", "HR User", "Recruitment Specialist", "CAO", "CFO", "CEO / Final Approver", "System Manager"]

# name, title, release, role, purpose, can_do, tools, lawful_basis, data
# agent -> additional roles so the out-of-the-box permission union covers the doctypes it reads
EXTRA_ROLES = {
    "Meera": ["HR User"],
    "Tara": ["HR User", "HR Manager"],
    "Atlas": ["HR User", "HR Manager"],
    "Sentinel": ["HR User", "HR Manager"],
    "Asha": ["HR User"],
    "Rohan": ["HR User", "Recruitment Specialist"],
    "Vikram": ["HR User"],
    "Karim": ["HR User"],
    "Noor": ["HR Manager"],
    "Dev": ["HR User"],
    "Leena": ["HR User"],
    "Samir": ["HR User"],
    "Navigator": ["HR User"],
    "Policy Pal": [],
    "Insight": ["HR Manager"],
    "Auditor": ["HR Manager", "System Manager"],
}

AGENTS = [
    ("Meera", "Talent Screener", "R1", "Recruitment Specialist", "Advisory ATS match of CVs against the Job Opening.", "Score a CV, list matched/missing keywords, summarise work profile, flag duplicates and salary-band issues. Compare candidates, find stuck candidates, summarise interview feedback, prepare a draft Job Offer.", ["ats_score", "ats_result", "compare_candidates", "stuck_candidates", "interview_feedback_summary", "propose_job_offer"], "Employment", "CV content (name, contact and protected attributes removed before any model call)"),
    ("Tara", "SLA Reminder", "R1", "HR User", "Watches SLAs and raises reminders and escalations.", "Compute SLA clocks, notify owners, queue or send reminders, escalate overdue items.", ["sla_status"], "Employment", "Record references and owner emails"),
    ("Atlas", "System Manager Reporter", "R1", "System Manager", "Daily EOD and on-demand status reports per Workforce Plan and Requisition.", "Read plan, requisition, opening and applicant counts and budget figures; write report records.", ["list_workforce_plans", "get_plan_status", "get_requisition_status", "generate_status_report", "list_findings", "sla_status", "export_file"], "Employment", "Aggregated hiring and budget figures"),
    ("Sentinel", "Quality and Anomaly Reviewer", "R1", "System Manager", "Finds missing important fields and anomalies in HR documents.", "Read documents, raise findings with impact and suggested fix.", ["check_document", "list_findings"], "Employment", "HR document fields"),
    ("Asha", "Workforce Planner", "R2", "HR Manager", "Plan drafting and cost benchmarking.", "Pre-check a Workforce Plan, benchmark cost per head from your own history, and prepare a draft Workforce Plan for review.", ["plan_precheck", "cost_benchmark", "propose_workforce_plan", "list_workforce_plans", "get_plan_status"], "Employment", "Workforce plan figures; internal cost history"),
    ("Rohan", "Requisition Builder", "R2", "HR Manager", "Requisition and JD drafting.", "Draft job-description text per role, check requisition cost against the plan budget, and prepare a draft Job Requisition from a Workforce Plan.", ["requisition_roles", "propose_role_text", "cost_check", "propose_requisition_from_plan", "get_requisition_status"], "Employment", "Requisition roles and costs"),
    ("Vikram", "Vendor Desk", "R2", "Recruitment Specialist", "Vendor dispatch and scorecard.", "Vendor scorecard, vendor ranking for a new opening, and a drafted vendor brief (never sent by the agent).", ["vendor_scorecard", "recommend_vendor_allocation", "opening_facts", "draft_vendor_brief", "sla_status"], "Employment", "Job Opening and vendor delivery records"),
    ("Karim", "CXO Briefer", "R2", "HR Manager", "CXO approval batches and briefs.", "Brief an approver on a document, list CXO approvals in progress with consistency issues, show what is waiting for the user.", ["approver_brief", "cxo_batch", "my_pending_approvals"], "Employment", "Approval documents"),
    ("Noor", "Onboarding Concierge", "R2", "HR User", "Onboarding checklists and completeness.", "Check an employee record for missing items, list employees onboarded against a Workforce Plan, show onboarding progress, and prepare reviewed updates to reporting line, department, designation and similar fields.", ["onboarding_status", "onboarded_by_plan", "propose_employee_update"], "Employment", "Employee master fields"),
    ("Dev", "Review Assistant", "R2", "HR Manager", "Probation and review drafts.", "Draft manager or HR feedback and goal evaluations for a probation review, show review status and upcoming deadlines. The decision is never proposed.", ["probation_context", "propose_probation_draft", "hr_review_brief", "probation_deadlines"], "Employment", "Probation review records"),
    ("Leena", "Leave Assistant", "R2", "Employee", "Leave form filling and allocation proposals.", "Show leave balances, check team overlap, and prepare a draft Leave Application (or allocation, for HR).", ["leave_balance", "team_leave_overlap", "propose_leave_application", "propose_leave_allocation"], "Employment", "Leave balances and applications"),
    ("Samir", "Exit Coordinator", "R2", "HR Manager", "Exit orchestration and insights.", "Show exit progress, propose missing offboarding activities, report exit patterns (small groups hidden), and prepare a draft replacement requisition.", ["exit_checklist", "propose_exit_activities", "exit_insights", "propose_replacement_requisition"], "Employment", "Employee Exit records"),
    ("Navigator", "Workflow Guide", "R2", "Employee", "Explains available workflow actions and blocks.", "Explain what a document is waiting for and why an action is blocked, list approvals waiting for you, and turn action items into draft To-Dos.", ["available_actions", "my_pending_approvals", "propose_todos"], "Employment", "Workflow state and roles"),
    ("Policy Pal", "Policy Assistant", "R2", "Employee", "HR policy Q&A.", "Answers questions only from the active policy documents in AI Policy Document, with citations.", ["policy_search"], "Employment", "Policy documents (no personal data)"),
    ("Insight", "HR Analytics Assistant", "R2", "HR User", "Aggregate HR metrics on request.", "Counts and averages by category over enabled doctypes. Groups under 3 are hidden; personal identifiers and protected attributes cannot be used.", ["hr_metrics"], "Employment", "Aggregated HR figures"),
    ("Auditor", "Compliance Monitor", "R3", "AI Compliance Auditor", "Reviews agent logs for policy breaches and builds the monthly governance pack.", "Daily scan of agent logs for out-of-role requests, repeated denials, blocked destructive attempts, high volume, after-hours use, cost spikes and broken log chains; data-subject inventory.", ["audit_observations", "run_audit_scan", "verify_log_chains", "list_audit_findings", "dsr_inventory"], "Legal obligation", "Agent logs and access records"),
]

# tools added to an existing profile on upgrade (never removes anything a manager did)
ADDITIVE_TOOLS = {
    "Meera": ["compare_candidates", "stuck_candidates", "interview_feedback_summary", "propose_job_offer"],
    "Atlas": ["export_file"],
}
# (marker already present in the prompt -> skip, text appended otherwise); lists so one agent can get several additions
ADDITIVE_PROMPT = {
    "Meera": [("propose_", " compare_candidates, stuck_candidates and interview_feedback_summary give supporting facts." + "{PROPOSE_RULE}")],
    "Atlas": [("export_file", " " + "When the user asks for a report, list or table as a file, call export_file (format word or excel) with the matching source; ERPNext builds the file and a download button appears under your answer. Prefer a data source over writing the content yourself; use source custom only for text you wrote in this chat. Never paste the whole table into the chat when a file was requested.")],
    "Noor": [("onboarded_by_plan", " onboarded_by_plan lists the employees who joined against a Workforce Plan (the latest one by default); it only returns what the user may read.")],
}
ADDITIVE_TOOLS["Noor"] = ["onboarded_by_plan"]

PROPOSE_RULE = (
    " You never change records yourself. To suggest a change or a new draft, call the matching propose_* tool; then tell the user a proposal "
    "was created and that they must open it (AI Proposal) and click Apply after reviewing the changes. If a proposal fails validation, fix the "
    "input and retry once, otherwise explain what is missing. Never invent figures, names or dates; ask the user when something is missing."
)
SYSTEM_PROMPTS = {
    "Meera": "You help recruiters understand how well a candidate matches a job opening. Use the ats_score / ats_result tools. Report score, matched and missing must-have keywords, and risks. Scores are advisory; never recommend rejecting a candidate. compare_candidates, stuck_candidates and interview_feedback_summary give supporting facts." + PROPOSE_RULE,
    "Tara": "You report on SLA status using sla_status. Summarise breaches by level and what is overdue. You cannot send messages yourself in chat.",
    "Atlas": "You are the system manager's reporting assistant. Use the status tools for plans and requisitions; quote figures exactly as returned. Offer to generate a saved report with generate_status_report. When the user asks for a report, list or table as a file, call export_file (format word or excel) with the matching source; ERPNext builds the file and a download button appears under your answer. Prefer a data source over writing the content yourself; use source custom only for text you wrote in this chat. Never paste the whole table into the chat when a file was requested.",
    "Sentinel": "You review documents for missing important fields and anomalies using check_document and list_findings. Explain the impact of each finding in plain language.",
    "Asha": "You help HR plan headcount and budget. Use plan_precheck before suggesting a plan, cost_benchmark for cost per head from internal history (say clearly it is internal, not market data) and propose_workforce_plan to prepare a draft." + PROPOSE_RULE,
    "Rohan": "You help build job requisitions. Use requisition_roles to see each role, write clear job-description text in the role's own terms (responsibilities, skills, experience, education), then call propose_role_text. Use cost_check before suggesting a requisition and propose_requisition_from_plan to create a draft. Use inclusive, non-discriminatory language; never mention age, gender, nationality or religion." + PROPOSE_RULE,
    "Vikram": "You support the recruitment vendor desk. Use vendor_scorecard and recommend_vendor_allocation (state the sample size and that the ranking is advisory). Use opening_facts then draft_vendor_brief to draft a vendor email; you cannot send it." + PROPOSE_RULE,
    "Karim": "You prepare short briefs for approvers. Call approver_brief and write: what is being approved, the key figures, open issues, and what the approver can do next. Quote figures exactly. You cannot approve, reject or route anything." + PROPOSE_RULE,
    "Noor": "You help HR complete employee records and onboarding. Use onboarding_status; use onboarded_by_plan to list who joined against a Workforce Plan (latest by default); propose changes only with propose_employee_update." + PROPOSE_RULE,
    "Dev": "You help managers and HR prepare probation reviews. Call probation_context, then draft balanced, factual feedback tied to the goal evaluations on record; call propose_probation_draft. Ratings are suggestions. You never propose or imply the final decision (confirm, extend or terminate); that belongs to people." + PROPOSE_RULE,
    "Leena": "You help employees with leave. Check leave_balance and team_leave_overlap, then prepare the draft with propose_leave_application. Do not approve or promise approval." + PROPOSE_RULE,
    "Samir": "You help HR coordinate employee exits. Use exit_checklist to see what is outstanding and propose_exit_activities for missing steps. Use exit_insights for patterns (small groups are hidden by design; do not try to work around that). Use propose_replacement_requisition only when HR asks for a replacement." + PROPOSE_RULE,
    "Navigator": "You explain workflows. Use available_actions to say which action the user can take and, if blocked, exactly why (role, creator rule or condition). Use my_pending_approvals for what is waiting on the user. You cannot perform any workflow action; the user does it. For action items the user provides, call propose_todos." + PROPOSE_RULE,
    "Policy Pal": "You answer HR policy questions. Call policy_search and answer ONLY from the passages returned, citing policy title, version and section. If nothing relevant is returned, say the policy library does not cover it and suggest contacting HR. Never answer policy questions from general knowledge.",
    "Insight": "You provide aggregate HR figures with hr_metrics. State the filters and the group sizes; mention if groups were hidden for small size. You cannot list individuals or use personal identifiers or protected attributes; if asked, decline and explain it protects privacy.",
    "Auditor": "You are the compliance reviewer of the AI agents themselves. Use audit_observations, run_audit_scan, verify_log_chains and list_audit_findings. Report facts from the logs without speculating about intent; recommend human follow-up.",
}

# agents can consult each other (tools_collab.consult_agent); the other agent only returns what the requesting user may read
COLLAB_RULE = (
    " If the answer needs facts another agent owns (for example employees for Noor, requisitions for Rohan), call consult_agent with a complete question, "
    "then combine the replies. Another agent only returns what the current user is allowed to see; if it reports a permission problem, tell the user plainly."
)
COLLAB_AGENTS = [a[0] for a in AGENTS if a[0] not in ("Auditor", "Policy Pal", "Insight")]
for _a in AGENTS:
    if _a[0] in COLLAB_AGENTS:
        if "consult_agent" not in _a[6]:
            _a[6].append("consult_agent")
        SYSTEM_PROMPTS[_a[0]] = SYSTEM_PROMPTS.get(_a[0], "") + COLLAB_RULE
        ADDITIVE_TOOLS.setdefault(_a[0], []).append("consult_agent")
        ADDITIVE_PROMPT.setdefault(_a[0], []).append(("consult_agent", COLLAB_RULE))

# doctype, field, label, severity, impact
COMPLETENESS = [
    ("Employee", "payroll_cost_center", "Cost Center", "Medium", "Cost reporting and payroll posting need a cost center."),
    ("Employee", "reports_to", "Reports To", "Medium", "Approvals (leave, probation, exit) route through the reporting manager."),
    ("Employee", "department", "Department", "Medium", "Headcount and analytics are reported by department."),
    ("Employee", "designation", "Designation", "Medium", "Salary band and role reporting depend on designation."),
    ("Job Requisition Role", "vendor", "Vendor", "Medium", "Without a vendor the opening cannot be pushed or its SLA tracked."),
    ("Job Requisition Role", "project", "Project", "Medium", "Project cost allocation and budget tracking."),
    ("Job Requisition Role", "sub_function", "Sub-function", "Low", "Function-level reporting and approvals."),
    ("Job Requisition Role", "location", "Location", "Medium", "Needed on the Job Opening and for candidate matching."),
    ("Job Requisition Role", "hiring_manager", "Hiring Manager", "Medium", "Interview and approval routing."),
    ("Job Requisition Role", "budgeted", "Budgeted?", "Medium", "Budget variance reporting."),
    ("Workforce Plan", "cost_center", "Cost Center", "Medium", "Budget consumption is posted against the cost center."),
    ("Workforce Plan", "custom_project", "Project", "Low", "Project-level budget tracking."),
    ("Workforce Plan Position", "hiring_manager", "Hiring Manager", "Low", "Requisition routing."),
    ("Job Applicant", "resume_attachment", "Resume", "Medium", "ATS matching and shortlisting need the CV."),
    ("Job Applicant", "custom_expected_salary", "Expected salary", "Medium", "Salary-band check and CXO approval need it."),
    ("Job Applicant", "phone_number", "Phone number", "Low", "Recruiter contact."),
    ("Job Opening", "custom_budget_plan", "Workforce Plan", "Medium", "Budget consumption and reporting link through the plan."),
    ("Job Opening", "job_requisition", "Job Requisition", "Medium", "Traceability to the approved requisition."),
    ("CXO Hiring Approval", "workforce_plan_summary", "Workforce plan summary", "Medium", "Approvers rely on the summary for budget impact."),
    ("Probation Review", "reviewer", "Reviewer", "Medium", "The review cannot progress without a reviewer."),
]

# name, doctype, filters, mode, start, due, target, target_field, warn, esc, roles, fields, vendor, title, enabled
SLA_RULES = [
    ("Vendor CV submission", "Job Opening", {"custom_pushed_to_vendor": 1, "status": "Open"}, "Elapsed since start", "custom_vendor_push_date", "", 14, "custom_vendor_sla_days", 80, 3, "Recruitment Specialist\nHR User", "owner", "custom_vendor", "job_title", 1),
    ("Probation review due", "Probation Review", {"workflow_state": ["in", ["Draft", "Manager Review", "HR Review"]]}, "Due date field", "", "probation_end_date", 14, "", 80, 3, "HR Manager", "owner", "", "employee_name", 0),
    ("Leave approval ageing", "Leave Application", {"workflow_state": ["in", ["Pending Manager Approval", "Pending HR Approval"]]}, "Elapsed since start", "", "", 3, "", 80, 2, "HR User", "leave_approver", "", "employee_name", 0),
]

SYNONYMS = [
    ("kubernetes", "k8s"), ("javascript", "js, ecmascript"), ("typescript", "ts"), ("amazon web services", "aws"),
    ("google cloud platform", "gcp, google cloud"), ("microsoft azure", "azure"), ("continuous integration", "ci/cd, ci cd, cicd"),
    ("business analysis", "business analyst, ba"), ("machine learning", "ml"), ("structured query language", "sql"),
    ("project management", "pmp, project manager"), ("infrastructure as code", "iac, terraform"),
]


def before_install():
    for r in AI_ROLES + REFERENCED_ROLES:
        if not frappe.db.exists("Role", r):
            frappe.get_doc({"doctype": "Role", "role_name": r, "desk_access": 1}).insert(ignore_permissions=True)


def after_install():
    # settings: everything off until a manager switches it on
    s = frappe.get_doc("AI Agent Settings")
    s.enabled = 0
    s.model_calls_approved = 0
    s.provider = "Azure OpenAI"
    for term, aliases in SYNONYMS:
        if not any(r.term == term for r in s.get("ats_synonyms") or []):
            s.append("ats_synonyms", {"term": term, "aliases": aliases})
    s.sentinel_doctypes = "\n".join([
        "Workforce Plan", "Job Requisition", "Job Opening", "Job Applicant", "CXO Hiring Approval",
        "Employee", "Probation Review", "Leave Application", "Employee Exit",
    ])
    s.flags.ignore_permissions = True
    s.save()

    ensure_roster()
    from hr_ai_agents.hr_ai_agents.agents import evals

    evals.seed()
    ensure_quick_actions()

    for dt, field, label, sev, impact in COMPLETENESS:
        if not frappe.db.exists("DocType", dt) or not frappe.get_meta(dt).has_field(field):
            continue  # this site does not have that doctype/field: skip the rule
        if not frappe.db.exists("Completeness Rule", {"reference_doctype": dt, "field_name": field}):
            frappe.get_doc({"doctype": "Completeness Rule", "reference_doctype": dt, "field_name": field, "label": label, "severity": sev, "impact": impact, "enabled": 1}).insert(ignore_permissions=True)

    for name, dt, filters, mode, start, due, target, tfield, warn, esc, roles, fields, vendor, title, enabled in SLA_RULES:
        if frappe.db.exists("SLA Rule", name) or not frappe.db.exists("DocType", dt):
            continue
        frappe.get_doc({
            "doctype": "SLA Rule", "rule_name": name, "enabled": enabled, "reference_doctype": dt, "filters_json": json.dumps(filters),
            "clock_mode": mode, "start_field": start, "due_field": due, "target_days": target, "target_days_field": tfield,
            "warn_at_pct": warn, "escalate_after_days": esc, "notify_roles": roles, "notify_fields": fields,
            "vendor_email_field": vendor, "title_field": title,
        }).insert(ignore_permissions=True)
    frappe.db.commit()


# live-data recipes: read-only tools run as the asking user, shown as tables, no model. (agent, title, tool, arguments, parameters, roles, hint)
QUICK_ACTIONS = [
    ("Noor", "Employees onboarded against the latest Workforce Plan", "onboarded_by_plan", {}, [{"name": "plan", "label": "Workforce Plan (leave blank for the latest)", "fieldtype": "Link", "options": "Workforce Plan"}], ["HR User", "HR Manager", "System Manager"], "Needs access to Employee records."),
    ("Navigator", "What is waiting for my approval?", "my_pending_approvals", {}, [], ["All"], "Only items you are allowed to act on."),
    ("Leena", "My leave balance", "leave_balance", {}, [], ["All"], "Your own balances, as of today."),
    ("Atlas", "Latest Workforce Plans: headcount and budget", "list_workforce_plans", {"limit": 10}, [], ["System Manager", "HR Manager"], ""),
    ("Tara", "Open SLA alerts", "sla_status", {}, [], ["HR User", "HR Manager", "System Manager"], ""),
    ("Sentinel", "Open high-severity findings", "list_findings", {"severity": "High"}, [], ["System Manager", "HR Manager"], ""),
    ("Dev", "Probation reviews due soon", "probation_deadlines", {"days": 30}, [{"name": "days", "label": "Within how many days", "fieldtype": "Int"}], ["HR User", "HR Manager"], ""),
    ("Meera", "Candidates stuck in the pipeline", "stuck_candidates", {"days": 7}, [{"name": "days", "label": "Stuck for at least (days)", "fieldtype": "Int"}], ["Recruitment Specialist", "HR User", "HR Manager"], ""),
]


def ensure_quick_actions():
    """Insert the standard quick actions that are missing (never touches ones a manager edited or removed from the list)."""
    from hr_ai_agents.hr_ai_agents.runtime import registry

    registry._load()
    for agent, title, tool, args, params, roles, hint in QUICK_ACTIONS:
        if frappe.db.exists("AI Quick Action", title) or tool not in registry.TOOLS or not frappe.db.exists("AI Agent Profile", agent):
            continue
        frappe.get_doc({
            "doctype": "AI Quick Action", "title": title, "agent": agent, "enabled": 1, "tool_name": tool, "arguments": json.dumps(args),
            "parameters": json.dumps(params), "description": hint, "roles": [{"role": r} for r in roles if frappe.db.exists("Role", r)],
        }).insert(ignore_permissions=True)


def ensure_roster(upgrade=False):
    """Create missing agent profiles. On upgrade, fill in profiles that are still the empty placeholders
    from an earlier release (no tools, no prompt) and add new tools to Meera; never overwrite a manager's edits."""
    for name, title, rel, role, purpose, can_do, tools, basis, data in AGENTS:
        extra = [{"role": r} for r in EXTRA_ROLES.get(name, []) if frappe.db.exists("Role", r)]
        if not frappe.db.exists("AI Agent Profile", name):
            frappe.get_doc({
                "doctype": "AI Agent Profile", "agent_name": name, "title": title, "release": rel, "role": role,
                "purpose": purpose, "can_do": can_do, "lawful_basis": basis, "data_categories": data,
                "enabled": 1, "autonomy": "Assist", "system_prompt": SYSTEM_PROMPTS.get(name, ""),
                "allowed_tools": [{"tool_name": t} for t in tools], "additional_roles": extra,
            }).insert(ignore_permissions=True)
            continue
        if not upgrade:
            continue
        p = frappe.get_doc("AI Agent Profile", name)
        changed = False
        if not p.get("allowed_tools") and not (p.system_prompt or "").strip():
            p.purpose, p.can_do, p.system_prompt, p.release = purpose, can_do, SYSTEM_PROMPTS.get(name, ""), rel
            p.data_categories, p.lawful_basis = data, basis
            for t in tools:
                p.append("allowed_tools", {"tool_name": t})
            for r in extra:
                if not any(x.role == r["role"] for x in p.get("additional_roles") or []):
                    p.append("additional_roles", r)
            p.enabled = 1
            changed = True
        elif name in ADDITIVE_TOOLS:
            have = {t.tool_name for t in p.get("allowed_tools") or []}
            for t in ADDITIVE_TOOLS[name]:
                if t not in have:
                    p.append("allowed_tools", {"tool_name": t})
                    changed = True
            for marker, text in ADDITIVE_PROMPT.get(name, []):
                if marker not in (p.system_prompt or ""):
                    p.system_prompt = (p.system_prompt or "") + text.replace("{PROPOSE_RULE}", PROPOSE_RULE)
                    changed = True
        if changed:
            p.flags.ignore_permissions = True
            p.save()
