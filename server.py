from flask import Flask, request, jsonify, render_template_string, redirect, url_for, session
import os
import sqlite3
import secrets
import string
from datetime import datetime, timezone

try:
    import psycopg
except ImportError:
    psycopg = None

app = Flask(__name__)

# ---------------------------------------------------------
# AYARLAR
# ---------------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
ADMIN_SECRET = os.getenv("LISANS_ADMIN_SECRET", "").strip()

# Flask oturumları için ayrı gizli anahtar
FLASK_SECRET = os.getenv(
    "FLASK_SECRET",
    "gecici-kooperatif-lisans-secret-2026"
)

app.secret_key = FLASK_SECRET


# ---------------------------------------------------------
# VERİTABANI
# ---------------------------------------------------------

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
            raise RuntimeError(
                "psycopg kurulu değil. requirements.txt dosyasını kontrol edin."
            )

        return psycopg.connect(
            DATABASE_URL,
            sslmode="require"
        )

    return sqlite3.connect(
        get_sqlite_path()
    )


def init_db():

    conn = get_connection()

    try:

        cur = conn.cursor()

        cur.execute(SQL_CREATE)

        conn.commit()

    finally:

        conn.close()


# ---------------------------------------------------------
# YARDIMCI FONKSİYONLAR
# ---------------------------------------------------------

def normalize_serial(value):

    return str(value or "").strip().upper()


def now_iso():

    return datetime.now(timezone.utc).isoformat()


def generate_serial():

    chars = string.ascii_uppercase + string.digits

    parts = []

    for _ in range(4):

        part = "".join(
            secrets.choice(chars)
            for _ in range(4)
        )

        parts.append(part)

    return "-".join(parts)


def admin_required():

    return bool(
        session.get("admin_logged_in")
    )


# ---------------------------------------------------------
# ANA SAYFA
# ---------------------------------------------------------

@app.get("/")
def home():

    return jsonify({
        "success": True,
        "service": "lisans-sunucusu",
        "database": db_mode()
    })


# ---------------------------------------------------------
# HEALTH
# ---------------------------------------------------------

@app.get("/health")
def health():

    try:

        init_db()

        return jsonify({
            "success": True,
            "database": db_mode()
        })

    except Exception as e:

        app.logger.exception(
            "Health check hatası"
        )

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ---------------------------------------------------------
# MÜŞTERİ AKTİVASYONU
# ---------------------------------------------------------

@app.post("/activate")
def activate():

    data = request.get_json(
        silent=True
    ) or {}

    serial = normalize_serial(
        data.get("serial")
    )

    machine_id = str(
        data.get("machine_id") or ""
    ).strip()

    if not serial or not machine_id:

        return jsonify({
            "success": False,
            "message": "Eksik bilgi"
        }), 400

    try:

        conn = get_connection()
        cur = conn.cursor()

        if DATABASE_URL:

            cur.execute(
                """
                SELECT used, machine_id
                FROM licenses
                WHERE serial = %s
                """,
                (serial,)
            )

        else:

            cur.execute(
                """
                SELECT used, machine_id
                FROM licenses
                WHERE serial = ?
                """,
                (serial,)
            )

        row = cur.fetchone()

        if row is None:

            conn.close()

            return jsonify({
                "success": False,
                "message": "Geçersiz seri"
            }), 404

        used, existing_machine = row

        # Daha önce bu bilgisayarda aktive edilmişse
        if int(used) == 1:

            if existing_machine == machine_id:

                conn.close()

                return jsonify({
                    "success": True,
                    "message": "Zaten aktif"
                })

            conn.close()

            return jsonify({
                "success": False,
                "message": "Bu seri başka bilgisayarda kullanılmış"
            }), 403

        # İlk aktivasyon
        if DATABASE_URL:

            cur.execute(
                """
                UPDATE licenses
                SET used = 1,
                    machine_id = %s,
                    activated_at = %s
                WHERE serial = %s
                """,
                (
                    machine_id,
                    now_iso(),
                    serial
                )
            )

        else:

            cur.execute(
                """
                UPDATE licenses
                SET used = 1,
                    machine_id = ?,
                    activated_at = ?
                WHERE serial = ?
                """,
                (
                    machine_id,
                    now_iso(),
                    serial
                )
            )

        conn.commit()
        conn.close()

        return jsonify({
            "success": True,
            "message": "Aktivasyon başarılı"
        })

    except Exception:

        app.logger.exception(
            "Aktivasyon hatası"
        )

        return jsonify({
            "success": False,
            "message": "Sunucu hatası"
        }), 500


