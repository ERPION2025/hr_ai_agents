"""0.3.0: give Atlas the export_file tool (Word / Excel downloads built by ERPNext). Additive only."""


def execute():
    from hr_ai_agents.hr_ai_agents.setup import install

    install.ensure_roster(upgrade=True)
