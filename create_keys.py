import sqlite3
import random
import string
from pathlib import Path
from datetime import datetime

DB = Path(__file__).resolve().parent / "licenses.db"


def generate_key():
    chars = string.ascii_uppercase + string.digits
    parts = ["".join(random.choice(chars) for _ in range(4)) for _ in range(4)]
    return "FD-" + "-".join(parts)


def init_db():
    conn = sqlite3.connect(DB)
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


def create_keys(count=10, max_devices=2):
    init_db()

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    created = []

    while len(created) < count:
        key = generate_key()

        try:
            cur.execute(
                "INSERT INTO licenses (license_key, active, max_devices, created_at) VALUES (?, 1, ?, ?)",
                (key, max_devices, datetime.utcnow().isoformat())
            )
            created.append(key)
        except sqlite3.IntegrityError:
            pass

    conn.commit()
    conn.close()

    print("\nNEW LICENSE KEYS:\n")
    for key in created:
        print(key)


if __name__ == "__main__":
    amount = input("How many keys? Default 10: ").strip()
    devices = input("Max devices per key? Default 2: ").strip()

    amount = int(amount) if amount else 10
    devices = int(devices) if devices else 2

    create_keys(amount, devices)
