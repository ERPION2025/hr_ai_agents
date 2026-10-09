# HR AI Agents (`hr_ai_agents`)

Optional, role-based AI agents for the Vodafone HRMS hiring-to-exit flow, built as a **separate Frappe app**. It reads the doctypes you already have and adds its own. It does not change any existing doctype, workflow, script or permission.

**Optional by design**
- Everything is off after install. A manager switches the app on in *AI Agent Settings*.
- Only users with the role **AI Agent User** see the "Ask AI" button and the AI buttons on forms. Everyone else sees an unchanged desk.
- Each user can switch agents off for themselves ("Turn AI off for me").
- No workflow step ever waits for an agent. Uninstalling the app leaves the manual process exactly as it was.

## What is in the app (version 0.3.2: Releases 1, 2 and 3, exports, knowledge base)

All agents are on by default once the app switch is on; a manager can disable any of them in *AI Agent Profile*. Roles shown are the starting roles and are editable.

| Agent | Starting role | What it does |
|---|---|---|
| **Meera** Talent Screener | Recruitment Specialist + HR User | ATS resume match on Job Applicant (score 0-100, matched/missing keywords with evidence, work-profile summary, risk flags, interview questions); compare candidates; stuck candidates; interview-feedback summary; prepares a draft Job Offer. Advisory only. |
| **Tara** SLA Reminder | HR User + HR Manager | Hourly SLA scan from *SLA Rule* records; in-app alerts, email (incl. vendors) sent automatically or queued for one-click review; escalation ladder. |
| **Atlas** System Manager Reporter | System Manager + HR roles | Daily EOD report at 22:00 Asia/Qatar per Workforce Plan and Job Requisition, emailed as PDF; on-demand reports. Every figure is a database query saved as the report's snapshot. **Downloads (0.3.0):** ask Atlas for a report, list or count-by-group as Word or Excel; ERPNext builds the file (the model only picks the source), and a Download button appears under the answer. Saved reports also have Download > Word / Excel buttons (no model call). |
| **Sentinel** Quality and Anomaly Reviewer | System Manager + HR roles | "Check completeness" on documents and a nightly scan: missing fields and anomalies with impact and suggested fix. |
| **Asha** Workforce Planner | HR Manager + HR User | Plan pre-check, cost-per-head benchmark from your own history, prepares a draft Workforce Plan. |
| **Rohan** Requisition Builder | HR Manager + HR User + Recruitment Specialist | Drafts job-description text per role, checks requisition cost against plan budget, prepares a draft Job Requisition from a plan. |
| **Vikram** Vendor Desk | Recruitment Specialist + HR User | Vendor scorecard and ranking for a new opening; drafts a vendor brief (never sends it). |
| **Karim** CXO Briefer | HR Manager + HR User | Brief for an approver on any approval document; CXO approvals in progress with consistency issues; what is waiting for you. |
| **Noor** Onboarding Concierge | HR User + HR Manager | Employee completeness and onboarding progress; reviewed updates to reporting line, department, designation, cost centre, branch, grade, employment type. |
| **Dev** Review Assistant | HR Manager + HR User | Drafts manager/HR feedback and goal evaluations for a probation review, HR review brief, upcoming deadlines. Never proposes the decision. |
| **Leena** Leave Assistant | Employee + HR User | Leave balance, team overlap, prepares a draft Leave Application (or a draft allocation for HR). |
| **Samir** Exit Coordinator | HR Manager + HR User | Exit checklist, proposes missing offboarding activities, exit statistics (groups under 3 hidden), draft replacement requisition. |
| **Navigator** Workflow Guide | Employee + HR User | Explains a document's workflow state, which action you can take and exactly why one is blocked; approvals waiting for you; turns action items into draft To-Dos. |
| **Policy Pal** Policy Assistant | Employee | Answers only from active *AI Policy Document* records with citations; works without any model (shows the closest passages). |
| **Insight** HR Analytics Assistant | HR User + HR Manager | Aggregate counts/averages by category over enabled doctypes; groups under 3 hidden; no personal identifiers or protected attributes; pay/cost sums need HR Manager. |
| **Auditor** Compliance Monitor | AI Compliance Auditor + HR Manager + System Manager | Daily scan of the agent logs (out-of-role requests, repeated denials, blocked destructive attempts, high volume, after-hours use, cost spikes, broken log chains) raising *AI Finding* records; monthly *AI Compliance Pack*; data-subject inventory. |

### How agents change anything: the proposal layer

