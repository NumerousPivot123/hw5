"""Campus Customs MCP server.

Tools that read and update campus_customs_new.db to help us understand and
respond to tickets. The original campus_customs.db is never touched.

Every tool action that changes the database requires the id of an approval the
boss has signed off on (see approval_tool). The server checks the approval
matches the ticket, the action and (where relevant) the amount, then marks it
used so it can't be replayed.
"""

import calendar
import json
import re
import sqlite3
import threading
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from fastmcp import FastMCP

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "campus_customs_new.db"

# Which vendor specialty covers which kind of good, matched on item name keywords.
SPECIALTY_KEYWORDS = {
    "apparel reprint": ["tee", "shirt", "hoodie", "sweatshirt", "cap", "hat", "jacket"],
    "mugs and small goods": ["mug", "cup", "glass", "bottle", "ornament"],
}

# Customer words that should match how items are named in inventory.
SEARCH_SYNONYMS = {
    "sweatshirt": ["hoodie", "sweatshirt", "crewneck"],
    "sweatshirts": ["hoodie", "sweatshirt", "crewneck"],
    "hoodies": ["hoodie"],
    "shirt": ["tee", "shirt"],
    "shirts": ["tee", "shirt"],
    "t-shirt": ["tee"],
    "tees": ["tee"],
    "hat": ["cap", "hat"],
    "hats": ["cap", "hat"],
    "caps": ["cap"],
    "mugs": ["mug"],
}

# Sample discounts used to model a price override.
SAMPLE_DISCOUNTS = [0.05, 0.10, 0.15, 0.20, 0.25]

mcp = FastMCP("campus-customs")


# Payments that also need a human to click Approve after the boss signs off.
HUMAN_APPROVAL_ACTIONS = ("pay_rent", "pay_invoice")

