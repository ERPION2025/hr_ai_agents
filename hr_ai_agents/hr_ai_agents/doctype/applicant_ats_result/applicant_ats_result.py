import frappe
from frappe import _
from frappe.model.document import Document


class ApplicantATSResult(Document):
    def validate(self):
        if not self.is_new() and not frappe.flags.get("ai_log_system_update"):
            frappe.throw(_("ATS results are history; run a new score instead of editing."))

    def on_trash(self):
        if not (frappe.flags.in_uninstall or frappe.flags.in_install or frappe.flags.get("ai_log_retention_purge")):
            frappe.throw(_("ATS results cannot be deleted."))
