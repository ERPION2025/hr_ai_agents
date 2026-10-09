import frappe
from frappe.utils import add_months, getdate, today


def execute(filters=None):
    f = frappe._dict(filters or {})
    frm, to = f.from_date or add_months(today(), -1), f.to_date or today()
    cond, vals = "date(creation) between %(frm)s and %(to)s", {"frm": getdate(frm), "to": getdate(to)}
    if f.agent:
        cond += " and agent = %(agent)s"
        vals["agent"] = f.agent
    cols = [
        {"label": "User", "fieldname": "run_user", "fieldtype": "Link", "options": "User", "width": 220},
        {"label": "Agent", "fieldname": "agent", "fieldtype": "Link", "options": "AI Agent Profile", "width": 130},
        {"label": "Requests", "fieldname": "runs", "fieldtype": "Int", "width": 90},
        {"label": "Tokens", "fieldname": "tokens", "fieldtype": "Int", "width": 100},
        {"label": "Cost", "fieldname": "cost", "fieldtype": "Float", "precision": 4, "width": 90},
        {"label": "Blocked / errors", "fieldname": "bad", "fieldtype": "Int", "width": 130},
        {"label": "Last request", "fieldname": "last", "fieldtype": "Datetime", "width": 160},
    ]
    rows = frappe.db.sql(
        f"""select run_user, agent, count(*) runs, coalesce(sum(input_tokens+output_tokens),0) tokens,
            coalesce(sum(cost),0) cost, sum(outcome in ('Blocked','Error')) bad, max(creation) `last`
            from `tabAI Agent Run Log` where {cond} group by run_user, agent order by tokens desc""",
        vals, as_dict=True,
    )
    return cols, rows
