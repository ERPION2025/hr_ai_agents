import frappe
from frappe import _
from frappe.model.document import Document


class AIAgentSettings(Document):
    def validate(self):
        for f in ("log_retention_months", "content_retention_months"):
            if (self.get(f) or 0) < 6:
                frappe.throw(_("{0} cannot be below 6 months").format(self.meta.get_label(f)))
        if (self.provider == "Azure OpenAI") and self.enabled and not (self.azure_endpoint and self.default_model):
            frappe.msgprint(_("Azure endpoint and default model (deployment name) are needed before models can be called."), indicator="orange")
        if self.kill_switch and not self.kill_reason:
            self.kill_reason = frappe.session.user
        for f in ("w_must","w_nice","w_exp","w_role","w_edu","w_loc","w_sal"):
            if (self.get(f) or 0) < 0:
                frappe.throw(_("Weights cannot be negative"))

    def on_update(self):
        frappe.cache().delete_key("hr_ai_agents_boot")
