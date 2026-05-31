import os
import json
import sqlite3
import secrets
import string
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

from flask import Flask, request, jsonify, send_file

app = Flask(__name__)

DB = Path(__file__).resolve().parent / "licenses.db"


def now():
    return datetime.utcnow().isoformat()


def generate_license_key():
    alphabet = string.ascii_uppercase + string.digits
    parts = []
    for _ in range(4):
        parts.append("".join(secrets.choice(alphabet) for _ in range(4)))
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
        created_at TEXT DEFAULT '',
        downloads_limit INTEGER DEFAULT -1,
        downloads_used INTEGER DEFAULT 0
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
    extra_columns = {
        "max_devices": "INTEGER DEFAULT 2",
        "customer_email": "TEXT DEFAULT ''",
        "customer_name": "TEXT DEFAULT ''",
        "source": "TEXT DEFAULT ''",
        "gumroad_sale_id": "TEXT DEFAULT ''",
        "product_name": "TEXT DEFAULT ''",
        "created_at": "TEXT DEFAULT ''",
        "downloads_limit": "INTEGER DEFAULT -1",
        "downloads_used": "INTEGER DEFAULT 0",
    }

    for name, definition in extra_columns.items():
        if name not in columns:
            cur.execute(f"ALTER TABLE licenses ADD COLUMN {name} {definition}")

    conn.commit()
    conn.close()


def product_download_limit(product_name):
    name = (product_name or "").lower()

    if "lifetime" in name or "doživot" in name or "lifetime-pro" in name:
        return -1

    if "5" in name or "five" in name:
        return 5

    if "3" in name or "three" in name:
        return 3

    if "1" in name or "one" in name:
        return 1

    return -1


def create_license(
    customer_email="",
    customer_name="",
    source="manual",
    gumroad_sale_id="",
    product_name="",
    max_devices=2,
    downloads_limit=-1,
):
    init_db()
    conn = get_db()
    cur = conn.cursor()

    if gumroad_sale_id:
        cur.execute("SELECT * FROM licenses WHERE gumroad_sale_id = ?", (gumroad_sale_id,))
        existing = cur.fetchone()
        if existing:
            conn.close()
            return dict(existing), False

    for _ in range(50):
        key = generate_license_key()
        try:
            cur.execute("""
                INSERT INTO licenses (
                    license_key, active, max_devices, customer_email, customer_name,
                    source, gumroad_sale_id, product_name, created_at,
                    downloads_limit, downloads_used
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                int(downloads_limit),
                0,
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


def verify_gumroad_license(key):
    product_ids = [
        os.getenv("GUMROAD_PRODUCT_ID", "").strip(),
        os.getenv("GUMROAD_PRODUCT_ID_1_SONG", "").strip(),
        os.getenv("GUMROAD_PRODUCT_ID_5_DOWNLOADS", "").strip(),
        os.getenv("GUMROAD_PRODUCT_ID_3_SONGS", "").strip(),
        os.getenv("GUMROAD_PRODUCT_ID_LIFETIME", "").strip(),
    ]
    product_ids = [p for p in product_ids if p]

    if not product_ids:
        return False, "Missing Gumroad product IDs", {}

    last_message = "Invalid Gumroad license key"

    for product_id in product_ids:
        payload = urllib.parse.urlencode({
            "product_id": product_id,
            "license_key": key,
            "increment_uses_count": "false",
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.gumroad.com/v2/licenses/verify",
            data=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "FreedomDownloader/1.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=12) as response:
                raw = response.read().decode("utf-8")
                gumroad_data = json.loads(raw)

            if gumroad_data.get("success") is True:
                purchase = gumroad_data.get("purchase") or {}

                if purchase.get("refunded") is True:
                    return False, "License refunded", gumroad_data

                if purchase.get("chargebacked") is True:
                    return False, "License chargebacked", gumroad_data

                return True, "Gumroad license valid", gumroad_data

            last_message = gumroad_data.get("message") or last_message

        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8")
                body_data = json.loads(body)
                last_message = body_data.get("message") or body
            except Exception:
                last_message = f"Gumroad HTTP {e.code}: {e.reason}"
            continue

        except Exception as e:
            last_message = "Gumroad error: " + str(e)
            continue

    return False, last_message, {}


def activate_local_license(key, device_id, gumroad_data=None):
    init_db()
    conn = get_db()
    cur = conn.cursor()

    if gumroad_data:
        purchase = gumroad_data.get("purchase") or {}

        gumroad_sale_id = str(
            purchase.get("id") or purchase.get("sale_id") or ""
        ).strip()

        customer_email = str(
            purchase.get("email") or purchase.get("purchaser_email") or ""
        ).strip()

        customer_name = str(
            purchase.get("full_name") or purchase.get("name") or ""
        ).strip()

        product_name = str(
            purchase.get("product_name")
            or purchase.get("product_permalink")
            or purchase.get("permalink")
            or "Freedom Downloader"
        ).strip()

        downloads_limit = product_download_limit(product_name)

        cur.execute("SELECT * FROM licenses WHERE license_key = ?", (key,))
        row = cur.fetchone()

        if not row:
            cur.execute("""
                INSERT INTO licenses (
                    license_key, active, max_devices, customer_email, customer_name,
                    source, gumroad_sale_id, product_name, created_at,
                    downloads_limit, downloads_used
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                key,
                1,
                2,
                customer_email,
                customer_name,
                "gumroad_api",
                gumroad_sale_id,
                product_name,
                now(),
                downloads_limit,
                0,
            ))
            conn.commit()
        else:
            cur.execute("""
                UPDATE licenses
                SET customer_email = ?, customer_name = ?, source = ?,
                    gumroad_sale_id = ?, product_name = ?, downloads_limit = ?
                WHERE license_key = ?
            """, (
                customer_email,
                customer_name,
                "gumroad_api",
                gumroad_sale_id,
                product_name,
                downloads_limit,
                key,
            ))
            conn.commit()

    cur.execute("SELECT * FROM licenses WHERE license_key = ?", (key,))
    license_row = cur.fetchone()

    if not license_row:
        conn.close()
        return False, "Invalid license key", None

    if int(license_row["active"]) != 1:
        conn.close()
        return False, "License disabled", None

    cur.execute(
        "SELECT * FROM activations WHERE license_key = ? AND device_id = ?",
        (key, device_id),
    )
    existing_device = cur.fetchone()

    if not existing_device:
        cur.execute("SELECT COUNT(*) FROM activations WHERE license_key = ?", (key,))
        current_devices = int(cur.fetchone()[0])
        max_devices = int(license_row["max_devices"])

        if current_devices >= max_devices:
            conn.close()
            return False, "Device limit reached", None

        cur.execute(
            "INSERT INTO activations (license_key, device_id, activated_at) VALUES (?, ?, ?)",
            (key, device_id, now()),
        )
        conn.commit()

    cur.execute("SELECT * FROM licenses WHERE license_key = ?", (key,))
    license_row = cur.fetchone()
    result = dict(license_row)
    conn.close()

    return True, "PRO activated", result


