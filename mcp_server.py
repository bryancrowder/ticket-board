#!/usr/bin/env python3
"""Ticket Board MCP server — exposes the board's REST API as MCP tools.

So Claude, ChatGPT, or any MCP client can read and manage tickets directly:
create, update, move, delete, filter, summarize.

Transports:
  stdio (default)  local clients (Claude Desktop, Claude Code) — the client
                   spawns this process on demand, so it is inherently on/off.
  http             streamable HTTP on port 8765, for remote clients that
                   connect over a URL (e.g. a ChatGPT MCP connector).

Environment:
  BOARD_URL   base URL of the board's REST API (default: http://localhost:8080)

No authentication is enforced — same trust model as the board itself.
Keep both on a trusted LAN/VPN.
"""

import argparse
import datetime
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from mcp.server.fastmcp import FastMCP

BOARD_URL = os.environ.get("BOARD_URL", "http://localhost:8080").rstrip("/")
STATUSES = "backlog | todo | in_progress | review | done"
PRIORITIES = "low | medium | high | critical"

mcp = FastMCP("ticket-board")


class BoardError(RuntimeError):
    pass


def _req(method: str, path: str, body: Optional[dict] = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BOARD_URL + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            txt = resp.read().decode()
            return json.loads(txt) if txt else None
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode() or "{}")
        except ValueError:
            detail = {"error": str(e)}
        raise BoardError(f"Board API error {e.code}: {json.dumps(detail)}")
    except urllib.error.URLError as e:
        raise BoardError(f"Cannot reach the board at {BOARD_URL}: {e.reason}")


def _today() -> str:
    return datetime.date.today().isoformat()


def _days_until(date_str: str) -> int:
    d = datetime.date.fromisoformat(date_str)
    return (d - datetime.date.today()).days


@mcp.tool()
def board_summary() -> dict:
    """Get a summary of the board: ticket counts per column, open / overdue /
    due-in-7-days totals, story points per column, and total hours logged."""
    tickets = _req("GET", "/api/tickets")
    today = _today()
    columns = {}
    open_t = overdue = due7 = 0
    hours = 0.0
    for t in tickets:
        col = columns.setdefault(t["status"], {"count": 0, "points": 0})
        col["count"] += 1
        col["points"] += t.get("points") or 0
        hours += t.get("time_hours") or 0
        if t["status"] != "done":
            open_t += 1
            if t.get("due_date"):
                if t["due_date"] < today:
                    overdue += 1
                elif _days_until(t["due_date"]) <= 7:
                    due7 += 1
    return {
        "board_url": BOARD_URL,
        "columns": columns,
        "open": open_t,
        "overdue": overdue,
        "due_within_7_days": due7,
        "hours_logged": hours,
        "total_tickets": len(tickets),
    }


@mcp.tool()
def list_tickets(
    status: Optional[str] = None,
    project: Optional[str] = None,
    group: Optional[str] = None,
    text: Optional[str] = None,
) -> list:
    """List tickets. Optional filters: status (backlog | todo | in_progress |
    review | done), project (e.g. Acme), group (e.g. Forms), and/or a free-text
    search over title, description, project and group. All filters combine."""
    params = []
    if status:
        params.append("status=" + urllib.parse.quote(status))
    if project:
        params.append("project=" + urllib.parse.quote(project))
    if group:
        params.append("group=" + urllib.parse.quote(group))
    tickets = _req("GET", "/api/tickets?" + "&".join(params)) if params else _req("GET", "/api/tickets")
    if text:
        q = text.lower()
        tickets = [
            t for t in tickets
            if q in (t.get("title", "") + " " + t.get("description", "")
                     + " " + t.get("project", "") + " " + t.get("group", "")).lower()
        ]
    return tickets


@mcp.tool()
def get_ticket(ticket_id: int) -> dict:
    """Get a single ticket by its numeric id, with all fields."""
    return _req("GET", f"/api/tickets/{ticket_id}")


@mcp.tool()
def create_ticket(
    title: str,
    description: str = "",
    project: str = "",
    group: str = "",
    status: str = "backlog",
    points: int = 0,
    priority: str = "medium",
    due_date: Optional[str] = None,
    time_hours: float = 0,
) -> dict:
    """Create a new ticket on the board and return it.

    Args:
        title: short summary (required)
        description: longer notes / context
        project: project tag, e.g. Acme
        group: group tag within the project, e.g. Forms
        status: one of backlog | todo | in_progress | review | done
        points: story-point estimate (0, 1, 2, 3, 5, 8, 13)
        priority: one of low | medium | high | critical
        due_date: due date as YYYY-MM-DD, or omit — REQUIRED when status is
            todo or in_progress
        time_hours: hours already spent (0-999)
    """
    return _req("POST", "/api/tickets", {
        "title": title,
        "description": description,
        "project": project,
        "group": group,
        "status": status,
        "points": points,
        "priority": priority,
        "due_date": due_date,
        "time_hours": time_hours,
    })


@mcp.tool()
def update_ticket(
    ticket_id: int,
    status: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    project: Optional[str] = None,
    group: Optional[str] = None,
    points: Optional[int] = None,
    priority: Optional[str] = None,
    due_date: Optional[str] = None,
    time_hours: Optional[float] = None,
) -> dict:
    """Update one or more fields of an existing ticket and return the updated
    ticket. Pass only the fields to change.

    Notes:
        status: backlog | todo | in_progress | review | done.
            Moving to done stamps completed_at; moving out of done clears it.
            todo and in_progress always require a due_date.
        due_date: YYYY-MM-DD, or pass null to clear it.
            (Cannot be cleared while the ticket is todo / in_progress.)
        time_hours: 0-999 hours spent.
    """
    body = {}
    for key, val in {
        "status": status, "title": title, "description": description,
        "project": project, "group": group, "points": points,
        "priority": priority, "due_date": due_date, "time_hours": time_hours,
    }.items():
        if val is not None:
            body[key] = val
    if not body:
        raise BoardError("No fields to update — pass at least one field.")
    return _req("PATCH", f"/api/tickets/{ticket_id}", body)


@mcp.tool()
def delete_ticket(ticket_id: int) -> dict:
    """Permanently delete a ticket by id."""
    _req("DELETE", f"/api/tickets/{ticket_id}")
    return {"deleted": ticket_id}


@mcp.tool()
def list_projects() -> list:
    """List all project tags currently in use on the board."""
    return _req("GET", "/api/meta")["projects"]


@mcp.tool()
def list_groups() -> list:
    """List all group tags currently in use on the board."""
    return _req("GET", "/api/meta")["groups"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Ticket Board MCP server")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio",
                        help="stdio = local clients (Claude Desktop/Code); "
                             "http = streamable HTTP endpoint for remote clients")
    parser.add_argument("--host", default="0.0.0.0", help="bind host for http transport")
    parser.add_argument("--port", type=int, default=8765, help="port for http transport")
    args = parser.parse_args()
    if args.transport == "http":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
