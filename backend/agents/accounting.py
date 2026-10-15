"""Accounting agent: vendor invoices and payments, and the cash balance."""

from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset

from ..models import Deps, ExecutionReport, Policy, Proposal
from .base import build_agent

NAME = "accounting"

PLAN_POLICY: Policy = {"account_tool": {"read"}, "payment_invoice_tool": {"read"}}
CONSULT_POLICY: Policy = {"payment_invoice_tool": {"read"}}
EXECUTE_POLICY: Policy = {
    "payment_invoice_tool": {"pay_invoice", "update_invoice_status"},
    "account_tool": {"read", "adjust"},
}


def build_plan(mcp: MCPToolset) -> Agent[Deps, Proposal]:
    return build_agent(
        NAME, mcp, PLAN_POLICY, Proposal,
        "PLANNING STEP. Your tools are read-only here. Read the invoice(s) and the cash balance, "
        "and return your proposal for the boss. A pay_invoice action's "
        "economic_impact is minus the invoice amount.",
    )


def build_consult(mcp: MCPToolset) -> Agent[Deps, str]:
    return build_agent(
        NAME, mcp, CONSULT_POLICY, str,
        "CONSULT STEP. The inventory agent is asking whether there are outstanding invoices for an "
        "item. Read the invoices with payment_invoice_tool ('read') and answer in a few plain "
        "sentences: any open invoice whose description matches the item (SKU and size), with its "
        "id, vendor, amount, due date, days overdue and the vendor's lead days, or that there is none.",
    )


def build_execute(mcp: MCPToolset) -> Agent[Deps, ExecutionReport]:
    return build_agent(
        NAME, mcp, EXECUTE_POLICY, ExecutionReport,
        "EXECUTION STEP. The boss approved the actions listed in the message. Make exactly those "
        "tool calls with the approval ids given, nothing more, then report each result (for a cash "
        "adjustment, the balance before and after).",
    )