Agents never edit HR records. A `propose_*` tool creates an **AI Proposal** (a reviewable diff, or draft text). The person opens it (from the answer, the "AI proposals" button, or a banner on the record), reviews the changes, and clicks **Apply**. Apply runs as that person through the normal save path, so their permissions, validation scripts and workflow apply. Proposals only create Drafts or edit Drafts; workflow state, docstatus, decisions, approvals and naming can never be proposed; the record is re-checked and refused if it changed after the proposal was made; proposals expire (7 days default) and cannot be edited or deleted. Before a proposal is even offered, the document's own validation is run in a rolled-back dry run, so a proposal that would fail is fixed first.

### Release 3 governance features

- **Auditor**: daily at 03:30 (Qatar) and "Run audit scan now"; findings de-duplicate per day. The same scan verifies the hash chain of the run, security and data-access logs.
- **Monthly compliance pack** (1st of the month): runs, users, cost, security events, findings, proposals, requests, chain integrity, agent roster and last evaluation, as an immutable record.
- **Data subject requests**: *AI Data Subject Request* + "Build data inventory" lists every record type that holds the person's data (linked documents, ATS results, agent data-access rows). It deletes and changes nothing.
- **Retention**: weekly flag (existing); *Retention: preview and purge* in AI Agent Settings deletes only expired ATS results, reports and finished proposals after a person confirms; every purge is logged. Audit logs are never purged by the app.
- **Evaluations**: *AI Eval Case* (seeded: ATS strong/weak/synonym/prompt-injection, delete refusal, injected instruction, tool use) and *AI Eval Run*. ATS cases are deterministic and need no model; chat cases need an approved model. Run on demand or weekly.

### Word / Excel downloads (0.3.0)

Atlas has an `export_file` tool. Sources: `status_overview` (plans, requisitions, pending items, attention list; or one plan/requisition), `job_openings`, `sla_alerts`, `records` (list or count-by-group of one document type) and `custom` (text the model wrote in the chat). Files are written by `agents/exports.py` (a small built-in .docx writer; .xlsx via openpyxl) and stored as **private** Files owned by the requesting user, named `AI-Export-...`. Data sources run with the user's own permissions; personal identifiers and protected attributes are never exported, pay/cost fields need HR Manager, small groups (under 3) are hidden in group-by tables, and document types must be on the analytics list in AI Agent Settings. Export files are covered by the retention purge. The Ask AI popup is now extra-large.

### ATS synonyms (0.3.0)

