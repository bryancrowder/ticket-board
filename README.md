# Ticket Board

A lightweight, self-hosted ticket & kanban tracker. One Docker container, one
SQLite file, zero build steps. Track what's on your plate with due dates,
effort estimates, and a scrum-style board — no account, no telemetry, no
database server.

![Ticket Board screenshot](docs/screenshot.png)

## Features

- **Scrum board** — five columns (Backlog → To Do → In Progress → Review → Done) with drag-and-drop between them
- **Due dates** — cards show `Overdue Nd` (red), `Due today` (amber), or `Due in Nd` chips; overdue cards get a red edge; header stats track Open / Due ≤7d / Overdue / Done
- **Effort estimates** — story points (1, 2, 3, 5, 8, 13) per card, with point totals per column so you can size a sprint at a glance
- **Priorities** — Low / Medium / High / Critical badges
- **Projects & groups** — tag any ticket with a project (e.g. `Acme`) and a group (e.g. `Forms`) with expandable pickers: click a field to expand the list of existing values, type to filter, or type a brand-new one; the board has matching dropdowns to filter everything by either, or both
- **Time tracking** — log hours spent per ticket (quarter-hour steps); cards show a ⏱ chip and the header totals time logged; moving a ticket to Done stamps a `completed_at` timestamp shown on the card
- **Week-by-week view** — one click filters the whole board (cards *and* header stats) to the week you pick; ‹ › walk any past or future week, and "This week" is the default landing spot — your selection is remembered between visits
- **No-date view** — one click shows everything that has no due date, so unscheduled work never slips through the cracks
- **Scheduled work stays scheduled** — a ticket in To Do or In Progress must always have a due date (the UI and API both enforce it); Backlog, Review, and Done may be unscheduled
- **Themes** — per-browser theme picker (saved locally): Neutral dark/light, Trail Blazers dark/light, Seahawks dark/light
- **Search & filters** — live text search across titles and descriptions, plus a one-click "overdue only" view
- **CSV export** — one click to dump the whole board for a spreadsheet or backup
- **MCP server (optional)** — expose the board as MCP tools so Claude (Desktop/Code) or ChatGPT can read it and add or move tickets; one command to turn it on, one to turn it off
- **Fast & private** — server-rendered single page, vanilla JS, no external CDNs, no fonts to fetch; runs fine on a Raspberry Pi
- **Durable** — all data lives in a single SQLite file on a Docker volume; survives restarts, rebuilds, and redeploys
- **REST API** — script it, automate it, or build on top of it
- **Keyboard** — `N` = new ticket, `Esc` = close dialog

## Quickstart

```bash
git clone <repo-url> ticket-board
cd ticket-board
docker compose up -d --build
```

Open `http://localhost:8080`. That's the whole setup.

## Deploy to a VM

```bash
# on your machine — copy the folder over (or git clone directly on the VM)
scp -r ticket-board <user>@<vm-ip>:~/ticket-board

# on the VM (Docker installed)
cd ~/ticket-board
docker compose up -d --build
```

Open `http://<vm-ip>:8080`. All data lives in the `tickets-data` Docker volume
(SQLite at `/data/tickets.db` inside the container), so it survives container
rebuilds and redeploys.

### Useful ops

```bash
docker compose logs -f tickets                              # live logs
docker compose cp tickets:/data/tickets.db ./tickets-backup-$(date +%F).db   # backup
docker compose cp tickets-backup-2026-11-17.db tickets:/data/tickets.db && docker compose restart tickets  # restore
git pull && docker compose up -d --build                    # update
docker compose down -v                                      # remove (DELETES DATA)
```

### Security notes

On a LAN, port 8080 is fine as-is. If you expose it beyond the LAN, put it
behind a reverse proxy with TLS (Caddy/nginx) and ideally auth, or expose it
only to a Tailscale/VPN address. There is no built-in login — treat the port as
a trust boundary.

