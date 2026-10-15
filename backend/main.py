"""Campus Customs backend API (FastAPI).

Start it from the backend folder, with the HW 5 virtual environment active:
    uvicorn main:app --reload --port 8000
(or without activating: ..\\.venv\\Scripts\\uvicorn main:app --reload --port 8000)

Interactive docs: http://localhost:8000/docs

Everything the agents read or change goes through the Campus Customs MCP server
(started with the app, as configured in .mcp.json). Only /reset touches the
database file directly: it's a human admin action, not an agent one.
"""

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # so `uvicorn main:app` works from the backend folder
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from dotenv import load_dotenv  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from backend.events import summarize  # noqa: E402
from backend.team import Team, reset_database  # noqa: E402

load_dotenv(ROOT / ".env")

team: Team
busy = asyncio.Lock()  # one agent run (or reset) at a time


@asynccontextmanager
async def lifespan(_: FastAPI):
    global team
    async with Team() as t:
        team = t
        yield


app = FastAPI(title="Campus Customs agent team", version="1.0", lifespan=lifespan)
# The Vite dev page (npm run dev) is allowed to call these routes from the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class HumanDecision(BaseModel):
    approver: str = "human"
    reason: str | None = None


def ticket_view(t: dict) -> dict:
    return {
        "id": t["id"],
        "type": t["type"],
        "requester": t["requester"],
        "subject": t["subject"],
        "state": "resolved" if t["status"] == "complete" else "open",
        "status": t["status"],
        "priority": t.get("priority"),
        "assigned_agent": t.get("assigned_agent"),
        "resolution": t.get("resolution"),
    }


async def all_tickets() -> list[dict]:
    return (await team.call("ticket_tool", {"action": "list"}, log=False))["tickets"]


async def get_ticket(ticket_id: int) -> dict:
    for t in await all_tickets():
        if t["id"] == ticket_id:
            return t
    raise HTTPException(404, f"Ticket {ticket_id} not found.")


async def checking() -> dict:
    accounts = (await team.call("account_tool", {"action": "read"}, log=False))["accounts"]
    return next(a for a in accounts if a["name"] == "checking")


def ensure_idle() -> None:
    if busy.locked():
        raise HTTPException(409, "The agent team is busy with another run. Try again when it finishes.")


# ---- tickets -----------------------------------------------------------------

@app.get("/tickets", summary="All tickets, and whether each is open or resolved")
async def list_tickets() -> list[dict]:
    return [ticket_view(t) for t in await all_tickets()]


@app.get("/tickets/{ticket_id}", summary="One ticket, and whether it is open or resolved")
async def read_ticket(ticket_id: int) -> dict:
    return ticket_view(await get_ticket(ticket_id))


@app.post("/tickets/{ticket_id}/run", summary="Run the agent team on one ticket")
async def run_ticket(ticket_id: int) -> dict:
    ticket = await get_ticket(ticket_id)
    if ticket["status"] != "open":
        reasons = {
            "complete": "it is already resolved",
            "flagged": "it is flagged; resolve its flag first",
            "waiting": "it is waiting (on a customer, a restock or a payment approval)",
            "in_progress": "it is already being worked",
        }
        raise HTTPException(409, f"Ticket {ticket_id} can't be run: {reasons.get(ticket['status'], ticket['status'])}.")
    ensure_idle()
    async with busy:
        await team.process_ticket(ticket_id)
    activity = [e for e in summarize(team.audit.load())["timeline"] if e["ticket_id"] == ticket_id]
    return {"ticket": ticket_view(await get_ticket(ticket_id)), "activity": activity}


@app.post("/run", summary="Run the agent team on every open ticket (urgent first, then in order added)")
async def run_all() -> dict:
    ensure_idle()
    async with busy:
        processed = await team.run_pending()
    return {"processed": processed, "tickets": [ticket_view(t) for t in await all_tickets()]}


# ---- activity ----------------------------------------------------------------

@app.get("/events", summary="Recent activity: what each agent has been doing and which tools it used")
async def events(limit: int = 300) -> dict:
    return summarize(team.audit.load(), limit)


# ---- payments a human approves -------------------------------------------------

@app.get("/payments/pending", summary="Payments the boss signed off on that are waiting for a human")
async def pending_payments() -> list[dict]:
    return await team.pending_payments()


@app.post("/payments/{approval_id}/approve", summary="A human clicks Approve: the payment is made and cash updated")
async def approve_payment(approval_id: int, decision: HumanDecision | None = None) -> dict:
    ensure_idle()
    async with busy:
        try:
            return await team.approve_payment(approval_id, (decision or HumanDecision()).approver)
        except LookupError as error:
            raise HTTPException(404, str(error)) from error


@app.post("/payments/{approval_id}/reject", summary="A human clicks Reject: nothing is paid and the ticket goes back on the ticket list")
async def reject_payment(approval_id: int, decision: HumanDecision | None = None) -> dict:
    decision = decision or HumanDecision()
    try:
        return await team.reject_payment(approval_id, decision.approver, decision.reason)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


# ---- cash ----------------------------------------------------------------------

@app.get("/cash", summary="Current checking balance")
async def cash() -> dict:
    return await checking()


# ---- database state --------------------------------------------------------------

@app.get("/database", summary="Current state of the database: date, cash, inventory, invoices, payments, leases, memos")
async def database() -> dict:
    invoices = await team.call("payment_invoice_tool", {"action": "read"}, log=False)
    leases = await team.call("rent_tool", {"action": "check"}, log=False)
    return {
        "desk": await team.call("desk_tool", {"action": "read"}, log=False),
        "cash_accounts": (await team.call("account_tool", {"action": "read"}, log=False))["accounts"],
        "inventory": (await team.call("customer_service_tool", {"action": "search"}, log=False))["matches"],
        "invoices": invoices["invoices"],
        "payments": invoices["payments"],
        "leases": leases["leases"],
        "memos": (await team.call("customer_service_tool", {"action": "list_messages"}, log=False))["messages"],
    }


# ---- flags (the Resolved button) -------------------------------------------------

@app.get("/flags", summary="Open flags for a human")
async def flags(include_resolved: bool = False) -> list[dict]:
    return (await team.call("flag_tool", {"action": "list", "include_resolved": include_resolved}, log=False))["flags"]


@app.post("/flags/{flag_id}/resolve", summary="The Resolved button: clears a flag and puts its ticket back in the queue")
async def resolve_flag(flag_id: int) -> dict:
    return await team.call("flag_tool", {"action": "resolve", "flag_id": flag_id}, "human")


# ---- reset ---------------------------------------------------------------------

@app.post("/reset", summary="Reset campus_customs_new.db to the original data for a fresh run")
async def reset() -> dict:
    ensure_idle()
    async with busy:
        reset_database()
        team.audit.log("database_reset", "human")
    return {"reset": True, "tickets": [ticket_view(t) for t in await all_tickets()], "checking": await checking()}
