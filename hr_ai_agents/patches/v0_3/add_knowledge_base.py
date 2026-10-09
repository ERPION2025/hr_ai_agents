"""0.3.2: knowledge base (AI FAQ, AI Quick Action). Seeds the standard quick actions; FAQs start empty (HR Managers approve them)."""


def execute():
    from hr_ai_agents.hr_ai_agents.setup import install

    install.ensure_quick_actions()
