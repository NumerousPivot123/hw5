# Campus Customs harness

## Database tables (campus_customs.db)

**desk**
- Fields (2): date_today, notes
- Why it matters: Gives us the current date, which is our frame of reference for tracking order and expense information and lets us determine what is overdue.

**inventory**
- Fields (5): sku, name, size, qty, location
- Why it matters: Summarizes all inventory we have in stock and where to find it in the store, across four aisles (A through D).

**pricing**
- Fields (3): sku, unit_cost, list_price
- Why it matters: Gives the cost and price of every item, so we can calculate the profit we make on each item we sell.

**vendors**
- Fields (4): id, name, specialty, lead_days
- Why it matters: Overview of the vendors we work with and how many days each vendor takes to deliver when we place an order.

**leases**
- Fields (6): id, space_name, landlord, monthly_rent, next_due, notes
- Why it matters: Overview of our rent and when it is next due, a critical expense.

**cash_accounts**
- Fields (3): name, balance, date
- Why it matters: Overview of our cash account balance.

**payments**
- Fields (7): id, kind, ref_id, amount, account, paid_at, approved_by
- Why it matters: Records the payments we make and who gave final approval for each one.

**invoices**
- Fields (6): id, vendor_id, amount, due_date, status, description
- Why it matters: View of all invoices: who they are from, how much they are for, and what they are for. Another critical expense.

**tickets**
- Fields (12): id, type, requester, subject, sku, size, qty, lease_id, invoice_id, status, notes, created_at
- Why it matters: Logs each incoming request, including its type and who requested it, giving us a summary of open items on our plate: rent expenses, vendor invoices, and customer purchase requests we need to fulfill.

## MCP server (mcp_server/)

Built with FastMCP and registered in the project's `.mcp.json`. Stores our tools that interact with the Campus Customs database to understand requests and how we are responding to them.

**Database:** The server reads and writes `data/campus_customs_new.db`. The original `data/campus_customs.db` is left as is and is never used by the server. Whenever it connects, the server adds the agent-team pieces to the working copy if they're missing (so it repairs itself after a reset): ticket columns `assigned_agent`, `resolution`, `completed_at`, `priority` and `priority_reason`, plus the `approvals` (with the human-approval columns), `flags` and `messages` tables.

**Boss sign-off is enforced by the server:** every write action needs the id of a boss-approved approval for the same ticket, action and (for money) amount. Approvals are single-use.

### Tools by table

| Tool | Tables it uses | Read-only actions | Write actions (boss approval required) |
|---|---|---|---|
| `order_tool` | tickets, inventory, pricing, vendors, invoices | `check` | `fulfill` → inventory |
| `rent_tool` | tickets, leases, desk, payments | `check` | `pay_rent` → payments, leases |
| `override_tool` | tickets, inventory, pricing | (all) | none |
| `account_tool` | cash_accounts, desk | `read` | `adjust` → cash_accounts |
| `payment_invoice_tool` | invoices, vendors, payments, desk | `read` | `pay_invoice` → payments, invoices; `update_invoice_status` → invoices |
| `customer_service_tool` | inventory, pricing, messages | `search`, `list_messages` | none |
| `memo_tool` | messages | `guidelines`, `check` | `save` → messages (saved as `approved_not_sent`, never sent) |
| `desk_tool` | desk | `read` | `set_date` → desk (forward only) |
| `ticket_tool` | tickets | `list`, `get` | `create`, `assign`, `set_status`, `set_priority`, `complete` |
| `approval_tool` | approvals | `list` | `request`, `decide`, `human_approve` / `human_reject` (a person, for payments) |
| `flag_tool` | flags, tickets | `list` | `raise`, `resolve` |

**order_tool**: Checks a ticket's SKU, size and quantity against inventory and returns whether the item is available (yes/no) and its location. Returns the list price the customer expects to pay, our unit cost and the calculated profit. If we can't fill the order from stock, returns the vendor whose specialty matches the item and any invoice linked to the ticket. `fulfill` removes the ordered quantity from inventory. Unlocks ticket 101.

**rent_tool**: Returns the space we're paying for, the landlord, the monthly rent, the next due date, notes and days until due (from the desk date). With no ticket it lists every lease, so due rent is spotted without a ticket. `pay_rent` records the payment and rolls `next_due` forward one month. Unlocks ticket 102.

