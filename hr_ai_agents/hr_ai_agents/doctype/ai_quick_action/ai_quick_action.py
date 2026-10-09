import json

import frappe
from frappe import _
from frappe.model.document import Document


class AIQuickAction(Document):
    def validate(self):
        from hr_ai_agents.hr_ai_agents.runtime import registry

        registry._load()
        t = registry.TOOLS.get(self.tool_name)
        if not t:
            frappe.throw(_("Unknown tool {0}.").format(self.tool_name))
        if t.get("writes"):
            frappe.throw(_("Quick actions can only run read-only tools; {0} creates records.").format(self.tool_name))
        for fld, kind in (("arguments", dict), ("parameters", list)):
            raw = (self.get(fld) or "").strip()
            if raw:
                try:
                    v = json.loads(raw)
                except ValueError:
                    frappe.throw(_("{0} must be valid JSON.").format(fld))
                if not isinstance(v, kind):
                    frappe.throw(_("{0} must be a JSON {1}.").format(fld, "object" if kind is dict else "list"))
        if not self.roles:
            frappe.throw(_("Choose at least one role under 'Visible to roles'."))
