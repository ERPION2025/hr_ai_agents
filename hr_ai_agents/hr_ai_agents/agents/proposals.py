"""Proposal layer: the only way an agent can suggest a change to an HR record.

An agent creates an AI Proposal (a diff). A person reviews it and clicks Apply; Apply runs as that
person through the normal save path, so their permissions, validation scripts and workflows all
apply. Proposals only ever create Draft documents or edit Draft documents, never submit anything,
and fields such as workflow state, docstatus, decisions and approvals can never be proposed.
"""
import json

import frappe
from frappe import _
from frappe.utils import add_to_date, now_datetime

from hr_ai_agents.hr_ai_agents.runtime import core

# doctypes agents may propose to create / edit (all under the user's own permissions on Apply)
PROPOSABLE = {
    "Leave Application", "Leave Allocation", "Job Requisition", "Workforce Plan", "Probation Review",
    "Employee Exit", "Job Offer", "ToDo", "Employee", "Job Opening",
}
# fields an agent may never set, on any doctype
PROTECTED_FIELDS = {
    "name", "owner", "creation", "modified", "modified_by", "docstatus", "idx", "amended_from", "parent", "parenttype",
    "parentfield", "workflow_state", "status_changed", "decision", "hr_approver", "approved_by", "cao_approved_by",
    "ceo_approved_by", "cao_approved_on", "ceo_approved_on", "naming_series",
}
# Employee is not submittable: restrict what an agent may propose to change on it
EMPLOYEE_EDITABLE = {"payroll_cost_center", "reports_to", "department", "designation", "branch", "grade", "employment_type", "company_email", "cell_number"}


class ProposalError(frappe.ValidationError):
    pass


class _no_agent_context:
    """Dry-run validation needs the guard off (it blocks edits of existing docs inside agent runs)."""

    def __enter__(self):
        self.prev = frappe.flags.get("ai_agent_context")
        frappe.flags.ai_agent_context = False

    def __exit__(self, *a):
        frappe.flags.ai_agent_context = self.prev


# ---------------------------------------------------------------- helpers
def _clean_values(doctype, values):
    meta = frappe.get_meta(doctype)
    out = {}
    for k, v in (values or {}).items():
        if k in PROTECTED_FIELDS:
            raise ProposalError(f"'{k}' cannot be set by an agent.")
        if doctype == "Employee" and k not in EMPLOYEE_EDITABLE:
            raise ProposalError(f"Agents may only propose these Employee fields: {', '.join(sorted(EMPLOYEE_EDITABLE))}.")
        df = meta.get_field(k)
        if not df:
            raise ProposalError(f"{doctype} has no field '{k}'.")
        if df.fieldtype in ("Table", "Table MultiSelect"):
            raise ProposalError(f"Use rows for table field '{k}'.")
        out[k] = v
    return out


def _clean_rows(doctype, rows):
    """rows: {table_field: {"append": [ {...} ], "update": [ {"idx": n, "set": {...}} ]}} or {table_field: [ {...} ]} (append)."""
    meta = frappe.get_meta(doctype)
    out = {}
    for table, spec in (rows or {}).items():
        df = meta.get_field(table)
        if not df or df.fieldtype != "Table":
            raise ProposalError(f"{doctype} has no table '{table}'.")
        child_meta = frappe.get_meta(df.options)
        if isinstance(spec, list):
            spec = {"append": spec}
        norm = {"append": [], "update": []}
        for r in spec.get("append") or []:
            norm["append"].append(_clean_child(child_meta, r))
        for u in spec.get("update") or []:
            norm["update"].append({"idx": int(u["idx"]), "set": _clean_child(child_meta, u.get("set") or {})})
        out[table] = norm
    return out


def _clean_child(child_meta, row):
    out = {}
    for k, v in row.items():
        if k in PROTECTED_FIELDS:
            raise ProposalError(f"'{k}' cannot be set by an agent.")
        if not child_meta.get_field(k):
            raise ProposalError(f"{child_meta.name} has no field '{k}'.")
        out[k] = v
    return out


def _apply_rows(doc, rows):
    for table, spec in (rows or {}).items():
        for r in spec.get("append") or []:
            doc.append(table, r)
        for u in spec.get("update") or []:
            tgt = next((x for x in doc.get(table) or [] if x.idx == u["idx"]), None)
            if not tgt:
                raise ProposalError(f"Row {u['idx']} of {table} no longer exists.")
            tgt.update(u["set"])