**override_tool**: Checks stock for the requested SKU, size and quantity, returns the standard unit cost and list price, reads the ticket notes for the requested change, and models profit at 5–25% off plus any requested or custom price, showing the change versus list. Unlocks ticket 103.

**account_tool**: Reads the cash balance. `adjust` applies the boss-confirmed economic impact so the balance is always up to date, and refuses to go below $0.

**payment_invoice_tool**: Reads invoices (with days overdue) and payments. Pays or updates vendor invoices, but not rent.

**customer_service_tool**: Searches inventory by a customer's words (e.g. "sweatshirts") and lists saved memos.

**memo_tool**: The memo writing tool. Customer service uses it whenever outreach to a customer is needed. Its stipulations:
- approximately four sentences, as a polished, well-written, professional email
- starts with "I hope this note finds you well."
- professionally explains the problem identified and what the customer needs to know
- direct, to the point, eloquent; no internal costs, margins or approvals
- never actually sent, and never approved without the boss's okay

`check` tests a draft against the stipulations. `save` stores a boss-approved memo as `approved_not_sent`, and only if it passes the check.

**desk_tool**: Reads the business date and lets the boss move it forward when a ticket or other information shows the date has changed. Anything that becomes due (such as rent) is then picked up right away.

**ticket_tool / approval_tool / flag_tool**: the ticket queue, the boss's sign-off records, and the flags a human clears with the Resolved button.

## Agent team (backend/)

Five Pydantic AI agents, all on `gpt-6-luna` through Portkey (`PORTKEY_API_KEY`). Prompt files are in `backend/prompts/` (one per agent), data types in `backend/models.py`, one agent file per role in `backend/agents/`, and the runner in `backend/team.py`.

| Agent | Prompt file | Job | Tools it relies on (tables) |
|---|---|---|---|
| **Boss** | `boss.md` | Routes every ticket, reviews each helper's work against its prompt file, gives final sign-off on every database change and customer message, confirms the economic impact and sends it to accounting, archives completed tickets, and runs the hourly review | Read-only: `desk_tool` (desk), `ticket_tool` (tickets), `approval_tool` (approvals), `flag_tool` (flags), plus the read actions of every tool below. Its decisions are carried out through `ticket_tool`, `approval_tool` and `flag_tool`. |
| **Inventory** | `inventory.md` | Checks stock for orders, fulfills orders that can be filled (reporting revenue and total profit), and when an item is out of stock asks accounting about outstanding invoices for it. If a restock is on order it hands the ticket to customer service for delay outreach; otherwise it flags the shortfall. Also answers customer service's stock questions. | `order_tool` (tickets, inventory, pricing, vendors, invoices); `customer_service_tool` `search` (inventory, pricing); `ask_accounting_agent` (consults the accounting agent) |
| **Accounting** | `accounting.md` | Handles vendor invoices and payments (not rent), applies every boss-confirmed economic impact to cash, and answers inventory's questions about outstanding invoices | `account_tool` (cash_accounts, desk); `payment_invoice_tool` (invoices, vendors, payments, desk) |
| **Facilities** | `facilities.md` | Pays rent as soon as a rent ticket comes in (or on the due date if no ticket comes in during an active lease) and keeps the lease's next due date current | `rent_tool` (tickets, leases, desk, payments) |
| **Customer service** | `customer_service.md` | Gets price-override tickets first. It checks stock with inventory first: if the full quantity isn't available it makes **no offer** and writes a "currently not in stock, we'll keep in touch if anything changes" memo. Otherwise it runs the profitability check, recommends a price that still makes a profit, and writes the offer memo. Writes restock-delay memos when inventory hands over an out-of-stock order with a restock on order. Answers other business questions (e.g. "what sweatshirts do you have?"). Declines off-topic requests. | `override_tool` (tickets, inventory, pricing); `customer_service_tool` (inventory, pricing, messages); `memo_tool` (messages); `ask_inventory_agent` (consults the inventory agent) |

**Permissions per step:** each agent only sees the tools and actions it is allowed in that step. Helpers get read-only actions while planning and only the approved write action while executing. Calls outside an agent's permissions are blocked and recorded.

### Ticket order and prioritization

