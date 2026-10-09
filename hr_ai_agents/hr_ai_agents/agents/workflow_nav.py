"""Navigator: read-only explanation of workflow state, available actions and why an action is blocked.
Never performs a transition."""
import frappe

APPROVAL_DOCTYPES = [
    "Workforce Plan", "Job Requisition", "CXO Hiring Approval", "Probation Review", "Leave Application",
    "Employee Exit", "Job Offer", "Job Applicant", "Job Opening",
]


def workflow_for(doctype):
    name = frappe.db.get_value("Workflow", {"document_type": doctype, "is_active": 1}, "name")
    return frappe.get_doc("Workflow", name) if name else None


def _condition_ok(transition, doc):
    cond = transition.get("condition")
    if not cond:
        return True, None
    try:
        from frappe.model.workflow import is_transition_condition_satisfied

        return bool(is_transition_condition_satisfied(transition, doc)), cond
    except Exception:
        try:
            return bool(frappe.safe_eval(cond, None, {"doc": doc.as_dict(), "frappe": frappe._dict(utils=frappe.utils)})), cond
        except Exception:
            return False, cond


def describe(doctype, name, user):
    """All transitions out of the document's current state, with a reason for each one the user cannot take."""
    wf = workflow_for(doctype)
    if not wf:
        return {"workflow": None, "note": f"{doctype} has no active workflow."}
    doc = frappe.get_doc(doctype, name)
    state_field = wf.workflow_state_field or "workflow_state"
    state = doc.get(state_field)
    roles = set(frappe.get_roles(user))
    out = []
    for t in wf.transitions:
        if t.state != state:
            continue
        has_role = t.allowed in roles or user == "Administrator"
        self_blocked = bool(user != "Administrator" and not t.get("allow_self_approval") and doc.owner == user)
        cond_ok, cond = _condition_ok(t, doc)
        can = has_role and not self_blocked and cond_ok
        reasons = []
        if not has_role:
            reasons.append(f"needs the role '{t.allowed}'")
        if self_blocked:
            reasons.append("the person who created the document cannot take this step")
        if not cond_ok:
            reasons.append("the workflow condition is not met" + (f" ({cond})" if cond else ""))
        out.append({
            "action": t.action, "moves_to": t.next_state, "needs_role": t.allowed, "you_can_do_this": can,
            "why_not": "; ".join(reasons) or None,
        })
    other_states = sorted({t.state for t in wf.transitions})
    return {
        "workflow": wf.name, "document": f"{doctype} {name}", "current_state": state, "docstatus": doc.docstatus,
        "created_by": doc.owner, "actions": out,
        "note": None if out else "No further actions from this state (it may be a final state).",
        "known_states": other_states,
    }


def pending_for(user, limit=20):
    """Documents sitting in a state where `user` holds a role that can act on them (user permissions apply)."""
    roles = set(frappe.get_roles(user))
    result = []
    for dt in APPROVAL_DOCTYPES:
        if not frappe.db.exists("DocType", dt):
            continue
        wf = workflow_for(dt)
        if not wf:
            continue
        field = wf.workflow_state_field or "workflow_state"
        states = {}
        for t in wf.transitions:
            if t.allowed in roles or user == "Administrator":
                states.setdefault(t.state, set()).add(t.action)
        if not states or not frappe.get_meta(dt).has_field(field):
            continue
        title = frappe.get_meta(dt).title_field or "name"
        fields = list({"name", field, "owner", "modified", title})
        try:
            rows = frappe.get_list(dt, filters={field: ("in", list(states)), "docstatus": ("<", 2)}, fields=fields, order_by="modified asc", limit_page_length=limit, user=user)
        except frappe.PermissionError:
            continue
        for r in rows:
            if r.owner == user and not all(
                t.get("allow_self_approval") for t in wf.transitions if t.state == r.get(field) and (t.allowed in roles)
            ):
                continue
            result.append({
                "doctype": dt, "name": r.name, "title": r.get(title), "state": r.get(field), "waiting_since": str(r.modified),
                "possible_actions": sorted(states[r.get(field)]),
            })
    return result[: limit * 2]