Meera no longer depends on the synonym table in settings. At every score she builds synonyms from the Job Opening: built-in groups for common HR/IT/finance terms, spelling variants of each requirement (Node.js / nodejs / node js, "(SQL)" in brackets, "A / B", "&" and "and"), and aliases the model suggested when the criteria were extracted (stored on that opening's *Job Opening ATS Criteria*, editable). If the opening or its requisition role text changes, criteria that were extracted automatically are refreshed; criteria a person edited (Source = Manual) are never overwritten. The settings table remains as an optional extra for company jargon. A matched requirement still needs its alias to appear in the CV, and the result shows which alias matched.

### Agents consulting each other, access driven (0.3.0)

Every agent except Auditor, Policy Pal and Insight has a `consult_agent` tool and sees a short list of the other agents. When a request needs facts another agent owns (for example Atlas asking Noor which employees joined against the latest Workforce Plan, via Noor's new `onboarded_by_plan` tool), it asks that agent and combines the replies. The consulted agent runs **as the same user** with its own tool allowlist and role limits, so the answer contains only what that user may read: without Employee access, Noor's tool is refused and no employee data is returned or passed on. Each consultation is its own run log (context records which agent asked); limits: only while a person is asking (never scheduled runs), at most three agents in a chain, no loops, four consultations per request, Auditor unreachable.

### Knowledge base: FAQs and quick actions (0.3.2)
Saves tokens without any model training. **AI FAQ** holds approved question/answer pairs per agent. When a user asks a question in Ask AI, the app first looks for an approved FAQ by exact, order-insensitive wording and then by keyword overlap (weighted, negations such as "not" are respected, so "can I not carry forward" does not match the plain FAQ). A match is answered straight from the database with **0 tokens** and labelled "From the knowledge base... Ask the AI anyway". Only keyword matching is used; there are no embeddings.

- **Role-based visibility.** Each FAQ lists the roles that may see it (child table; role `All` means everyone). FAQs a user's roles do not cover are neither served nor suggested.
- **HR Manager approves.** Only HR Manager or System Manager can set an FAQ to Approved (buttons: Approve, Verify and re-approve, Reject, Mark verified today, Retire). Approval stamps approver, date and the policy version.
- **Staleness.** If the linked policy version changes, or users rate an FAQ "not helpful" repeatedly, it moves to *Needs review* and stops being served until re-verified.
- **Candidates.** A thumbs-up on an AI answer that read no employee records (no record IDs or personal data, 40-6000 characters) creates a de-identified *Candidate* FAQ with no roles. Candidates are never served; HR reviews and approves them.
- **Quick actions (AI Quick Action).** One-click chips in Ask AI that run a **read-only** tool live as the asking user, with no model (for example "My leave balance", "Open SLA alerts", "Employees onboarded against the latest Workforce Plan"). Tools that create records cannot be used. Permissions are those of the user, so a user without Employee access gets a refusal, not data. Results are rendered as tables and logged (tokens 0). Eight standard actions are seeded on install.
- Popups for findings and permission checks now show tables (Severity, Finding, Why it matters, Suggested action).

### My history

The Ask AI popup has a **My history** button listing the signed-in user's own requests and answers (never anyone else's; consultations between agents are hidden). Text is shown as stored, so emails, phone numbers and IDs may be masked.

### ATS panel and unreadable CVs

The AI Resume Match panel is laid out in sections (score ring, must/nice-to-have matched vs missing, work profile, strengths and gaps, interview questions, flags, score bars). If the CV has no readable text (for example a PDF that is only a scanned image), the panel says so at the top and why, the summary says that nothing was sent to the model, and the score is greyed as not meaningful. If the server has the `tesseract` program, image-only PDFs are read with local OCR before masking; otherwise attach a text PDF/DOCX or paste the CV into Cover Letter / Notes.

## Install

Requirements: Frappe/ERPNext v15 with HRMS v15, Python 3.10+.

**Frappe Cloud:** push this folder to a Git repository, then *Apps > Add App > from GitHub*, add it to the bench group, and install it on the site. **Self-hosted:** `bench get-app <repo-url> && bench --site <site> install-app hr_ai_agents`.

Installing creates the roles *AI Agent User*, *AI Agent Manager*, *AI Compliance Auditor*, the settings, the agent roster, ATS synonyms, completeness rules, SLA rules, evaluation cases and an **AI Governance** workspace. Seeded rules skip any doctype/field your site does not have. **Upgrading from 0.1.x:** `bench migrate` runs a patch that fills in the empty Release 2/3 placeholder profiles adds Meera's new tools and Atlas's export tool; it never overwrites edits a manager made to a profile.

## First-run checklist (about 10 minutes)

1. Give yourself the **AI Agent Manager** role. Give pilot users **AI Agent User**.
2. Open **AI Agent Settings**:
   - Provider: *Azure OpenAI* (production target) with endpoint, API version, key and your **deployment name** as the default model; or *Anthropic* with a key for testing (if the key is not workspace-scoped, also fill *Anthropic workspace ID*).
   - Fill the **price table** (so cost shows in logs and reports) and set token budgets.
   - Atlas: time (default 22:00), timezone (default Asia/Qatar), recipients.
   - Retention: audit logs default 12 months, content 6 months; the minimum accepted is 6.
   - Tick **Model data transfer approved by DPO** only when the DPO has approved the provider/region. Until then no model is called; ATS keyword matching, SLA clocks and report figures still work.
   - Tick **Enable AI agents**.
3. Tools > **Check agent permissions**. If an agent cannot read a doctype it needs, add the role that has read access under *Additional roles* on the agent profile (permissions are the union of its roles).
4. Review **SLA Rule** (the vendor rule is on; the probation and leave rules are off until you confirm the state names) and **Completeness Rule**.
5. Add your HR policies as *AI Policy Document* records (paste text or attach PDF/DOCX) for Policy Pal. Optionally list the doctypes Insight may aggregate in *AI Agent Settings > Proposals, auditor and evals*.
6. Open a Job Applicant > **AI > AI Resume Match**. Review criteria in *Job Opening ATS Criteria* (created automatically from the requisition role / job description, editable).

## How the safety model works

- **Effective permission = requesting user AND the agent's role(s).** Tools run only if both hold the permission.
- **Create-only.** Tools are a fixed allowlist (`runtime/registry.py`, `agents/tools*.py`). There is no delete, submit, approve, cancel or workflow tool. Agents write only this app's own records (ATS results, findings, reports, alert logs, proposals).
- **Server guard.** While an agent run is active, deleting, submitting, cancelling, changing workflow state or editing an existing HR record is refused and logged as a High security event. Outside agent runs the guard does nothing.
- **Append-only audit logs** (no edit/delete by anyone in the UI): AI Session Log, AI Agent Run Log, AI Security Log, AI Data Access Log. The run, security and data-access logs carry a hash chain; *Tools > Verify run-log integrity* checks it. Reports, ATS results and findings cannot be deleted either (findings are dismissed or fixed).
- **Data minimisation.** CVs have names, contact details and protected attributes removed before any model call; emails, phone numbers and ID-like numbers are masked; CV text is treated as data, never as instructions; semantic matches proposed by a model count only if the quoted evidence exists in the CV.
- **Numbers from code, words from the model.** ATS score, SLA levels and every report figure are computed deterministically. A model-written report narrative is rejected (and replaced by a deterministic one) if it contains a number that is not in the data snapshot.
- **Budgets and limits.** Daily, monthly, per-agent and per-user token budgets, per-run cap, requests per hour, over-budget action (block or use fallback model), and a global kill switch.

## Monitoring (AI Governance workspace)

Run, session, security and data-access logs; findings; daily reports; SLA alerts; ATS results; reports *AI Agent Performance* (runs, success/error/blocked, latency, tokens, cost, helpful rate) and *AI Usage by User*. Each run shows who asked, which agent, masked prompt/response, tools called, records read and created, model, tokens and cost. Users can rate any answer; ratings feed the performance report.

## Compliance notes (not legal advice)

Designed around India DPDP Act 2023 / Rules 2025 and Qatar PDPPL (Law No. 13 of 2016): purpose-limited agents (each profile states purpose, lawful basis and data categories), data minimisation and masking, a data-access log of what personal data reached which model, retention floors (6 months minimum), provider/region approval switch, opt-out, no automated hiring decisions (scores are advisory), breach-relevant security events. Reported DPDP rules include one-year retention of access logs, which is why the default log retention is 12 months. Retention is **flagged weekly, never deleted automatically**. Have the DPO/counsel confirm obligations, notification timelines and the provider's DPA, no-training and residency terms before production.

## Assumptions about your site

Reads these existing doctypes and fields by name and skips what is absent: Workforce Plan (budget/headcount fields, `custom_budget_plan` links), Job Requisition and Job Requisition Role, Job Opening (`custom_vendor*`, `custom_sla_breached`, `job_requisition`), Job Applicant (`custom_expected_salary`, `lower_range`, `upper_range`, `custom_budget_plan`, `resume_attachment`), CXO Hiring Approval, Probation Review, Leave Application, Employee Exit. Release 2 also uses Probation Review Goal (goal, self_evaluation, manager_evaluation, rating), Employee Exit Activity (activity, role, status, remarks), Job Requisition Role (role_type, role_being_replaced, budgeted, role_description, scope, expertise_skillset, experience_required, education, estimated_cost_usd), Workforce Plan Position (designation, existing_cost_usd), Leave Allocation, Job Offer, Employee Onboarding, Interview and ToDo. Fields that do not exist are skipped or reported as a clear error. Workflow state names used by the optional SLA seeds ("Pending Manager Approval", "Manager Review", ...) should be confirmed in *SLA Rule*.

## Provider note

Azure OpenAI is called through its chat-completions API with tool calling. A "Microsoft Copilot" experience (Copilot Studio / Microsoft 365 Copilot) is a different product surface; confirm with Microsoft which API your tenant is licensed to call. The provider layer (`runtime/providers.py`) is the only place to change.

## Tests

- `python -m unittest discover -s tests` : pure-python unit tests (24: ATS maths, redaction, provider payloads, SLA levels, no destructive tool in any release, every propose tool writes only proposals, roster/tool consistency, protected proposal fields, policy chunking).
- `tests/integration_check.py <site>` and `tests/integration_r2.py <site>` : developer scripts for a throw-away bench site (see file headers). Release 1 script: 87 checks (install state, gating, ATS, logs and immutability, delete/edit guard, Sentinel, Tara, Atlas, AI path with a fake provider, budgets, opt-out, scheduler, reports). Release 2/3 script: 171 checks (incl. knowledge base (FAQ approval, role visibility, negation, staleness, candidates, quick actions), exports, ATS criteria refresh, agent consultation and access, my history, OCR: proposal apply/reject/stale/expiry, protected fields, every drafting agent, k-anonymity, policy search, workflow explanations, Auditor and tamper detection, compliance pack, data-subject inventory, purge, evals).

## Known limits

The browser UI (floating button, dialogs, ATS panel, proposal review) has been syntax-checked but should be clicked through once on your site. Release 2 tools were tested against stand-ins of your custom doctypes using the field names recorded from your site; run **Tools > Check agent permissions** and try each agent once, because a renamed field or a stricter permission on your real site will surface as a clear error in the proposal or answer rather than a silent change. Auto-score of new CVs is off by default. OCR for scanned CVs is not included. WhatsApp/SMS channels are not included. Compliance paperwork (DPIA, RoPA, DPO and provider documents) is intentionally outside this package.
