"""SLA clock maths (no frappe import)."""


def sla_level(elapsed_days, target_days, warn_pct=80, escalate_after_days=3):
    """None / 'Due Soon' / 'Breached' / 'Escalated'."""
    if not target_days or target_days <= 0 or elapsed_days is None:
        return None
    over = elapsed_days - target_days
    if over >= escalate_after_days:
        return "Escalated"
    if elapsed_days >= target_days:
        return "Breached"
    if elapsed_days / target_days * 100 >= warn_pct:
        return "Due Soon"
    return None


def elapsed_from_due(days_to_due, window_days):
    """Due-date mode: 'elapsed' = window - days remaining (negative remaining => past due)."""
    return window_days - days_to_due
