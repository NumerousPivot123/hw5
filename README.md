# Campus Customs agent desk (HW 5)

Five AI agents run the back office of Campus Customs, the Yale Bulldog Blue merchandise shop on Chapel Street: a **boss** plus **inventory**, **accounting**, **facilities** and **customer service**. They work a ticket queue, read and update the shop's SQLite database only through an **MCP server**, and need the boss's sign-off for every change (and a human's Approve click for every payment). A **React dashboard** shows the tickets, the database, and five Yale bulldogs that light up as each agent works.

## Project structure

```
HW 5/
├── AI_prompts.md            # Every prompt given to the vibe coder, by problem
├── README.md
├── requirements.txt         # Python dependencies
├── .env.example             # Template for your own .env (never commit the real one)
├── .gitignore
├── .mcp.json                # Registers the MCP server (used by Claude Code and the backend)
├── data/
│   ├── campus_customs.db        # Original data, never modified
│   └── campus_customs_new.db    # Working copy the agents update
├── mcp_server/
│   ├── server.py            # FastMCP server: the 11 tools the agents use
│   ├── smoke_test.py        # Problem 4 smoke test
│   └── README.md            # Tool reference
├── backend/
│   ├── main.py              # FastAPI app (routes for the dashboard)
│   ├── models.py            # Data types
│   ├── team.py              # Runs the agent team over the ticket queue
│   ├── audit.py             # Append-only audit trail
│   ├── events.py            # Summarizes recent agent activity
│   ├── cli.py               # Command-line runner
│   ├── agents/              # One file per agent (boss, inventory, accounting, facilities, customer_service)
│   └── prompts/             # One prompt file per agent
├── frontend/                # React + Vite + TypeScript dashboard
└── output/
    ├── harness.md           # Tables, MCP tools, agents, routes, dashboard, safety rules
    ├── mcp_smoke.json       # Problem 4 tool test results
    ├── desk_tickets.html    # Expected vs. actual for each ticket, cash, reflection
    ├── design.md            # Dashboard design notes
    ├── resolved_tickets.json
    ├── resolved_board.html  # Dashboard screenshots per resolved ticket (double-click to enlarge)
    ├── board_screenshots/
    └── audit_trail.json     # Every agent loop step from the run
```

## Setup

You need Python 3.14 and Node.js 20.19+ (or 22.12+).

1. From the `HW 5` folder, create the Python environment and install dependencies:
   ```
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```
   (macOS/Linux: `.venv/bin/python -m pip install -r requirements.txt`)
2. Set your Portkey key: copy `.env.example` to `.env` and fill in `PORTKEY_API_KEY` (or set it as an environment variable). Every agent uses the `gpt-6-luna` model through Portkey.
3. Install the dashboard's packages:
   ```
   cd frontend
   npm install
   ```
4. Nothing to configure for the MCP server: `.mcp.json` uses paths relative to the project folder, and the backend resolves them wherever the repo is cloned. On macOS/Linux the backend automatically uses its own Python in place of the Windows `.venv\Scripts\python.exe` path.

## Running

**Backend** (from the `backend` folder):
```
..\.venv\Scripts\uvicorn main:app --reload --port 8000
```
API docs: http://localhost:8000/docs

**Dashboard** (from the `frontend` folder, in a second terminal):
```
npm run dev
```
Then open http://localhost:5173. In Windows PowerShell, use `npm.cmd run dev` if script execution is blocked.

On the dashboard, press **Start agent team** on a ticket and watch the bulldogs light up as each agent works. Approve or reject any payment the agents prepare. **Reset database** restores `campus_customs_new.db` from the original for a fresh run.

**Without the dashboard** (from the `HW 5` folder): `.venv\Scripts\python -m backend.cli run` processes every open ticket; `python -m backend.cli --help` lists the other commands.

## How it works

1. **Route:** the boss sends each ticket to the right agent. Urgent tickets go first; otherwise tickets run in the order they were added.
2. **Plan:** the agent investigates with read-only tools, asking other agents when it needs to (e.g. inventory asks accounting about restock invoices), and proposes changes.
3. **Sign-off:** the boss checks the proposal and the evidence against the agent's prompt and the shop rules. It approves, rejects, asks a question, or sends the work back once for a revision.
4. **Execute:** the agent makes exactly the approved changes through the MCP server. Payments wait for a human's Approve click.
5. **Cash and close:** accounting applies the confirmed cash impact, and the boss closes the ticket.

## Safety

- Every database change needs the boss's sign-off, and the MCP server refuses changes without a matching approval. Payments also need a human's Approve.
- Agents only reach the database through the MCP server; the original `campus_customs.db` is never touched.
- Each ticket is capped at $0.50 of model usage and 1 minute. A ticket that hits a limit is flagged, and pressing **Resolved** puts it back in the queue.
- Customer memos are saved as `approved_not_sent` and never actually sent. Off-topic customer requests get no reply.
- Every agent step is appended to `output/audit_trail.json`.

See `output/harness.md` for the full reference.