# ---------------------------------------------------------
# ESKİ /ADD_SERIAL API
# ---------------------------------------------------------

@app.post("/add_serial")
def add_serial():

    if not ADMIN_SECRET:

        return jsonify({
            "error": "Sunucu LISANS_ADMIN_SECRET ayarlanmamış"
        }), 500

    secret = request.headers.get(
        "X-Secret",
        ""
    )

    if secret != ADMIN_SECRET:

        return jsonify({
            "error": "Yetkisiz"
        }), 403

    data = request.get_json(
        silent=True
    ) or {}

    serial = normalize_serial(
        data.get("serial")
    )

    if not serial:

        return jsonify({
            "error": "Seri gerekli"
        }), 400

    try:

        conn = get_connection()
        cur = conn.cursor()

        if DATABASE_URL:

            cur.execute(
                """
                INSERT INTO licenses (serial)
                VALUES (%s)
                """,
                (serial,)
            )

        else:

            cur.execute(
                """
                INSERT INTO licenses (serial)
                VALUES (?)
                """,
                (serial,)
            )

        conn.commit()
        conn.close()

        return jsonify({
            "success": True,
            "message": "Seri eklendi"
        })

    except Exception as e:

        try:
            conn.close()
        except Exception:
            pass

        error_text = str(e).lower()

        if (
            "unique" in error_text
            or "duplicate" in error_text
        ):

            return jsonify({
                "error": "Seri zaten var"
            }), 400

        app.logger.exception(
            "Seri ekleme hatası"
        )

        return jsonify({
            "error": "Sunucu veritabanı hatası"
        }), 500


# =========================================================
# YÖNETİCİ PANELİ
# =========================================================

LOGIN_HTML = """
<!DOCTYPE html>
<html lang="tr">
<head>

<meta charset="UTF-8">

<title>Lisans Yönetimi</title>

<style>

body {
    font-family: Arial, sans-serif;
    background: #679bb8;
    margin: 0;
    padding: 0;
}

.box {
    width: 420px;
    margin: 100px auto;
    background: white;
    padding: 30px;
    border-radius: 15px;
    box-shadow: 0 10px 30px rgba(0,0,0,.2);
}

h1 {
    text-align: center;
    color: #174a68;
}

input {
    width: 100%;
    box-sizing: border-box;
    padding: 13px;
    margin-top: 10px;
    border: 1px solid #ccc;
    border-radius: 7px;
    font-size: 16px;
}

button {
    width: 100%;
    padding: 13px;
    margin-top: 15px;
    border: none;
    border-radius: 7px;
    background: #006abc;
    color: white;
    font-size: 16px;
    cursor: pointer;
}

button:hover {
    background: #00539a;
}

.error {
    color: #b00020;
    text-align: center;
    margin-top: 15px;
}

</style>

</head>

<body>

<div class="box">

<h1>🔐 Lisans Yönetimi</h1>

<form method="POST">

<input
    type="password"
    name="secret"
    placeholder="Yönetici şifresi"
    autofocus
>

<button type="submit">
    Giriş Yap
</button>

</form>

{% if error %}

<div class="error">
{{ error }}
</div>

{% endif %}

</div>

</body>
</html>
"""


