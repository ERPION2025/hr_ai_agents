import frappe
from frappe import _
from frappe.model.document import Document


class AICompliancePack(Document):
    def validate(self):
        if not self.is_new() and not frappe.flags.get("ai_log_system_update"):
            frappe.throw(_("Compliance packs are read-only."))

    def on_trash(self):
        if not (frappe.flags.in_uninstall or frappe.flags.in_install):
            frappe.throw(_("Compliance packs cannot be deleted."))
