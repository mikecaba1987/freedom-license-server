from flask import Flask, request, jsonify
import sqlite3
import secrets
import string
from pathlib import Path

app = Flask(__name__)

DB = "licenses.db"


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
        license_key TEXT UNIQUE,
        customer_email TEXT,
        customer_name TEXT,
        gumroad_sale_id TEXT,
        product_name TEXT,
        downloads_limit INTEGER DEFAULT -1,
        downloads_used INTEGER DEFAULT 0,
        active INTEGER DEFAULT 1
    )
    """)

    conn.commit()
    conn.close()


def generate_license():
    alphabet = string.ascii_uppercase + string.digits

    def part():
        return ''.join(secrets.choice(alphabet) for _ in range(5))

    return f"FD-{part()}-{part()}-{part()}-{part()}"


def product_download_limit(product_name):
    name = (product_name or "").lower()

    if "lifetime" in name:
        return -1

    if "3" in name:
        return 3

    if "1" in name:
        return 1

    return -1


@app.route("/")
def home():
    return jsonify({
        "status": "online",
        "name": "Freedom License Server"
    })


@app.route("/gumroad/webhook", methods=["POST"])
def gumroad_webhook():

    init_db()

    data = request.form.to_dict()

    customer_email = data.get("email", "")
    customer_name = data.get("full_name", "")
    gumroad_sale_id = data.get("sale_id", "")
    product_name = data.get("product_name", "")

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM licenses WHERE gumroad_sale_id = ?",
        (gumroad_sale_id,)
    )

    existing = cur.fetchone()

    if existing:
        conn.close()

        return jsonify({
            "ok": True,
            "license_key": existing["license_key"],
            "existing": True
        })

    license_key = generate_license()

    cur.execute("""
    INSERT INTO licenses (
        license_key,
        customer_email,
        customer_name,
        gumroad_sale_id,
        product_name,
        downloads_limit,
        downloads_used,
        active
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        license_key,
        customer_email,
        customer_name,
        gumroad_sale_id,
        product_name,
        product_download_limit(product_name),
        0,
        1
    ))

    conn.commit()
    conn.close()

    return jsonify({
        "ok": True,
        "license_key": license_key,
        "customer_email": customer_email,
        "product_name": product_name
    })


@app.route("/admin/list")
def admin_list():

    init_db()

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM licenses ORDER BY id DESC")

    licenses = [dict(row) for row in cur.fetchall()]

    conn.close()

    return jsonify({
        "licenses": licenses
    })


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000)