ADMIN_HTML = """
<!DOCTYPE html>
<html lang="tr">

<head>

<meta charset="UTF-8">

<title>Kooperatif Lisans Yönetimi</title>

<style>

body {
    font-family: Arial, sans-serif;
    background: #679bb8;
    margin: 0;
    padding: 25px;
}

.container {
    max-width: 1200px;
    margin: auto;
}

.card {
    background: white;
    padding: 25px;
    margin-bottom: 20px;
    border-radius: 15px;
    box-shadow: 0 5px 20px rgba(0,0,0,.15);
}

h1 {
    color: white;
    text-align: center;
    margin-bottom: 30px;
}

h2 {
    color: #174a68;
}

input {
    padding: 11px;
    border: 1px solid #ccc;
    border-radius: 7px;
    font-size: 15px;
}

button {
    padding: 11px 18px;
    border: none;
    border-radius: 7px;
    background: #006abc;
    color: white;
    cursor: pointer;
    font-weight: bold;
}

button:hover {
    background: #00539a;
}

.btn-red {
    background: #c62828;
}

.btn-orange {
    background: #e67e22;
}

.btn-green {
    background: #2e7d32;
}

table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 15px;
}

th {
    background: #174a68;
    color: white;
    padding: 12px;
    text-align: left;
}

td {
    padding: 11px;
    border-bottom: 1px solid #ddd;
}

.used {
    color: #c62828;
    font-weight: bold;
}

.free {
    color: #2e7d32;
    font-weight: bold;
}

.serial {
    font-family: Consolas, monospace;
    font-weight: bold;
}

.actions {
    display: flex;
    gap: 7px;
}

.logout {
    text-align: right;
    margin-bottom: 15px;
}

.logout a {
    color: white;
    text-decoration: none;
    font-weight: bold;
}

.generated {
    font-size: 20px;
    font-family: Consolas, monospace;
    font-weight: bold;
    padding: 15px;
    background: #eef6fa;
    border-radius: 8px;
    margin-top: 15px;
}

.message {
    background: #e8f5e9;
    color: #2e7d32;
    padding: 12px;
    border-radius: 7px;
    margin-bottom: 15px;
}

.error {
    background: #ffebee;
    color: #c62828;
    padding: 12px;
    border-radius: 7px;
    margin-bottom: 15px;
}

</style>

</head>

<body>

<div class="container">

<h1>🔑 KOOPERATİF LİSANS YÖNETİMİ</h1>

<div class="logout">
<a href="/admin/logout">Çıkış Yap</a>
</div>


{% if message %}

<div class="message">
{{ message }}
</div>

{% endif %}


{% if error %}

<div class="error">
{{ error }}
</div>

{% endif %}


<div class="card">

<h2>➕ Yeni Lisans</h2>

<form method="POST" action="/admin/add">

<input
    type="text"
    name="serial"
    placeholder="Özel lisans kodu (boş bırakılırsa otomatik üretilir)"
    style="width:60%;"
>

<button type="submit">
Lisans Ekle
</button>

</form>

{% if generated %}

<div class="generated">
{{ generated }}
</div>

{% endif %}

</div>


<div class="card">

<h2>📋 Lisanslar</h2>

<table>

<tr>

<th>Lisans Kodu</th>
<th>Durum</th>
<th>Bilgisayar</th>
<th>Aktivasyon</th>
<th>İşlem</th>

</tr>


{% for item in licenses %}

<tr>

<td class="serial">
{{ item.serial }}
</td>

<td>

{% if item.used %}

<span class="used">
🟢 Kullanıldı
</span>

{% else %}

<span class="free">
⚪ Kullanılmadı
</span>

{% endif %}

</td>

<td>

{% if item.machine_id %}

{{ item.machine_id[:20] }}...

{% else %}

—

{% endif %}

</td>

<td>

{{ item.activated_at or "—" }}

</td>

<td>

<div class="actions">

{% if item.used %}

<form method="POST" action="/admin/reset">

<input
    type="hidden"
    name="serial"
    value="{{ item.serial }}"
>

<button
    class="btn-orange"
    type="submit"
>
Sıfırla
</button>

</form>

{% endif %}


<form method="POST" action="/admin/delete">

<input
    type="hidden"
    name="serial"
    value="{{ item.serial }}"
>

<button
    class="btn-red"
    type="submit"
    onclick="return confirm('Bu lisans silinsin mi?')"
>
Sil
</button>

</form>

</div>

</td>

</tr>

{% endfor %}

</table>

</div>

</div>

</body>

</html>
"""


# ---------------------------------------------------------
# ADMIN GİRİŞ
# ---------------------------------------------------------

@app.route("/admin", methods=["GET", "POST"])
def admin():

    if admin_required():

        return redirect(
            url_for("admin_panel")
        )

    error = ""

    if request.method == "POST":

        secret = request.form.get(
            "secret",
            ""
        )

        if (
            ADMIN_SECRET
            and secrets.compare_digest(
                secret,
                ADMIN_SECRET
            )
        ):

            session["admin_logged_in"] = True

            return redirect(
                url_for("admin_panel")
            )

        error = "Yönetici şifresi hatalı."

    return render_template_string(
        LOGIN_HTML,
        error=error
    )


# ---------------------------------------------------------
# ADMIN PANEL
# ---------------------------------------------------------

