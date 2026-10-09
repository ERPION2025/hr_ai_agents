"""Tool registry: a hard allowlist of named functions with fixed JSON schemas.
There is deliberately no delete / submit / cancel / workflow tool. Anything not registered here
cannot be called by a model."""

TOOLS = {}


def tool(name, description, schema, writes=None):
    """Decorator. `writes` documents which app-owned doctype the tool may create/update (None = read-only)."""

    def deco(fn):
        TOOLS[name] = {"name": name, "description": description, "schema": schema, "fn": fn, "writes": writes}
        return fn

    return deco


def tools_for(allowed_names):
    _load()
    return [TOOLS[n] for n in allowed_names if n in TOOLS]


def _load():
    # import for side effects (registration)
    from hr_ai_agents.hr_ai_agents.agents import tools, tools_collab, tools_exports, tools_r2, tools_r3  # noqa: F401
