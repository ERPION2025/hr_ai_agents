"""Whitelisted endpoints used by the Ask-an-agent panel and the form buttons."""
import json

import frappe
from frappe import _
from frappe.utils import cint

from hr_ai_agents.hr_ai_agents.agents import ats, atlas, auditor, dsr, evals, faq, policy, proposals, retention, sentinel, sla
from hr_ai_agents.hr_ai_agents.runtime import core
from hr_ai_agents.hr_ai_agents.runtime.core import AgentBlocked
from hr_ai_agents.hr_ai_agents.runtime.runner import AgentRun

CHECK_DOCTYPES = ["Workforce Plan", "Job Requisition", "Job Opening", "Job Applicant", "CXO Hiring Approval", "Employee", "Probation Review", "Leave Application", "Employee Exit"]


def boot_session(bootinfo):
    """Adds a small flag to the desk boot. Absent flag = the UI shows nothing."""
    try:
        user = frappe.session.user
        if user == "Guest" or not core.is_enabled() or not core.user_can_use(user) or core.user_opted_out(user):
            return
        agents = frappe.get_all(
            "AI Agent Profile", filters={"enabled": 1}, fields=["name", "title", "purpose", "can_do", "release"], order_by="name asc"
        )
        bootinfo.hr_ai_agents = {
            "agents": agents,
            "check_doctypes": CHECK_DOCTYPES,
            "is_manager": "AI Agent Manager" in frappe.get_roles(user) or user == "Administrator",
            "proposal_doctypes": sorted(proposals.PROPOSABLE),
            "is_auditor": bool(set(frappe.get_roles(user)) & set(auditor.AUDITOR_ROLES)) or user == "Administrator",
        }
    except Exception:
        pass  # never break login because of this app


def _guard(fn):
    try:
        return fn()
    except AgentBlocked as e:
        return {"error": str(e)}
    except frappe.PermissionError as e:
        return {"error": str(e) or "You or the agent are not permitted to do that."}


@frappe.whitelist()
def ask(agent, prompt, context=None, skip_faq=0):
    ctx = frappe.parse_json(context) if context else {}
    extra = ""
    if ctx.get("doctype") and ctx.get("name"):
        extra = f"\n[The user is currently viewing {ctx['doctype']} {ctx['name']}.]"

    def go():
        if not cint(skip_faq):
            hit = faq.find(agent, prompt, frappe.session.user)["serve"]
            if hit:  # answered from the approved knowledge base: no model, no tokens
                with AgentRun(agent, prompt, context={**ctx, "served_from": "faq", "faq": hit.name}) as run:
                    run.response = hit.answer
                faq.record_hit(hit.name)
                return {"answer": hit.answer, "run": run.run_id, "tokens": 0, "model": None, "proposals": [], "files": [], "served_from": "faq", "faq": faq.served_payload(hit)}
        with AgentRun(agent, prompt, context=ctx) as run:
            if agent == "Policy Pal" and not run.model_ready():
                answer = _policy_fallback(prompt)
            else:
                answer = run.chat(prompt + extra)
            run.response = answer
        return {
            "answer": answer, "run": run.run_id, "tokens": run.in_tokens + run.out_tokens, "model": run.model_used,
            "proposals": [c[1] for c in run.created if c and c[0] == "AI Proposal"],
            "files": [{"name": f["file"], "file_name": f["file_name"], "url": f["url"], "format": f["format"], "size": f["size"]} for f in run.files],
        }

    return _guard(go)


@frappe.whitelist()
def faq_suggest(agent, text):
    """Type-ahead: approved FAQs the user may see that look like what they are typing. No model."""
    if not core.is_enabled() or not core.user_can_use():
        return []
    return faq.find(agent, text or "", frappe.session.user)["suggest"]


@frappe.whitelist()
def quick_list(agent):
    """Quick-question chips for the Ask panel: approved FAQs and live-data quick actions visible to this user."""
    if not core.is_enabled() or not core.user_can_use():
        return {"faqs": [], "actions": []}
    return {"faqs": faq.listing(agent), "actions": faq.quick_actions(agent)}


@frappe.whitelist()
def run_quick_action(name, params=None):
    if not core.is_enabled() or not core.user_can_use():
        frappe.throw(_("AI agents are not available to you."), frappe.PermissionError)
    return _guard(lambda: faq.run_quick_action(name, frappe.parse_json(params) if params else {}))


def _faq_manager():
    if not ({"HR Manager", "System Manager"} & set(frappe.get_roles())):
        frappe.throw(_("Only an HR Manager can review FAQs."), frappe.PermissionError)


