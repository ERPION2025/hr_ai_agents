import frappe
from frappe import _
from frappe.model.document import Document


class AIFinding(Document):
    def validate(self):
        if self.status == "Dismissed" and not self.dismissed_reason:
            frappe.throw(_("Please give a reason for dismissing a finding."))

    def on_trash(self):
        if not (frappe.flags.in_uninstall or frappe.flags.in_install):
            frappe.throw(_("Findings are kept for audit; set the status to Dismissed or Fixed instead."))
