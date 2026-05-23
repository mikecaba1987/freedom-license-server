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
        "customer_name": "TEXT DEFAULT ''",
        "source": "TEXT DEFAULT ''",
        "gumroad_sale_id": "TEXT DEFAULT ''",
        "product_name": "TEXT DEFAULT ''",
        "downloads_limit": "INTEGER DEFAULT -1",
        "downloads_used": "INTEGER DEFAULT 0",
    }

    for name, definition in extra_columns.items():
        if name not in columns:
            cur.execute(f"ALTER TABLE licenses ADD COLUMN {name} {definition}")

    conn.commit()
    conn.close()


def product_download_limit(product_name):
    value = (product_name or "").lower()

    if "lifetime" in value or "freedom-lifetime-pro" in value:
        return -1

    if "3 song" in value or "3-song" in value or "three" in value or "freedom-3-songs" in value:
        return 3

    if "1 song" in value or "1-song" in value or "one" in value or "freedom-1-song" in value:
        return 1

    # Safe default for paid keys where Gumroad does not return a name.
    return -1


def create_license(customer_email="", customer_name="", source="manual", gumroad_sale_id="", product_name="", max_devices=2, downloads_limit=-1):
    init_db()
    conn = get_db()
    cur = conn.cursor()

    if gumroad_sale_id:
        cur.execute("SELECT * FROM licenses WHERE gumroad_sale_id = ?", (gumroad_sale_id,))
        existing = cur.fetchone()
        if existing:
            conn.close()
            return dict(existing), False

    for _ in range(20):
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
                key, 1, int(max_devices), customer_email, customer_name,
                source, gumroad_sale_id, product_name, now(), int(downloads_limit), 0
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


def extract_youtube_id(url):
    parsed = urllib.parse.urlparse(url)

    if parsed.hostname in ["youtu.be", "www.youtu.be"]:
        return parsed.path.strip("/")

    if parsed.hostname in ["youtube.com", "www.youtube.com", "m.youtube.com"]:
        query = urllib.parse.parse_qs(parsed.query)
        if "v" in query:
            return query["v"][0]
        if parsed.path.startswith("/shorts/"):
            return parsed.path.split("/shorts/")[1].split("/")[0]

    return ""


