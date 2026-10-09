"""Release 3 tools: Auditor (compliance reviewer). Restricted to auditor/manager roles on top of the usual
user-and-agent permission check. Read-only on logs; writes only AI Finding / AI Compliance Pack."""
import frappe

from hr_ai_agents.hr_ai_agents.agents import auditor, dsr
from hr_ai_agents.hr_ai_agents.runtime.registry import tool


def _auditor_only(run):
    if not set(frappe.get_roles(run.user)) & set(auditor.AUDITOR_ROLES):
        frappe.throw("Audit tools are available to compliance auditors and AI managers only.", frappe.PermissionError)


def _obj(props, required=None):
    return {"type": "object", "properties": props, "required": required or []}


@tool("audit_observations", "Scan the agent logs for the last N hours and list observations (out-of-role requests, repeated denials, blocked destructive attempts, high volume, after-hours use, cost spikes, broken log chains). Does not save findings.", _obj({"hours": {"type": "integer"}}))
def audit_observations(run, hours=24):
    _auditor_only(run)
    obs = auditor.detect(min(int(hours or 24), 720))
    return {"observations": [{"severity": o["severity"], "message": o["message"], "impact": o["impact"], "suggested_fix": o["fix"], "rule": o["rule"]} for o in obs]}


@tool("run_audit_scan", "Run the audit scan and save new findings (AI Finding, detected by Auditor).", _obj({"hours": {"type": "integer"}}), writes="AI Finding")
def run_audit_scan(run, hours=24):
    _auditor_only(run)
    fs = auditor.daily_scan(run, min(int(hours or 24), 720))
    return {"observations": len(fs), "note": "New findings are under AI Governance > AI Finding."}


@tool("verify_log_chains", "Verify the tamper-evident hash chain of the run, security and data-access logs.", _obj({}))
def verify_log_chains(run):
    _auditor_only(run)
    return {"chains": [auditor.verify_chain(d) for d in auditor.CHAINED]}


@tool("list_audit_findings", "List findings raised by the Auditor.", _obj({"status": {"type": "string"}, "limit": {"type": "integer"}}))
def list_audit_findings(run, status="Open", limit=20):
    _auditor_only(run)
    return frappe.get_all("AI Finding", filters={"detected_by": "Auditor", "status": status or "Open"}, fields=["name", "severity", "message", "impact", "detected_on"], order_by="detected_on desc", limit=min(int(limit or 20), 50))


@tool("dsr_inventory", "Build the inventory of where a person's data is held for a Data Subject Request (inventory only; nothing is changed or erased).", _obj({"request": {"type": "string"}}, ["request"]), writes="AI Data Subject Request")
def dsr_inventory(run, request):
    _auditor_only(run)
    run.require("AI Data Subject Request", "read")
    inv = dsr.fill_request(request)
    run.touch("AI Data Subject Request", request, "inventory built")
    return {"request": request, "record_types": len(inv["held"]), "summary": [{"doctype": h["doctype"], "count": h["count"]} for h in inv["held"]]}
