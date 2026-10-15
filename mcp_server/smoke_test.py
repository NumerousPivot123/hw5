"""Smoke test: connect to the server through .mcp.json, run one prompt per tool,
check every returned number against campus_customs_new.db, and save the results
to output/mcp_smoke.json."""

import asyncio
import json
import sys
import sqlite3
from datetime import date
from pathlib import Path

from fastmcp import Client

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "campus_customs_new.db"

PROMPTS = [
    ("Do we have the inventory in stock to support ticket one? And are there any vendors we need to contact?",
     "order_tool", 101),
    ("What are our upcoming rent expenses based on the ticket, who are we paying them to, and is it aligned with the lease table?",
     "rent_tool", 102),
    ("Can we support a bulk hoodie order? And what would our profit be at a price point that is 75% of what the current price point is?",
     "override_tool", 103),
]


def q(sql, *args):
    with sqlite3.connect(DB) as c:
        c.row_factory = sqlite3.Row
        return c.execute(sql, args).fetchone()


def check(checks, name, got, expected):
    ok = got == expected
    checks.append({"field": name, "tool": got, "database": expected, "match": ok})


def verify_order(out):
    checks = []
    t = q("SELECT * FROM tickets WHERE id = ?", out["ticket_id"])
    inv = q("SELECT * FROM inventory WHERE sku = ? AND size = ?", t["sku"], t["size"])
    p = q("SELECT * FROM pricing WHERE sku = ?", t["sku"])
    v = q("SELECT * FROM vendors WHERE specialty = 'apparel reprint'")
    i = q("SELECT * FROM invoices WHERE id = ?", t["invoice_id"])
    check(checks, "qty_requested", out["qty_requested"], t["qty"])
    check(checks, "qty_in_stock", out["qty_in_stock"], inv["qty"])
    check(checks, "available", out["available"], "yes" if inv["qty"] >= t["qty"] else "no")
    check(checks, "location", out["location"], inv["location"])
    check(checks, "list_price", out["list_price"], p["list_price"])
    check(checks, "unit_cost", out["unit_cost"], p["unit_cost"])
    check(checks, "total_profit", out["total_profit"], (p["list_price"] - p["unit_cost"]) * t["qty"])
    check(checks, "vendor_to_contact.name", out["vendor_to_contact"]["name"], v["name"])
    check(checks, "vendor_to_contact.lead_days", out["vendor_to_contact"]["lead_days"], v["lead_days"])
    check(checks, "linked_invoice.amount", out["linked_invoice"]["amount"], i["amount"])
    check(checks, "linked_invoice.due_date", out["linked_invoice"]["due_date"], i["due_date"])
    return checks


def verify_rent(out):
    checks = []
    t = q("SELECT * FROM tickets WHERE id = ?", out["ticket_id"])
    l = q("SELECT * FROM leases WHERE id = ?", t["lease_id"])
    today = date.fromisoformat(q("SELECT date_today FROM desk")["date_today"])
    check(checks, "space_name", out["space_name"], l["space_name"])
    check(checks, "landlord", out["landlord"], l["landlord"])
    check(checks, "monthly_rent", out["monthly_rent"], l["monthly_rent"])
    check(checks, "next_due", out["next_due"], l["next_due"])
    check(checks, "days_until_due", out["days_until_due"], (date.fromisoformat(l["next_due"]) - today).days)
    check(checks, "ticket requester is the landlord", out["requester"], l["landlord"])
    check(checks, "ticket says due in 2 days", out["days_until_due"], 2)
    return checks


def verify_override(out):
    checks = []
    t = q("SELECT * FROM tickets WHERE id = ?", out["ticket_id"])
    inv = q("SELECT * FROM inventory WHERE sku = ? AND size = ?", t["sku"], t["size"])
    p = q("SELECT * FROM pricing WHERE sku = ?", t["sku"])
    check(checks, "qty_requested", out["qty_requested"], t["qty"])
    check(checks, "qty_in_stock", out["qty_in_stock"], inv["qty"])
    check(checks, "shortfall", out["shortfall"], max(t["qty"] - inv["qty"], 0))
    check(checks, "list_price", out["list_price"], p["list_price"])
    check(checks, "unit_cost", out["unit_cost"], p["unit_cost"])
    check(checks, "standard_total_profit", out["standard_total_profit"], (p["list_price"] - p["unit_cost"]) * t["qty"])
    s = next(s for s in out["scenarios"] if s["label"] == "25% off")
    price = round(p["list_price"] * 0.75, 2)
    check(checks, "75% price: unit_price", s["unit_price"], price)
    check(checks, "75% price: profit_per_unit", s["profit_per_unit"], round(price - p["unit_cost"], 2))
    check(checks, "75% price: total_profit", s["total_profit"], round((price - p["unit_cost"]) * t["qty"], 2))
    return checks


VERIFY = {"order_tool": verify_order, "rent_tool": verify_rent, "override_tool": verify_override}


async def main():
    config = json.loads((ROOT / ".mcp.json").read_text())
    # .mcp.json paths are relative to the project folder; resolve them so this runs from anywhere.
    for server in config["mcpServers"].values():
        server["args"] = [str(ROOT / a) if (ROOT / a).exists() else a for a in server.get("args", [])]
        command = ROOT / server["command"]
        server["command"] = str(command) if command.exists() else sys.executable
        server["cwd"] = str(ROOT)
    results = []
    async with Client(config) as client:
        for prompt, tool, ticket_id in PROMPTS:
            out = (await client.call_tool(tool, {"ticket_id": ticket_id})).data
            checks = VERIFY[tool](out)
            results.append({
                "prompt": prompt,
                "tool": tool,
                "tool_input": {"ticket_id": ticket_id},
                "tool_output": out,
                "verification": {
                    "database": "data/campus_customs_new.db",
                    "all_match": all(c["match"] for c in checks),
                    "checks": checks,
                },
            })
    path = ROOT / "output" / "mcp_smoke.json"
    path.write_text(json.dumps(results, indent=2))
    for r in results:
        print(r["tool"], "all_match =", r["verification"]["all_match"])


if __name__ == "__main__":
    asyncio.run(main())