@frappe.whitelist()
def faq_review(name, action):
    """HR Manager: approve a candidate, re-verify, retire or reject. Approval needs roles chosen on the FAQ."""
    _faq_manager()
    d = frappe.get_doc("AI FAQ", name)
    if action == "approve":
        d.status = "Approved"
    elif action == "verify":
        if d.status not in ("Approved", "Needs review"):
            frappe.throw(_("Only an approved FAQ can be verified."))
        d.status = "Approved"
        d.approved_by, d.approved_on, d.last_verified = frappe.session.user, frappe.utils.now_datetime(), frappe.utils.today()
        if d.source_policy:
            d.source_policy_version = frappe.db.get_value("AI Policy Document", d.source_policy, "version")
        d.helpful = d.not_helpful = 0
    elif action == "retire":
        d.status = "Retired"
    elif action == "reject":
        d.status = "Rejected"
    else:
        frappe.throw(_("Unknown action"))
    d.save()
    return {"name": d.name, "status": d.status}


@frappe.whitelist()
def my_history(limit=30, start=0, agent=None):
    """The signed-in user's own requests to agents (never anyone else's), newest first. Text is shown as it was stored
    (emails, phone numbers and IDs are masked unless the manager chose otherwise)."""
    user = frappe.session.user
    if not core.is_enabled() or not core.user_can_use(user):
        return {"rows": [], "more": False}
    limit, start = min(cint(limit) or 30, 100), cint(start)
    filters = {"run_user": user, "trigger": "User"}
    if agent:
        filters["agent"] = agent
    raw = frappe.get_all(
        "AI Agent Run Log", filters=filters, order_by="creation desc", limit_start=start, limit_page_length=limit * 2,
        fields=["name", "creation", "agent", "prompt", "response", "outcome", "context", "input_tokens", "output_tokens"],
    )
    rows = []
    for r in raw:
        if "delegated_by" in (r.context or ""):
            continue  # a consultation between agents, not something the user typed
        hidden = (r.prompt or "").startswith("sha256:")
        rows.append({
            "run": r.name, "when": r.creation, "agent": r.agent, "outcome": r.outcome, "tokens": cint(r.input_tokens) + cint(r.output_tokens),
            "served_from": (frappe.parse_json(r.context or "{}") or {}).get("served_from") if (r.context or "").startswith("{") else None,
            "prompt": "" if hidden else r.prompt, "response": "" if hidden else (r.response or "")[:2500], "stored": not hidden,
        })
        if len(rows) >= limit:
            break
    return {"rows": rows, "more": len(raw) >= limit * 2}


@frappe.whitelist()
def export_daily_report(report, format="word"):
    """Word / Excel copy of a saved Atlas report. Built from the report's saved data snapshot; no model call."""
    import re

    from hr_ai_agents.hr_ai_agents.agents import exports

    if not frappe.has_permission("AI Daily Report", "read", doc=report):
        frappe.throw(_("You do not have permission to download this report."), frappe.PermissionError)
    doc = frappe.get_doc("AI Daily Report", report)
    snap = frappe.parse_json(doc.data_snapshot or "{}")
    sections = exports.sections_from_snapshot(snap)
    head = re.split(r"<h3", doc.summary or "", maxsplit=1)[0]
    head = re.sub(r"<h2>.*?</h2>", "", head, flags=re.S)
    paras = [re.sub(r"<[^>]+>", "", x).strip() for x in re.split(r"</p>|</li>", head)]
    paras = [p for p in paras if p]
    if paras:
        sections.insert(0, {"heading": "Narrative", "paragraphs": paras})
    d = {"title": f"HR Hiring Progress Report {doc.report_date}", "subtitle": f"{doc.kind or ''} report {doc.name}".strip(), "sections": sections,
         "notes": ["Figures are the report's saved data snapshot." + (" Narrative checked against the snapshot." if doc.figures_verified else "")]}
    info = exports.save_file(d, format, attached_to=("AI Daily Report", doc.name), who=frappe.utils.get_fullname(frappe.session.user))
    return {"name": info["file"], "file_name": info["file_name"], "url": info["url"], "format": info["format"], "size": info["size"]}


def _policy_fallback(prompt):
    hits = policy.search(prompt, 3)
    if not hits:
        return "I could not find this in the policy library. Please contact HR."
    return "The AI model is not enabled, so here are the closest policy passages:\n\n" + "\n\n".join(
        f"**{h['policy']}** (version {h['version'] or 'n/a'}, {h['section']})\n{h['text'][:700]}" for h in hits
    )


