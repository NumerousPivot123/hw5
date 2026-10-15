"""Command line for the Campus Customs agent team (until the dashboard exists).

Run from the HW 5 folder:
    .venv/Scripts/python -m backend.cli run            # process every open ticket once
    .venv/Scripts/python -m backend.cli watch          # keep running: new tickets + hourly boss review
    .venv/Scripts/python -m backend.cli review         # run the boss's hourly review now
    .venv/Scripts/python -m backend.cli tickets        # show the queue (completed at the bottom)
    .venv/Scripts/python -m backend.cli flags          # show open flags
    .venv/Scripts/python -m backend.cli resolve 3      # the "Resolved" button for flag 3
    .venv/Scripts/python -m backend.cli new-ticket --type customer_inquiry --requester "Jo" --subject "Sweatshirts" --notes "What sweatshirts do you have?"
    .venv/Scripts/python -m backend.cli state          # cash, invoices, payments, leases, messages
    .venv/Scripts/python -m backend.cli reset --yes    # copy campus_customs.db over campus_customs_new.db
"""

import argparse
import asyncio
import json
import os

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from dotenv import load_dotenv  # noqa: E402

from .agents.base import ROOT  # noqa: E402
from .team import Team, reset_database  # noqa: E402

load_dotenv(ROOT / ".env")


def show(value: object) -> None:
    print(json.dumps(value, indent=2, default=str))


async def main(args: argparse.Namespace) -> None:
    async with Team() as team:
        if args.command == "run":
            count = await team.run_pending()
            print(f"Processed {count} open ticket(s).")
            if args.review:
                await team.hourly_review()
                print("Hourly review done.")
        elif args.command == "watch":
            await team.watch()
        elif args.command == "review":
            await team.hourly_review()
            print("Hourly review done.")
        elif args.command == "tickets":
            for t in (await team.call("ticket_tool", {"action": "list"}, log=False))["tickets"]:
                print(f"{t['id']:>4}  {t['status']:<11} {t['type']:<16} {t['requester']:<22} {t['subject']}"
                      f"{'  -> ' + t['assigned_agent'] if t.get('assigned_agent') else ''}")
        elif args.command == "flags":
            show((await team.call("flag_tool", {"action": "list", "include_resolved": args.all}, log=False))["flags"])
        elif args.command == "resolve":
            show(await team.call("flag_tool", {"action": "resolve", "flag_id": args.flag_id}, "human"))
        elif args.command == "new-ticket":
            fields = {k: v for k, v in vars(args).items() if k in {
                "type", "requester", "subject", "notes", "sku", "size", "qty", "lease_id", "invoice_id"} and v is not None}
            show(await team.call("ticket_tool", {"action": "create", **fields}, "boss"))
        elif args.command == "state":
            show({
                "cash": await team.call("account_tool", {"action": "read"}, log=False),
                "invoices_and_payments": await team.call("payment_invoice_tool", {"action": "read"}, log=False),
                "leases": await team.call("rent_tool", {"action": "check"}, log=False),
                "messages": await team.call("customer_service_tool", {"action": "list_messages"}, log=False),
            })


def parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="backend.cli", description="Campus Customs agent team")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="process every open ticket once")
    run.add_argument("--review", action="store_true", help="also run the hourly review afterwards")
    sub.add_parser("watch", help="keep processing new tickets; boss review every hour")
    sub.add_parser("review", help="run the boss's hourly review now")
    sub.add_parser("tickets", help="show the ticket queue")
    flags = sub.add_parser("flags", help="show open flags")
    flags.add_argument("--all", action="store_true", help="include resolved flags")
    resolve = sub.add_parser("resolve", help="resolve a flag (puts its ticket back in the queue)")
    resolve.add_argument("flag_id", type=int)
    new = sub.add_parser("new-ticket", help="add a request to the queue")
    new.add_argument("--type", required=True)
    new.add_argument("--requester", required=True)
    new.add_argument("--subject", required=True)
    new.add_argument("--notes")
    new.add_argument("--sku")
    new.add_argument("--size")
    new.add_argument("--qty", type=int)
    new.add_argument("--lease-id", dest="lease_id", type=int)
    new.add_argument("--invoice-id", dest="invoice_id", type=int)
    sub.add_parser("state", help="show cash, invoices, payments, leases and saved messages")
    reset = sub.add_parser("reset", help="restore campus_customs_new.db from the untouched original")
    reset.add_argument("--yes", action="store_true", help="confirm the reset")
    return p.parse_args()


if __name__ == "__main__":
    arguments = parse()
    if arguments.command == "reset":
        if not arguments.yes:
            raise SystemExit("This overwrites data/campus_customs_new.db. Re-run with --yes to confirm.")
        reset_database()
        print("data/campus_customs_new.db restored from data/campus_customs.db.")
    else:
        asyncio.run(main(arguments))
