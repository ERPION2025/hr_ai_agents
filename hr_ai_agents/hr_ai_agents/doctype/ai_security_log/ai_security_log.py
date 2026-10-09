import frappe
from frappe import _
from frappe.model.document import Document

from hr_ai_agents.hr_ai_agents.runtime.core import stamp_hash

HASHED = ['event_type', 'severity', 'event_user', 'agent', 'tool', 'detail']


class AISecurityLog(Document):
    def before_insert(self):
        stamp_hash(self, HASHED)

    def validate(self):
        if not self.is_new() and not frappe.flags.get("ai_log_system_update"):
            frappe.throw(_("Audit records are append-only and cannot be edited."))

    def on_trash(self):
        if not (frappe.flags.in_uninstall or frappe.flags.in_install or frappe.flags.get("ai_log_retention_purge")):
            frappe.throw(_("Audit records cannot be deleted."))