@frappe.whitelist()
def ats_score(applicant):
    def go():
        with AgentRun(ats.AGENT, f"ATS resume match for {applicant}", context={"doctype": "Job Applicant", "name": applicant}) as run:
            res = ats.score_applicant(run, applicant)
        res["run"] = run.run_id
        return res

    return _guard(go)


@frappe.whitelist()
def ats_latest(applicant):
    if not frappe.has_permission("Job Applicant", "read", doc=applicant):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    if not core.is_enabled() or not core.user_can_use():
        return None
    return ats.latest_result(applicant)


@frappe.whitelist()
def check_document(doctype, name):
    def go():
        with AgentRun(sentinel.AGENT, f"Completeness and anomaly review of {doctype} {name}", context={"doctype": doctype, "name": name}) as run:
            findings = sentinel.review_document(run, doctype, name)
        return {"run": run.run_id, "findings": [{k: f[k] for k in ("severity", "message", "impact", "suggested_fix")} for f in findings]}

    return _guard(go)


@frappe.whitelist()
def open_findings(doctype, name):
    if not frappe.has_permission(doctype, "read", doc=name):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    return frappe.get_all(
        "AI Finding", filters={"reference_doctype": doctype, "reference_name": name, "status": ("in", ["Open", "Acknowledged"])},
        fields=["name", "severity", "message", "impact", "suggested_fix", "status"], order_by="creation desc",
    )


@frappe.whitelist()
def run_report_now(subject_doctype=None, subject_name=None):
    def go():
        with AgentRun(atlas.AGENT, f"On-demand status report {subject_doctype or ''} {subject_name or 'all'}", context={"on_demand": True}) as run:
            doc = atlas.build_report(run, "On demand", subject_doctype, subject_name)
        return {"report": doc.name, "run": run.run_id}

    return _guard(go)


@frappe.whitelist()
def feedback(run, rating, note=None):
    """Thumbs up/down on a run. Only the requesting user may rate; only the feedback fields change."""
    if rating not in ("Up", "Down"):
        frappe.throw(_("Invalid rating"))
    owner = frappe.db.get_value("AI Agent Run Log", run, "run_user")
    if owner != frappe.session.user:
        frappe.throw(_("You can only rate your own requests."), frappe.PermissionError)
    frappe.db.set_value("AI Agent Run Log", run, {"feedback_rating": rating, "feedback_note": (note or "")[:500]}, update_modified=False)
    cand = None
    try:
        cand = faq.on_feedback(run, rating, frappe.session.user)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AI FAQ feedback hook")
    return {"ok": True, "candidate": cand}


@frappe.whitelist()
def set_opt_out(opted_out):
    user = frappe.session.user
    val = cint(opted_out)
    if frappe.db.exists("AI User Preference", user):
        frappe.db.set_value("AI User Preference", user, "opted_out", val)
    else:
        frappe.get_doc({"doctype": "AI User Preference", "user": user, "opted_out": val}).insert(ignore_permissions=True)
    return val


@frappe.whitelist()
def send_sla_alert(alert):
    frappe.only_for(["AI Agent Manager", "HR Manager", "System Manager"])
    return sla.send_pending(alert)


@frappe.whitelist()
def verify_log_chain(doctype="AI Agent Run Log"):
    """Recompute the hash chain of an append-only log and report the first mismatch."""
    frappe.only_for(auditor.AUDITOR_ROLES)
    return auditor.verify_chain(doctype)


AGENT_DOCTYPES = {
    "Meera": ["Job Applicant", "Job Opening", "Job Requisition"],
    "Tara": ["Job Opening", "Probation Review", "Leave Application"],
    "Atlas": ["Workforce Plan", "Job Requisition", "Job Opening", "Job Applicant", "CXO Hiring Approval", "Probation Review", "Leave Application", "Employee Exit"],
    "Sentinel": sentinel.DEFAULT_DOCTYPES,
    "Asha": ["Workforce Plan", "Workforce Plan Position", "Job Requisition", "Job Requisition Role", "Job Applicant"],
    "Rohan": ["Job Requisition", "Job Requisition Role", "Workforce Plan"],
    "Vikram": ["Job Opening", "Job Applicant"],
    "Karim": ["CXO Hiring Approval", "Job Requisition", "Workforce Plan", "Probation Review", "Employee Exit", "Leave Application", "Job Offer"],
    "Noor": ["Employee", "Employee Onboarding"],
    "Dev": ["Probation Review", "Employee"],
    "Leena": ["Leave Application", "Leave Allocation", "Employee"],
    "Samir": ["Employee Exit", "Employee", "Job Requisition"],
    "Navigator": ["Workforce Plan", "Job Requisition", "Probation Review", "Leave Application", "Employee Exit"],
    "Insight": ["Employee", "Leave Application", "Job Applicant", "Job Opening", "Employee Exit", "Probation Review"],
    "Auditor": ["AI Agent Run Log", "AI Security Log", "AI Finding"],
}


