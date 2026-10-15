"""Turns the audit trail into a readable summary of recent activity."""

from typing import Any

READ_ACTIONS = {"list", "get", "read", "check", "search", "guidelines", "list_messages", None}


def _describe(e: dict) -> str | None:
    """One line for a notable audit entry; None for low-level steps."""
    event = e["event"]
    args = e.get("args") or {}
    if event == "ticket_start":
        return "Started working the ticket"
    if event == "ticket_end":
        return f"Finished the ticket ({e.get('seconds')}s, ${e.get('cost')})"
    if event == "agent_message":
        return f"To {e.get('to')}: {str(e.get('message'))[:240]}"
    if event == "tool_call":
        return f"Used {e.get('tool')} ({args.get('action', 'default')})"
    if event == "tool_call_blocked":
        return f"Blocked from using {e.get('tool')} ({args.get('action', 'default')})"
    if event == "tool_error":
        return f"{e.get('tool')} returned an error: {str(e.get('error'))[:200]}"
    if event == "boss_decision":
        r = e.get("review") or {}
        return (f"Reviewed {e.get('helper')}'s proposal: {r.get('decision')}, outcome {r.get('ticket_outcome')}, "
                f"cash impact {r.get('economic_impact', 0):+.2f}")
    if event == "awaiting_human_approval":
        return f"Prepared {e.get('payment')} of {e.get('amount'):+.2f}; waiting for a human to approve (approval {e.get('approval_id')})"
    if event == "ticket_failed":
        return f"Ticket flagged: {e.get('reason')}"
    if event == "mcp_call" and args.get("action") not in READ_ACTIONS:
        return f"{e.get('tool')}: {args.get('action')}"
    if event in ("review_start", "review_end"):
        return "Hourly review " + ("started" if event == "review_start" else "finished")
    return None


def summarize(entries: list[dict], limit: int = 300) -> dict[str, Any]:
    """Per-agent activity and a timeline of the most recent `limit` audit entries."""
    recent = entries[-limit:]
    agents: dict[str, dict] = {}
    timeline = []
    for e in recent:
        info = agents.setdefault(e["agent"], {
            "steps": 0, "tools_used": {}, "tickets": [], "last_active": None, "recent_activity": [],
        })
        info["steps"] += 1
        info["last_active"] = e["ts"]
        if e.get("ticket_id") is not None and e["ticket_id"] not in info["tickets"]:
            info["tickets"].append(e["ticket_id"])
        if e["event"] == "tool_call":
            tool = f"{e['tool']} ({(e.get('args') or {}).get('action', 'default')})"
            info["tools_used"][tool] = info["tools_used"].get(tool, 0) + 1
        line = _describe(e)
        if line:
            item = {"ts": e["ts"], "ticket_id": e.get("ticket_id"), "agent": e["agent"], "event": e["event"], "summary": line}
            timeline.append(item)
            info["recent_activity"] = (info["recent_activity"] + [line])[-5:]
    return {"entries_considered": len(recent), "agents": agents, "timeline": timeline[-100:]}