We did a prioritization exercise to decide the order tickets are worked in:
- **Base rule: first in, first out.** Tickets run in the order they were added (ticket number order). After 101, 102 and 103, the next ticket added is 104. If 101 hasn't run yet, work starts there and moves through to 104.
- **Urgent tickets jump the queue.** When the team works the queue (**Run all open tickets** / `POST /run`, or the CLI's `run` and `watch`), the boss looks over newly added tickets before each one (a priority check) and decides whether any is urgent. Starting one ticket by itself with its **Start agent team** button runs just that ticket, with no priority check. It can also bump a ticket already in the queue if new information makes it urgent. Urgent tickets move to the top and run next, in the order they were added. Each is stored with a reason (`tickets.priority` and `priority_reason`).
- **Urgent means real harm from waiting:** a payment due today or overdue, a customer who needs something today, a safety or legal issue, or something blocking the rest of the queue.
- **In order by default:** most tickets aren't urgent, and the boss generally keeps the order tickets came in.
- If the priority check fails, tickets stay in arrival order and the problem is flagged.

### How a ticket is handled

1. **Route:** the boss reads the new ticket and assigns it: customer orders → inventory; invoices and payments → accounting; rent → facilities; price overrides and customer questions → customer service (overrides go there first even if they affect inventory). Unclear tickets get the boss's best judgment.
2. **Plan:** the helper investigates with read-only tools and returns a proposal: facts, decision, the database changes it wants, the economic impact, and concerns. A helper with nothing to approve yet can hand the ticket straight to the agent that should take it next. For example, inventory finds an item out of stock, accounting confirms a restock invoice, and the ticket goes to customer service for a restock-delay memo. The boss then reviews the whole chain together: the original ticket, the invoice and the memo.
3. **Review:** the boss checks the proposal against the helper's prompt file, the shop rules and the evidence. The evidence is every MCP tool call and every agent-to-agent consultation since its last sign-off: who asked whom, the question and the answer, each labeled with the agent. It approves, rejects, or asks the helper a question (up to twice). If the economic impact isn't clear it asks for it.
4. **Execute:** for each approved change the boss's sign-off is recorded in `approvals`, and the helper makes exactly that change through the MCP server.
5. **Cash:** the boss's confirmed economic impact goes to accounting, which applies it to `cash_accounts` with its own approval.
   **Payments wait for a human.** Payments and purchases (`pay_rent`, `pay_invoice`) are only *prepared* by the agents. The boss still signs off as above, then the payment waits for a human to click **Approve** (`POST /payments/{approval_id}/approve`). Only then is it made and its cash impact applied, and until then the ticket stays `waiting`. The MCP server refuses a payment without the human's approval.
6. **Hand-off or send-back (if needed):** the boss can send separate related work to another agent (e.g. an unpaid invoice linked to an order → accounting). It can also send a ticket back **once** to an agent that already worked it, with its feedback, so the agent can revise (e.g. rewrite a rejected memo). The hand-off chain is recorded in `assigned_agent` (e.g. `customer_service > inventory > customer_service`).
7. **Close:** the boss archives the ticket as **complete** (moved to the bottom of the list), parks it as **waiting** (only while the team's own work is unfinished, such as a payment waiting for a human's Approve), or **flags** it for a human. An approved customer memo completes the ticket; a customer's reply would come in as a new ticket.

**Economic impact (cash basis):** sale fulfilled = + revenue; invoice paid = − amount; rent paid = − rent; offers, replies and checks = 0.

### Shop rules (defaults, editable in the prompt files)

- **Pricing:** a negotiated price must still make a profit (above unit cost), checked with the profitability calculation.
- **Inventory:** never fulfill more than is in stock; no partial fills without boss approval and the customer's agreement.
- **Rent:** paid as soon as a rent ticket arrives, or on the due date if none arrives during an active lease. Never paid twice for one period.
- **Today** is the desk date. **No invented numbers:** every figure comes from a tool output.

## Backend API (backend/main.py)

Built with FastAPI. Start it from the `backend` folder, with the HW 5 virtual environment active:

```
uvicorn main:app --reload --port 8000
```

Interactive docs are at http://localhost:8000/docs. The app starts the Campus Customs MCP server, and every agent read and write goes through it. Only `/reset` touches the database file directly; it is a human admin action, not an agent one.

| Method | Route | What it does |
|---|---|---|
| GET | `/tickets` | All tickets, each marked **open** or **resolved** (resolved = status `complete`), with status, priority, assigned agent and resolution |
| GET | `/tickets/{ticket_id}` | One ticket, open or resolved |
| POST | `/tickets/{ticket_id}/run` | Runs the agent team on that ticket (it must be open). Returns the updated ticket and its activity. |
| POST | `/run` | Runs the agent team on every open ticket: urgent first, then in the order added |
| GET | `/events?limit=300` | Summary of recent events: per agent, what it has been doing, the tools it used (with counts) and the tickets it touched, plus a timeline |
| GET | `/payments/pending` | Payments (rent, vendor invoices) the boss signed off on that are waiting for a human |
| POST | `/payments/{approval_id}/approve` | A **human clicks Approve**: the preparing agent makes the payment, accounting updates cash, and the ticket is completed once nothing else is waiting |
| POST | `/payments/{approval_id}/reject` | A human clicks Reject: nothing is paid and the ticket goes back on the ticket list (open) |
| GET | `/cash` | The current checking balance from `cash_accounts` |
| GET | `/database` | Current state of the database for the dashboard: desk date, cash, inventory, invoices, payments, lease and saved memos |
| GET | `/flags` | Open flags for a human |
| POST | `/flags/{flag_id}/resolve` | The **Resolved** button: clears a flag and puts its ticket back in the queue |
| POST | `/reset` | Resets `campus_customs_new.db` to the original data for a fresh run (the audit trail is kept) |

Only one run, payment approval or reset happens at a time. A second request while the team is busy gets a 409. The Vite dev page (`http://localhost:5173`) is an allowed CORS origin, so the browser can call these routes.

## Agent dashboard (frontend/)

React + Vite + TypeScript. It talks to the backend at `http://localhost:8000` and uses all of the routes above. Start it from the `frontend` folder with `npm run dev` (first time: `npm install`), then open http://localhost:5173.

Top to bottom:
1. Checking balance in the top right corner.
2. Latest tickets, each with a **Start agent team** button. Resolved tickets get a green check mark and a summary of what the agents did.
3. Payments waiting for approval, with **Accept payment** (a pop-up with a green check mark says Payment approved) and **Reject payment** (the ticket goes back on the list).
4. Flags with a **Resolved** button.
5. The latest resolution and a recent activity feed.
6. The five bulldogs, which light up as their agents work.
7. The database.
8. A bulldog photo.

See `output/design.md` for the design choices.

## Safety measures

- **Final sign-off:** the boss approves every database change before it is made, and the MCP server refuses changes without a matching approval.
- **Human approval for payments:** agents only prepare payments. Rent and vendor invoice payments are made only after a human clicks Approve, and the MCP server enforces this.
- **MCP only:** the agents never bypass the MCP server. They connect to it through `.mcp.json`.
- **Per-ticket limits:** no more than **$0.50** of model tokens and no more than **1 minute** per ticket. The runner stops a ticket that hits a limit or is on track to (over $0.40 or 50 seconds), or that errors. It raises a `ticket_failure` flag with an error message explaining the difficulty, and moves on to the next ticket.
- **Resolved button:** flags appear for a human with a **Resolved** button (on the dashboard's Flags section, or `python -m backend.cli resolve <flag_id>`). Resolving a ticket's flag puts the ticket back in the queue for the agents.
- **Customer service scope:** the customer service and boss agents do not respond to requests that aren't directly related to the Campus Customs business.
- **No messages are sent:** approved memos are saved as `approved_not_sent`, the point where they would be sent. Nothing is ever delivered, and `memo_tool` won't save a memo without the boss's approval or one that breaks the memo stipulations.
- **Boss always on:** in `watch` mode the boss picks up new tickets as they arrive (checks every 15 seconds) and does a general look-over every hour. The hourly review checks for missed tickets, rent due with no ticket (it opens one), agents acting outside their prompt files, workflow optimizations, and safety weaknesses. These are flagged the same way, with a Resolved button. Most of the time nothing is flagged.
- **Continuous refinement:** the boss looks for ways to refine and optimize the workflow, but never in a way that threatens the quality of the work.
- **Audit trail:** every agent loop step (prompts, model responses, tool calls and results), every hand-off message and every boss decision is appended to `output/audit_trail.json`. It is never erased between runs.
