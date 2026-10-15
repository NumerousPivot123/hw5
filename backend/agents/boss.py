"""Boss agent: routes tickets, signs off on every change, confirms economic
impact, closes tickets, and runs the hourly review."""

from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset

from ..models import BossReview, Deps, HourlyReview, Policy, PriorityReview, Proposal, Triage
from .base import as_json, build_agent, load_prompt

NAME = "boss"
HELPERS = ["inventory", "accounting", "facilities", "customer_service"]

# The boss reads everything to check the helpers' work. Its own decisions
# (routing, approvals, cash impact, closing tickets, flags) are returned as
# structured output and carried out through the MCP server by the runner.
READ_POLICY: Policy = {
    "desk_tool": {"read"},
    "ticket_tool": {"list", "get"},
    "approval_tool": {"list"},
    "flag_tool": {"list"},
    "order_tool": {"check"},
    "rent_tool": {"check"},
    "override_tool": None,
    "account_tool": {"read"},
    "payment_invoice_tool": {"read"},
    "customer_service_tool": {"search", "list_messages"},
    "memo_tool": {"guidelines", "check"},
}


def system_prompt() -> str:
    """The boss's prompt plus every helper's prompt file, so it can hold them to it."""
    helper_prompts = "\n\n".join(
        f"---\n## Prompt file: {name}.md\n\n{load_prompt(name)}" for name in HELPERS
    )
    return f"{load_prompt(NAME)}\n\n# Helper prompt files\n\n{helper_prompts}"


def build_prioritize(mcp: MCPToolset) -> Agent[Deps, PriorityReview]:
    return build_agent(
        NAME, mcp, READ_POLICY, PriorityReview,
        "PRIORITY STEP. New tickets just came in. Following 'Ticket order and urgent tickets' in "
        "your prompt, decide whether any of them (or, in light of them, any ticket already in the "
        "queue) is urgent enough to move to the top. Most of the time none are.",
        system_prompt=system_prompt(),
    )


def prioritize_prompt(new_tickets: list[dict], queue: list[dict], desk: dict) -> str:
    return (
        f"Desk date: {as_json(desk)}\n\n"
        f"Newly added tickets (not yet prioritized):\n{as_json(new_tickets)}\n\n"
        f"The rest of the open queue, in current working order:\n{as_json(queue)}"
    )


def build_triage(mcp: MCPToolset) -> Agent[Deps, Triage]:
    return build_agent(
        NAME, mcp, {}, Triage,
        "ROUTING STEP. Pick the helper agent that should pick up this ticket, per section 1 of "
        "your prompt. If the ticket shows the business date has changed, set new_desk_date (see "
        "'The desk date' in your prompt). If changing the date is all the ticket needs, set agent "
        "to null.",
        system_prompt=system_prompt(),
    )


def build_review(mcp: MCPToolset) -> Agent[Deps, BossReview]:
    return build_agent(
        NAME, mcp, READ_POLICY, BossReview,
        "SIGN-OFF STEP. Review the helper's proposal against its prompt file and the shop rules, "
        "check the numbers against the tool outputs shown (use your read-only tools if you need "
        "more), and return your decision. Approve only actions that are correct and in scope. The "
        "economic_impact you confirm is sent to accounting automatically and applied to cash right "
        "after the approved actions run, so never use route_to to ask accounting to apply it. Use "
        "route_to only for separate work another agent owns. Your tools are read-only: close the "
        "ticket through ticket_outcome and raise flags through your `flags` output.",
        system_prompt=system_prompt(),
    )


def build_hourly(mcp: MCPToolset) -> Agent[Deps, HourlyReview]:
    return build_agent(
        NAME, mcp, READ_POLICY, HourlyReview,
        "HOURLY REVIEW STEP. Do the look-over in section 8 of your prompt using the snapshot "
        "provided. Don't repeat anything already in open_flags. assigned_agent shows the hand-off "
        "chain (e.g. 'inventory > accounting'). Expect nothing to flag most of the time.",
        system_prompt=system_prompt(),
    )


def triage_prompt(ticket: dict) -> str:
    return f"New ticket in the queue:\n{as_json(ticket)}"


def review_prompt(ticket: dict, agent: str, proposal: Proposal, tool_log: list[dict], cash: dict, history: list[str]) -> str:
    earlier = "\n".join(f"- {h}" for h in history) or "- none"
    return (
        f"Ticket:\n{as_json(ticket)}\n\n"
        f"Earlier steps on this ticket:\n{earlier}\n\n"
        f"Current cash (account_tool read):\n{as_json(cash)}\n\n"
        f"Everything the team did on this ticket since your last sign-off, in order. Each entry names the "
        f"agent that did it: MCP tool calls (with results; 'blocked' means the permission layer stopped it "
        f"and nothing ran) and agent-to-agent consultations ('consulted', with the question asked and the "
        f"answer given):\n{as_json(tool_log)}\n\n"
        f"The {agent} agent's proposal (actions are indexed from 0):\n{as_json(proposal)}"
    )


def hourly_prompt(snapshot: dict) -> str:
    return f"System snapshot for the hourly review:\n{as_json(snapshot)}"
