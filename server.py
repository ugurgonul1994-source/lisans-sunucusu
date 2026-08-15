from flask import Flask, request, jsonify
import os
import sqlite3
from datetime import datetime, timezone

try:
    import psycopg
except ImportError:
    psycopg = None

app = Flask(__name__)

# Render'da DATABASE_URL tanımlanırsa PostgreSQL kullanılır.
# Yerel bilgisayarda DATABASE_URL yoksa licenses.db kullanılır.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
ADMIN_SECRET = os.getenv("LISANS_ADMIN_SECRET", "").strip()

SQL_CREATE = """
CREATE TABLE IF NOT EXISTS licenses (
    serial TEXT PRIMARY KEY,
    used INTEGER NOT NULL DEFAULT 0,
    machine_id TEXT,
    activated_at TEXT
)
"""


def db_mode():
    return "postgres" if DATABASE_URL else "sqlite"


def get_sqlite_path():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "licenses.db")


def get_connection():
    if DATABASE_URL:
        if psycopg is None:
            raise RuntimeError("psycopg kurulu değil. requirements.txt dosyasını kontrol edin.")
        return psycopg.connect(DATABASE_URL, sslmode="require")
    return sqlite3.connect(get_sqlite_path())


def init_db():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(SQL_CREATE)
        conn.commit()
    finally:
        conn.close()


def normalize_serial(value):
    return str(value or "").strip().upper()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


@app.get("/")
def home():
    return jsonify({
        "success": True,
        "service": "lisans-sunucusu",
        "database": db_mode()
    })


@app.get("/health")
def health():
    try:
        init_db()
        return jsonify({"success": True, "database": db_mode()})
    except Exception as e:
        app.logger.exception("Health check hatası")
        return jsonify({"success": False, "error": str(e)}), 500


@app.post("/activate")
def activate():
    data = request.get_json(silent=True) or {}
    serial = normalize_serial(data.get("serial"))
    machine_id = str(data.get("machine_id") or "").strip()

    if not serial or not machine_id:
        return jsonify({"success": False, "message": "Eksik bilgi"}), 400

    try:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute(
            "SELECT used, machine_id FROM licenses WHERE serial = %s"
            if DATABASE_URL else
            "SELECT used, machine_id FROM licenses WHERE serial = ?",
            (serial,)
        )
        row = cur.fetchone()

        if row is None:
            conn.close()
            return jsonify({"success": False, "message": "Geçersiz seri"}), 404

        used, existing_machine = row

        if int(used) == 1:
            if existing_machine == machine_id:
                conn.close()
                return jsonify({"success": True, "message": "Zaten aktif"})
            conn.close()
            return jsonify({
                "success": False,
                "message": "Bu seri başka bilgisayarda kullanılmış"
            }), 403

        if DATABASE_URL:
            cur.execute(
                "UPDATE licenses SET used = 1, machine_id = %s, activated_at = %s WHERE serial = %s",
                (machine_id, now_iso(), serial)
            )
        else:
            cur.execute(
                "UPDATE licenses SET used = 1, machine_id = ?, activated_at = ? WHERE serial = ?",
                (machine_id, now_iso(), serial)
            )

        conn.commit()
        conn.close()

        return jsonify({"success": True, "message": "Aktivasyon başarılı"})

    except Exception as e:
        app.logger.exception("Aktivasyon hatası")
        return jsonify({"success": False, "message": "Sunucu hatası"}), 500


@app.post("/add_serial")
def add_serial():
    if not ADMIN_SECRET:
        return jsonify({"error": "Sunucu LISANS_ADMIN_SECRET ayarlanmamış"}), 500

    secret = request.headers.get("X-Secret", "")
    if secret != ADMIN_SECRET:
        return jsonify({"error": "Yetkisiz"}), 403

    data = request.get_json(silent=True) or {}
    serial = normalize_serial(data.get("serial"))

    if not serial:
        return jsonify({"error": "Seri gerekli"}), 400

    try:
        conn = get_connection()
        cur = conn.cursor()

        if DATABASE_URL:
            cur.execute(
                "INSERT INTO licenses (serial) VALUES (%s)",
                (serial,)
            )
        else:
            cur.execute(
                "INSERT INTO licenses (serial) VALUES (?)",
                (serial,)
            )

        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Seri eklendi"})

    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass

        # PostgreSQL unique ihlali veya SQLite UNIQUE ihlali
        error_text = str(e).lower()
        if "unique" in error_text or "duplicate" in error_text:
            return jsonify({"error": "Seri zaten var"}), 400

        app.logger.exception("Seri ekleme hatası")
        return jsonify({"error": "Sunucu veritabanı hatası"}), 500


# Gunicorn import ettiğinde tabloyu oluştur.
try:
    init_db()
except Exception:
    # Uygulama ayağa kalksın; /health ve loglar gerçek hatayı gösterecek.
    app.logger.exception("Veritabanı başlatılamadı")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
