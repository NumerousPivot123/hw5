"""Data types the Campus Customs agent team passes around."""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai.mcp import MCPToolset

from .audit import Audit

HelperName = Literal["inventory", "accounting", "facilities", "customer_service"]
AgentName = Literal["boss", "inventory", "accounting", "facilities", "customer_service"]

# Database changes a helper can ask the boss to approve, and the helper that owns each.
ActionKind = Literal[
    "fulfill_order", "pay_rent", "pay_invoice", "update_invoice_status", "save_memo"
]
ACTION_OWNER: dict[str, HelperName] = {
    "fulfill_order": "inventory",
    "pay_rent": "facilities",
    "pay_invoice": "accounting",
    "update_invoice_status": "accounting",
    "save_memo": "customer_service",
}

# Payments the agents only prepare: after the boss signs off, a human must click Approve.
HUMAN_APPROVAL_ACTIONS = ("pay_rent", "pay_invoice")

FlagKind = Literal["needs_human", "scope", "optimization", "safety"]

# MCP tool name -> actions an agent may run in a step (None = the tool has no action argument).
Policy = dict[str, set[str] | None]


@dataclass
class Deps:
    """What every agent run gets: the audit trail, the MCP connection, and the ticket."""

    audit: Audit
    mcp: MCPToolset
    ticket_id: int | None = None
    # Tool calls made by helpers in the current step, shown to the boss for checking.
    tool_log: list[dict] = field(default_factory=list)


class Triage(BaseModel):
    """The boss's routing decision for a new ticket."""

    agent: HelperName | None = Field(
        description="Helper agent that should pick up the ticket. Null only if the ticket is just a "
        "date change the boss handles itself."
    )
    reason: str = Field(description="One sentence on why this agent is the right fit.")
    new_desk_date: str | None = Field(
        None, description="YYYY-MM-DD if this ticket shows the business date has moved forward."
    )
    date_change_reason: str | None = Field(None, description="What in the ticket shows the date change.")


class UrgentTicket(BaseModel):
    ticket_id: int
    reason: str = Field(description="Why this can't wait its turn in the queue.")


class PriorityReview(BaseModel):
    """The boss's urgency check on newly added tickets."""

    urgent: list[UrgentTicket] = Field(
        default_factory=list,
        description="Open tickets (new, or already queued) to move to the top of the queue. Usually "
        "empty: tickets normally run in the order they were added.",
    )


class ProposedAction(BaseModel):
    """One database change a helper wants the boss to approve."""

    kind: ActionKind
    description: str = Field(description="What the change does, in one sentence.")
    economic_impact: float = Field(
        0.0, description="Cash impact of this action: + money in, - money out, 0 if none."
    )
    invoice_id: int | None = Field(None, description="For pay_invoice / update_invoice_status.")
    new_status: Literal["open", "paid", "disputed", "cancelled"] | None = Field(
        None, description="For update_invoice_status."
    )
    message_recipient: str | None = Field(None, description="For save_memo: the customer.")
    message_body: str | None = Field(None, description="For save_memo: the full memo, written with memo_tool.")


class Proposal(BaseModel):
    """A helper's report to the boss: what it found and what it wants to do."""

    summary: str = Field(description="The facts, with numbers from tool outputs.")
    decision: str = Field(description="What you decided and why.")
    actions: list[ProposedAction] = Field(
        default_factory=list, description="Database changes to approve. Empty if none."
    )
    economic_impact: float = Field(description="Total cash impact if every action is approved.")
    revenue: float | None = Field(None, description="Sale revenue (list_price x qty), if a sale.")
    total_profit: float | None = Field(None, description="Total profit, if a sale or offer.")
    concerns: list[str] = Field(default_factory=list, description="Problems the boss should know about.")
    recommend_route_to: HelperName | None = Field(
        None,
        description="Another agent that should handle related work on this ticket, if any. With no "
        "actions proposed, the ticket goes straight to that agent, and the boss reviews the whole "
        "chain at the end.",
    )


class FlagNote(BaseModel):
    kind: FlagKind
    message: str = Field(description="Specific, actionable description of the issue.")


class BossReview(BaseModel):
    """The boss's sign-off decision on a helper's proposal."""

    decision: Literal["approve", "reject", "ask_agent"]
    question_for_agent: str | None = Field(None, description="Required when decision is ask_agent.")
    approved_actions: list[int] = Field(
        default_factory=list, description="Indexes (0-based) of proposed actions you approve."
    )
    economic_impact: float = Field(
        description="Cash impact you confirm for the approved actions (+ in, - out, 0 if none)."
    )
    impact_reasoning: str = Field(description="How you checked the economic impact.")
    message_review: str | None = Field(
        None, description="Your check of any customer message (accuracy, tone, on-topic)."
    )
    route_to: HelperName | None = Field(
        None,
        description="Send separate follow-up work on this ticket to another agent, or send it back "
        "once to an agent that already worked it so it can revise (put what to change in "
        "route_instructions). Not for cash updates: the confirmed economic_impact is applied by "
        "accounting automatically.",
    )
    route_instructions: str | None = Field(None, description="What that agent should look at.")
    new_desk_date: str | None = Field(
        None, description="YYYY-MM-DD if information in this work shows the business date has moved forward."
    )
    date_change_reason: str | None = Field(None, description="What shows the date change.")
    ticket_outcome: Literal["complete", "waiting", "flag"] = Field(
        description="complete = archive; waiting = blocked on outside event; flag = needs a human."
    )
    resolution: str = Field(description="One-paragraph resolution note for the ticket.")
    flags: list[FlagNote] = Field(
        default_factory=list, description="Only real, specific issues. Usually empty."
    )


class ExecutionReport(BaseModel):
    """A helper's report after carrying out boss-approved actions."""

    done: list[str] = Field(description="Each approved action carried out, with the tool result.")
    problems: list[str] = Field(default_factory=list, description="Anything that failed.")


class HourlyReview(BaseModel):
    """The boss's periodic look-over of the whole system."""

    open_rent_tickets_for_leases: list[int] = Field(
        default_factory=list,
        description="Lease ids whose rent is due today or overdue with no open rent ticket.",
    )
    flags: list[FlagNote] = Field(
        default_factory=list, description="Only real, specific issues. Usually empty."
    )
    summary: str = Field(description="Short summary of the review.")
