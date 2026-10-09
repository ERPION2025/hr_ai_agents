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
        {"label": "Agent", "fieldname": "agent", "fieldtype": "Link", "options": "AI Agent Profile", "width": 130},
        {"label": "Runs", "fieldname": "runs", "fieldtype": "Int", "width": 70},
        {"label": "Users", "fieldname": "users", "fieldtype": "Int", "width": 70},
        {"label": "Success", "fieldname": "ok", "fieldtype": "Int", "width": 80},
        {"label": "Errors", "fieldname": "err", "fieldtype": "Int", "width": 70},
        {"label": "Blocked", "fieldname": "blocked", "fieldtype": "Int", "width": 80},
        {"label": "Avg latency (ms)", "fieldname": "lat", "fieldtype": "Int", "width": 120},
        {"label": "Input tokens", "fieldname": "tin", "fieldtype": "Int", "width": 110},
        {"label": "Output tokens", "fieldname": "tout", "fieldtype": "Int", "width": 110},
        {"label": "Cost", "fieldname": "cost", "fieldtype": "Float", "precision": 4, "width": 90},
        {"label": "Helpful", "fieldname": "up", "fieldtype": "Int", "width": 80},
        {"label": "Not helpful", "fieldname": "down", "fieldtype": "Int", "width": 100},
        {"label": "Helpful %", "fieldname": "pct", "fieldtype": "Percent", "width": 90},
    ]
    rows = frappe.db.sql(
        f"""select agent, count(*) runs, count(distinct run_user) users,
            sum(outcome='Success') ok, sum(outcome='Error') err, sum(outcome='Blocked') blocked,
            coalesce(avg(latency_ms),0) lat, coalesce(sum(input_tokens),0) tin, coalesce(sum(output_tokens),0) tout,
            coalesce(sum(cost),0) cost, sum(feedback_rating='Up') up, sum(feedback_rating='Down') down
            from `tabAI Agent Run Log` where {cond} group by agent order by runs desc""",
        vals, as_dict=True,
    )
    for r in rows:
        rated = (r.up or 0) + (r.down or 0)
        r.pct = round((r.up or 0) * 100 / rated, 1) if rated else None
    return cols, rows
