"""Retention: preview and human-confirmed purge of expired ATS results, reports and finished proposals.
Chained audit logs are never purged by the app."""
import frappe
from frappe.utils import add_to_date, cint, now_datetime

from hr_ai_agents.hr_ai_agents.runtime import core

TARGETS = [
    ("Applicant ATS Result", {}),
    ("AI Daily Report", {}),
    ("AI Proposal", {"status": ("in", ["Applied", "Rejected", "Expired", "Failed"])}),
    ("File", {"file_name": ("like", "AI-Export-%")}),  # Word / Excel exports made by agents
]


def cutoff():
    s = core.settings()
    return add_to_date(now_datetime(), months=-(cint(s.content_retention_months) or 6))


def preview():
    cut = cutoff()
    out = {}
    for dt, extra in TARGETS:
        out[dt] = frappe.db.count(dt, {"creation": ("<", cut), **extra})
    return {"older_than": str(cut), "counts": out}


def purge(confirmed_by):
    cut = cutoff()
    deleted = {}
    frappe.flags.ai_log_retention_purge = True
    try:
        for dt, extra in TARGETS:
            names = frappe.get_all(dt, filters={"creation": ("<", cut), **extra}, pluck="name", limit=5000)
            for n in names:
                frappe.delete_doc(dt, n, force=1, ignore_permissions=True, delete_permanently=True)
            deleted[dt] = len(names)
    finally:
        frappe.flags.ai_log_retention_purge = False
    core.security_event("Retention purge", f"{confirmed_by} purged records older than {cut}: {deleted}", severity="Medium", user=confirmed_by)
    return {"older_than": str(cut), "deleted": deleted}
