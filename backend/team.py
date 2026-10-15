"""Runs the agent team over the ticket queue.

Per ticket: the boss routes it, the helper investigates and proposes, the boss
signs off, the helper carries out exactly the approved changes, accounting
applies the boss-confirmed cash impact, and the boss closes the ticket. Every
database read and write goes through the Campus Customs MCP server.

Payments (rent, vendor invoices) are only prepared: after the boss signs off
they wait for a human to click Approve, and only then is the payment made and
cash updated (approve_payment).

Each ticket is capped at $0.50 of model usage and 1 minute. A ticket that hits
(or is on track to hit) a limit, or errors, is flagged and the team moves on.
Resolving the flag puts the ticket back in the queue.
"""

import asyncio
import json
import shutil
import time
from typing import Any

from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.usage import RunUsage

from .agents import HELPERS, boss
from .agents.base import (
    COST_WARNING, ROOT, TICKET_COST_LIMIT, TICKET_TIME_LIMIT, TIME_WARNING, as_json, connect_mcp, run_agent,
)
from .audit import Audit
from .models import ACTION_OWNER, HUMAN_APPROVAL_ACTIONS, BossReview, Deps, Proposal, ProposedAction

MAX_HANDOFFS = 4  # helper turns on one ticket (including one send-back per helper)
MAX_QUESTIONS = 2  # times the boss can send a proposal back with a question
POLL_SECONDS = 15
REVIEW_SECONDS = 3600


def reset_database() -> None:
    """Restore campus_customs_new.db from the untouched original for a fresh run."""
    shutil.copyfile(ROOT / "data" / "campus_customs.db", ROOT / "data" / "campus_customs_new.db")


class TicketProblem(Exception):
    """A ticket can't be finished and needs a human to look at it."""


