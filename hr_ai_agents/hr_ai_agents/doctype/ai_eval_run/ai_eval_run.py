import frappe
from frappe import _
from frappe.model.document import Document


class AIEvalRun(Document):
    def validate(self):
        if not self.is_new() and not frappe.flags.get("ai_log_system_update"):
            frappe.throw(_("Eval runs are history and cannot be edited."))

    def on_trash(self):
        if not (frappe.flags.in_uninstall or frappe.flags.in_install):
            frappe.throw(_("Eval runs cannot be deleted."))