@frappe.whitelist()
def diagnose_permissions():
    """For managers: which doctypes each agent needs but its roles cannot read."""
    frappe.only_for(["AI Agent Manager", "System Manager"])
    from hr_ai_agents.hr_ai_agents.runtime.runner import _role_has_perm

    out = []
    for name, dts in AGENT_DOCTYPES.items():
        if not frappe.db.exists("AI Agent Profile", name):
            continue
        p = frappe.get_doc("AI Agent Profile", name)
        roles = [p.role] + [r.role for r in p.get("additional_roles") or []]
        missing = [d for d in dts if frappe.db.exists("DocType", d) and not _role_has_perm(roles, d, "read")]
        out.append({"agent": name, "roles": roles, "cannot_read": missing})
    return out


# ------------------------------------------------------------------ proposals (human side)
PROPOSAL_FIELDS = ["name", "title", "agent", "operation", "status", "reference_doctype", "reference_name", "rationale", "creation", "expires_on", "requested_by", "result_doctype", "result_name", "error"]


def _is_manager(user=None):
    user = user or frappe.session.user
    return user == "Administrator" or "AI Agent Manager" in frappe.get_roles(user)


@frappe.whitelist()
def my_proposals(status="Pending"):
    flt_ = {"status": status} if status else {}
    if not _is_manager():
        flt_["requested_by"] = frappe.session.user
    return frappe.get_all("AI Proposal", filters=flt_, fields=PROPOSAL_FIELDS, order_by="creation desc", limit=50)


@frappe.whitelist()
def proposals_for(doctype, name):
    """Pending proposals for the open form (the requester's own, or all for managers)."""
    flt_ = {"status": "Pending", "reference_doctype": doctype, "reference_name": name}
    if not _is_manager():
        flt_["requested_by"] = frappe.session.user
    return frappe.get_all("AI Proposal", filters=flt_, fields=PROPOSAL_FIELDS, order_by="creation desc", limit=10)


@frappe.whitelist()
def get_proposal(name):
    p = frappe.get_doc("AI Proposal", name)
    if p.requested_by != frappe.session.user and not _is_manager():
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    return {k: p.get(k) for k in PROPOSAL_FIELDS + ["diff_html", "text", "base_modified"]}


@frappe.whitelist()
def apply_proposal(name):
    return proposals.apply(name)


@frappe.whitelist()
def reject_proposal(name, reason=None):
    return proposals.reject(name, reason)


# ------------------------------------------------------------------ governance actions
@frappe.whitelist()
def build_dsr_inventory(name):
    frappe.only_for(["HR Manager", "AI Compliance Auditor", "AI Agent Manager", "System Manager"])
    inv = dsr.fill_request(name)
    core.security_event("Data subject inventory built", f"{name}: {len(inv['held'])} record types", severity="Low")
    return {"record_types": len(inv["held"])}


@frappe.whitelist()
def retention_preview():
    frappe.only_for(["AI Agent Manager", "System Manager"])
    return retention.preview()


@frappe.whitelist()
def retention_purge(confirm=0):
    frappe.only_for(["AI Agent Manager", "System Manager"])
    if not cint(confirm):
        return retention.preview()
    return retention.purge(frappe.session.user)


@frappe.whitelist()
def run_evals(background=1):
    frappe.only_for(["AI Agent Manager", "AI Compliance Auditor", "System Manager"])
    if cint(background):
        frappe.enqueue("hr_ai_agents.hr_ai_agents.agents.evals.run_all", queue="long", timeout=1800, triggered_by=frappe.session.user)
        return {"started": True}
    return {"eval_run": evals.run_all(frappe.session.user).name}


@frappe.whitelist()
def run_audit_now(hours=24):
    frappe.only_for(auditor.AUDITOR_ROLES)

    def go():
        with AgentRun(auditor.AGENT, f"Manual audit scan by {frappe.session.user}", user="Administrator", trigger="Scheduler", context={"by": frappe.session.user}) as run:
            fs = auditor.daily_scan(run, min(cint(hours) or 24, 720))
        return {"observations": len(fs), "run": run.run_id}

    return _guard(go)


@frappe.whitelist()
def build_pack_now():
    frappe.only_for(auditor.AUDITOR_ROLES)
    return {"pack": auditor.build_pack().name}