class Team:
    def __init__(self) -> None:
        self.audit = Audit()
        self.mcp = connect_mcp()
        self.last_review = 0.0

    async def __aenter__(self) -> "Team":
        await self.mcp.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.mcp.__aexit__(*exc)

    async def call(self, tool: str, args: dict, agent: str = "runner", ticket_id: int | None = None, log: bool = True) -> Any:
        """Call an MCP tool directly to carry out a decision an agent returned."""
        result = await self.mcp.direct_call_tool(tool, args)
        if log:
            self.audit.log("mcp_call", agent, ticket_id, tool=tool, args=args, result=result)
        return result

    # ---- the queue -----------------------------------------------------------

    async def open_tickets(self) -> list[dict]:
        """Open tickets in working order: urgent first, then in the order they were added."""
        result = await self.call("ticket_tool", {"action": "list", "status_filter": ["open"]}, log=False)
        return result["tickets"]

    async def run_pending(self) -> int:
        """Work the queue one ticket at a time. Before each ticket the boss checks
        any newly added tickets for urgency, so an urgent one that arrives mid-run
        jumps ahead. Returns how many tickets were processed."""
        done: set[int] = set()
        while True:
            await self.prioritize_new()
            queue = [t for t in await self.open_tickets() if t["id"] not in done]
            if not queue:
                return len(done)
            done.add(queue[0]["id"])
            await self.process_ticket(queue[0]["id"])

    async def prioritize_new(self) -> None:
        """The boss looks over newly added tickets and moves any urgent ones to
        the top. Everything else keeps the order it came in."""
        queue = await self.open_tickets()
        new = [t for t in queue if t.get("priority") is None]
        if not new:
            return
        usage = RunUsage()
        deps = Deps(self.audit, self.mcp, None)
        urgent: dict[int, str] = {}
        try:
            async with asyncio.timeout(TICKET_TIME_LIMIT):
                desk = await self.call("desk_tool", {"action": "read"}, "boss")
                rest = [t for t in queue if t.get("priority") is not None]
                review = (await run_agent(
                    boss.build_prioritize(self.mcp), boss.prioritize_prompt(new, rest, desk), deps, usage, "boss",
                )).output
                open_ids = {t["id"] for t in queue}
                urgent = {u.ticket_id: u.reason for u in review.urgent if u.ticket_id in open_ids}
        except Exception as error:  # noqa: BLE001 - fall back to the order tickets came in
            message = (
                f"Having difficulty checking new tickets for urgency: {type(error).__name__}: {error}. "
                f"They were kept in the order they came in. Fix the cause, then press Resolved."
            )
            await self.call("flag_tool", {"action": "raise", "kind": "ticket_failure", "message": message}, "runner")
        # New tickets get a priority; an already-queued ticket only changes if the boss bumps it up.
        for t in queue:
            if t["id"] in urgent and t.get("priority") != "urgent":
                priority, reason = "urgent", urgent[t["id"]]
            elif t.get("priority") is None:
                priority, reason = "normal", "Not urgent: runs in the order it came in."
            else:
                continue
            await self.call("ticket_tool", {
                "action": "set_priority", "ticket_id": t["id"], "priority": priority, "priority_reason": reason,
            }, "boss", t["id"])

    async def watch(self) -> None:
        """Pick up new tickets as they arrive and do the boss's review every hour."""
        self.audit.log("watch_start", "runner", poll_seconds=POLL_SECONDS, review_seconds=REVIEW_SECONDS)
        while True:
            await self.run_pending()
            if time.monotonic() - self.last_review >= REVIEW_SECONDS:
                await self.hourly_review()
            await asyncio.sleep(POLL_SECONDS)

    # ---- one ticket ----------------------------------------------------------

    async def process_ticket(self, ticket_id: int) -> None:
        usage = RunUsage()
        start = time.monotonic()
        deps = Deps(self.audit, self.mcp, ticket_id)
        self.audit.log("ticket_start", "boss", ticket_id)
        try:
            async with asyncio.timeout(TICKET_TIME_LIMIT):
                await self._work_ticket(ticket_id, deps, usage, start)
        except TimeoutError:
            await self._fail(ticket_id, f"took longer than the {TICKET_TIME_LIMIT:.0f}-second limit and was stopped")
        except UsageLimitExceeded as error:
            await self._fail(ticket_id, f"hit the ${TICKET_COST_LIMIT} token budget ({error})")
        except TicketProblem as error:
            await self._fail(ticket_id, str(error))
        except Exception as error:  # noqa: BLE001 - any failure is flagged so the queue keeps moving
            await self._fail(ticket_id, f"hit an error: {type(error).__name__}: {error}")
        finally:
            self.audit.log(
                "ticket_end", "boss", ticket_id,
                cost=str(usage.cost), requests=usage.requests, seconds=round(time.monotonic() - start, 1),
            )

    def _check_budget(self, usage: RunUsage, start: float) -> None:
        """Stop early when a ticket is on track to break a limit."""
        elapsed = time.monotonic() - start
        if usage.cost >= COST_WARNING:
            raise TicketProblem(f"is on track to exceed the ${TICKET_COST_LIMIT} token budget (${usage.cost} used)")
        if elapsed >= TIME_WARNING:
            raise TicketProblem(f"is on track to exceed the {TICKET_TIME_LIMIT:.0f}-second limit ({elapsed:.0f}s used)")

    async def _fail(self, ticket_id: int, reason: str) -> None:
        message = (
            f"Having difficulty with ticket {ticket_id}: it {reason}. The team moved on to the next "
            f"ticket. Fix the cause, then press Resolved to put the ticket back in the queue."
        )
        self.audit.log("ticket_failed", "runner", ticket_id, reason=reason)
        await self.call("flag_tool", {"action": "raise", "kind": "ticket_failure", "message": message, "ticket_id": ticket_id}, "runner", ticket_id)

    async def _work_ticket(self, ticket_id: int, deps: Deps, usage: RunUsage, start: float) -> None:
        ticket = (await self.call("ticket_tool", {"action": "get", "ticket_id": ticket_id}, "boss", ticket_id))["ticket"]

        triage = (await run_agent(boss.build_triage(self.mcp), boss.triage_prompt(ticket), deps, usage, "boss")).output
        if triage.new_desk_date:
            await self._set_desk_date(ticket_id, triage.new_desk_date, triage.date_change_reason or triage.reason)
        if triage.agent is None:
            resolution = f"Handled by the boss: {triage.reason}"
            await self.call("ticket_tool", {"action": "complete", "ticket_id": ticket_id, "resolution": resolution}, "boss", ticket_id)
            return
        await self.call("ticket_tool", {"action": "assign", "ticket_id": ticket_id, "agent": triage.agent}, "boss", ticket_id)
        self.audit.log("agent_message", "boss", ticket_id, to=triage.agent, message=triage.reason)

        agent_name, instructions = triage.agent, triage.reason
        handled: list[str] = []
        sent_back: set[str] = set()  # helpers the boss has already sent this ticket back to
        history: list[str] = []
        reviews: list[BossReview] = []

        deps.tool_log = []
        sender = "the boss"
        awaiting: list[dict] = []  # boss-approved payments waiting for a human
        for _ in range(MAX_HANDOFFS + 1):
            self._check_budget(usage, start)
            handled.append(agent_name)
            helper = HELPERS[agent_name]
            plan_agent = helper.build_plan(self.mcp)
            result = await run_agent(
                plan_agent,
                f"Ticket:\n{as_json(ticket)}\n\nFrom {sender}: {instructions}",
                deps, usage, agent_name,
            )
            proposal: Proposal = result.output
            messages = result.all_messages()

            # Nothing to approve yet and the helper says another agent should take it from
            # here (e.g. inventory -> customer service for restock-delay outreach): hand it
            # straight over. The boss reviews the whole chain once that agent reports.
            nxt = proposal.recommend_route_to
            if not proposal.actions and nxt and nxt not in handled and len(handled) < MAX_HANDOFFS:
                history.append(f"{agent_name}: {proposal.summary} Decision: {proposal.decision} Handed to {nxt}.")
                instructions = f"{proposal.decision}\n\nWhat {agent_name} found: {proposal.summary}"
                self.audit.log("agent_message", agent_name, ticket_id, to=nxt, message=instructions)
                await self.call("ticket_tool", {"action": "assign", "ticket_id": ticket_id, "agent": " > ".join(handled + [nxt])}, agent_name, ticket_id)
                sender, agent_name = f"the {agent_name} agent", nxt
                continue

            for asked in range(MAX_QUESTIONS + 1):
                self._check_budget(usage, start)
                cash = await self.call("account_tool", {"action": "read"}, "boss", ticket_id)
                review = (await run_agent(
                    boss.build_review(self.mcp),
                    boss.review_prompt(ticket, agent_name, proposal, deps.tool_log, cash, history),
                    deps, usage, "boss",
                )).output
                if review.decision != "ask_agent" or asked == MAX_QUESTIONS or not review.question_for_agent:
                    break
                self.audit.log("agent_message", "boss", ticket_id, to=agent_name, message=review.question_for_agent)
                result = await run_agent(
                    plan_agent,
                    f"The boss asks: {review.question_for_agent}\nAnswer and return your updated proposal.",
                    deps, usage, agent_name, message_history=messages,
                )
                proposal, messages = result.output, result.all_messages()

            reviews.append(review)
            self.audit.log("boss_decision", "boss", ticket_id, helper=agent_name, review=review.model_dump())
            if review.new_desk_date:
                await self._set_desk_date(ticket_id, review.new_desk_date, review.date_change_reason or review.resolution)
            executed = []
            cash_note = ""
            if review.decision == "approve":
                executed, prepared = await self._execute(ticket_id, agent_name, proposal, review, deps, usage, start)
                awaiting.extend(prepared)
                # Payments wait for a human, so their cash impact is applied when the human approves.
                amount = review.economic_impact - sum(a["economic_impact"] for a in prepared)
                if executed and abs(amount) >= 0.005:
                    cash_note = await self._apply_cash(ticket_id, amount, review.impact_reasoning, deps, usage, start)
                elif prepared:
                    cash_note = (
                        f"payment prepared and signed off by the boss; it is made, and cash updated, only after a "
                        f"human approves (approval {', '.join(str(a['id']) for a in prepared)})"
                    )
                elif abs(amount) >= 0.005:
                    self.audit.log(
                        "cash_skipped", "boss", ticket_id,
                        reason="Economic impact given but no database action was carried out.",
                        economic_impact=review.economic_impact,
                    )
            for flag in review.flags:
                await self.call("flag_tool", {"action": "raise", "kind": flag.kind, "message": flag.message, "ticket_id": ticket_id}, "boss", ticket_id)

            history.append(
                f"{agent_name}: {proposal.decision} | boss {review.decision}"
                f"{f', executed {[c[0] for c in executed]}' if executed else ''}"
                f", cash impact {review.economic_impact:+.2f}"
                f"{f'; {cash_note}' if cash_note else ''}"
            )
            deps.tool_log = []
            # The boss can hand the ticket to a new agent, or send it back once to an agent
            # that already worked it so it can revise (e.g. rewrite a rejected memo).
            target = review.route_to
            send_back = target in handled
            if target and len(handled) < MAX_HANDOFFS and not (send_back and target in sent_back):
                instructions = review.route_instructions or review.resolution
                if send_back:
                    sent_back.add(target)
                    feedback = review.message_review or review.impact_reasoning
                    instructions = (
                        f"The boss is sending this ticket back to you for one revision.\n{instructions}\n\n"
                        f"The boss's feedback: {feedback}\n\nWhat the team has done so far:\n"
                        + "\n".join(f"- {h}" for h in history)
                    )
                agent_name = target
                sender = "the boss"
                chain = " > ".join(handled + [agent_name])
                await self.call("ticket_tool", {"action": "assign", "ticket_id": ticket_id, "agent": chain}, "boss", ticket_id)
                self.audit.log("agent_message", "boss", ticket_id, to=agent_name, message=instructions)
                continue
            break

        await self._close(ticket_id, reviews, awaiting)

    async def _execute(
        self, ticket_id: int, agent_name: str, proposal: Proposal, review: BossReview,
        deps: Deps, usage: RunUsage, start: float,
    ) -> tuple[list[tuple[str, dict]], list[dict]]:
        """Record the boss's sign-off for each approved action, then have the
        helper carry out exactly those actions through the MCP server.
        Payments are only prepared: they wait for a human to click Approve.
        Returns (calls made now, payment approvals waiting for a human)."""
        chosen = [proposal.actions[i] for i in review.approved_actions if 0 <= i < len(proposal.actions)]
        calls: list[tuple[str, dict]] = []
        prepared: list[dict] = []
        for action in chosen:
            owner = ACTION_OWNER[action.kind]
            if owner != agent_name:
                await self.call("flag_tool", {
                    "action": "raise", "kind": "scope", "ticket_id": ticket_id,
                    "message": f"The {agent_name} agent proposed '{action.kind}' on ticket {ticket_id}, which belongs to {owner}. It was not carried out.",
                }, "boss", ticket_id)
                continue
            impact = -abs(action.economic_impact) if action.kind in HUMAN_APPROVAL_ACTIONS else action.economic_impact
            tool, args = self._tool_call(action, ticket_id, None)
            approval = (await self.call("approval_tool", {
                "action": "request", "ticket_id": ticket_id, "agent": agent_name,
                "approval_action": action.kind, "details": action.description, "economic_impact": impact,
                "call_args": {"tool": tool, "args": args},
            }, agent_name, ticket_id))["approval"]
            approval = (await self.call("approval_tool", {
                "action": "decide", "approval_id": approval["id"], "approve": True, "boss_notes": review.impact_reasoning,
            }, "boss", ticket_id))["approval"]
            if action.kind in HUMAN_APPROVAL_ACTIONS:
                prepared.append(approval)
                self.audit.log(
                    "awaiting_human_approval", agent_name, ticket_id,
                    approval_id=approval["id"], payment=action.kind, amount=impact, details=action.description,
                )
                continue
            calls.append((tool, {**args, "approval_id": approval["id"]}))
        if calls:
            self._check_budget(usage, start)
            await self._run_execution(ticket_id, agent_name, calls, deps, usage)
        return calls, prepared

    @staticmethod
    def _tool_call(action: ProposedAction, ticket_id: int, approval_id: int | None) -> tuple[str, dict]:
        base = {"ticket_id": ticket_id, "approval_id": approval_id}
        if action.kind == "fulfill_order":
            return "order_tool", {**base, "action": "fulfill"}
        if action.kind == "pay_rent":
            return "rent_tool", {**base, "action": "pay_rent"}
        if action.kind == "pay_invoice":
            return "payment_invoice_tool", {**base, "action": "pay_invoice", "invoice_id": action.invoice_id}
        if action.kind == "update_invoice_status":
            return "payment_invoice_tool", {
                **base, "action": "update_invoice_status", "invoice_id": action.invoice_id, "new_status": action.new_status,
            }
        return "memo_tool", {
            **base, "action": "save", "recipient": action.message_recipient, "body": action.message_body,
        }

    async def _run_execution(self, ticket_id: int, agent_name: str, calls: list[tuple[str, dict]], deps: Deps, usage: RunUsage) -> None:
        """The helper makes the approved calls; then confirm every approval was used."""
        listing = "\n".join(f"{i + 1}. {tool} with arguments {json.dumps(args)}" for i, (tool, args) in enumerate(calls))
        deps.tool_log = []
        await run_agent(
            HELPERS[agent_name].build_execute(self.mcp),
            f"Boss-approved actions for ticket {ticket_id}. Make each call exactly as written:\n{listing}",
            deps, usage, agent_name,
        )
        approvals = (await self.call("approval_tool", {"action": "list", "ticket_id": ticket_id}, "boss", ticket_id))["approvals"]
        wanted = {args["approval_id"] for _, args in calls}
        unused = [a for a in approvals if a["id"] in wanted and a["status"] != "used"]
        if unused:
            raise TicketProblem(
                f"had approved changes that weren't carried out ({', '.join(a['action'] for a in unused)})"
            )

    async def _apply_cash(
        self, ticket_id: int, amount: float, reasoning: str, deps: Deps, usage: RunUsage, start: float,
    ) -> str:
        """The boss voices the confirmed economic impact to accounting, which updates cash."""
        self._check_budget(usage, start)
        amount = round(amount, 2)
        self.audit.log(
            "agent_message", "boss", ticket_id, to="accounting",
            message=f"Confirmed economic impact for ticket {ticket_id}: {amount:+.2f}. {reasoning} Update checking.",
        )
        approval = (await self.call("approval_tool", {
            "action": "request", "ticket_id": ticket_id, "agent": "accounting",
            "approval_action": "cash_adjustment", "details": reasoning, "economic_impact": amount,
        }, "accounting", ticket_id))["approval"]
        await self.call("approval_tool", {
            "action": "decide", "approval_id": approval["id"], "approve": True, "boss_notes": "Boss-confirmed economic impact.",
        }, "boss", ticket_id)
        call = ("account_tool", {
            "action": "adjust", "amount": amount, "ticket_id": ticket_id, "approval_id": approval["id"],
            "reason": f"Ticket {ticket_id}: {reasoning}"[:300],
        })
        await self._run_execution(ticket_id, "accounting", [call], deps, usage)
        balance = (await self.call("account_tool", {"action": "read"}, "boss", ticket_id))["accounts"][0]["balance"]
        return f"accounting already applied {amount:+.2f} to checking (now {balance:.2f}); this cash change is done and must not be applied again"

    async def _set_desk_date(self, ticket_id: int | None, new_date: str, reason: str) -> None:
        """The boss moves the business date forward, then checks whether that
        makes any rent due so a rent ticket is opened right away."""
        current = (await self.call("desk_tool", {"action": "read"}, "boss", ticket_id))["date_today"]
        if new_date <= current:
            self.audit.log("desk_date_unchanged", "boss", ticket_id, current=current, proposed=new_date, reason=reason)
            return
        approval = (await self.call("approval_tool", {
            "action": "request", "ticket_id": ticket_id, "agent": "boss",
            "approval_action": "set_desk_date", "details": f"Move desk date {current} -> {new_date}: {reason}",
        }, "boss", ticket_id))["approval"]
        await self.call("approval_tool", {"action": "decide", "approval_id": approval["id"], "approve": True, "boss_notes": reason}, "boss", ticket_id)
        await self.call("desk_tool", {
            "action": "set_date", "new_date": new_date, "reason": reason, "ticket_id": ticket_id, "approval_id": approval["id"],
        }, "boss", ticket_id)
        tickets = (await self.call("ticket_tool", {"action": "list"}, "boss", ticket_id))["tickets"]
        await self._open_due_rent_tickets(tickets, [])

    async def _close(self, ticket_id: int, reviews: list[BossReview], awaiting: list[dict]) -> None:
        """Archive, park or flag the ticket. If any agent's step left it waiting
        or needing a human, or a payment is waiting for a human's Approve, the
        ticket isn't complete."""
        if not reviews:
            raise TicketProblem("ended without a boss decision")
        severity = {"complete": 0, "waiting": 1, "flag": 2}
        outcome = max((r.ticket_outcome for r in reviews), key=severity.__getitem__)
        resolution = " ".join(r.resolution for r in reviews)
        if awaiting:
            outcome = max(outcome, "waiting", key=severity.__getitem__)
            resolution += (
                f" Payment prepared and signed off by the boss; waiting for a human to approve it "
                f"(approval {', '.join(str(a['id']) for a in awaiting)})."
            )
        if outcome == "complete":
            await self.call("ticket_tool", {"action": "complete", "ticket_id": ticket_id, "resolution": resolution}, "boss", ticket_id)
        elif outcome == "waiting":
            await self.call("ticket_tool", {"action": "set_status", "ticket_id": ticket_id, "new_status": "waiting", "resolution": resolution}, "boss", ticket_id)
        else:
            await self.call("ticket_tool", {"action": "set_status", "ticket_id": ticket_id, "new_status": "flagged", "resolution": resolution}, "boss", ticket_id)
            already_flagged = any(f.kind == "needs_human" for r in reviews for f in r.flags)
            if not already_flagged:
                await self.call("flag_tool", {"action": "raise", "kind": "needs_human", "message": f"Ticket {ticket_id}: {reviews[-1].resolution}", "ticket_id": ticket_id}, "boss", ticket_id)

    # ---- payments a human approves ---------------------------------------------

    async def pending_payments(self) -> list[dict]:
        """Payments the boss signed off on that are waiting for a human to click Approve."""
        result = await self.call("approval_tool", {"action": "list", "awaiting_human": True}, log=False)
        return result["approvals"]

    async def _pending_payment(self, approval_id: int) -> dict:
        for a in await self.pending_payments():
            if a["id"] == approval_id:
                return a
        raise LookupError(f"Approval {approval_id} isn't a payment waiting for a human.")

    async def approve_payment(self, approval_id: int, approver: str = "human") -> dict:
        """A human clicked Approve: the preparing agent makes the payment through
        the MCP server, accounting applies its cash impact, and the ticket is
        completed once nothing else is waiting on it."""
        approval = await self._pending_payment(approval_id)
        ticket_id, agent_name = approval["ticket_id"], approval["agent"]
        await self.call("approval_tool", {"action": "human_approve", "approval_id": approval_id, "approver": approver}, "human", ticket_id)
        usage, start = RunUsage(), time.monotonic()
        deps = Deps(self.audit, self.mcp, ticket_id)
        try:
            async with asyncio.timeout(TICKET_TIME_LIMIT):
                prepared = json.loads(approval["call_args"])
                call = (prepared["tool"], {**prepared["args"], "approval_id": approval_id})
                await self._run_execution(ticket_id, agent_name, [call], deps, usage)
                cash_note = await self._apply_cash(
                    ticket_id, approval["economic_impact"],
                    f"{approver} approved the {approval['action']} ({approval['details']}).", deps, usage, start,
                )
                if not [a for a in await self.pending_payments() if a["ticket_id"] == ticket_id]:
                    ticket = (await self.call("ticket_tool", {"action": "get", "ticket_id": ticket_id}, "boss", ticket_id))["ticket"]
                    if ticket["status"] == "waiting":
                        resolution = f"{ticket['resolution'] or ''} Payment approved by {approver} and made; {cash_note}.".strip()
                        await self.call("ticket_tool", {"action": "complete", "ticket_id": ticket_id, "resolution": resolution}, "boss", ticket_id)
        except Exception as error:  # noqa: BLE001 - flag it so a person can look
            await self._fail(ticket_id, f"hit an error making the approved payment (approval {approval_id}): {type(error).__name__}: {error}")
            raise
        finally:
            self.audit.log("payment_end", "runner", ticket_id, approval_id=approval_id, cost=str(usage.cost))
        balance = (await self.call("account_tool", {"action": "read"}, log=False))["accounts"][0]
        return {"approval_id": approval_id, "ticket_id": ticket_id, "paid": approval["action"], "amount": approval["economic_impact"], "checking": balance}

    async def reject_payment(self, approval_id: int, approver: str = "human", reason: str | None = None) -> dict:
        """A human clicked Reject: nothing is paid and the ticket goes back on the
        ticket list (open) so it can be worked again."""
        approval = await self._pending_payment(approval_id)
        ticket_id = approval["ticket_id"]
        await self.call("approval_tool", {"action": "human_reject", "approval_id": approval_id, "approver": approver}, "human", ticket_id)
        note = f"{approver} rejected the prepared {approval['action']} ({approval['details']}); nothing was paid." + (f" Reason: {reason}" if reason else "")
        await self.call("ticket_tool", {"action": "set_status", "ticket_id": ticket_id, "new_status": "open", "resolution": note}, "human", ticket_id)
        return {"approval_id": approval_id, "ticket_id": ticket_id, "rejected": approval["action"], "ticket_status": "open"}

    # ---- the boss's hourly review ---------------------------------------------

    async def hourly_review(self) -> None:
        self.last_review = time.monotonic()
        usage = RunUsage()
        deps = Deps(self.audit, self.mcp, None)
        self.audit.log("review_start", "boss")
        try:
            async with asyncio.timeout(TICKET_TIME_LIMIT):
                await self._review(deps, usage)
        except Exception as error:  # noqa: BLE001
            message = f"Having difficulty with the hourly review: {type(error).__name__}: {error}. Fix the cause, then press Resolved."
            await self.call("flag_tool", {"action": "raise", "kind": "ticket_failure", "message": message}, "runner")
        finally:
            self.audit.log("review_end", "boss", cost=str(usage.cost), requests=usage.requests)

    async def _review(self, deps: Deps, usage: RunUsage) -> None:
        tickets = (await self.call("ticket_tool", {"action": "list"}, "boss"))["tickets"]
        leases = await self.call("rent_tool", {"action": "check"}, "boss")
        flags = (await self.call("flag_tool", {"action": "list"}, "boss"))["flags"]
        snapshot = {
            "tickets": tickets,
            "leases": leases,
            "open_flags": flags,
            "agent_activity": self._activity_summary(flags),
        }
        review = (await run_agent(boss.build_hourly(self.mcp), boss.hourly_prompt(snapshot), deps, usage, "boss")).output

        await self._open_due_rent_tickets(tickets, review.open_rent_tickets_for_leases)

        # Nothing is mid-flight during a review, so any in_progress ticket is stuck.
        for ticket_id in (t["id"] for t in tickets if t["status"] == "in_progress"):
            await self.call("ticket_tool", {"action": "set_status", "ticket_id": ticket_id, "new_status": "open"}, "boss", ticket_id)

        for flag in review.flags:
            await self.call("flag_tool", {"action": "raise", "kind": flag.kind, "message": flag.message}, "boss")

    async def _open_due_rent_tickets(self, tickets: list[dict], boss_leases: list[int]) -> None:
        """Rent due with no ticket: the boss opens one. Checked in code too, so a
        due rent payment is never missed even if the model overlooks it."""
        leases = await self.call("rent_tool", {"action": "check"}, "boss")
        rent_ticket_leases = {
            t["lease_id"] for t in tickets if t["type"] == "rent_notice" and t["status"] != "complete"
        }
        for lease in leases["leases"]:
            if lease["days_until_due"] <= 0 and lease["id"] not in rent_ticket_leases:
                backstop = lease["id"] not in boss_leases
                await self.call("ticket_tool", {
                    "action": "create", "type": "rent_notice", "requester": lease["landlord"],
                    "subject": "Rent due", "lease_id": lease["id"],
                    "notes": f"Opened by the boss: rent of {lease['monthly_rent']:.2f} for "
                             f"{lease['space_name']} is due {lease['next_due']} and no rent ticket came in.",
                }, "boss")
                if backstop:
                    self.audit.log("rent_backstop", "boss", lease_id=lease["id"], note="Rent ticket opened by the runner's check.")

    def _activity_summary(self, open_flags: list[dict], last: int = 400) -> dict:
        """What each agent did recently (tools used, blocked calls, unresolved failures), from the audit trail."""
        entries = self.audit.load()[-last:]
        unresolved = {f["ticket_id"] for f in open_flags if f["kind"] == "ticket_failure"}
        tools: dict[str, dict[str, int]] = {}
        blocked, failures = [], []
        for e in entries:
            if e["event"] == "tool_call":
                key = f"{e['tool']}:{(e.get('args') or {}).get('action', 'default')}"
                tools.setdefault(e["agent"], {}).setdefault(key, 0)
                tools[e["agent"]][key] += 1
            elif e["event"] == "tool_call_blocked":
                blocked.append({"agent": e["agent"], "tool": e.get("tool"), "args": e.get("args"), "ticket_id": e["ticket_id"]})
            elif e["event"] == "ticket_failed" and e["ticket_id"] in unresolved:
                failures.append({"ticket_id": e["ticket_id"], "reason": e.get("reason")})
        return {
            "tool_use_by_agent": tools,
            "blocked_tool_calls": blocked,
            "blocked_tool_calls_note": (
                "Calls the permission layer stopped, as designed; nothing was written. Only an agent "
                "repeatedly trying tools outside its prompt file is worth a scope flag."
            ),
            "unresolved_ticket_failures": failures,
        }
