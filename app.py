import os
from flask import Flask, request, jsonify
import sqlite3
from pathlib import Path
from datetime import datetime
import secrets
import string
import yt_dlp

app = Flask(__name__)

DB = Path(__file__).resolve().parent / "licenses.db"


def now():
    return datetime.utcnow().isoformat()


def generate_license_key():
    alphabet = string.ascii_uppercase + string.digits
    parts = []
    for _ in range(4):
        part = "".join(secrets.choice(alphabet) for _ in range(5))
        parts.append(part)
    return "FD-" + "-".join(parts)


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS licenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        license_key TEXT UNIQUE NOT NULL,
        active INTEGER DEFAULT 1,
        max_devices INTEGER DEFAULT 2,
        customer_email TEXT DEFAULT '',
        customer_name TEXT DEFAULT '',
        source TEXT DEFAULT '',
        gumroad_sale_id TEXT DEFAULT '',
        product_name TEXT DEFAULT '',
        created_at TEXT DEFAULT ''
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS activations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        license_key TEXT NOT NULL,
        device_id TEXT NOT NULL,
        activated_at TEXT DEFAULT ''
    )
    """)
    
     
    columns = [row["name"] for row in cur.execute("PRAGMA table_info(licenses)").fetchall()]

    if "customer_name" not in columns:
        cur.execute("ALTER TABLE licenses ADD COLUMN customer_name TEXT DEFAULT ''")

    if "source" not in columns:
        cur.execute("ALTER TABLE licenses ADD COLUMN source TEXT DEFAULT ''")

    if "gumroad_sale_id" not in columns:
        cur.execute("ALTER TABLE licenses ADD COLUMN gumroad_sale_id TEXT DEFAULT ''")

    if "product_name" not in columns:
        cur.execute("ALTER TABLE licenses ADD COLUMN product_name TEXT DEFAULT ''")
    conn.commit()
    conn.close()


def create_license(
    customer_email="",
    customer_name="",
    source="manual",
    gumroad_sale_id="",
    product_name="",
    max_devices=2,
):
    init_db()

    conn = get_db()
    cur = conn.cursor()

    if gumroad_sale_id:
        cur.execute(
            "SELECT * FROM licenses WHERE gumroad_sale_id = ?",
            (gumroad_sale_id,)
        )
        existing = cur.fetchone()
        if existing:
            conn.close()
            return dict(existing), False

    for _ in range(20):
        key = generate_license_key()

        try:
            cur.execute("""
                INSERT INTO licenses (
                    license_key,
                    active,
                    max_devices,
                    customer_email,
                    customer_name,
                    source,
                    gumroad_sale_id,
                    product_name,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                key,
                1,
                int(max_devices),
                customer_email,
                customer_name,
                source,
                gumroad_sale_id,
                product_name,
                now(),
            ))

            conn.commit()

            cur.execute("SELECT * FROM licenses WHERE license_key = ?", (key,))
            row = cur.fetchone()
            conn.close()

            return dict(row), True

        except sqlite3.IntegrityError:
            continue

    conn.close()
    raise RuntimeError("Could not generate unique license key")


@app.route("/", methods=["GET"])
def home():
    init_db()
    return jsonify({
        "name": "Freedom Downloader License Server",
        "status": "online"
    })


@app.route("/activate", methods=["POST"])
def activate():
    init_db()

    data = request.get_json(silent=True) or {}

    key = str(data.get("key", "")).strip()
    device_id = str(data.get("device_id", "")).strip()

    if not key:
        return jsonify({
            "valid": False,
            "pro": False,
            "message": "Missing license key"
        }), 400

    if not device_id:
        return jsonify({
            "valid": False,
            "pro": False,
            "message": "Missing device ID"
        }), 400

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM licenses WHERE license_key = ?", (key,))
    license_row = cur.fetchone()

    if not license_row:
        conn.close()
        return jsonify({
            "valid": False,
            "pro": False,
            "message": "Invalid license key"
        })

    if int(license_row["active"]) != 1:
        conn.close()
        return jsonify({
            "valid": False,
            "pro": False,
            "message": "License disabled"
        })

    cur.execute(
        "SELECT * FROM activations WHERE license_key = ? AND device_id = ?",
        (key, device_id)
    )
    existing_device = cur.fetchone()

    if not existing_device:
        cur.execute(
            "SELECT COUNT(*) FROM activations WHERE license_key = ?",
            (key,)
        )
        current_devices = int(cur.fetchone()[0])

        max_devices = int(license_row["max_devices"])

        if current_devices >= max_devices:
            conn.close()
            return jsonify({
                "valid": False,
                "pro": False,
                "message": "Device limit reached"
            })

        cur.execute(
            "INSERT INTO activations (license_key, device_id, activated_at) VALUES (?, ?, ?)",
            (key, device_id, now())
        )
        conn.commit()

    conn.close()

    return jsonify({
        "valid": True,
        "pro": True,
        "message": "PRO activated"
    })


