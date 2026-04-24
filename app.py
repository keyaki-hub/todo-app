import os
import sqlite3
from datetime import date, timedelta
from flask import Flask, render_template, Response, request, jsonify

app = Flask(__name__)

USERNAME = "keyaki"
PASSWORD = "XXXXXXXX"  # ← 要変更

DB_PATH = os.path.join(os.path.dirname(__file__), 'todo.db')
LEAD_DAYS = 7  # 定期ToDoを発生日の何日前から表示するか


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                due_date TEXT,
                completed INTEGER DEFAULT 0,
                completed_at TEXT,
                template_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                recur_type TEXT NOT NULL,
                recur_day INTEGER,
                recur_month INTEGER,
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')


init_db()


def next_occurrence(recur_type, recur_day, recur_month, today):
    """今日以降で次に発生する日付を返す。"""
    if recur_type == 'weekly':
        # recur_day: 0=月 ... 6=日 (Python datetime.weekday())
        days_ahead = (recur_day - today.weekday()) % 7
        return today + timedelta(days=days_ahead)
    if recur_type == 'monthly':
        year, month = today.year, today.month
        for _ in range(13):
            try:
                candidate = date(year, month, recur_day)
                if candidate >= today:
                    return candidate
            except ValueError:
                pass
            if month == 12:
                year, month = year + 1, 1
            else:
                month += 1
        return None
    if recur_type == 'yearly':
        for year in (today.year, today.year + 1):
            try:
                candidate = date(year, recur_month, recur_day)
                if candidate >= today:
                    return candidate
            except ValueError:
                return None
        return None
    return None


def generate_from_templates():
    """有効なテンプレートから、発生日が7日以内のToDoを生成する。"""
    today = date.today()
    horizon = today + timedelta(days=LEAD_DAYS)
    with get_db() as conn:
        templates = conn.execute("SELECT * FROM templates WHERE active=1").fetchall()
        for t in templates:
            nxt = next_occurrence(t['recur_type'], t['recur_day'], t['recur_month'], today)
            if nxt is None or nxt > horizon:
                continue
            existing = conn.execute(
                "SELECT id FROM todos WHERE template_id=? AND due_date=?",
                (t['id'], nxt.isoformat())
            ).fetchone()
            if existing:
                continue
            conn.execute(
                "INSERT INTO todos (title, due_date, template_id) VALUES (?, ?, ?)",
                (t['title'], nxt.isoformat(), t['id'])
            )


def check_auth(username, password):
    return username == USERNAME and password == PASSWORD


def require_auth():
    return Response(
        "ログインが必要です", 401,
        {"WWW-Authenticate": 'Basic realm="Login Required"'}
    )


@app.before_request
def before_request():
    auth = request.authorization
    if not auth or not check_auth(auth.username, auth.password):
        return require_auth()


@app.route("/")
def index():
    generate_from_templates()
    return render_template("index.html")


@app.route("/templates")
def templates_page():
    return render_template("templates.html")


@app.route("/api/todos", methods=["GET"])
def list_todos():
    show_completed = request.args.get("completed") == "1"
    with get_db() as conn:
        if show_completed:
            rows = conn.execute(
                "SELECT * FROM todos WHERE completed=1 ORDER BY completed_at DESC LIMIT 100"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM todos WHERE completed=0 "
                "ORDER BY CASE WHEN due_date IS NULL THEN 1 ELSE 0 END, due_date ASC, id ASC"
            ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/todos", methods=["POST"])
def create_todo():
    data = request.json or {}
    title = (data.get("title") or "").strip()
    due_date = data.get("due_date") or None
    if not title:
        return jsonify({"ok": False, "error": "タイトル必須"}), 400
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO todos (title, due_date) VALUES (?, ?)",
            (title, due_date)
        )
        new_id = cur.lastrowid
    return jsonify({"ok": True, "id": new_id})


@app.route("/api/todos/<int:todo_id>", methods=["PATCH"])
def update_todo(todo_id):
    data = request.json or {}
    fields = []
    values = []
    for key in ("title", "due_date"):
        if key in data:
            fields.append(f"{key}=?")
            values.append(data[key] or None)
    if not fields:
        return jsonify({"ok": True})
    values.append(todo_id)
    with get_db() as conn:
        conn.execute(f"UPDATE todos SET {', '.join(fields)} WHERE id=?", values)
    return jsonify({"ok": True})


@app.route("/api/todos/<int:todo_id>/complete", methods=["POST"])
def complete_todo(todo_id):
    with get_db() as conn:
        conn.execute(
            "UPDATE todos SET completed=1, completed_at=datetime('now','localtime') WHERE id=?",
            (todo_id,)
        )
    return jsonify({"ok": True})


@app.route("/api/todos/<int:todo_id>/uncomplete", methods=["POST"])
def uncomplete_todo(todo_id):
    with get_db() as conn:
        conn.execute(
            "UPDATE todos SET completed=0, completed_at=NULL WHERE id=?",
            (todo_id,)
        )
    return jsonify({"ok": True})


@app.route("/api/todos/<int:todo_id>", methods=["DELETE"])
def delete_todo(todo_id):
    with get_db() as conn:
        conn.execute("DELETE FROM todos WHERE id=?", (todo_id,))
    return jsonify({"ok": True})


@app.route("/api/templates", methods=["GET"])
def list_templates():
    today = date.today()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM templates ORDER BY active DESC, id DESC"
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        nxt = next_occurrence(d['recur_type'], d['recur_day'], d['recur_month'], today)
        d['next_date'] = nxt.isoformat() if nxt else None
        result.append(d)
    return jsonify(result)


@app.route("/api/templates", methods=["POST"])
def create_template():
    data = request.json or {}
    title = (data.get("title") or "").strip()
    recur_type = data.get("recur_type")
    recur_day = data.get("recur_day")
    recur_month = data.get("recur_month")
    if not title or recur_type not in ("weekly", "monthly", "yearly"):
        return jsonify({"ok": False, "error": "入力不正"}), 400
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO templates (title, recur_type, recur_day, recur_month) VALUES (?, ?, ?, ?)",
            (title, recur_type, recur_day, recur_month)
        )
        new_id = cur.lastrowid
    generate_from_templates()
    return jsonify({"ok": True, "id": new_id})


@app.route("/api/templates/<int:tpl_id>", methods=["PATCH"])
def update_template(tpl_id):
    data = request.json or {}
    fields = []
    values = []
    for key in ("title", "recur_type", "recur_day", "recur_month", "active"):
        if key in data:
            fields.append(f"{key}=?")
            values.append(data[key])
    if not fields:
        return jsonify({"ok": True})
    values.append(tpl_id)
    with get_db() as conn:
        conn.execute(f"UPDATE templates SET {', '.join(fields)} WHERE id=?", values)
    return jsonify({"ok": True})


@app.route("/api/templates/<int:tpl_id>", methods=["DELETE"])
def delete_template(tpl_id):
    with get_db() as conn:
        conn.execute("DELETE FROM templates WHERE id=?", (tpl_id,))
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5007, debug=False)
