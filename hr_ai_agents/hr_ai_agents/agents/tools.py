"""Registered tools. Read tools return aggregates or small records; writers only create the app's
own records (ATS results, findings, reports). No delete / submit / approve tool exists."""
import frappe

from hr_ai_agents.hr_ai_agents.agents import ats, atlas, sentinel, sla
from hr_ai_agents.hr_ai_agents.runtime.registry import tool


@tool(
    "list_workforce_plans",
    "List recent Workforce Plans with headcount and budget figures.",
    {"type": "object", "properties": {"limit": {"type": "integer", "description": "max 20"}}},
)
def list_workforce_plans(run, limit=10):
    run.require("Workforce Plan", "read")
    rows = frappe.get_all("Workforce Plan", filters={"docstatus": ("<", 2)}, fields=atlas._fields("Workforce Plan", atlas.PLAN_FIELDS), order_by="modified desc", limit=min(int(limit or 10), 20))
    for r in rows:
        run.touch("Workforce Plan", r.name)
        run.sent_to_model("Workforce Plan", r.name, ["budget and headcount figures"])
    return rows


@tool(
    "get_plan_status",
    "Progress of one Workforce Plan: budget, requisitions, openings, applicants by status, items needing attention.",
    {"type": "object", "properties": {"plan": {"type": "string", "description": "Workforce Plan name, e.g. REC-BUD-2026-00024"}}, "required": ["plan"]},
)
def get_plan_status(run, plan):
    snap = atlas.collect_snapshot(run, "Workforce Plan", plan)
    run.sent_to_model("Workforce Plan", plan, ["status snapshot"])
    return {"plans": snap["plans"], "attention": snap["attention"]}


@tool(
    "get_requisition_status",
    "Progress of one Job Requisition: roles, cost, openings, vendor SLA, applicants by status, ATS average.",
    {"type": "object", "properties": {"requisition": {"type": "string"}}, "required": ["requisition"]},
)
def get_requisition_status(run, requisition):
    snap = atlas.collect_snapshot(run, "Job Requisition", requisition)
    run.sent_to_model("Job Requisition", requisition, ["status snapshot"])
    return {"requisitions": snap["requisitions"], "attention": snap["attention"]}


@tool(
    "generate_status_report",
    "Create a saved status report (AI Daily Report) for one Workforce Plan or Job Requisition, or for everything if no subject is given.",
    {"type": "object", "properties": {"subject_doctype": {"type": "string", "enum": ["Workforce Plan", "Job Requisition"]}, "subject_name": {"type": "string"}}},
    writes="AI Daily Report",
)
def generate_status_report(run, subject_doctype=None, subject_name=None):
    doc = atlas.build_report(run, "On demand", subject_doctype, subject_name)
    return {"report": doc.name, "note": "Open it from AI Governance > AI Daily Report"}


@tool(
    "ats_score",
    "Score one Job Applicant's CV against its Job Opening (keywords matched/missing, score, summary). Advisory only.",
    {"type": "object", "properties": {"applicant": {"type": "string"}}, "required": ["applicant"]},
    writes="Applicant ATS Result",
)
def ats_score(run, applicant):
    r = ats.score_applicant(run, applicant)
    return {k: r[k] for k in ("name", "applicant_name", "score", "band", "summary", "risk_flags")} | {
        "matched_must_have": [m["term"] for m in r["matched"].get("must_have", [])],
        "missing_must_have": r["missing"].get("must_have", []),
        "missing_nice_to_have": r["missing"].get("nice_have", []),
    }


@tool(
    "ats_result",
    "Latest saved ATS result for a Job Applicant, without re-scoring.",
    {"type": "object", "properties": {"applicant": {"type": "string"}}, "required": ["applicant"]},
)
def ats_result(run, applicant):
    run.require("Job Applicant", "read", doc=applicant)
    return ats.latest_result(applicant) or {"note": "No ATS result yet"}


@tool(
    "check_document",
    "Review a document for missing important fields and anomalies; saves findings. Read-only on the document.",
    {"type": "object", "properties": {"doctype": {"type": "string"}, "name": {"type": "string"}}, "required": ["doctype", "name"]},
    writes="AI Finding",
)
def check_document(run, doctype, name):
    findings = sentinel.review_document(run, doctype, name)
    return {"count": len(findings), "findings": [{k: f[k] for k in ("severity", "message", "impact", "suggested_fix")} for f in findings]}


@tool(
    "list_findings",
    "List AI findings (anomalies and missing-field reviews), newest first.",
    {"type": "object", "properties": {"severity": {"type": "string", "enum": ["High", "Medium", "Low"]}, "status": {"type": "string"}, "limit": {"type": "integer"}}},
)
def list_findings(run, severity=None, status="Open", limit=15):
    flt_ = {"status": status or "Open"}
    if severity:
        flt_["severity"] = severity
    return frappe.get_all("AI Finding", filters=flt_, fields=["name", "severity", "message", "reference_doctype", "reference_name", "impact", "status"], order_by="detected_on desc", limit=min(int(limit or 15), 30))


@tool(
    "sla_status",
    "List open SLA alerts raised by Tara (Due Soon / Breached / Escalated).",
    {"type": "object", "properties": {"level": {"type": "string", "enum": ["Due Soon", "Breached", "Escalated"]}, "limit": {"type": "integer"}}},
)
def sla_status(run, level=None, limit=20):
    flt_ = {"status": ("!=", "Dismissed")}
    if level:
        flt_["level"] = level
    return frappe.get_all("SLA Alert Log", filters=flt_, fields=["name", "rule", "reference_doctype", "reference_name", "level", "status", "elapsed_days", "target_days"], order_by="creation desc", limit=min(int(limit or 20), 50))