@app.route("/", methods=["GET"])
def home():
    init_db()
    return jsonify({
        "name": "Freedom Downloader License Server",
        "status": "online",
        "endpoints": [
            "/activate",
            "/consume-download",
            "/check",
            "/admin/list",
            "/admin/create-license",
            "/gumroad/webhook",
            "/gumroad/ping",
        ],
    })


@app.route("/activate", methods=["POST"])
def activate():
    data = request.get_json(silent=True) or {}

    key = str(data.get("key", "")).strip()
    device_id = str(data.get("device_id", "")).strip()

    if not key:
        return jsonify({"valid": False, "pro": False, "message": "Missing license key"}), 400

    if not device_id:
        return jsonify({"valid": False, "pro": False, "message": "Missing device ID"}), 400

    gumroad_ok, gumroad_message, gumroad_data = verify_gumroad_license(key)

    if gumroad_ok:
        ok, message, license_row = activate_local_license(key, device_id, gumroad_data)
    else:
        ok, message, license_row = activate_local_license(key, device_id, None)
        if not ok:
            message = gumroad_message or message

    if not ok:
        return jsonify({
            "valid": False,
            "pro": False,
            "message": message,
        })

    downloads_limit = int(license_row.get("downloads_limit", -1))
    downloads_used = int(license_row.get("downloads_used", 0))
    remaining = -1 if downloads_limit < 0 else max(0, downloads_limit - downloads_used)

    return jsonify({
        "valid": True,
        "pro": True,
        "message": "PRO activated",
        "license_key": license_row.get("license_key", key),
        "product_name": license_row.get("product_name", ""),
        "downloads_limit": downloads_limit,
        "downloads_used": downloads_used,
        "downloads_remaining": remaining,
    })


