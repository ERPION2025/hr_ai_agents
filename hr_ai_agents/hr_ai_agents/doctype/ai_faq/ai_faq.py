import frappe
from frappe import _
from frappe.model.document import Document


class AIFAQ(Document):
    def validate(self):
        from hr_ai_agents.hr_ai_agents.agents import faq

        self.question = (self.question or "").strip()
        self.question_key = faq.key(self.question)
        if self.status == "Approved":
            if not self.roles:
                frappe.throw(_("Choose at least one role under 'Visible to roles' before approving."))
            roles = set(frappe.get_roles(frappe.session.user))
            if not ({"HR Manager", "System Manager"} & roles):
                frappe.throw(_("Only an HR Manager can approve an FAQ."), frappe.PermissionError)
            if not self.approved_by or self.has_value_changed("status"):
                self.approved_by = frappe.session.user
                self.approved_on = frappe.utils.now_datetime()
                self.last_verified = frappe.utils.today()
                if self.source_policy:
                    self.source_policy_version = frappe.db.get_value("AI Policy Document", self.source_policy, "version")
