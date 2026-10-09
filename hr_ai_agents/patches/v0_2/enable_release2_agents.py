"""0.2.0: fill in the Release 2/3 agent profiles (still-empty placeholders only), add Meera's new tools,
seed evaluation cases. Never overwrites edits a manager made."""
import frappe


def execute():
    from hr_ai_agents.hr_ai_agents.agents import evals
    from hr_ai_agents.hr_ai_agents.setup import install

    install.before_install()  # roles (idempotent)
    install.ensure_roster(upgrade=True)
    evals.seed()
    s = frappe.get_doc("AI Agent Settings")
    if not (s.get("proposal_expiry_days") or 0):
        s.proposal_expiry_days = 7
    if not (s.get("aud_max_runs_per_user_day") or 0):
        s.aud_max_runs_per_user_day = 50
    if not (s.get("aud_denials_threshold") or 0):
        s.aud_denials_threshold = 5
    s.flags.ignore_permissions = True
    s.save()
