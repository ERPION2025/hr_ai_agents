"""Scheduler entry point. Runs every 15 minutes and decides what is due in the configured
timezone. Does nothing at all unless AI Agent Settings is enabled and the kill switch is off."""
import frappe
from frappe.utils import add_to_date, cint, now_datetime, today

from hr_ai_agents.hr_ai_agents.agents import atlas, auditor, evals, proposals, sentinel, sla, ats
from hr_ai_agents.hr_ai_agents.runtime import core
from hr_ai_agents.hr_ai_agents.runtime.core import AgentBlocked
from hr_ai_agents.hr_ai_agents.runtime.runner import AgentRun


def tick():
    try:
        if not core.is_enabled():
            return
    except Exception:
        return  # tables not ready (during install/migrate)
    s = core.settings()
    local = sla.local_now(s)
    local_date = local.strftime("%Y-%m-%d")
    weekend = local.weekday() in (4, 5)  # Friday, Saturday (Qatar)

    # Atlas: daily EOD report
    if cint(s.atlas_enabled) and (cint(s.atlas_include_weekends) or not weekend):
        if local.time() >= sla._t(s.atlas_time, local.time().replace(hour=22, minute=0)) and not frappe.db.exists(
            "AI Daily Report", {"kind": "Daily EOD", "report_date": local_date}
        ):
            _safe(lambda: _atlas(local_date), "Atlas EOD")

    # Tara: hourly SLA scan
    if cint(s.tara_enabled) and local.minute < 15:
        _safe(_tara, "Tara SLA scan")

    # Sentinel: nightly scan once per local day, after the configured time
    if cint(s.sentinel_enabled) and local.time() >= sla._t(s.sentinel_time, local.time().replace(hour=2, minute=0)):
        key = "hr_ai_agents_sentinel_last"
        if frappe.cache().get_value(key) != local_date:
            frappe.cache().set_value(key, local_date, expires_in_sec=60 * 60 * 30)
            _safe(_sentinel, "Sentinel nightly")

    # proposals past their expiry are marked Expired (cheap)
    _safe(proposals.expire_old, "Proposal expiry")

    # Auditor: daily scan after 03:30 local; monthly governance pack on the 1st after 04:00
    if cint(s.auditor_enabled):
        if local.hour * 60 + local.minute >= 3 * 60 + 30:
            key = "hr_ai_agents_auditor_last"
            if frappe.cache().get_value(key) != local_date:
                frappe.cache().set_value(key, local_date, expires_in_sec=60 * 60 * 30)
                _safe(_auditor, "Auditor daily scan")
        if local.day == 1 and local.hour >= 4:
            key = "hr_ai_agents_pack_last"
            if frappe.cache().get_value(key) != local_date:
                frappe.cache().set_value(key, local_date, expires_in_sec=60 * 60 * 30)
                _safe(auditor.build_pack, "Compliance pack")

    # weekly evaluation run (Monday 04:xx local)
    if cint(s.evals_weekly) and local.weekday() == 0 and local.hour == 4 and local.minute < 15:
        _safe(lambda: evals.run_all("Scheduler"), "Weekly evals")

    # weekly retention review (Monday 03:xx local): flags only, never deletes
    if local.weekday() == 0 and local.hour == 3 and local.minute < 15:
        _safe(retention_review, "Retention review")


def _safe(fn, label):
    try:
        fn()
    except AgentBlocked as e:
        frappe.logger("hr_ai_agents").info(f"{label} skipped: {e}")
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"hr_ai_agents {label}")


def _atlas(local_date):
    with AgentRun(atlas.AGENT, f"Daily EOD report {local_date}", user="Administrator", trigger="Scheduler", context={"kind": "daily"}) as run:
        atlas.build_report(run, "Daily EOD", email=True)


def _tara():
    with AgentRun(sla.AGENT, "Hourly SLA scan", user="Administrator", trigger="Scheduler", context={"kind": "sla_scan"}) as run:
        sla.scan(run)


def _sentinel():
    with AgentRun(sentinel.AGENT, "Nightly anomaly and completeness scan", user="Administrator", trigger="Scheduler", context={"kind": "nightly"}) as run:
        sentinel.nightly_scan(run)


def _auditor():
    with AgentRun(auditor.AGENT, "Daily audit scan", user="Administrator", trigger="Scheduler", context={"kind": "audit"}) as run:
        auditor.daily_scan(run)


def retention_review():
    """Flags (never removes) ATS results and logs older than the retention period."""
    s = core.settings()
    content_cut = add_to_date(now_datetime(), months=-(cint(s.content_retention_months) or 6))
    log_cut = add_to_date(now_datetime(), months=-(cint(s.log_retention_months) or 12))
    items = {
        "ats": frappe.db.count("Applicant ATS Result", {"creation": ("<", content_cut)}),
        "reports": frappe.db.count("AI Daily Report", {"creation": ("<", content_cut)}),
        "run_logs": frappe.db.count("AI Agent Run Log", {"creation": ("<", log_cut)}),
    }
    fp = "retention-review"
    msg = f"Retention review: {items['ats']} ATS result(s) and {items['reports']} report(s) older than {s.content_retention_months} months; {items['run_logs']} run log(s) older than {s.log_retention_months} months."
    existing = frappe.db.get_value("AI Finding", {"fingerprint": fp}, "name")
    if not any(items.values()):
        if existing:
            frappe.db.set_value("AI Finding", existing, "status", "Resolved (auto)")
        return
    if existing:
        frappe.db.set_value("AI Finding", existing, {"message": msg, "last_seen": now_datetime(), "status": "Open"})
    else:
        frappe.get_doc({
            "doctype": "AI Finding", "message": msg, "severity": "Low", "status": "Open", "finding_type": "Integrity",
            "impact": "Personal data kept longer than the configured retention period.",
            "suggested_fix": "A person reviews and removes expired records as per policy. Agents never delete.",
            "rule": "retention", "detected_by": "System", "detected_on": now_datetime(), "last_seen": now_datetime(), "fingerprint": fp,
        }).insert(ignore_permissions=True)
