"""Synthetic supply records for isolated tests only."""
from pathlib import Path
import sqlite3
from datetime import datetime, timedelta, timezone

def seed_demo_supply(path: Path) -> None:
    """Create fictional, time-relative data; never replace a real database."""
    if path.exists():
        raise ValueError("Refusing to replace an existing database")
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    observed = now.isoformat()
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE inventory (sku TEXT PRIMARY KEY, product_name TEXT NOT NULL, "
                   "location TEXT NOT NULL, on_hand INTEGER NOT NULL, reserved INTEGER NOT NULL, "
                   "reorder_point INTEGER NOT NULL, observed_at TEXT NOT NULL)")
        db.execute("CREATE TABLE purchase_orders (id TEXT PRIMARY KEY, supplier TEXT NOT NULL, "
                   "sku TEXT NOT NULL, quantity INTEGER NOT NULL, state TEXT NOT NULL, "
                   "expected_at TEXT, observed_at TEXT NOT NULL)")
        db.execute("CREATE TABLE shipments (id TEXT PRIMARY KEY, purchase_order_id TEXT NOT NULL, "
                   "carrier TEXT NOT NULL, state TEXT NOT NULL, eta TEXT, observed_at TEXT NOT NULL)")
        db.execute("CREATE TABLE source_metadata (kind TEXT NOT NULL, "
                   "source_system TEXT NOT NULL, synced_at TEXT NOT NULL)")
        db.execute("INSERT INTO source_metadata VALUES ('demo', 'fictional fixture', ?)", (observed,))
        db.executemany("INSERT INTO inventory VALUES (?, ?, ?, ?, ?, ?, ?)", [
            ("SKU-100", "Steel Bottle", "East DC", 120, 25, 40, observed),
            ("SKU-200", "Cotton Tote", "West DC", 18, 8, 25, observed),
        ])
        db.executemany("INSERT INTO purchase_orders VALUES (?, ?, ?, ?, ?, ?, ?)", [
            ("PO-2001", "Northstar Supply", "SKU-200", 200, "confirmed",
             (now + timedelta(days=5)).date().isoformat(), observed),
            ("PO-2002", "Northstar Supply", "SKU-100", 100, "in transit",
             (now + timedelta(days=2)).date().isoformat(), observed),
        ])
        db.executemany("INSERT INTO shipments VALUES (?, ?, ?, ?, ?, ?)", [
            ("SHP-3001", "PO-2002", "Demo Freight", "in transit",
             (now + timedelta(days=2)).isoformat(), observed),
            ("SHP-3003", "PO-2001", "Demo Freight", "in transit",
             (now - timedelta(days=1)).isoformat(), observed),
        ])
        db.commit()
