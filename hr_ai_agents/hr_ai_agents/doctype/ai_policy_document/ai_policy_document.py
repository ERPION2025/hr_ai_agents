import frappe
from frappe.model.document import Document


class AIPolicyDocument(Document):
    def before_save(self):
        # index the attached file when it is new or changed; never fail the save because of extraction
        if self.attachment and (self.is_new() or self.has_value_changed("attachment")):
            try:
                from hr_ai_agents.hr_ai_agents.agents import policy

                policy.extract_text(self)
            except Exception:
                frappe.log_error(frappe.get_traceback(), "Policy extraction failed")
        self.extracted_chars = len(self.content or "")
