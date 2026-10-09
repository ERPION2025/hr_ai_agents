"""0.3.0: agents can consult each other (consult_agent) and Noor lists employees onboarded against a Workforce Plan. Additive only."""


def execute():
    from hr_ai_agents.hr_ai_agents.setup import install

    install.ensure_roster(upgrade=True)
