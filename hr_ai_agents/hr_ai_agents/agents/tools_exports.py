"""export_file: lets an agent hand the user a Word or Excel file. ERPNext builds the file (agents/exports.py);
the model only picks the source and options, and never receives the rows."""
import frappe

from hr_ai_agents.hr_ai_agents.agents import exports
from hr_ai_agents.hr_ai_agents.runtime.registry import tool

_SECTION = {
    "type": "object",
    "properties": {
        "heading": {"type": "string"},
        "paragraphs": {"type": "array", "items": {"type": "string"}},
        "columns": {"type": "array", "items": {"type": "string"}},
        "rows": {"type": "array", "items": {"type": "array", "items": {}}},
    },
}


@tool(
    "export_file",
    "Create a downloadable Word (.docx) or Excel (.xlsx) file; ERPNext builds the file, you do not format anything. "
    "source: status_overview (plans, requisitions, pending items, attention list; optional subject_doctype/subject_name to limit to one Workforce Plan or Job Requisition), "
    "job_openings, sla_alerts, records (a list or count-by-group of one document type: doctype + optional fields, filters, group_by, months), "
    "or custom (only for text you wrote yourself in this chat: pass title and sections). Prefer a data source over custom, because the figures then come straight from the system. "
    "After the file is created tell the user it is ready under your answer; do not paste the rows.",
    {
        "type": "object",
        "properties": {
            "source": {"type": "string", "enum": exports.SOURCES},
            "format": {"type": "string", "enum": ["word", "excel"]},
            "title": {"type": "string"},
            "subject_doctype": {"type": "string", "enum": ["Workforce Plan", "Job Requisition"]},
            "subject_name": {"type": "string"},
            "doctype": {"type": "string"},
            "fields": {"type": "array", "items": {"type": "string"}},
            "filters": {"type": "object", "description": "field -> value, or field -> [values]"},
            "group_by": {"type": "string"},
            "months": {"type": "integer"},
            "sections": {"type": "array", "items": _SECTION, "description": "only for source=custom"},
        },
        "required": ["source", "format"],
    },
    writes="File",
)
def export_file(run, source, format, title=None, subject_doctype=None, subject_name=None, doctype=None, fields=None, filters=None, group_by=None, months=None, sections=None):
    if source == "status_overview":
        doc = exports.source_status(run, subject_doctype, subject_name)
    elif source == "plan_status" or source == "requisition_status":
        if not subject_name:
            raise frappe.ValidationError("subject_name is required.")
        doc = exports.source_status(run, "Workforce Plan" if source == "plan_status" else "Job Requisition", subject_name)
    elif source == "job_openings":
        doc = exports.source_openings(run)
    elif source == "sla_alerts":
        run.require("SLA Alert Log", "read")
        doc = exports.source_sla(run)
    elif source == "records":
        if not doctype:
            raise frappe.ValidationError("doctype is required for source=records.")
        doc = exports.source_records(run, doctype, fields, filters, group_by, months)
    elif source == "custom":
        if not sections:
            raise frappe.ValidationError("sections are required for source=custom.")
        doc = exports.source_custom(sections, title)
    else:
        raise frappe.ValidationError("Unknown source.")
    if title:
        doc["title"] = title
    doc = exports.clean_doc(doc)
    info = exports.save_file(doc, format, who=frappe.utils.get_fullname(run.user) if run.user else None)
    run.files.append(info)
    run.created.append(["File", info["file"]])
    rows = sum(len(s["rows"]) for s in doc["sections"])
    return {"created": True, "file_name": info["file_name"], "format": format, "tables": sum(1 for s in doc["sections"] if s["columns"]), "rows": rows,
            "note": "The file is ready; a download button appears under your answer. Tell the user what it contains in one or two sentences.", "left_out": doc["notes"][:1]}
