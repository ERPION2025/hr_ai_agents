"""consult_agent: one agent asks another for facts, then combines the answers.

The consulted agent runs as the SAME requesting user, with its own tool allowlist and role limits, so what comes back is
exactly what that user is allowed to see: someone without access to Employee records gets a refusal from the other agent,
not the employee list. Nothing is shared outside the two runs; each is logged separately (the consulted run records who
asked it). Limits: only while a person is asking, a call chain at most two agents deep, no loops, at most four
consultations per request."""
import frappe

from hr_ai_agents.hr_ai_agents.runtime.registry import tool

MAX_DEPTH = 2          # agents in one chain (the asker plus one consulted agent; that agent may consult one more)
MAX_CONSULTS = 4       # per user request
NOT_CONSULTABLE = {"Auditor"}   # auditor-only data is never reachable through another agent
NO_CONSULT = {"Auditor", "Policy Pal", "Insight"}  # these keep their narrow, self-contained behaviour


def directory(exclude=()):
    rows = frappe.get_all("AI Agent Profile", filters={"enabled": 1}, fields=["name", "title", "can_do"], order_by="name")
    return [r for r in rows if r.name not in NOT_CONSULTABLE and r.name not in exclude]


def directory_text(agent_name):
    rows = directory(exclude=(agent_name,))
    if not rows:
        return ""
    return ("Other agents you can consult with consult_agent (they answer only with what the current user is allowed to see): "
            + "; ".join(f"{r.name} ({(r.title or '').strip()}: {(r.can_do or '').strip()[:140]})" for r in rows))


@tool(
    "consult_agent",
    "Ask another agent a precise question when the answer needs data or skills that agent has, then combine its reply with your own findings. "
    "The other agent works for the same user and can only read what that user may read; if it reports a permission problem, tell the user instead of guessing. "
    "Use sparingly (max 4 per request).",
    {"type": "object", "properties": {"agent": {"type": "string", "description": "agent name, e.g. Noor"}, "question": {"type": "string", "description": "a complete, self-contained question including any plan, requisition or employee names"}}, "required": ["agent", "question"]},
)
def consult_agent(run, agent, question):
    from hr_ai_agents.hr_ai_agents.runtime.runner import AgentRun

    if run.trigger != "User":
        raise frappe.PermissionError("Agents can consult each other only while a person is asking.")
    agent = (agent or "").strip()
    chain = list(frappe.flags.get("ai_agent_chain") or [run.agent_name])
    if agent in chain:
        raise frappe.ValidationError(f"{agent} is already part of this conversation chain.")
    if len(chain) >= MAX_DEPTH + 1:
        raise frappe.ValidationError("Consultation chain is too deep; answer with what you have.")
    if run.consults >= MAX_CONSULTS:
        raise frappe.ValidationError("Consultation limit for this request reached; answer with what you have.")
    if agent in NOT_CONSULTABLE or not frappe.db.exists("AI Agent Profile", {"name": agent, "enabled": 1}):
        names = ", ".join(r.name for r in directory(exclude=(run.agent_name,)))
        raise frappe.ValidationError(f"'{agent}' is not available. Available agents: {names}.")
    run.consults += 1
    saved = {k: frappe.flags.get(k) for k in ("ai_agent_name", "ai_run_id", "ai_agent_context", "ai_agent_chain")}
    frappe.flags.ai_agent_chain = chain + [agent]
    try:
        with AgentRun(agent, question, user=run.user, trigger="User", context={"delegated_by": run.agent_name, "chain": chain + [agent]}) as sub:
            answer = sub.chat(
                question,
                system_extra=f"This question comes from the agent {run.agent_name}, working for the same user. Answer only from your tools, briefly, with exact figures and names as returned. "
                "If a tool refuses because of permissions, say so plainly and do not guess or work around it.",
            )
            sub.response = answer
    finally:
        for k, v in saved.items():
            frappe.flags[k] = v
    run.created.extend(sub.created)
    run.files.extend(sub.files)
    return {"agent": agent, "answer": answer}