@app.route("/consume-download", methods=["POST"])
def consume_download():
    init_db()
    data = request.get_json(silent=True) or {}

    key = str(data.get("key", "")).strip()
    device_id = str(data.get("device_id", "")).strip()

    if not key or not device_id:
        return jsonify({"allowed": False, "message": "Missing license or device"}), 400

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

    if not row:
        conn.close()
        return jsonify({
            "allowed": False,
            "message": "Activate PRO first",
        })

    downloads_limit = int(row["downloads_limit"])
    downloads_used = int(row["downloads_used"])

    if downloads_limit >= 0 and downloads_used >= downloads_limit:
        conn.close()
        return jsonify({
            "allowed": False,
            "message": "Download limit reached",
            "downloads_limit": downloads_limit,
            "downloads_used": downloads_used,
            "downloads_remaining": 0,
        })

    if downloads_limit >= 0:
        downloads_used += 1
        cur.execute(
            "UPDATE licenses SET downloads_used = ? WHERE license_key = ?",
            (downloads_used, key),
        )
        conn.commit()

    remaining = -1 if downloads_limit < 0 else max(0, downloads_limit - downloads_used)

    conn.close()

    return jsonify({
        "allowed": True,
        "message": "Download allowed",
        "downloads_limit": downloads_limit,
        "downloads_used": downloads_used,
        "downloads_remaining": remaining,
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
        return jsonify({"valid": True, "pro": True, "message": "License valid"})

    return jsonify({"valid": False, "pro": False, "message": "License not active on this device"})


@app.route("/admin/create-license", methods=["GET", "POST"])
def admin_create_license():
    data = request.get_json(silent=True) or {}

    customer_email = str(data.get("customer_email", request.args.get("email", ""))).strip()
    customer_name = str(data.get("customer_name", request.args.get("name", ""))).strip()
    product_name = str(data.get("product_name", request.args.get("product", "manual"))).strip()

    limit_raw = data.get("downloads_limit", request.args.get("limit", ""))
    if str(limit_raw).strip() == "":
        downloads_limit = product_download_limit(product_name)
    else:
        downloads_limit = int(limit_raw)

    license_row, created = create_license(
        customer_email=customer_email,
        customer_name=customer_name,
        source="manual",
        product_name=product_name,
        max_devices=2,
        downloads_limit=downloads_limit,
    )

    return jsonify({
        "created": created,
        "license": license_row,
    })


@app.route("/admin/list", methods=["GET"])
def admin_list():
    init_db()
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM licenses ORDER BY id DESC")
    licenses = []
    for row in cur.fetchall():
        item = dict(row)
        downloads_limit = int(item.get("downloads_limit", -1))
        downloads_used = int(item.get("downloads_used", 0))
        item["downloads_remaining"] = -1 if downloads_limit < 0 else max(0, downloads_limit - downloads_used)
        licenses.append(item)

    cur.execute("SELECT * FROM activations ORDER BY id DESC")
    activations = [dict(row) for row in cur.fetchall()]

    conn.close()

    return jsonify({
        "licenses": licenses,
        "activations": activations,
    })


@app.route("/gumroad/ping", methods=["POST"])
def gumroad_ping():
    init_db()

    data = request.form.to_dict()

    customer_email = str(data.get("email", "")).strip()
    customer_name = str(data.get("full_name", "")).strip()
    gumroad_sale_id = str(data.get("sale_id", data.get("id", ""))).strip()
    product_name = str(data.get("product_name", data.get("product_permalink", ""))).strip()

    downloads_limit = product_download_limit(product_name)

    license_row, created = create_license(
        customer_email=customer_email,
        customer_name=customer_name,
        source="gumroad_ping",
        gumroad_sale_id=gumroad_sale_id,
        product_name=product_name,
        max_devices=2,
        downloads_limit=downloads_limit,
    )

    return jsonify({
        "ok": True,
        "created": created,
        "license_key": license_row["license_key"],
        "customer_email": customer_email,
        "customer_name": customer_name,
        "product_name": product_name,
        "downloads_limit": license_row["downloads_limit"],
        "message": "License generated",
    })


@app.route("/gumroad/webhook", methods=["POST"])
def gumroad_webhook():
    init_db()

    data = request.form.to_dict()

    customer_email = str(
        data.get("email") or data.get("purchaser_email") or data.get("customer_email") or ""
    ).strip()

    customer_name = str(
        data.get("full_name") or data.get("name") or data.get("customer_name") or ""
    ).strip()

    gumroad_sale_id = str(
        data.get("sale_id") or data.get("id") or data.get("order_id") or ""
    ).strip()

    product_name = str(
        data.get("product_name") or data.get("product_permalink") or data.get("permalink") or ""
    ).strip()

    downloads_limit = product_download_limit(product_name)

    license_row, created = create_license(
        customer_email=customer_email,
        customer_name=customer_name,
        source="gumroad_webhook",
        gumroad_sale_id=gumroad_sale_id,
        product_name=product_name,
        max_devices=2,
        downloads_limit=downloads_limit,
    )

    return jsonify({
        "ok": True,
        "created": created,
        "license_key": license_row["license_key"],
        "customer_email": customer_email,
        "customer_name": customer_name,
        "product_name": product_name,
        "downloads_limit": license_row["downloads_limit"],
        "message": "License generated",
    })


@app.route("/api/preview", methods=["POST"])
def preview():
    data = request.get_json(silent=True) or {}
    url = str(data.get("url", "")).strip()

    if not url:
        return jsonify({"error": "Missing URL"}), 400

    return jsonify({
        "title": "Media Preview",
        "duration": 0,
        "thumbnail": "",
        "url": url,
    })


@app.route("/api/download-pro", methods=["GET"])
def download_pro():
    file_path = Path(__file__).resolve().parent / "files" / "demo.mp3"

    if not file_path.exists():
        return jsonify({
            "error": "demo.mp3 not found",
            "path": str(file_path),
        }), 404

    return send_file(
        file_path,
        as_attachment=True,
        download_name="freedom-demo.mp3",
        mimetype="audio/mpeg",
    )


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
