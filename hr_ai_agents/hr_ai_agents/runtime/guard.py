"""Create-only, never-delete guard.

These hooks are registered on every doctype but do nothing unless an agent run is active
(frappe.flags.ai_agent_context is set by AgentRun). Manual user activity is never affected.
"""
import frappe


def _block(doc, what):
    from hr_ai_agents.hr_ai_agents.runtime.core import security_event

    security_event(
        "Blocked destructive action",
        f"{what} on {doc.doctype} {doc.name} refused inside an agent run",
        severity="High",
        agent=frappe.flags.get("ai_agent_name"),
        run=frappe.flags.get("ai_run_id"),
    )
    frappe.throw(
        f"AI agents cannot {what}. A person must do this.", frappe.PermissionError, title="Blocked for AI agents"
    )


def on_destructive(doc, method=None):
    if not frappe.flags.get("ai_agent_context"):
        return
    # the app's own append-only logs are written by code through AgentRun, never deleted
    _block(doc, {"on_trash": "delete records", "before_submit": "submit documents", "before_cancel": "cancel documents"}.get(method, "perform this action"))


def on_validate(doc, method=None):
    if not frappe.flags.get("ai_agent_context"):
        return
    if doc.doctype.startswith("AI ") or doc.doctype in ("SLA Rule", "SLA Alert Log", "Completeness Rule", "Applicant ATS Result", "Job Opening ATS Criteria", "ATS Synonym"):
        return  # the app's own doctypes: the only things agents may write
    if not doc.is_new():
        # R1 agents never edit existing HR records. (R2 drafting agents will write through AI Proposal,
        # applied by a person under their own permissions.)
        meta = frappe.get_meta(doc.doctype)
        before = doc.get_doc_before_save()
        if before and meta.get_field("workflow_state") and before.get("workflow_state") != doc.get("workflow_state"):
            _block(doc, "change workflow state")
        _block(doc, "modify existing records")