# Tool calls can run at the same time; only one of them should add missing columns.
_schema_lock = threading.Lock()


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _schema_ready(conn: sqlite3.Connection) -> bool:
    return bool(
        conn.execute("SELECT 1 FROM pragma_table_info('tickets') WHERE name = 'priority_reason'").fetchone()
        and conn.execute("SELECT 1 FROM pragma_table_info('approvals') WHERE name = 'human_approved_at'").fetchone()
    )


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Add the agent-team columns and tables to campus_customs_new.db if missing.
    Runs on every connection, so it also repairs the schema after a reset."""
    if _schema_ready(conn):
        return
    with _schema_lock:
        if not _schema_ready(conn):
            _add_schema(conn)


def _add_schema(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(tickets)")}
    for col in ("assigned_agent", "resolution", "completed_at", "priority", "priority_reason"):
        if col not in cols:
            conn.execute(f"ALTER TABLE tickets ADD COLUMN {col} TEXT")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS approvals (
            id INTEGER PRIMARY KEY,
            ticket_id INTEGER,
            agent TEXT NOT NULL,
            action TEXT NOT NULL,
            details TEXT,
            economic_impact REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            boss_notes TEXT,
            created_at TEXT NOT NULL,
            decided_at TEXT,
            used_at TEXT,
            call_args TEXT,
            human_approved_by TEXT,
            human_approved_at TEXT
        );
        CREATE TABLE IF NOT EXISTS flags (
            id INTEGER PRIMARY KEY,
            ticket_id INTEGER,
            kind TEXT NOT NULL,
            message TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            resolved_at TEXT
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            ticket_id INTEGER,
            recipient TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    approval_cols = {r["name"] for r in conn.execute("PRAGMA table_info(approvals)")}
    for col in ("call_args", "human_approved_by", "human_approved_at"):
        if col not in approval_cols:
            conn.execute(f"ALTER TABLE approvals ADD COLUMN {col} TEXT")
    conn.commit()


def get_ticket(conn: sqlite3.Connection, ticket_id: int, expected_type: str | None = None) -> dict:
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if row is None:
        raise ValueError(f"Ticket {ticket_id} not found.")
    if expected_type is not None and row["type"] != expected_type:
        raise ValueError(
            f"Ticket {ticket_id} is type '{row['type']}', but this tool handles '{expected_type}' tickets."
        )
    return dict(row)


def today(conn: sqlite3.Connection) -> date:
    return date.fromisoformat(conn.execute("SELECT date_today FROM desk").fetchone()["date_today"])


def add_month(d: date) -> date:
    year, month = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def use_approval(
    conn: sqlite3.Connection,
    approval_id: int | None,
    ticket_id: int | None,
    action: str,
    amount: float | None = None,
) -> dict:
    """Check the boss approved exactly this change, then mark the approval used."""
    if approval_id is None:
        raise ValueError(f"'{action}' changes the database and needs boss sign-off: pass an approved approval_id.")
    a = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    if a is None:
        raise ValueError(f"Approval {approval_id} not found.")
    if a["status"] != "approved":
        raise ValueError(f"Approval {approval_id} is '{a['status']}', not 'approved'.")
    if a["action"] != action:
        raise ValueError(f"Approval {approval_id} is for '{a['action']}', not '{action}'.")
    if ticket_id is not None and a["ticket_id"] != ticket_id:
        raise ValueError(f"Approval {approval_id} is for ticket {a['ticket_id']}, not ticket {ticket_id}.")
    if amount is not None and abs(a["economic_impact"] - amount) > 0.005:
        raise ValueError(f"Approval {approval_id} is for {a['economic_impact']:+.2f}, not {amount:+.2f}.")
    if action in HUMAN_APPROVAL_ACTIONS and not a["human_approved_at"]:
        raise ValueError(f"Approval {approval_id} is a payment: a human must click Approve before it can be made.")
    conn.execute("UPDATE approvals SET status = 'used', used_at = ? WHERE id = ?", (now(), approval_id))
    return dict(a)


def lookup_item(conn: sqlite3.Connection, sku: str, size: str, qty: int) -> dict:
    """Inventory and pricing for one SKU/size, plus whether we can cover qty."""
    inv = conn.execute(
        "SELECT name, qty, location FROM inventory WHERE sku = ? AND size = ?", (sku, size)
    ).fetchone()
    if inv is None:
        raise ValueError(f"No inventory row for {sku} size {size}.")
    price = conn.execute(
        "SELECT unit_cost, list_price FROM pricing WHERE sku = ?", (sku,)
    ).fetchone()
    if price is None:
        raise ValueError(f"No pricing row for {sku}.")
    return {
        "sku": sku,
        "name": inv["name"],
        "size": size,
        "qty_requested": qty,
        "qty_in_stock": inv["qty"],
        "available": "yes" if inv["qty"] >= qty else "no",
        "shortfall": max(qty - inv["qty"], 0),
        "location": inv["location"],
        "unit_cost": price["unit_cost"],
        "list_price": price["list_price"],
    }


def matching_vendor(conn: sqlite3.Connection, item_name: str) -> dict | None:
    name = item_name.lower()
    for specialty, keywords in SPECIALTY_KEYWORDS.items():
        if any(k in name for k in keywords):
            row = conn.execute(
                "SELECT name, specialty, lead_days FROM vendors WHERE specialty = ?", (specialty,)
            ).fetchone()
            return dict(row) if row else None
    return None


@mcp.tool
def order_tool(
    ticket_id: int,
    action: Literal["check", "fulfill"] = "check",
    approval_id: int | None = None,
) -> dict:
    """Customer order lookup and fulfillment. Used by the inventory agent.

    Tables: tickets, inventory, pricing, vendors, invoices.

    action="check" (read-only): reads the ticket's SKU, size and quantity, then
    checks inventory to see whether the item is in stock (available: yes/no)
    and where it is located. Pulls pricing to return the list price the
    customer expects to pay, our unit cost, and the calculated profit. If we
    can't fill the order from stock, returns the vendor whose specialty matches
    the item so we know who to contact, plus any invoice linked to the ticket.
    Works for any ticket that names an item (customer_order or price_override).

    action="fulfill" (writes inventory): for a customer_order ticket, removes
    the ordered quantity from inventory. Needs an approved 'fulfill_order'
    approval_id for this ticket. Does not change cash; accounting records the
    sale revenue separately with account_tool.

    Helps resolve ticket 101: can we fulfill the order, and what is its impact
    on the business?
    """
    with closing(connect()) as conn:
        t = get_ticket(conn, ticket_id)
        if not (t["sku"] and t["size"] and t["qty"]):
            raise ValueError(f"Ticket {ticket_id} doesn't name an item (sku, size, qty).")
        item = lookup_item(conn, t["sku"], t["size"], t["qty"])
        profit_per_unit = item["list_price"] - item["unit_cost"]

        if action == "fulfill":
            if t["type"] != "customer_order":
                raise ValueError("Only customer_order tickets can be fulfilled.")
            if item["available"] != "yes":
                raise ValueError(f"Can't fulfill: {item['qty_in_stock']} in stock, {t['qty']} ordered.")
            use_approval(conn, approval_id, ticket_id, "fulfill_order")
            conn.execute(
                "UPDATE inventory SET qty = qty - ? WHERE sku = ? AND size = ?",
                (t["qty"], t["sku"], t["size"]),
            )
            conn.commit()
            return {
                "ticket_id": ticket_id,
                "fulfilled": True,
                "sku": t["sku"],
                "size": t["size"],
                "qty": t["qty"],
                "qty_before": item["qty_in_stock"],
                "qty_after": item["qty_in_stock"] - t["qty"],
                "revenue": item["list_price"] * t["qty"],
                "total_profit": profit_per_unit * t["qty"],
                "cash_note": "Cash not changed here; accounting records the revenue with account_tool.",
            }

        result = {
            "ticket_id": t["id"],
            "requester": t["requester"],
            "notes": t["notes"],
            **item,
            "customer_pays": item["list_price"] * t["qty"],
            "profit_per_unit": profit_per_unit,
            "total_profit": profit_per_unit * t["qty"],
            "vendor_to_contact": None,
        }
        if item["available"] == "no":
            result["vendor_to_contact"] = matching_vendor(conn, item["name"])
        if t["invoice_id"] is not None:
            inv = conn.execute(
                "SELECT id, amount, due_date, status, description FROM invoices WHERE id = ?",
                (t["invoice_id"],),
            ).fetchone()
            result["linked_invoice"] = dict(inv) if inv else None
        return result


@mcp.tool
def rent_tool(
    ticket_id: int | None = None,
    action: Literal["check", "pay_rent"] = "check",
    approval_id: int | None = None,
) -> dict:
    """Rent lookup and payment. Used by the facilities agent.

    Tables: tickets, leases, desk, payments.

    action="check" with a rent_notice ticket_id (read-only): returns the space
    we're paying for, the landlord, the monthly rent, the next due date, and any
    lease notes, plus days until rent is due measured from the desk date.
    action="check" with no ticket_id: returns every lease with days until due,
    so we can spot rent coming due even when no ticket has arrived.

    action="pay_rent" (writes payments and leases): for a rent_notice ticket,
    records the monthly rent in payments (kind 'rent', approved_by 'boss') and
    rolls the lease's next_due forward one month. Needs an approved 'pay_rent'
    approval_id for this ticket. Does not change cash; accounting records the
    payment with account_tool.

    Helps resolve ticket 102, which warns rent is due in two days.
    """
    with closing(connect()) as conn:
        d = today(conn)
        if ticket_id is None:
            if action != "check":
                raise ValueError("pay_rent needs a rent_notice ticket_id.")
            leases = []
            for lease in conn.execute("SELECT * FROM leases ORDER BY id"):
                days = (date.fromisoformat(lease["next_due"]) - d).days
                leases.append({**dict(lease), "days_until_due": days, "overdue": days < 0})
            return {"today": d.isoformat(), "leases": leases}

        t = get_ticket(conn, ticket_id, "rent_notice")
        lease = conn.execute("SELECT * FROM leases WHERE id = ?", (t["lease_id"],)).fetchone()
        if lease is None:
            raise ValueError(f"Lease {t['lease_id']} not found.")
        days_until_due = (date.fromisoformat(lease["next_due"]) - d).days

        if action == "pay_rent":
            use_approval(conn, approval_id, ticket_id, "pay_rent", -lease["monthly_rent"])
            cur = conn.execute(
                "INSERT INTO payments (kind, ref_id, amount, account, paid_at, approved_by) "
                "VALUES ('rent', ?, ?, 'checking', ?, 'boss')",
                (lease["id"], lease["monthly_rent"], d.isoformat()),
            )
            new_due = add_month(date.fromisoformat(lease["next_due"])).isoformat()
            note = f"Paid {lease['monthly_rent']:.2f} due {lease['next_due']} on {d.isoformat()} (ticket {ticket_id})."
            conn.execute("UPDATE leases SET next_due = ?, notes = ? WHERE id = ?", (new_due, note, lease["id"]))
            conn.commit()
            return {
                "ticket_id": ticket_id,
                "payment_id": cur.lastrowid,
                "amount": lease["monthly_rent"],
                "paid_at": d.isoformat(),
                "previous_due": lease["next_due"],
                "next_due": new_due,
                "cash_note": "Cash not changed here; accounting records the payment with account_tool.",
            }

        return {
            "ticket_id": t["id"],
            "requester": t["requester"],
            "ticket_notes": t["notes"],
            "space_name": lease["space_name"],
            "landlord": lease["landlord"],
            "monthly_rent": lease["monthly_rent"],
            "next_due": lease["next_due"],
            "lease_notes": lease["notes"],
            "days_until_due": days_until_due,
            "overdue": days_until_due < 0,
        }


@mcp.tool
def override_tool(ticket_id: int, custom_prices: list[float] | None = None) -> dict:
    """Price override economics (read-only). Used by the customer service agent.

    Tables: tickets, inventory, pricing.

    Reads the ticket's SKU, size and quantity, checks inventory availability,
    and returns the standard unit cost, list price and profit. Reads the ticket
    notes for the requested change (e.g. a bulk discount) and models the profit
    at several sample discounted prices (5% to 25% off), showing the change in
    profit versus list price. If the notes name a specific discount (a % or $
    price), that scenario is included too. Pass custom_prices to model specific
    negotiated unit prices.

    Helps decide ticket 103: what would the override actually cost the
    business, and should we accept it?
    """
    with closing(connect()) as conn:
        t = get_ticket(conn, ticket_id, "price_override")
        item = lookup_item(conn, t["sku"], t["size"], t["qty"])
    qty, cost, list_price = t["qty"], item["unit_cost"], item["list_price"]
    standard_profit = (list_price - cost) * qty

    def scenario(price: float, label: str) -> dict:
        profit = (price - cost) * qty
        return {
            "label": label,
            "unit_price": round(price, 2),
            "profit_per_unit": round(price - cost, 2),
            "total_profit": round(profit, 2),
            "change_vs_list": round(profit - standard_profit, 2),
            "margin_pct": round((price - cost) / price * 100, 1),
        }

    scenarios = [scenario(list_price * (1 - d), f"{d:.0%} off") for d in SAMPLE_DISCOUNTS]
    for p in custom_prices or []:
        scenarios.append(scenario(p, f"custom ${p:.2f}"))

    notes = t["notes"] or ""
    pct = re.search(r"(\d+(?:\.\d+)?)\s*%", notes)
    dollars = re.search(r"\$\s*(\d+(?:\.\d+)?)", notes)
    if pct:
        scenarios.insert(0, scenario(list_price * (1 - float(pct.group(1)) / 100), f"requested {pct.group(1)}% off"))
    elif dollars:
        scenarios.insert(0, scenario(float(dollars.group(1)), f"requested ${dollars.group(1)}"))

    return {
        "ticket_id": t["id"],
        "requester": t["requester"],
        "notes": notes,
        "discount_requested": bool(re.search(r"discount|override|cheaper|lower price", notes, re.I)),
        **item,
        "standard_profit_per_unit": list_price - cost,
        "standard_total_profit": standard_profit,
        "break_even_price": cost,
        "scenarios": scenarios,
    }


@mcp.tool
def account_tool(
    action: Literal["read", "adjust"] = "read",
    amount: float | None = None,
    reason: str | None = None,
    ticket_id: int | None = None,
    approval_id: int | None = None,
    account: str = "checking",
) -> dict:
    """Cash account lookup and balance updates. Used by the accounting agent.

    Tables: cash_accounts, desk.

    action="read" (read-only): returns every cash account with its balance and
    as-of date.

    action="adjust" (writes cash_accounts): adds amount (negative for money
    out, positive for money in) to the account balance and sets its date to the
    desk date. Needs an approved 'cash_adjustment' approval_id whose ticket and
    economic_impact match ticket_id and amount exactly. Refuses any change that
    would take the balance below zero.
    """
    with closing(connect()) as conn:
        if action == "read":
            return {"accounts": [dict(r) for r in conn.execute("SELECT * FROM cash_accounts")]}
        if amount is None:
            raise ValueError("adjust needs an amount.")
        row = conn.execute("SELECT * FROM cash_accounts WHERE name = ?", (account,)).fetchone()
        if row is None:
            raise ValueError(f"Cash account '{account}' not found.")
        new_balance = round(row["balance"] + amount, 2)
        if new_balance < 0:
            raise ValueError(f"Refused: {account} would go to {new_balance:.2f}.")
        use_approval(conn, approval_id, ticket_id, "cash_adjustment", amount)
        d = today(conn).isoformat()
        conn.execute("UPDATE cash_accounts SET balance = ?, date = ? WHERE name = ?", (new_balance, d, account))
        conn.commit()
        return {
            "account": account,
            "balance_before": row["balance"],
            "amount": amount,
            "balance_after": new_balance,
            "date": d,
            "reason": reason,
            "ticket_id": ticket_id,
        }


@mcp.tool
def payment_invoice_tool(
    action: Literal["read", "pay_invoice", "update_invoice_status"] = "read",
    invoice_id: int | None = None,
    new_status: Literal["open", "paid", "disputed", "cancelled"] | None = None,
    ticket_id: int | None = None,
    approval_id: int | None = None,
) -> dict:
    """Vendor invoices and payments. Used by the accounting agent. Not for rent
    (rent is paid by facilities with rent_tool).

    Tables: invoices, vendors, payments, desk.

    action="read" (read-only): returns invoices (with vendor name and days
    overdue versus the desk date) and all recorded payments. Pass invoice_id to
    see one invoice.

    action="pay_invoice" (writes payments and invoices): records a payment for
    an open invoice (kind 'invoice', approved_by 'boss') and marks the invoice
    paid. Needs an approved 'pay_invoice' approval_id for this ticket whose
    economic_impact is minus the invoice amount. Does not change cash;
    accounting records the payment with account_tool.

    action="update_invoice_status" (writes invoices): sets an invoice's status.
    Needs an approved 'update_invoice_status' approval_id for this ticket.
    """
    with closing(connect()) as conn:
        d = today(conn)
        if action == "read":
            sql = (
                "SELECT i.*, v.name AS vendor_name, v.lead_days FROM invoices i "
                "JOIN vendors v ON v.id = i.vendor_id"
            )
            args = (invoice_id,) if invoice_id else ()
            invoices = []
            for r in conn.execute(sql + (" WHERE i.id = ?" if invoice_id else ""), args):
                late = (d - date.fromisoformat(r["due_date"])).days
                invoices.append({**dict(r), "days_overdue": late if r["status"] == "open" and late > 0 else 0})
            payments = [dict(r) for r in conn.execute("SELECT * FROM payments ORDER BY id")]
            return {"today": d.isoformat(), "invoices": invoices, "payments": payments}

        if invoice_id is None:
            raise ValueError(f"{action} needs an invoice_id.")
        inv = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if inv is None:
            raise ValueError(f"Invoice {invoice_id} not found.")

        if action == "pay_invoice":
            if inv["status"] != "open":
                raise ValueError(f"Invoice {invoice_id} is '{inv['status']}', not open.")
            use_approval(conn, approval_id, ticket_id, "pay_invoice", -inv["amount"])
            cur = conn.execute(
                "INSERT INTO payments (kind, ref_id, amount, account, paid_at, approved_by) "
                "VALUES ('invoice', ?, ?, 'checking', ?, 'boss')",
                (invoice_id, inv["amount"], d.isoformat()),
            )
            conn.execute("UPDATE invoices SET status = 'paid' WHERE id = ?", (invoice_id,))
            conn.commit()
            return {
                "invoice_id": invoice_id,
                "payment_id": cur.lastrowid,
                "amount": inv["amount"],
                "paid_at": d.isoformat(),
                "cash_note": "Cash not changed here; accounting records the payment with account_tool.",
            }

        if new_status is None:
            raise ValueError("update_invoice_status needs new_status.")
        use_approval(conn, approval_id, ticket_id, "update_invoice_status")
        conn.execute("UPDATE invoices SET status = ? WHERE id = ?", (new_status, invoice_id))
        conn.commit()
        return {"invoice_id": invoice_id, "old_status": inv["status"], "new_status": new_status}


@mcp.tool
def customer_service_tool(
    action: Literal["search", "list_messages"] = "search",
    query: str | None = None,
    ticket_id: int | None = None,
) -> dict:
    """Customer service lookups (read-only). Used by the customer service agent
    (search is also used by the inventory agent when answering customer
    service questions). Customer outreach itself is written with memo_tool.

    Tables: inventory, pricing, messages.

    action="search": finds items matching a customer's words (e.g.
    "sweatshirts", "mug") and returns each match with stock by size, location
    and list price. An empty query returns everything.

    action="list_messages": lists saved customer memos, optionally for one
    ticket.
    """
    with closing(connect()) as conn:
        if action == "search":
            words = re.findall(r"[a-z0-9-]+", (query or "").lower())
            terms = {t for w in words for t in SEARCH_SYNONYMS.get(w, [w]) if len(t) > 2}
            rows = conn.execute(
                "SELECT i.sku, i.name, i.size, i.qty, i.location, p.list_price FROM inventory i "
                "JOIN pricing p ON p.sku = i.sku ORDER BY i.sku"
            ).fetchall()
            items: dict[str, dict] = {}
            for r in rows:
                haystack = f"{r['name']} {r['sku']}".lower()
                if terms and not any(t in haystack for t in terms):
                    continue
                item = items.setdefault(r["sku"], {
                    "sku": r["sku"], "name": r["name"], "location": r["location"],
                    "list_price": r["list_price"], "stock_by_size": {}, "total_in_stock": 0,
                })
                item["stock_by_size"][r["size"]] = r["qty"]
                item["total_in_stock"] += r["qty"]
            return {"query": query, "matches": list(items.values())}

        sql = "SELECT * FROM messages" + (" WHERE ticket_id = ?" if ticket_id else "") + " ORDER BY id"
        return {"messages": [dict(r) for r in conn.execute(sql, (ticket_id,) if ticket_id else ())]}


MEMO_OPENING = "I hope this note finds you well."
MEMO_GUIDELINES = [
    "Write a polished, well-written, professional email to the customer.",
    "Keep it to approximately four sentences.",
    f'Start with "{MEMO_OPENING}" (an optional greeting line such as "Dear Tauhid Zaman," may come before it).',
    "Then professionally explain the problem that has been identified and what the customer needs to know.",
    "Be direct, to the point, eloquent and professional.",
    "Keep internal details out: no costs, margins, profit, or approvals.",
    "Memos are never actually sent.",
    "A memo is never approved without the boss's okay; it is saved only after the boss approves it.",
]
INTERNAL_WORDS = re.compile(r"\b(unit cost|margin|profit|approval|approved by|boss)\b", re.I)


def check_memo(body: str) -> dict:
    """Check a memo draft against the memo stipulations."""
    lines = [line.strip() for line in body.strip().splitlines() if line.strip()]
    if lines and lines[0].endswith(",") and len(lines[0]) <= 60:
        lines = lines[1:]  # greeting line
    while lines and len(lines[-1]) <= 40 and not re.search(r"[.!?]$", lines[-1]):
        lines.pop()  # sign-off lines ("Best regards," / "Campus Customs")
    text = " ".join(lines)
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    issues = []
    if not text.startswith(MEMO_OPENING):
        issues.append(f'The memo must start with "{MEMO_OPENING}"')
    if not 3 <= len(sentences) <= 5:
        issues.append(f"Memos should be approximately four sentences (3 to 5); this one has {len(sentences)}.")
    internal = sorted({m.group(0).lower() for m in INTERNAL_WORDS.finditer(text)})
    if internal:
        issues.append(f"Remove internal details from the customer memo: {', '.join(internal)}.")
    return {"ok": not issues, "issues": issues, "sentence_count": len(sentences)}


@mcp.tool
def memo_tool(
    action: Literal["guidelines", "check", "save"] = "guidelines",
    body: str | None = None,
    recipient: str | None = None,
    ticket_id: int | None = None,
    approval_id: int | None = None,
) -> dict:
    """The memo writing tool. Used by the customer service agent whenever
    outreach to a customer is needed (e.g. a restock delay, a price offer, an
    answer to a question).

    Memo stipulations:
    - a polished, well-written, professional email of approximately four sentences
    - starts with "I hope this note finds you well."
    - then professionally explains the problem identified and what the customer needs to know
    - direct, to the point, eloquent, professional; no internal costs, margins or approvals
    - memos are NEVER actually sent
    - a memo is NEVER approved without the boss's okay

    Tables: messages.

    action="guidelines" (read-only): returns the stipulations above.
    action="check" (read-only): checks a draft body against the stipulations
    and lists any issues to fix.
    action="save" (writes messages): saves a boss-approved memo with status
    'approved_not_sent'. The draft must pass the check, and it needs an
    approved 'save_memo' approval_id for this ticket. Nothing is sent.
    """
    if action == "guidelines":
        return {"opening": MEMO_OPENING, "guidelines": MEMO_GUIDELINES}
    if not body:
        raise ValueError(f"{action} needs the memo body.")
    result = check_memo(body)
    if action == "check":
        return result
    if not result["ok"]:
        raise ValueError("Memo doesn't meet the stipulations: " + " ".join(result["issues"]))
    if not recipient:
        raise ValueError("save needs a recipient.")
    with closing(connect()) as conn:
        use_approval(conn, approval_id, ticket_id, "save_memo")
        cur = conn.execute(
            "INSERT INTO messages (ticket_id, recipient, body, status, created_at) "
            "VALUES (?, ?, ?, 'approved_not_sent', ?)",
            (ticket_id, recipient, body, now()),
        )
        conn.commit()
        return {
            "message_id": cur.lastrowid,
            "status": "approved_not_sent",
            "note": "Saved only. Memos are never sent.",
        }


@mcp.tool
def desk_tool(
    action: Literal["read", "set_date"] = "read",
    new_date: str | None = None,
    reason: str | None = None,
    ticket_id: int | None = None,
    approval_id: int | None = None,
) -> dict:
    """The shop's current business date ("today" for every agent). Used by the
    boss agent.

    Tables: desk.

    action="read": the desk date and notes.
    action="set_date" (writes desk): moves the desk date to new_date
    (YYYY-MM-DD) when a ticket or other information shows the date has changed,
    and records the reason in desk notes. Needs a boss-approved 'set_desk_date'
    approval_id. The date can only move forward.
    """
    with closing(connect()) as conn:
        row = dict(conn.execute("SELECT * FROM desk").fetchone())
        if action == "read":
            return row
        if not new_date:
            raise ValueError("set_date needs new_date (YYYY-MM-DD).")
        target = date.fromisoformat(new_date)
        current = date.fromisoformat(row["date_today"])
        if target <= current:
            raise ValueError(f"Refused: the desk date can only move forward (now {current}, asked {target}).")
        use_approval(conn, approval_id, ticket_id, "set_desk_date")
        note = f"Date moved from {current} to {target}" + (f" (ticket {ticket_id})" if ticket_id else "") + (f": {reason}" if reason else ".")
        conn.execute("UPDATE desk SET date_today = ?, notes = ?", (target.isoformat(), note))
        conn.commit()
        return {"previous_date": current.isoformat(), "date_today": target.isoformat(), "notes": note}


@mcp.tool
def ticket_tool(
    action: Literal["list", "get", "create", "assign", "set_status", "set_priority", "complete"] = "list",
    ticket_id: int | None = None,
    status_filter: list[str] | None = None,
    type: str | None = None,
    requester: str | None = None,
    subject: str | None = None,
    notes: str | None = None,
    sku: str | None = None,
    size: str | None = None,
    qty: int | None = None,
    lease_id: int | None = None,
    invoice_id: int | None = None,
    agent: str | None = None,
    new_status: Literal["open", "in_progress", "waiting", "flagged"] | None = None,
    resolution: str | None = None,
    priority: Literal["urgent", "normal"] | None = None,
    priority_reason: str | None = None,
) -> dict:
    """The ticket queue. Used by the boss agent.

    Tables: tickets.

    action="list": all tickets in working order: urgent tickets first, then
    the rest in the order they were added (by id), with completed tickets at
    the bottom. Pass status_filter (e.g. ["open"]) to narrow it.
    action="get": one ticket.
    action="create": adds a new request to the queue with status 'open'
    (needs type, requester, subject; other fields optional).
    action="assign": records which agent picked up the ticket and sets it to
    'in_progress'.
    action="set_status": sets status to open, in_progress, waiting or flagged,
    with an optional resolution note.
    action="set_priority": marks a ticket 'urgent' (moved to the top of the
    queue) or 'normal' (stays in the order it was added), with a reason. New
    tickets have no priority until the boss has looked at them.
    action="complete": archives the ticket: marks it 'complete', stores the
    resolution and completion time, and moves it to the bottom of the list.
    """
    with closing(connect()) as conn:
        if action == "list":
            sql = "SELECT * FROM tickets"
            args: tuple = ()
            if status_filter:
                sql += f" WHERE status IN ({','.join('?' * len(status_filter))})"
                args = tuple(status_filter)
            sql += " ORDER BY status = 'complete', priority IS NOT 'urgent', id"
            return {"tickets": [dict(r) for r in conn.execute(sql, args)]}

        if action == "create":
            if not (type and requester and subject):
                raise ValueError("create needs type, requester and subject.")
            cur = conn.execute(
                "INSERT INTO tickets (type, requester, subject, sku, size, qty, lease_id, invoice_id, "
                "status, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)",
                (type, requester, subject, sku, size, qty, lease_id, invoice_id, notes, now()),
            )
            conn.commit()
            return {"ticket": get_ticket(conn, cur.lastrowid)}

        if ticket_id is None:
            raise ValueError(f"{action} needs a ticket_id.")
        get_ticket(conn, ticket_id)
        if action == "assign":
            if not agent:
                raise ValueError("assign needs an agent.")
            conn.execute(
                "UPDATE tickets SET assigned_agent = ?, status = 'in_progress' WHERE id = ?", (agent, ticket_id)
            )
        elif action == "set_status":
            if new_status is None:
                raise ValueError("set_status needs new_status.")
            conn.execute(
                "UPDATE tickets SET status = ?, resolution = COALESCE(?, resolution) WHERE id = ?",
                (new_status, resolution, ticket_id),
            )
        elif action == "set_priority":
            if priority is None:
                raise ValueError("set_priority needs priority ('urgent' or 'normal').")
            conn.execute(
                "UPDATE tickets SET priority = ?, priority_reason = ? WHERE id = ?",
                (priority, priority_reason, ticket_id),
            )
        elif action == "complete":
            conn.execute(
                "UPDATE tickets SET status = 'complete', resolution = ?, completed_at = ? WHERE id = ?",
                (resolution, now(), ticket_id),
            )
        conn.commit()
        return {"ticket": get_ticket(conn, ticket_id)}


@mcp.tool
def approval_tool(
    action: Literal["request", "decide", "list", "human_approve", "human_reject"] = "list",
    ticket_id: int | None = None,
    agent: str | None = None,
    approval_action: Literal[
        "fulfill_order", "pay_rent", "pay_invoice", "update_invoice_status",
        "save_memo", "cash_adjustment", "set_desk_date",
    ] | None = None,
    details: str | None = None,
    economic_impact: float = 0.0,
    approval_id: int | None = None,
    approve: bool | None = None,
    boss_notes: str | None = None,
    call_args: dict | None = None,
    approver: str | None = None,
    awaiting_human: bool = False,
) -> dict:
    """Boss sign-off records. Every database change needs one.

    Tables: approvals.

    action="request": a helper agent asks the boss to approve one change
    (approval_action) on a ticket, with details and the cash economic_impact
    (negative = money out). Starts 'pending'.
    action="decide": the boss approves or rejects a pending request
    (approve=true/false, boss_notes).
    action="list": approvals, optionally for one ticket. awaiting_human=true
    lists only payments the boss approved that still need a human.
    action="human_approve" / "human_reject": a person clicks Approve or Reject
    on a boss-approved payment (approver = who clicked). Agents never do this.

    Payments (pay_rent, pay_invoice) are only prepared by the agents: after the
    boss signs off they also need a human to click Approve. call_args stores the
    prepared tool call so it can be made once the human approves.

    Write actions in other tools check for an 'approved' record matching the
    ticket, action and amount, then mark it 'used'.
    """
    with closing(connect()) as conn:
        if action == "list":
            where, args = [], []
            if ticket_id:
                where.append("ticket_id = ?")
                args.append(ticket_id)
            if awaiting_human:
                marks = ",".join("?" * len(HUMAN_APPROVAL_ACTIONS))
                where.append(f"status = 'approved' AND human_approved_at IS NULL AND action IN ({marks})")
                args.extend(HUMAN_APPROVAL_ACTIONS)
            sql = "SELECT * FROM approvals" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id"
            return {"approvals": [dict(r) for r in conn.execute(sql, args)]}

        if action == "request":
            if not (agent and approval_action):
                raise ValueError("request needs agent and approval_action.")
            cur = conn.execute(
                "INSERT INTO approvals (ticket_id, agent, action, details, economic_impact, status, created_at, call_args) "
                "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
                (ticket_id, agent, approval_action, details, economic_impact, now(),
                 json.dumps(call_args) if call_args else None),
            )
            conn.commit()
            return {"approval": dict(conn.execute("SELECT * FROM approvals WHERE id = ?", (cur.lastrowid,)).fetchone())}

        if action in ("human_approve", "human_reject"):
            a = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
            if a is None:
                raise ValueError(f"Approval {approval_id} not found.")
            if a["action"] not in HUMAN_APPROVAL_ACTIONS or a["status"] != "approved" or a["human_approved_at"]:
                raise ValueError(f"Approval {approval_id} isn't a payment waiting for a human.")
            if action == "human_approve":
                conn.execute(
                    "UPDATE approvals SET human_approved_by = ?, human_approved_at = ? WHERE id = ?",
                    (approver or "human", now(), approval_id),
                )
            else:
                conn.execute(
                    "UPDATE approvals SET status = 'rejected', boss_notes = COALESCE(boss_notes, '') || ? WHERE id = ?",
                    (f" | Rejected by {approver or 'human'}.", approval_id),
                )
            conn.commit()
            return {"approval": dict(conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone())}

        if approval_id is None or approve is None:
            raise ValueError("decide needs approval_id and approve.")
        a = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if a is None:
            raise ValueError(f"Approval {approval_id} not found.")
        if a["status"] != "pending":
            raise ValueError(f"Approval {approval_id} is already '{a['status']}'.")
        conn.execute(
            "UPDATE approvals SET status = ?, boss_notes = ?, decided_at = ? WHERE id = ?",
            ("approved" if approve else "rejected", boss_notes, now(), approval_id),
        )
        conn.commit()
        return {"approval": dict(conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone())}


@mcp.tool
def flag_tool(
    action: Literal["raise", "list", "resolve"] = "list",
    kind: Literal["ticket_failure", "needs_human", "scope", "optimization", "safety"] | None = None,
    message: str | None = None,
    ticket_id: int | None = None,
    flag_id: int | None = None,
    include_resolved: bool = False,
) -> dict:
    """Flags for a human to review. Raised by the boss agent and the team
    runner; resolved by a person (the 'Resolved' button on the dashboard).

    Tables: flags, tickets.

    action="raise": records a flag. kind is ticket_failure (a ticket hit the
    cost or time limit or errored), needs_human (a decision outside the shop
    rules), scope (an agent acted outside its prompt file), optimization (a
    workflow improvement) or safety (a weakness in the system). ticket_failure
    and needs_human flags set the ticket to 'flagged'. An identical open flag
    is not duplicated.
    action="list": open flags (include_resolved=true for all).
    action="resolve": marks a flag resolved. If its ticket has no other open
    flags, the ticket goes back to 'open' so the agents pick it up again.
    """
    with closing(connect()) as conn:
        if action == "list":
            sql = "SELECT * FROM flags" + ("" if include_resolved else " WHERE status = 'open'") + " ORDER BY id"
            return {"flags": [dict(r) for r in conn.execute(sql)]}

        if action == "raise":
            if not (kind and message):
                raise ValueError("raise needs kind and message.")
            dup = conn.execute(
                "SELECT * FROM flags WHERE status = 'open' AND kind = ? AND message = ? AND ticket_id IS ?",
                (kind, message, ticket_id),
            ).fetchone()
            if dup:
                return {"flag": dict(dup), "duplicate": True}
            cur = conn.execute(
                "INSERT INTO flags (ticket_id, kind, message, status, created_at) VALUES (?, ?, ?, 'open', ?)",
                (ticket_id, kind, message, now()),
            )
            if ticket_id is not None and kind in ("ticket_failure", "needs_human"):
                conn.execute("UPDATE tickets SET status = 'flagged' WHERE id = ?", (ticket_id,))
            conn.commit()
            return {"flag": dict(conn.execute("SELECT * FROM flags WHERE id = ?", (cur.lastrowid,)).fetchone())}

        if flag_id is None:
            raise ValueError("resolve needs a flag_id.")
        f = conn.execute("SELECT * FROM flags WHERE id = ?", (flag_id,)).fetchone()
        if f is None:
            raise ValueError(f"Flag {flag_id} not found.")
        conn.execute("UPDATE flags SET status = 'resolved', resolved_at = ? WHERE id = ?", (now(), flag_id))
        reopened = False
        if f["ticket_id"] is not None:
            still_open = conn.execute(
                "SELECT COUNT(*) FROM flags WHERE ticket_id = ? AND status = 'open'", (f["ticket_id"],)
            ).fetchone()[0]
            if not still_open:
                cur = conn.execute(
                    "UPDATE tickets SET status = 'open' WHERE id = ? AND status = 'flagged'", (f["ticket_id"],)
                )
                reopened = cur.rowcount > 0
        conn.commit()
        return {"flag_id": flag_id, "resolved": True, "ticket_reopened": reopened, "ticket_id": f["ticket_id"]}


if __name__ == "__main__":
    mcp.run(show_banner=False)
