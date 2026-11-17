"""Ticket Board — lightweight kanban/ticket tracker.

Single-file Flask app + SQLite. No ORM, no build step, one container.
"""
import os
import sqlite3
from datetime import datetime, timezone

from flask import Flask, g, jsonify, render_template, request

DB_PATH = os.environ.get("TICKETS_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "tickets.db"))

SCHEMA_TABLE = """
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    project TEXT NOT NULL DEFAULT '',
    group_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'backlog',
    points INTEGER NOT NULL DEFAULT 0,
    priority TEXT NOT NULL DEFAULT 'medium',
    due_date TEXT,
    time_hours REAL NOT NULL DEFAULT 0,
    completed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0
);
"""

SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status, position);
CREATE INDEX IF NOT EXISTS idx_tickets_project ON tickets(project);
CREATE INDEX IF NOT EXISTS idx_tickets_group ON tickets(group_name);
"""

VALID_STATUSES = ("backlog", "todo", "in_progress", "review", "done")
VALID_PRIORITIES = ("low", "medium", "high", "critical")

app = Flask(__name__)


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    d = os.path.dirname(os.path.abspath(DB_PATH))
    os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA_TABLE)
    # Migrate databases created before projects/groups/time/completed existed.
    # Must run BEFORE creating indexes on those columns.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tickets)").fetchall()}
    if "project" not in cols:
        conn.execute("ALTER TABLE tickets ADD COLUMN project TEXT NOT NULL DEFAULT ''")
    if "group_name" not in cols:
        conn.execute("ALTER TABLE tickets ADD COLUMN group_name TEXT NOT NULL DEFAULT ''")
    if "time_hours" not in cols:
        conn.execute("ALTER TABLE tickets ADD COLUMN time_hours REAL NOT NULL DEFAULT 0")
    if "completed_at" not in cols:
        conn.execute("ALTER TABLE tickets ADD COLUMN completed_at TEXT")
    conn.executescript(SCHEMA_INDEXES)
    conn.commit()
    conn.close()


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def row_to_dict(row):
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "project": row["project"] or "",
        "group": row["group_name"] or "",
        "status": row["status"],
        "points": row["points"],
        "priority": row["priority"],
        "due_date": row["due_date"] or None,
        "time_hours": row["time_hours"] or 0,
        "completed_at": row["completed_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "position": row["position"],
    }


def validate_payload(payload):
    errs = {}
    title = (payload.get("title") or "").strip()
    if not title:
        errs["title"] = "Title is required."
    if len(title) > 500:
        errs["title"] = "Title too long (max 500 chars)."
    status = payload.get("status", "backlog")
    if status not in VALID_STATUSES:
        errs["status"] = "Invalid status."
    priority = payload.get("priority", "medium")
    if priority not in VALID_PRIORITIES:
        errs["priority"] = "Invalid priority."
    pts = payload.get("points", 0)
    try:
        pts = int(pts)
        if pts < 0 or pts > 999:
            raise ValueError
    except (TypeError, ValueError):
        errs["points"] = "Points must be an integer 0-999."
    due = payload.get("due_date")
    if due not in (None, ""):
        try:
            datetime.strptime(due, "%Y-%m-%d")
        except (TypeError, ValueError):
            errs["due_date"] = "due_date must be YYYY-MM-DD."
            due = None
    for field in ("project", "group"):
        val = payload.get(field)
        if val is not None and not isinstance(val, str):
            errs[field] = "Must be a string."
        elif isinstance(val, str) and len(val) > 80:
            errs[field] = "Too long (max 80 chars)."
    th = payload.get("time_hours", 0)
    try:
        th = float(th)
        if th < 0 or th > 999:
            raise ValueError
    except (TypeError, ValueError):
        errs["time_hours"] = "Time must be a number of hours between 0 and 999."
    return errs


def clean_fields(payload):
    def text(k, limit=80):
        v = payload.get(k)
        if v is None:
            return ""
        return str(v).strip()[:limit]

    return {
        "title": (payload.get("title") or "").strip(),
        "description": payload.get("description") or "",
        "project": text("project"),
        "group_name": text("group"),
        "status": payload.get("status", "backlog"),
        "points": int(payload.get("points", 0)),
        "priority": payload.get("priority", "medium"),
        "due_date": payload.get("due_date") or None,
        "time_hours": float(payload.get("time_hours") or 0),
    }


def max_position(db, status):
    row = db.execute("SELECT COALESCE(MAX(position), 0) AS m FROM tickets WHERE status = ?", (status,)).fetchone()
    return row["m"]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"ok": True})


@app.route("/api/meta")
def meta():
    """Distinct non-empty project and group values, for dynamic filter dropdowns."""
    db = get_db()
    projects = [r[0] for r in db.execute(
        "SELECT DISTINCT project FROM tickets WHERE project <> '' ORDER BY LOWER(project), project"
    ).fetchall()]
    groups = [r[0] for r in db.execute(
        "SELECT DISTINCT group_name FROM tickets WHERE group_name <> '' ORDER BY LOWER(group_name), group_name"
    ).fetchall()]
    return jsonify({"projects": projects, "groups": groups})


@app.route("/api/tickets", methods=["GET"])
def list_tickets():
    status = request.args.get("status")
    project = request.args.get("project")
    group = request.args.get("group")
    db = get_db()
    where, args = [], []
    if status:
        where.append("status = ?")
        args.append(status)
    if project:
        where.append("project = ?")
        args.append(project)
    if group:
        where.append("group_name = ?")
        args.append(group)
    sql = "SELECT * FROM tickets"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY position, id"
    rows = db.execute(sql, args).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@app.route("/api/tickets", methods=["POST"])
def create_ticket():
    payload = request.get_json(silent=True) or {}
    errs = validate_payload(payload)
    if errs:
        return jsonify({"error": errs}), 400
    db = get_db()
    f = clean_fields(payload)
    ts = now_iso()
    pos = max_position(db, f["status"]) + 1000
    cur = db.execute(
        """INSERT INTO tickets
           (title, description, project, group_name, status, points, priority, due_date,
            time_hours, completed_at, created_at, updated_at, position)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (f["title"], f["description"], f["project"], f["group_name"], f["status"],
         f["points"], f["priority"], f["due_date"], f["time_hours"],
         ts if f["status"] == "done" else None, ts, ts, pos),
    )
    db.commit()
    row = db.execute("SELECT * FROM tickets WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(row_to_dict(row)), 201


@app.route("/api/tickets/<int:ticket_id>", methods=["PATCH"])
def update_ticket(ticket_id):
    payload = request.get_json(silent=True) or {}
    db = get_db()
    existing = db.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if existing is None:
        return jsonify({"error": "Ticket not found."}), 404

    # Merge current values with patch so validation sees the full record.
    merged = row_to_dict(existing)
    for k in ("title", "description", "project", "group", "status", "points", "priority", "due_date", "time_hours"):
        if k in payload:
            merged[k] = payload[k]
    errs = validate_payload(merged)
    if errs:
        return jsonify({"error": errs}), 400

    f = clean_fields(merged)

    # Completed timestamp: stamp when a card enters Done, clear when it leaves.
    old_status = existing["status"]
    new_status_in = f["status"]
    if new_status_in == "done" and old_status != "done":
        completed_at = now_iso()
    elif new_status_in != "done":
        completed_at = None
    else:
        completed_at = existing["completed_at"]
    # Moving to another column (or reordering) puts the card at that column's end
    # unless an explicit position was provided.
    new_status = f["status"]
    if "position" in payload and payload["position"] is not None:
        pos = int(payload["position"])
    else:
        pos = max_position(db, new_status) + 1000

    db.execute(
        """UPDATE tickets
           SET title = ?, description = ?, project = ?, group_name = ?, status = ?,
               points = ?, priority = ?, due_date = ?, time_hours = ?, completed_at = ?,
               updated_at = ?, position = ?
           WHERE id = ?""",
        (f["title"], f["description"], f["project"], f["group_name"], new_status,
         f["points"], f["priority"], f["due_date"], f["time_hours"], completed_at,
         now_iso(), pos, ticket_id),
    )
    db.commit()
    row = db.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return jsonify(row_to_dict(row))


@app.route("/api/tickets/<int:ticket_id>", methods=["DELETE"])
def delete_ticket(ticket_id):
    db = get_db()
    cur = db.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "Ticket not found."}), 404
    return ("", 204)


@app.route("/api/tickets/reorder", methods=["POST"])
def reorder_tickets():
    payload = request.get_json(silent=True) or {}
    status = payload.get("status")
    ids = payload.get("ids")
    if status not in VALID_STATUSES or not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        return jsonify({"error": "Expected {status, ids:[...]}."}), 400
    db = get_db()
    for idx, tid in enumerate(ids):
        db.execute("UPDATE tickets SET position = ? WHERE id = ? AND status = ?", (idx * 1000, tid, status))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/tickets/export.csv")
def export_csv():
    import csv
    import io

    db = get_db()
    rows = db.execute("SELECT * FROM tickets ORDER BY status, position, id").fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "title", "project", "group", "status", "priority", "points", "due_date", "time_hours", "completed_at", "description", "created_at", "updated_at"])
    for r in rows:
        w.writerow([r["id"], r["title"], r["project"], r["group_name"], r["status"], r["priority"],
                    r["points"], r["due_date"] or "", r["time_hours"] or 0, r["completed_at"] or "",
                    r["description"], r["created_at"], r["updated_at"]])
    resp = app.response_class(
        buf.getvalue().encode("utf-8"),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=tickets.csv"},
    )
    return resp


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