```bash
# example: restrict the port to your home/office IP
sudo ufw allow from <your-ip> to any port 8080 proto tcp
```

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/api/tickets?status=todo&project=Acme&group=Forms` | list (optional status/project/group filters) |
| POST | `/api/tickets` | create — `{title, description, project, group, status, points, priority, due_date, time_hours}` |
| PATCH | `/api/tickets/<id>` | partial update, e.g. `{status: "in_progress"}` |
| DELETE | `/api/tickets/<id>` | delete |
| GET | `/api/tickets/export.csv` | CSV dump (includes project, group, time_hours, completed_at) |
| GET | `/api/meta` | distinct projects and groups currently in use |
| GET | `/api/health` | health check |

Example:

```bash
curl -X POST http://localhost:8080/api/tickets \
  -H 'Content-Type: application/json' \
  -d '{"title":"Renew TLS cert","status":"todo","points":2,"priority":"high","due_date":"2026-12-01","project":"Acme","group":"Forms"}'
```

Statuses: `backlog`, `todo`, `in_progress`, `review`, `done`.
Priorities: `low`, `medium`, `high`, `critical`.

`completed_at` is managed automatically: it is stamped when a ticket's status
changes to `done` and cleared when it leaves `done`. `time_hours` is a number
from 0 to 999.

**Due-date rule:** a ticket whose status is `todo` or `in_progress` must have a
`due_date` — creating it without one, or clearing it while in those columns,
returns `400`. Move it to `backlog` (or `review`/`done`) first to unschedule it.

## MCP server (optional)

The board can double as an MCP (Model Context Protocol) server, so AI clients
can work with it directly — "add a ticket for Acme / Forms, due Friday, 3
points" — instead of you clicking through the UI. It ships **off by default**
and is toggled with Docker Compose profiles.

### Turn it on / off

```bash
# ON — starts the MCP service on port 8765 (streamable HTTP)
docker compose --profile mcp up -d mcp

# OFF — stops and removes it; the board itself keeps running
docker compose stop mcp && docker compose --profile mcp rm -f mcp
```

The board (port 8080) is never affected by the MCP service.

### What the MCP server exposes

| Tool | What it does |
|---|---|
| `board_summary` | counts per column, open / overdue / due-≤7d, points, hours logged |
| `list_tickets` | filter by status / project / group / free text |
| `get_ticket` | one ticket by id |
| `create_ticket` | add a ticket (title, project, group, status, points, priority, due_date, time_hours) |
| `update_ticket` | change any field, move between columns, log hours |
| `delete_ticket` | remove by id |
| `list_projects` / `list_groups` | the tags currently in use |

### Connect a client

**Local client (stdio)** — Claude Desktop, Claude Code, or any client that
spawns the server itself. It points at your board over the local network; the
client runs `mcp_server.py` as a child process (no port exposed):

```jsonc
// claude_desktop_config.json (or equivalent)
{
  "mcpServers": {
    "ticket-board": {
      "command": "python",
      "args": ["/path/to/ticket-board/mcp_server.py"],
      "env": { "BOARD_URL": "http://<board-host>:8080" }
    }
  }
}
```

Run the command from the `ticket-board` folder (or use an absolute script
path) in a Python environment with `mcp<2` installed:
`pip install "mcp<2"`.

**Remote client (HTTP)** — ChatGPT-style connectors or any client that speaks
a URL. Use the port-8765 service from above:

```jsonc
{
  "mcpServers": {
    "ticket-board": {
      "url": "http://<board-host>:8765/mcp"
    }
  }
}
```

### Security notes

The MCP server enforces no auth of its own — it trusts whoever can reach the
port, same model as the board. Keep 8765 on the LAN/VPN, or put it behind the
same reverse proxy as 8080 if you expose it. `BOARD_URL` controls where the
server reaches the board (defaults to `http://localhost:8080`).

## Development

No build step. Install Flask + waitress and run:

```bash
pip install flask waitress
TICKETS_DB=~/dev-tickets.db python server.py
```

To try the MCP server locally (stdio transport):

```bash
pip install "mcp<2"
BOARD_URL=http://localhost:8080 python mcp_server.py
```

- `app.py` — the whole backend (Flask + stdlib `sqlite3`, no ORM)
- `templates/index.html` — the whole frontend (inline CSS/JS, no framework)
- `server.py` — waitress entrypoint (the container CMD)
- `mcp_server.py` — optional MCP server (FastMCP) exposing the REST API as tools
- DB path comes from the `TICKETS_DB` env var (defaults to `/data/tickets.db`
  in the container, project dir locally)

## Tech stack

Python 3.12 · Flask · waitress · SQLite · vanilla JS — that's it.

## License

MIT — use it, fork it, self-host it.
