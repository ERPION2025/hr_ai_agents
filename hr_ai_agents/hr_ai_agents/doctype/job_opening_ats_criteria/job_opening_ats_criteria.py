import frappe
from frappe.model.document import Document


class JobOpeningATSCriteria(Document):
    def before_save(self):
        if not self.is_new():
            before = self.get_doc_before_save()
            if before and not self.flags.get("auto_refresh") and any(before.get(f) != self.get(f) for f in ("must_have", "nice_have", "min_years", "certifications")):
                self.version = (before.version or 1) + 1
                if self.source != "Manual":
                    self.source = "Manual"
