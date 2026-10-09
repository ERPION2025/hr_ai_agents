"""Data-subject request helper: finds where a person's data is held. It never changes or erases anything;
corrections and erasure are decided and done by a person."""
import json

import frappe
from frappe.utils import now_datetime

SKIP_DOCTYPES = {"AI Data Subject Request"}
DYNAMIC = [
    ("AI Data Access Log", "reference_doctype", "reference_name"), ("AI Finding", "reference_doctype", "reference_name"),
    ("AI Proposal", "reference_doctype", "reference_name"), ("Comment", "reference_doctype", "reference_name"),
    ("Communication", "reference_doctype", "reference_name"), ("File", "attached_to_doctype", "attached_to_name"),
    ("ToDo", "reference_type", "reference_name"), ("Version", "ref_doctype", "docname"),
]


def _link_fields(target):
    out = set()
    for r in frappe.get_all("DocField", filters={"fieldtype": "Link", "options": target}, fields=["parent", "fieldname"], limit=2000):
        out.add((r.parent, r.fieldname))
    for r in frappe.get_all("Custom Field", filters={"fieldtype": "Link", "options": target}, fields=["dt", "fieldname"], limit=2000):
        out.add((r.dt, r.fieldname))
    return sorted(out)


def build(subject_type, subject):
    if not frappe.db.exists(subject_type, subject):
        frappe.throw(f"{subject_type} {subject} not found")
    held = []
    for dt, fn in _link_fields(subject_type):
        if dt in SKIP_DOCTYPES or not frappe.db.exists("DocType", dt) or not frappe.db.table_exists(dt):
            continue
        meta = frappe.get_meta(dt)
        if meta.issingle or meta.get("is_virtual"):
            continue
        try:
            if meta.istable:
                rows = frappe.get_all(dt, filters={fn: subject}, fields=["name", "parent", "parenttype"], limit=50, ignore_permissions=True)
                cnt = frappe.db.count(dt, {fn: subject})
                if cnt:
                    held.append({"doctype": dt, "via": f"child table of {rows[0].parenttype}", "field": fn, "count": cnt, "records": sorted({r.parent for r in rows})[:10]})
            else:
                cnt = frappe.db.count(dt, {fn: subject})
                if cnt:
                    held.append({"doctype": dt, "via": "link", "field": fn, "count": cnt, "records": frappe.get_all(dt, filters={fn: subject}, pluck="name", limit=10, ignore_permissions=True)})
        except Exception:
            continue
    for dt, tf, nf in DYNAMIC:
        if not frappe.db.table_exists(dt):
            continue
        try:
            cnt = frappe.db.count(dt, {tf: subject_type, nf: subject})
            if cnt:
                held.append({"doctype": dt, "via": "reference", "field": nf, "count": cnt, "records": frappe.get_all(dt, filters={tf: subject_type, nf: subject}, pluck="name", limit=10, ignore_permissions=True)})
        except Exception:
            continue
    runs = frappe.db.sql("select count(*) from `tabAI Agent Run Log` where records_touched like %s", (f'%"{subject}"%',))[0][0]
    if runs:
        held.append({"doctype": "AI Agent Run Log", "via": "records touched by agents", "field": "records_touched", "count": runs, "records": []})
    extra = {}
    if subject_type == "Employee":
        extra["user_account"] = frappe.db.get_value("Employee", subject, "user_id")
    return {"subject_type": subject_type, "subject": subject, "generated_on": str(now_datetime()), "held": held, "extra": extra}


def render(inv):
    esc = frappe.utils.escape_html
    h = [f"<p>Personal data held for <b>{esc(inv['subject_type'])} {esc(inv['subject'])}</b> (generated {esc(inv['generated_on'])}).</p>"]
    h.append("<table class='table table-bordered'><tr><th>Record type</th><th>How linked</th><th>Count</th><th>Examples</th></tr>")
    for r in inv["held"]:
        h.append(f"<tr><td>{esc(r['doctype'])}</td><td>{esc(r['via'])} ({esc(r['field'])})</td><td>{r['count']}</td><td>{esc(', '.join(map(str, r['records'][:5])))}</td></tr>")
    h.append("</table>")
    h.append("<p><i>This is an inventory only. The app deletes and edits nothing. Corrections, erasure and any retention exceptions (legal, payroll, tax, audit logs) are decided by a person under your data-protection procedure.</i></p>")
    return "".join(h)


def fill_request(name):
    d = frappe.get_doc("AI Data Subject Request", name)
    inv = build(d.subject_type, d.subject)
    frappe.db.set_value("AI Data Subject Request", name, {
        "inventory": json.dumps(inv, default=str, indent=1), "inventory_html": render(inv),
        "status": "In review" if d.status == "Open" else d.status, "handled_by": d.handled_by or frappe.session.user,
    })
    return inv
