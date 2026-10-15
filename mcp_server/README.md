# Campus Customs MCP server

This is where we store our tools that interact with the Campus Customs database to understand requests (tickets) and how we are responding to them. The server is built with [FastMCP](https://gofastmcp.com) and is registered for Claude Code in the project's `.mcp.json`. The agent team in `backend/` connects to it through that same `.mcp.json` entry. **The agents never bypass this server**: every database read and write goes through these tools.

## Database

The server reads and writes **`data/campus_customs_new.db`**, never `data/campus_customs.db`. The original `campus_customs.db` is left as is; `campus_customs_new.db` is the working copy the agents update. (`python -m backend.cli reset --yes` restores the working copy from the original.)

Whenever it connects, the server adds what the agent team needs to `campus_customs_new.db` if it's missing (so it also repairs itself after a reset):
- `tickets` columns `assigned_agent`, `resolution`, `completed_at`, `priority` and `priority_reason`
- tables `approvals` (boss sign-offs, plus the human approval on payments), `flags` (items for a human, with a Resolved state) and `messages` (customer memos, saved as `approved_not_sent`, never sent)

## Boss sign-off is enforced here

Every action that changes the database needs the id of an approval the boss signed off on (`approval_tool`). The server checks that the approval is `approved`, is for the same ticket and action, and, for money, is for exactly the same amount. Then it marks the approval `used` so it can't be replayed. A change without a matching approval is refused.

**Payments also need a human.** `pay_rent` and `pay_invoice` are refused until a person has clicked Approve (`human_approve`), on top of the boss's sign-off. A rent payment must also be for exactly the lease's monthly rent, and an invoice payment for exactly the invoice amount.

## Tools

| Tool | Used by | Tables | Read-only actions | Write actions (need boss approval) |
|---|---|---|---|---|
| `order_tool` | Inventory (boss reads) | tickets, inventory, pricing, vendors, invoices | `check`: stock yes/no, location, list price, unit cost, profit, vendor to contact, linked invoice | `fulfill`: removes the ordered qty from inventory |
| `rent_tool` | Facilities (boss reads) | tickets, leases, desk, payments | `check`: lease, rent, next due, days until due (or all leases with no ticket) | `pay_rent`: records the rent payment, rolls `next_due` forward a month |
| `override_tool` | Customer service (boss reads) | tickets, inventory, pricing | profit at 5–25% off and any requested or custom price, stock and shortfall | none |
| `account_tool` | Accounting (boss reads) | cash_accounts, desk | `read`: cash balances | `adjust`: applies the boss-confirmed cash impact (never below $0) |
| `payment_invoice_tool` | Accounting (boss reads) | invoices, vendors, payments, desk | `read`: invoices with days overdue, all payments | `pay_invoice`, `update_invoice_status` (not rent) |
| `customer_service_tool` | Customer service; inventory uses `search` | inventory, pricing, messages | `search`: items matching a customer's words with stock by size; `list_messages` | none |
| `memo_tool` | Customer service (boss checks) | messages | `guidelines`: the memo stipulations; `check`: tests a draft against them | `save`: stores a boss-approved memo as `approved_not_sent` (never sent; must pass the check) |
| `desk_tool` | Boss | desk | `read`: the business date | `set_date`: moves the date forward when a ticket or other information shows it changed |
| `ticket_tool` | Boss | tickets | `list` (urgent first, then in the order added; completed at the bottom), `get` | `create`, `assign`, `set_status`, `set_priority` (urgent / normal), `complete` (archive) |
| `approval_tool` | Boss and helpers; a human for payments | approvals | `list` (`awaiting_human` for payments waiting on a person) | `request` (helper asks), `decide` (boss approves or rejects), `human_approve` / `human_reject` (a person clicks Approve or Reject on a payment) |
| `flag_tool` | Boss and runner; a human resolves | flags, tickets | `list` | `raise`, `resolve` (puts the flagged ticket back in the queue) |

Recording a payment (`pay_rent`, `pay_invoice`) or a sale (`fulfill`) never changes cash by itself. Cash moves only through `account_tool` `adjust`, so the same money is never counted twice.

## Running

From the `HW 5` folder:

```bash
.venv/Scripts/python mcp_server/server.py
```

The Problem 4 smoke test (`mcp_server/smoke_test.py`) connects through `.mcp.json` and checks the first three tools against the database.
