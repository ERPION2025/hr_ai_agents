import re

import frappe
from frappe.model.document import Document


class AIAgentProfile(Document):
    def validate(self):
        if self.autonomy not in ("Assist", "Draft"):
            frappe.throw("Autonomy can only be Assist or Draft. Acting on records is always a human click.")

    def on_update(self):
        self.ensure_service_user()

    def all_roles(self):
        return [self.role] + [r.role for r in (self.get("additional_roles") or []) if r.role and r.role != self.role]

    def ensure_service_user(self):
        email = "%s@ai-agents.invalid" % re.sub(r"[^a-z0-9]+", ".", self.agent_name.lower()).strip(".")
        roles = self.all_roles()
        if not frappe.db.exists("User", email):
            u = frappe.get_doc({
                "doctype": "User", "email": email, "first_name": self.agent_name,
                "last_name": "(AI)", "enabled": 0, "send_welcome_email": 0,
                "user_type": "System User", "roles": [{"role": r} for r in roles],
            })
            u.flags.ignore_permissions = True
            u.insert()
        else:
            u = frappe.get_doc("User", email)
            if sorted(r.role for r in u.roles) != sorted(roles):
                u.set("roles", [{"role": r} for r in roles])
                u.flags.ignore_permissions = True
                u.save()
        if self.service_user != email:
            frappe.db.set_value("AI Agent Profile", self.name, "service_user", email, update_modified=False)
        frappe.cache().delete_key("hr_ai_agents_boot")