def _build_doc(operation, doctype, name, values, rows):
    if operation == "Create draft":
        doc = frappe.get_doc({"doctype": doctype, **values})
    else:
        doc = frappe.get_doc(doctype, name)
        doc.update(values)
    _apply_rows(doc, rows)
    return doc


def dry_run(operation, doctype, name, values, rows):
    """Run the document's own validation without saving. Returns an error string or None."""
    frappe.db.savepoint("ai_dry_run")
    try:
        with _no_agent_context():
            doc = _build_doc(operation, doctype, name, values, rows)
            doc.flags.ignore_permissions = True
            if operation == "Create draft":
                doc.set("__islocal", 1)  # behave as a new document, exactly as insert() does
                doc._set_defaults()
                doc.name = "AI-DRY-RUN"  # placeholder only so child rows get a parent; nothing is saved
                doc.set_parent_in_children()  # same as insert(): meta defaults, naming series, today's date, ...
            doc._action = "save"
            if operation == "Update fields":
                doc.load_doc_before_save()
            doc.run_method("before_validate")
            doc.run_method("validate")
            for step in ("_validate_selects", "_validate_non_negative", "_validate_length", "_validate_mandatory", "_validate_links"):
                if hasattr(doc, step):
                    getattr(doc, step)()
        return None
    except Exception as e:  # noqa
        return _plain(str(e))
    finally:
        frappe.db.rollback(save_point="ai_dry_run")


def _plain(msg):
    import re

    return re.sub(r"<[^>]+>", "", msg or "")[:400]


def _fmt(v):
    return frappe.utils.escape_html("" if v is None else str(v))


def diff_html(operation, doctype, name, values, rows):
    esc = frappe.utils.escape_html
    h = []
    if operation == "Create draft":
        h.append(f"<p>New <b>{esc(doctype)}</b> as a <b>Draft</b>. Nothing is submitted or approved.</p>")
        h.append("<table class='table table-bordered'><tr><th>Field</th><th>Value</th></tr>")
        meta = frappe.get_meta(doctype)
        for k, v in values.items():
            h.append(f"<tr><td>{esc(meta.get_label(k) or k)}</td><td>{_fmt(v)}</td></tr>")
        h.append("</table>")
    else:
        doc = frappe.get_doc(doctype, name)
        meta = frappe.get_meta(doctype)
        h.append(f"<p>Changes to <b>{esc(doctype)} {esc(name)}</b> (Draft only).</p>")
        h.append("<table class='table table-bordered'><tr><th>Field</th><th>Now</th><th>Proposed</th></tr>")
        for k, v in values.items():
            h.append(f"<tr><td>{esc(meta.get_label(k) or k)}</td><td>{_fmt(doc.get(k))}</td><td><b>{_fmt(v)}</b></td></tr>")
        h.append("</table>")
    for table, spec in (rows or {}).items():
        label = frappe.get_meta(doctype).get_label(table) or table
        for r in spec.get("append") or []:
            h.append(f"<p><b>Add row to {esc(label)}</b>: " + ", ".join(f"{esc(k)} = {_fmt(v)}" for k, v in r.items()) + "</p>")
        for u in spec.get("update") or []:
            h.append(f"<p><b>Change row {u['idx']} of {esc(label)}</b>: " + ", ".join(f"{esc(k)} = {_fmt(v)}" for k, v in u["set"].items()) + "</p>")
    return "".join(h)


