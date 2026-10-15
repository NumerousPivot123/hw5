"""Inventory agent: stock checks, order fulfillment, stock questions from customer service."""

from pydantic_ai import Agent, RunContext
from pydantic_ai.mcp import MCPToolset

from ..models import Deps, ExecutionReport, Policy, Proposal
from . import accounting
from .base import build_agent, run_agent

NAME = "inventory"

PLAN_POLICY: Policy = {"order_tool": {"check"}, "customer_service_tool": {"search"}}
EXECUTE_POLICY: Policy = {"order_tool": {"fulfill"}}
CONSULT_POLICY: Policy = {"order_tool": {"check"}, "customer_service_tool": {"search"}}


async def ask_accounting_agent(ctx: RunContext[Deps], question: str) -> str:
    """Ask the Accounting agent whether there are outstanding invoices for an
    item (e.g. a restock already ordered). Returns its answer."""
    deps = ctx.deps
    deps.audit.log("agent_message", NAME, deps.ticket_id, to="accounting", message=question)
    result = await run_agent(
        accounting.build_consult(deps.mcp),
        f"Ticket {deps.ticket_id}. The inventory agent asks: {question}",
        deps, ctx.usage, "accounting",
    )
    deps.audit.log("agent_message", "accounting", deps.ticket_id, to=NAME, message=result.output)
    # The boss reviews this exchange along with the tool calls.
    deps.tool_log.append({"agent": NAME, "consulted": "accounting", "question": question, "answer": result.output})
    return result.output


def build_plan(mcp: MCPToolset) -> Agent[Deps, Proposal]:
    return build_agent(
        NAME, mcp, PLAN_POLICY, Proposal,
        "PLANNING STEP. Your tools are read-only here. Investigate the ticket with order_tool "
        "(action 'check'). Propose a fulfill_order action only if the order can be filled from "
        "stock. If it can't, ask the accounting agent (ask_accounting_agent) whether there is an "
        "outstanding invoice for the item; if there is, propose no actions and set "
        "recommend_route_to 'customer_service' so it prepares restock-delay outreach to the "
        "customer, with the invoice details in your summary.",
        tools=(ask_accounting_agent,),
    )


def build_execute(mcp: MCPToolset) -> Agent[Deps, ExecutionReport]:
    return build_agent(
        NAME, mcp, EXECUTE_POLICY, ExecutionReport,
        "EXECUTION STEP. The boss approved the actions listed in the message. Make exactly those "
        "tool calls with the approval ids given, nothing more, then report each result.",
    )


def build_consult(mcp: MCPToolset) -> Agent[Deps, str]:
    return build_agent(
        NAME, mcp, CONSULT_POLICY, str,
        "CONSULT STEP. The customer service agent is asking you a stock question. Look it up with "
        "order_tool ('check') for an item on the ticket or customer_service_tool ('search') for "
        "general questions, and answer in a few plain sentences with quantities by size, shortfall, "
        "location, list price, and restock vendor and lead time where relevant.",
    )
