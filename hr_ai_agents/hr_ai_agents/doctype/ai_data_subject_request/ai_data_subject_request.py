import frappe
from frappe import _
from frappe.model.document import Document


class AIDataSubjectRequest(Document):
    def validate(self):
        if self.status == "Fulfilled" and self.request_type == "Erasure":
            frappe.msgprint(_("Agents never erase data. Confirm that a person has completed the erasure through the normal procedure."), indicator="orange")

    def on_trash(self):
        if not (frappe.flags.in_uninstall or frappe.flags.in_install):
            frappe.throw(_("Requests are kept for the audit trail."))
