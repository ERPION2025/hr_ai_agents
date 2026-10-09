"""Tara: SLA reminders. Clocks are computed in code from SLA Rule records; messages are
deterministic. Internal users get in-app notifications; emails (including vendors) go out only
when 'Send reminders automatically' is on, otherwise they wait as Pending Review for one click."""
import json
from datetime import datetime, time

import frappe
from frappe.utils import cint, flt, get_datetime, getdate, now_datetime, today

from hr_ai_agents.hr_ai_agents.runtime import core
from hr_ai_agents.hr_ai_agents.runtime.sla_core import elapsed_from_due, sla_level

AGENT = "Tara"
LEVEL_ORDER = {"Due Soon": 1, "Breached": 2, "Escalated": 3}


def local_now(s=None):
    from zoneinfo import ZoneInfo

    s = s or core.settings()
    try:
        return datetime.now(ZoneInfo(s.timezone or "Asia/Qatar"))
    except Exception:
        return datetime.now()


def _t(v, default):
    """Convert a Time field value (timedelta / str / time) to datetime.time."""
    if isinstance(v, time):
        return v
    if hasattr(v, "total_seconds"):
        sec = int(v.total_seconds())
        return time(sec // 3600 % 24, sec % 3600 // 60)
    if isinstance(v, str) and v:
        parts = [int(x) for x in v.split(":")[:2]]
        return time(parts[0], parts[1])
    return default


def in_quiet_hours(s=None):
    s = s or core.settings()
    now = local_now(s).time()
    start, end = _t(s.sla_quiet_start, time(20, 0)), _t(s.sla_quiet_end, time(7, 0))
    return (now >= start or now < end) if start > end else (start <= now < end)


def _lines(v):
    return [x.strip() for x in (v or "").replace(",", "\n").split("\n") if x.strip()]


def _role_users(roles):
    if not roles:
        return []
    users = frappe.get_all("Has Role", filters={"role": ("in", roles), "parenttype": "User"}, pluck="parent")
    if not users:
        return []
    enabled = frappe.get_all("User", filters={"name": ("in", list(set(users))), "enabled": 1, "user_type": "System User"}, pluck="name")
    return [u for u in enabled if u not in ("Administrator", "Guest")]


def _recipients(rule, doc, level):
    internal = set()
    for fld in _lines(rule.notify_fields):
        v = doc.get(fld)
        if v and isinstance(v, str):
            if "@" in v and not frappe.db.exists("User", v):
                internal.add(v)  # plain email
            elif frappe.db.exists("User", v):
                internal.add(v)
            else:  # Employee link -> user_id
                uid = frappe.db.get_value("Employee", v, "user_id") if frappe.db.exists("Employee", v) else None
                if uid:
                    internal.add(uid)
    if LEVEL_ORDER[level] >= 2:
        internal.update(_role_users(_lines(rule.notify_roles)))
    if LEVEL_ORDER[level] >= 3:
        internal.update(_role_users([r for r in ("HR Manager", "Head of OD & Total Rewards") if frappe.db.exists("Role", r)]))
    vendor_email = None
    if rule.vendor_email_field and doc.get(rule.vendor_email_field):
        sup = doc.get(rule.vendor_email_field)
        meta = frappe.get_meta("Supplier")
        if meta.has_field("email_id"):
            vendor_email = frappe.db.get_value("Supplier", sup, "email_id")
    return sorted(internal), vendor_email


def _message(rule, doc, level, elapsed, target):
    title = doc.get(rule.title_field) if rule.title_field else ""
    word = {"Due Soon": "due soon", "Breached": "BREACHED", "Escalated": "ESCALATED (overdue)"}[level]
    return (
        f"[SLA {word}] {rule.reference_doctype} {doc.name}" + (f" ({title})" if title else "")
        + f": rule '{rule.rule_name}', target {target:g} days, elapsed {elapsed:.1f} days."
    )


def _notify_internal(users, rule, doc, message):
    for u in users:
        if "@" in u and not frappe.db.exists("User", u):
            continue
        try:
            frappe.get_doc(
                {
                    "doctype": "Notification Log", "for_user": u, "type": "Alert", "from_user": "Administrator",
                    "document_type": rule.reference_doctype, "document_name": doc.name,
                    "subject": message, "email_content": message,
                }
            ).insert(ignore_permissions=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Tara in-app notification")


def _send_email(recipients, subject, message):
    recipients = [r for r in recipients if r]
    if not recipients:
        return False
    frappe.sendmail(recipients=recipients, subject=subject, message=frappe.utils.escape_html(message).replace("\n", "<br>"), now=False)
    return True


def _user_emails(users):
    out = []
    for u in users:
        out.append(u if "@" in u and not frappe.db.exists("User", u) else frappe.db.get_value("User", u, "email"))
    return [e for e in out if e]


def scan(run):
    s = core.settings()
    created = 0
    for r in frappe.get_all("SLA Rule", filters={"enabled": 1}, pluck="name"):
        rule = frappe.get_doc("SLA Rule", r)
        dt = rule.reference_doctype
        if not run.can(dt, "read"):
            continue
        try:
            filters = json.loads(rule.filters_json or "{}")
        except ValueError:
            continue
        meta = frappe.get_meta(dt)
        fields = {"name", "owner", "creation"}
        for fld in [rule.start_field, rule.due_field, rule.target_days_field, rule.title_field, rule.vendor_email_field] + _lines(rule.notify_fields):
            if fld and meta.has_field(fld):
                fields.add(fld)
        if any(not meta.has_field(k) and k not in ("name", "owner", "creation", "modified", "docstatus") for k in filters):
            continue
        rows = frappe.get_all(dt, filters=filters, fields=list(fields), limit=1000)
        for d in rows:
            d = frappe._dict(d)
            target = flt(d.get(rule.target_days_field)) if rule.target_days_field else 0
            target = target or flt(rule.target_days)
            if not target:
                continue
            if rule.clock_mode == "Due date field":
                due = d.get(rule.due_field)
                if not due:
                    continue
                elapsed = elapsed_from_due((getdate(due) - getdate(today())).days, target)
            else:
                start = d.get(rule.start_field) or d.creation
                elapsed = (now_datetime() - get_datetime(start)).total_seconds() / 86400
            level = sla_level(elapsed, target, cint(rule.warn_at_pct) or 80, cint(rule.escalate_after_days) or 3)
            if not level:
                continue
            if frappe.db.exists("SLA Alert Log", {"rule": rule.name, "reference_doctype": dt, "reference_name": d.name, "level": level}):
                continue
            internal, vendor = _recipients(rule, d, level)
            msg = _message(rule, d, level, elapsed, target)
            _notify_internal(internal, rule, d, msg)
            status, sent_on = "Notified in-app", None
            email_targets = _user_emails(internal) + ([vendor] if vendor else [])
            if email_targets and cint(s.sla_auto_send) and not in_quiet_hours(s):
                if _send_email(email_targets, f"SLA {level}: {dt} {d.name}", msg):
                    status, sent_on = "Sent", now_datetime()
            elif vendor or (email_targets and not cint(s.sla_auto_send)):
                status = "Pending Review"
            run.create_own(
                {
                    "doctype": "SLA Alert Log", "rule": rule.name, "reference_doctype": dt, "reference_name": d.name,
                    "level": level, "status": status, "elapsed_days": round(elapsed, 2), "target_days": target,
                    "recipients": ", ".join(email_targets), "sent_on": sent_on, "message": msg,
                }
            )
            run.touch(dt, d.name)
            created += 1
    run.response = f"SLA scan complete: {created} new alert(s)."
    return created


def send_pending(alert_name):
    """Human click: send the queued email for a Pending Review alert."""
    a = frappe.get_doc("SLA Alert Log", alert_name)
    if a.status != "Pending Review":
        frappe.throw("This alert is not waiting for review.")
    targets = [x.strip() for x in (a.recipients or "").split(",") if x.strip()]
    if not _send_email(targets, f"SLA {a.level}: {a.reference_doctype} {a.reference_name}", a.message):
        frappe.throw("No recipients with an email address.")
    frappe.db.set_value("SLA Alert Log", a.name, {"status": "Sent", "sent_on": now_datetime()})
    return a.name