# ---------------------------------------------------------------- agent side
def propose(run, operation, doctype=None, name=None, values=None, rows=None, title="", rationale="", text=None):
    """Create an AI Proposal. Returns a small dict for the model. Raises ProposalError for the model to fix."""
    if operation == "Draft text":
        doc = run.create_own({
            "doctype": "AI Proposal", "title": (title or "Draft text")[:290], "agent": run.agent_name, "operation": operation,
            "status": "Pending", "requested_by": run.user, "rationale": rationale, "text": text or "",
            "expires_on": add_to_date(now_datetime(), days=core.settings().proposal_expiry_days or 7), "run": run.run_id,
        })
        return {"proposal": doc.name, "note": "Draft text saved for the user to review and copy."}

    if doctype not in PROPOSABLE:
        raise ProposalError(f"Agents cannot propose changes to {doctype}.")
    values = _clean_values(doctype, values)
    rows = _clean_rows(doctype, rows)
    base_modified = None
    if operation == "Update fields":
        if not name or not frappe.db.exists(doctype, name):
            raise ProposalError(f"{doctype} {name} not found.")
        run.require(doctype, "write", doc=name)
        target = frappe.get_doc(doctype, name)
        if target.get("docstatus") not in (0, None):
            raise ProposalError("Only Draft documents can be edited through a proposal.")
        base_modified = str(target.modified)
        run.touch(doctype, name, "proposed edit")
    elif operation == "Create draft":
        run.require(doctype, "create")
    else:
        raise ProposalError("Unknown operation.")
    err = dry_run(operation, doctype, name, values, rows)
    if err:
        raise ProposalError(f"Validation would fail: {err}")
    doc = run.create_own({
        "doctype": "AI Proposal", "title": (title or f"{operation}: {doctype} {name or ''}").strip()[:290], "agent": run.agent_name,
        "operation": operation, "status": "Pending", "requested_by": run.user, "reference_doctype": doctype,
        "reference_name": name if operation == "Update fields" else None, "rationale": rationale,
        "diff_html": diff_html(operation, doctype, name, values, rows), "values": json.dumps(values, default=str),
        "rows": json.dumps(rows, default=str), "base_modified": base_modified,
        "expires_on": add_to_date(now_datetime(), days=core.settings().proposal_expiry_days or 7), "run": run.run_id,
    })
    return {"proposal": doc.name, "status": "Pending", "note": "Tell the user to open the proposal, review the changes and click Apply. Nothing has been saved to the record yet."}


# ---------------------------------------------------------------- human side
def _load_pending(name):
    p = frappe.get_doc("AI Proposal", name)
    user = frappe.session.user
    if p.requested_by != user and "AI Agent Manager" not in frappe.get_roles(user) and user != "Administrator":
        frappe.throw(_("Only the person who asked can apply this proposal."), frappe.PermissionError)
    if p.status != "Pending":
        frappe.throw(_("This proposal is already {0}.").format(p.status))
    if p.expires_on and now_datetime() > p.expires_on:
        _set(p.name, {"status": "Expired"})
        frappe.throw(_("This proposal has expired. Ask the agent again."))
    return p


def _set(name, vals):
    frappe.flags.ai_log_system_update = True
    try:
        frappe.db.set_value("AI Proposal", name, vals, update_modified=False)
    finally:
        frappe.flags.ai_log_system_update = False


def apply(name):
    """Runs as the signed-in person. Never inside an agent run."""
    if frappe.flags.get("ai_agent_context"):
        frappe.throw(_("Agents cannot apply proposals."), frappe.PermissionError)
    p = _load_pending(name)
    user = frappe.session.user
    if p.operation == "Draft text":
        _set(p.name, {"status": "Applied", "applied_by": user, "applied_on": now_datetime()})
        return {"status": "Applied", "text": p.text}
    values, rows = json.loads(p.values or "{}"), json.loads(p.rows or "{}")
    try:
        if p.operation == "Create draft":
            doc = _build_doc("Create draft", p.reference_doctype, None, values, rows)
            doc.insert()  # as the user: permissions + validation + workflow initial state all apply
        else:
            target = frappe.get_doc(p.reference_doctype, p.reference_name)
            if str(target.modified) != (p.base_modified or ""):
                raise ProposalError("The record changed after this proposal was made. Ask the agent again so it works from the latest version.")
            if target.get("docstatus") not in (0, None):
                raise ProposalError("The record is no longer a Draft.")
            doc = _build_doc("Update fields", p.reference_doctype, p.reference_name, values, rows)
            doc.save()
    except Exception as e:  # noqa
        frappe.db.rollback()
        msg = _plain(str(e))
        _set(p.name, {"status": "Failed", "error": msg, "applied_by": user, "applied_on": now_datetime()})
        frappe.db.commit()
        return {"status": "Failed", "error": msg}
    _set(p.name, {"status": "Applied", "applied_by": user, "applied_on": now_datetime(), "result_doctype": doc.doctype, "result_name": doc.name})
    return {"status": "Applied", "doctype": doc.doctype, "name": doc.name}


def reject(name, reason=None):
    p = _load_pending(name)
    _set(p.name, {"status": "Rejected", "applied_by": frappe.session.user, "applied_on": now_datetime(), "error": (reason or "")[:300]})
    return {"status": "Rejected"}


def expire_old():
    for n in frappe.get_all("AI Proposal", filters={"status": "Pending", "expires_on": ("<", now_datetime())}, pluck="name"):
        _set(n, {"status": "Expired"})