@app.get("/admin/panel")
def admin_panel():

    if not admin_required():

        return redirect(
            url_for("admin")
        )

    try:

        conn = get_connection()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT serial,
                   used,
                   machine_id,
                   activated_at
            FROM licenses
            ORDER BY serial
            """
        )

        rows = cur.fetchall()

        conn.close()

        licenses = []

        for row in rows:

            licenses.append({
                "serial": row[0],
                "used": row[1],
                "machine_id": row[2],
                "activated_at": row[3]
            })

        return render_template_string(
            ADMIN_HTML,
            licenses=licenses,
            generated=None,
            message=None,
            error=None
        )

    except Exception:

        app.logger.exception(
            "Admin panel hatası"
        )

        return "Veritabanı hatası", 500


# ---------------------------------------------------------
# ADMIN - LİSANS EKLE
# ---------------------------------------------------------

@app.post("/admin/add")
def admin_add():

    if not admin_required():

        return redirect(
            url_for("admin")
        )

    serial = normalize_serial(
        request.form.get("serial")
    )

    if not serial:

        serial = generate_serial()

    try:

        conn = get_connection()
        cur = conn.cursor()

        if DATABASE_URL:

            cur.execute(
                """
                INSERT INTO licenses (serial)
                VALUES (%s)
                """,
                (serial,)
            )

        else:

            cur.execute(
                """
                INSERT INTO licenses (serial)
                VALUES (?)
                """,
                (serial,)
            )

        conn.commit()
        conn.close()

        # Listeyi yeniden al
        return redirect(
            url_for(
                "admin_panel",
                generated=serial
            )
        )

    except Exception as e:

        try:
            conn.close()
        except Exception:
            pass

        if (
            "unique" in str(e).lower()
            or "duplicate" in str(e).lower()
        ):

            return redirect(
                url_for(
                    "admin_panel"
                )
            )

        app.logger.exception(
            "Admin lisans ekleme hatası"
        )

        return "Lisans eklenemedi", 500


# ---------------------------------------------------------
# ADMIN - LİSANS SIFIRLA
# ---------------------------------------------------------

@app.post("/admin/reset")
def admin_reset():

    if not admin_required():

        return redirect(
            url_for("admin")
        )

    serial = normalize_serial(
        request.form.get("serial")
    )

    try:

        conn = get_connection()
        cur = conn.cursor()

        if DATABASE_URL:

            cur.execute(
                """
                UPDATE licenses
                SET used = 0,
                    machine_id = NULL,
                    activated_at = NULL
                WHERE serial = %s
                """,
                (serial,)
            )

        else:

            cur.execute(
                """
                UPDATE licenses
                SET used = 0,
                    machine_id = NULL,
                    activated_at = NULL
                WHERE serial = ?
                """,
                (serial,)
            )

        conn.commit()
        conn.close()

        return redirect(
            url_for("admin_panel")
        )

    except Exception:

        app.logger.exception(
            "Lisans sıfırlama hatası"
        )

        return "Lisans sıfırlanamadı", 500


# ---------------------------------------------------------
# ADMIN - LİSANS SİL
# ---------------------------------------------------------

@app.post("/admin/delete")
def admin_delete():

    if not admin_required():

        return redirect(
            url_for("admin")
        )

    serial = normalize_serial(
        request.form.get("serial")
    )

    try:

        conn = get_connection()
        cur = conn.cursor()

        if DATABASE_URL:

            cur.execute(
                """
                DELETE FROM licenses
                WHERE serial = %s
                """,
                (serial,)
            )

        else:

            cur.execute(
                """
                DELETE FROM licenses
                WHERE serial = ?
                """,
                (serial,)
            )

        conn.commit()
        conn.close()

        return redirect(
            url_for("admin_panel")
        )

    except Exception:

        app.logger.exception(
            "Lisans silme hatası"
        )

        return "Lisans silinemedi", 500


# ---------------------------------------------------------
# ADMIN - ÇIKIŞ
# ---------------------------------------------------------

@app.get("/admin/logout")
def admin_logout():

    session.clear()

    return redirect(
        url_for("admin")
    )


# ---------------------------------------------------------
# VERİTABANI BAŞLAT
# ---------------------------------------------------------

try:

    init_db()

except Exception:

    app.logger.exception(
        "Veritabanı başlatılamadı"
    )


# ---------------------------------------------------------
# ÇALIŞTIR
# ---------------------------------------------------------

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "5000"
            )
        )
    )