@app.route("/check", methods=["POST"])
def check():
    init_db()

    data = request.get_json(silent=True) or {}

    key = str(data.get("key", "")).strip()
    device_id = str(data.get("device_id", "")).strip()

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT licenses.*
        FROM licenses
        JOIN activations ON licenses.license_key = activations.license_key
        WHERE licenses.license_key = ?
        AND activations.device_id = ?
        AND licenses.active = 1
    """, (key, device_id))

    row = cur.fetchone()
    conn.close()

    if row:
        return jsonify({
            "valid": True,
            "pro": True,
            "message": "License valid"
        })

    return jsonify({
        "valid": False,
        "pro": False,
        "message": "License not active on this device"
    })


@app.route("/admin/create-license", methods=["POST", "GET"])
def admin_create_license():

    data = request.get_json(silent=True) or {}

    customer_email = str(data.get("customer_email", request.args.get("email", ""))).strip()
    customer_name = str(data.get("customer_name", request.args.get("name", ""))).strip()

    license_row, created = create_license(
        customer_email=customer_email,
        customer_name=customer_name,
        source="manual",
        max_devices=2,
    )

    return jsonify({
        "created": created,
        "license": license_row
    })


@app.route("/gumroad/ping", methods=["POST"])
def gumroad_ping():

    init_db()

    secret = request.form.get("secret", "")
    expected_secret = os.getenv("GUMROAD_SECRET", "")

    if secret != expected_secret:
        return jsonify({
            "ok": False,
            "message": "Invalid secret"
        }), 403

    customer_email = request.form.get("email", "").strip()
    customer_name = request.form.get("full_name", "").strip()

    license_row, created = create_license(
        customer_email=customer_email,
        customer_name=customer_name,
        source="gumroad",
        max_devices=2,
    )

    return jsonify({
        "ok": True,
        "license_key": license_row["license_key"],
        "created": created
    })


@app.route("/gumroad/webhook", methods=["POST"])
def gumroad_webhook():
    init_db()

    data = request.form.to_dict()

    customer_email = str(
        data.get("email")
        or data.get("purchaser_email")
        or data.get("customer_email")
        or ""
    ).strip()

    customer_name = str(
        data.get("full_name")
        or data.get("name")
        or data.get("customer_name")
        or ""
    ).strip()

    gumroad_sale_id = str(
        data.get("sale_id")
        or data.get("id")
        or data.get("order_id")
        or ""
    ).strip()

    product_name = str(
        data.get("product_name")
        or data.get("product_permalink")
        or ""
    ).strip()

    license_row, created = create_license(
        customer_email=customer_email,
        customer_name=customer_name,
        source="gumroad",
        gumroad_sale_id=gumroad_sale_id,
        product_name=product_name,
        max_devices=2,
    )

    return jsonify({
        "ok": True,
        "created": created,
        "license_key": license_row["license_key"],
        "customer_email": customer_email,
        "message": "License generated"
    })


@app.route("/admin/list", methods=["GET"])
def admin_list():
    init_db()

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM licenses ORDER BY id DESC")
    licenses = [dict(row) for row in cur.fetchall()]

    cur.execute("SELECT * FROM activations ORDER BY id DESC")
    activations = [dict(row) for row in cur.fetchall()]

    conn.close()

    return jsonify({
        "licenses": licenses,
        "activations": activations
    })


@app.route("/api/preview", methods=["POST"])
def preview():

    data = request.json
    url = data.get("url")

    if not url:
        return jsonify({"error": "Missing URL"}), 400

    try:
        ydl_opts = {
            "quiet": True,
            "extract_flat": False,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        result = {
            "title": info.get("title"),
            "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail"),
        }

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
      
        if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5000, debug=True)
