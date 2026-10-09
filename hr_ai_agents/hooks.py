app_name = "hr_ai_agents"
app_title = "HR AI Agents"
app_publisher = "ERPion"
app_description = "Optional, role-based AI agents for the HRMS flow, with full audit logging"
app_email = "pranav@erpion.in"
app_license = "mit"
required_apps = ["hrms"]

# Client scripts. They do nothing unless the signed-in user has the "AI Agent User"
# role, the app is enabled in AI Agent Settings, and the user has not opted out.
app_include_js = ["/assets/hr_ai_agents/js/ai_agents.js"]
app_include_css = ["/assets/hr_ai_agents/css/ai_agents.css"]

boot_session = "hr_ai_agents.hr_ai_agents.api.boot_session"

before_install = "hr_ai_agents.hr_ai_agents.setup.install.before_install"
after_install = "hr_ai_agents.hr_ai_agents.setup.install.after_install"

# Passive guards. Both are no-ops unless an agent run is in progress
# (frappe.flags.ai_agent_context). They never touch manual user activity.
doc_events = {
    "*": {
        "validate": "hr_ai_agents.hr_ai_agents.runtime.guard.on_validate",
        "on_trash": "hr_ai_agents.hr_ai_agents.runtime.guard.on_destructive",
        "before_submit": "hr_ai_agents.hr_ai_agents.runtime.guard.on_destructive",
        "before_cancel": "hr_ai_agents.hr_ai_agents.runtime.guard.on_destructive",
    },
    # Optional auto-scoring, gated by AI Agent Settings > "Auto-score new CVs" (off by default).
    "Job Applicant": {
        "after_insert": "hr_ai_agents.hr_ai_agents.agents.ats.auto_score_hook",
        "on_update": "hr_ai_agents.hr_ai_agents.agents.ats.auto_score_hook",
    },
}

scheduler_events = {
    "cron": {
        # Every 15 minutes. The tick itself decides what is due, in the timezone set in
        # AI Agent Settings (default Asia/Qatar): Atlas EOD report, Tara SLA scan,
        # Sentinel nightly scan, retention flagging.
        "*/15 * * * *": ["hr_ai_agents.hr_ai_agents.tasks.tick"],
    }
}
