"""Customer service agent: price overrides, customer questions, customer messages."""

from pydantic_ai import Agent, RunContext
from pydantic_ai.mcp import MCPToolset

from ..models import Deps, ExecutionReport, Policy, Proposal
from . import inventory
from .base import build_agent, run_agent

NAME = "customer_service"

PLAN_POLICY: Policy = {
    "override_tool": None,
    "customer_service_tool": {"search", "list_messages"},
    "memo_tool": {"guidelines", "check"},
}
EXECUTE_POLICY: Policy = {"memo_tool": {"save"}}


async def ask_inventory_agent(ctx: RunContext[Deps], question: str) -> str:
    """Ask the Inventory agent a stock question, e.g. whether we can supply a
    quantity in a size, or what items of a kind are in stock. Returns its answer."""
    deps = ctx.deps
    deps.audit.log("agent_message", NAME, deps.ticket_id, to="inventory", message=question)
    result = await run_agent(
        inventory.build_consult(deps.mcp),
        f"Ticket {deps.ticket_id}. Customer service asks: {question}",
        deps, ctx.usage, "inventory",
    )
    deps.audit.log("agent_message", "inventory", deps.ticket_id, to=NAME, message=result.output)
    # The boss reviews this exchange along with the tool calls.
    deps.tool_log.append({"agent": NAME, "consulted": "inventory", "question": question, "answer": result.output})
    return result.output


def build_plan(mcp: MCPToolset) -> Agent[Deps, Proposal]:
    return build_agent(
        NAME, mcp, PLAN_POLICY, Proposal,
        "PLANNING STEP. Your tools are read-only here. Confirm stock with ask_inventory_agent first. "
        "If inventory can't supply the full quantity requested, make no offer: write a memo saying the "
        "item is currently not in stock and that we'll keep in touch if anything changes (unless "
        "inventory handed you an order whose restock is already on order: then write the "
        "restock-delay memo). Otherwise "
        "gather the facts (override_tool for price overrides, customer_service_tool 'search' for "
        "questions) and return your proposal for the boss. If outreach to the customer is "
        "needed, write a memo following memo_tool's guidelines, run memo_tool 'check' and fix any "
        "issues, then put the final memo in one save_memo action (economic_impact 0) addressed to "
        "the ticket's requester. For requests unrelated to Campus Customs, propose no actions.",
        tools=(ask_inventory_agent,),
    )


def build_execute(mcp: MCPToolset) -> Agent[Deps, ExecutionReport]:
    return build_agent(
        NAME, mcp, EXECUTE_POLICY, ExecutionReport,
        "EXECUTION STEP. The boss approved the memo listed below. Save it with exactly the tool "
        "call given (it is stored as approved_not_sent; it is never sent), then report the result.",
    )
