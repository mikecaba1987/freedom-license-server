from flask import Flask, request, jsonify
import sqlite3
from pathlib import Path
from datetime import datetime

app = Flask(__name__)

DB = Path(__file__).resolve().parent / "licenses.db"


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

    conn.commit()
    conn.close()


@app.route("/", methods=["GET"])
def home():
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
            (key, device_id, datetime.utcnow().isoformat())
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


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5000, debug=True)
