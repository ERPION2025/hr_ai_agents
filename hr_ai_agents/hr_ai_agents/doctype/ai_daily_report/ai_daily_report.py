import frappe
from frappe import _
from frappe.model.document import Document


class AIDailyReport(Document):
    def validate(self):
        if not self.is_new() and not frappe.flags.get("ai_log_system_update"):
            frappe.throw(_("Reports are read-only after creation."))

    def on_trash(self):
        if not (frappe.flags.in_uninstall or frappe.flags.in_install or frappe.flags.get("ai_log_retention_purge")):
            frappe.throw(_("Reports cannot be deleted."))
