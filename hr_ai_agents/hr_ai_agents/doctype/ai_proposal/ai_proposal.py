import frappe
from frappe import _
from frappe.model.document import Document


class AIProposal(Document):
    def validate(self):
        if not self.is_new() and not frappe.flags.get("ai_log_system_update"):
            frappe.throw(_("Proposals are changed only by Apply or Reject."))

    def on_trash(self):
        if not (frappe.flags.in_uninstall or frappe.flags.in_install or frappe.flags.get("ai_log_retention_purge")):
            frappe.throw(_("Proposals are kept for audit; unapplied ones expire."))