def verify_gumroad_license(key):
    gumroad_product_id = os.getenv("GUMROAD_PRODUCT_ID", "").strip()
    if not gumroad_product_id:
        return False, "Missing GUMROAD_PRODUCT_ID", {}

    payload = urllib.parse.urlencode({
        "product_id": gumroad_product_id,
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
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8")
        except Exception:
            body = ""
        return False, f"Gumroad HTTP {e.code}: {body or e.reason}", {}
    except Exception as e:
        return False, "Gumroad error: " + str(e), {}

    if gumroad_data.get("success") is not True:
        return False, gumroad_data.get("message") or "Invalid Gumroad license key", gumroad_data

    purchase = gumroad_data.get("purchase") or {}

    if purchase.get("refunded") is True:
        return False, "License refunded", gumroad_data

    if purchase.get("chargebacked") is True:
        return False, "License chargebacked", gumroad_data

    return True, "Gumroad license valid", gumroad_data


def activate_local_license(key, device_id, gumroad_data=None):
    init_db()
    conn = get_db()
    cur = conn.cursor()

    if gumroad_data:
        purchase = gumroad_data.get("purchase") or {}
        gumroad_sale_id = str(purchase.get("id") or purchase.get("sale_id") or "").strip()
        customer_email = str(purchase.get("email") or purchase.get("purchaser_email") or "").strip()
        customer_name = str(purchase.get("full_name") or purchase.get("name") or "").strip()
        product_name = str(
            purchase.get("product_name")
            or purchase.get("product_permalink")
            or purchase.get("permalink")
            or "Freedom Downloader"
        ).strip()
        downloads_limit = product_download_limit(product_name)

        cur.execute("SELECT * FROM licenses WHERE license_key = ?", (key,))
        license_row = cur.fetchone()
        if not license_row:
            cur.execute("""
                INSERT INTO licenses (
                    license_key, active, max_devices, customer_email, customer_name,
                    source, gumroad_sale_id, product_name, created_at,
                    downloads_limit, downloads_used
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                key, 1, 2, customer_email, customer_name,
                "gumroad_api", gumroad_sale_id, product_name, now(), downloads_limit, 0
            ))
            conn.commit()
            cur.execute("SELECT * FROM licenses WHERE license_key = ?", (key,))
            license_row = cur.fetchone()
        else:
            cur.execute("""
                UPDATE licenses
                SET customer_email = ?, customer_name = ?, source = ?, gumroad_sale_id = ?,
                    product_name = ?, downloads_limit = ?
                WHERE license_key = ?
            """, (customer_email, customer_name, "gumroad_api", gumroad_sale_id, product_name, downloads_limit, key))
            conn.commit()
            cur.execute("SELECT * FROM licenses WHERE license_key = ?", (key,))
            license_row = cur.fetchone()
    else:
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
    return jsonify({"name": "Freedom Downloader License Server", "status": "online"})


@app.route("/api/preview", methods=["POST"])
def preview():
    data = request.get_json(silent=True) or {}
    url = str(data.get("url", "")).strip()
    if not url:
        return jsonify({"error": "Missing URL"}), 400

    video_id = extract_youtube_id(url)
    try:
        oembed_url = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(url, safe="")
        req = urllib.request.Request(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as response:
            raw = response.read().decode("utf-8")
            info = json.loads(raw)

        title = info.get("title", "YouTube Audio Preview")
        thumbnail = info.get("thumbnail_url", "") or (f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg" if video_id else "")
        return jsonify({"title": title, "duration": 0, "thumbnail": thumbnail, "video_id": video_id})
    except Exception:
        thumbnail = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg" if video_id else ""
        return jsonify({"title": "YouTube Audio Preview", "duration": 0, "thumbnail": thumbnail, "video_id": video_id})


@app.route("/api/download-pro", methods=["GET"])
def download_pro():
    file_path = Path(__file__).resolve().parent / "files" / "demo.mp3"
    if not file_path.exists():
        return jsonify({"error": "demo.mp3 not found", "path": str(file_path)}), 404
    return send_file(file_path, as_attachment=True, download_name="freedom-demo.mp3", mimetype="audio/mpeg")


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
        return jsonify({"valid": False, "pro": False, "message": message})

    downloads_limit = int(license_row.get("downloads_limit", -1))
    downloads_used = int(license_row.get("downloads_used", 0))
    remaining = -1 if downloads_limit < 0 else max(0, downloads_limit - downloads_used)

    return jsonify({
        "valid": True,
        "pro": True,
        "message": "PRO activated",
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
        return jsonify({"allowed": False, "message": "Activate PRO first"})

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
        cur.execute("UPDATE licenses SET downloads_used = ? WHERE license_key = ?", (downloads_used, key))
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


@app.route("/admin/create-license", methods=["POST", "GET"])
def admin_create_license():
    data = request.get_json(silent=True) or {}
    customer_email = str(data.get("customer_email", request.args.get("email", ""))).strip()
    customer_name = str(data.get("customer_name", request.args.get("name", ""))).strip()
    limit = int(request.args.get("limit", data.get("downloads_limit", -1)))
    license_row, created = create_license(
        customer_email=customer_email,
        customer_name=customer_name,
        source="manual",
        max_devices=2,
        downloads_limit=limit,
    )
    return jsonify({"created": created, "license": license_row})


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
    return jsonify({"licenses": licenses, "activations": activations})


@app.route("/gumroad/ping", methods=["POST"])
def gumroad_ping():
    init_db()
    secret = request.form.get("secret", "")
    expected_secret = os.getenv("GUMROAD_SECRET", "")
    if secret != expected_secret:
        return jsonify({"ok": False, "message": "Invalid secret"}), 403

    customer_email = request.form.get("email", "").strip()
    customer_name = request.form.get("full_name", "").strip()
    license_row, created = create_license(customer_email=customer_email, customer_name=customer_name, source="gumroad", max_devices=2)
    return jsonify({"ok": True, "license_key": license_row["license_key"], "created": created})


@app.route("/gumroad/webhook", methods=["POST"])
def gumroad_webhook():
    init_db()
    data = request.form.to_dict()
    customer_email = str(data.get("email") or data.get("purchaser_email") or data.get("customer_email") or "").strip()
    customer_name = str(data.get("full_name") or data.get("name") or data.get("customer_name") or "").strip()
    gumroad_sale_id = str(data.get("sale_id") or data.get("id") or data.get("order_id") or "").strip()
    product_name = str(data.get("product_name") or data.get("product_permalink") or "").strip()
    license_row, created = create_license(
        customer_email=customer_email,
        customer_name=customer_name,
        source="gumroad",
        gumroad_sale_id=gumroad_sale_id,
        product_name=product_name,
        max_devices=2,
        downloads_limit=product_download_limit(product_name),
    )
    return jsonify({"ok": True, "created": created, "license_key": license_row["license_key"], "customer_email": customer_email, "message": "License generated"})


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
