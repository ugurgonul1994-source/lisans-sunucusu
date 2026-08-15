from flask import Flask, request, jsonify
import sqlite3
from datetime import datetime

app = Flask(__name__)
DB_NAME = "licenses.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS licenses
                 (serial TEXT PRIMARY KEY,
                  used INTEGER DEFAULT 0,
                  machine_id TEXT,
                  activated_at TEXT)''')
    conn.commit()
    conn.close()

@app.route('/activate', methods=['POST'])
def activate():
    data = request.json
    serial = data.get('serial', '').strip().upper()
    machine_id = data.get('machine_id', '').strip()

    if not serial or not machine_id:
        return jsonify({"success": False, "message": "Eksik bilgi"}), 400

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("SELECT used, machine_id FROM licenses WHERE serial = ?", (serial,))
    row = c.fetchone()

    if row is None:
        conn.close()
        return jsonify({"success": False, "message": "Geçersiz seri"}), 404

    used, existing_machine = row

    if used == 1:
        if existing_machine == machine_id:
            conn.close()
            return jsonify({"success": True, "message": "Zaten aktif"})
        else:
            conn.close()
            return jsonify({"success": False, "message": "Bu seri başka bilgisayarda kullanılmış"}), 403

    now = datetime.now().isoformat()
    c.execute("UPDATE licenses SET used = 1, machine_id = ?, activated_at = ? WHERE serial = ?",
              (machine_id, now, serial))
    conn.commit()
    conn.close()

    return jsonify({"success": True, "message": "Aktivasyon başarılı"})

@app.route('/add_serial', methods=['POST'])
def add_serial():
    secret = request.headers.get('X-Secret')
    if secret != "benim_gizli_sifrem_123":
        return jsonify({"error": "Yetkisiz"}), 403

    serial = request.json.get('serial', '').strip().upper()
    if not serial:
        return jsonify({"error": "Seri gerekli"}), 400

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO licenses (serial) VALUES (?)", (serial,))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except:
        conn.close()
        return jsonify({"error": "Seri zaten var"}), 400

if __name__ == '__main__':
    init_db()
    print("Sunucu çalışıyor...")
    app.run(host='0.0.0.0', port=5000)