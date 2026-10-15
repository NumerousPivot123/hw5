"""Facilities agent: the lease and rent payments."""

from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset

from ..models import Deps, ExecutionReport, Policy, Proposal
from .base import build_agent

NAME = "facilities"

PLAN_POLICY: Policy = {"rent_tool": {"check"}}
EXECUTE_POLICY: Policy = {"rent_tool": {"pay_rent"}}


def build_plan(mcp: MCPToolset) -> Agent[Deps, Proposal]:
    return build_agent(
        NAME, mcp, PLAN_POLICY, Proposal,
        "PLANNING STEP. Your tools are read-only here. Check the lease with rent_tool ('check'), "
        "confirm it matches the ticket, and return your proposal for the boss. A pay_rent action's "
        "economic_impact is minus the monthly rent.",
    )


def build_execute(mcp: MCPToolset) -> Agent[Deps, ExecutionReport]:
    return build_agent(
        NAME, mcp, EXECUTE_POLICY, ExecutionReport,
        "EXECUTION STEP. The boss approved the actions listed in the message. Make exactly those "
        "tool calls with the approval ids given, nothing more, then report each result.",
    )
