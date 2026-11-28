"""Synthetic records used only by isolated tests."""
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from support_agent.agent import SupportAgent
from support_agent.data import StoreData, Status, StatusError

FIXTURES = Path(__file__).parent / 'fixtures'

class FixtureStatusProvider:
    def __init__(self, path=FIXTURES / 'statuses.json'):
        self.rows = json.loads(path.read_text())
    def get(self, order_id):
        row = self.rows.get(order_id)
        if not row:
            raise StatusError('No status record')
        return Status(order_id, row['state'], row['detail'], datetime.now(timezone.utc), 'test fixture')

def test_agent(**kwargs):
    kwargs.setdefault('data', StoreData(FIXTURES))
    kwargs.setdefault('status_provider', FixtureStatusProvider())
    return SupportAgent(**kwargs)

def seed_catalog(database):
    policies = json.loads((FIXTURES / 'policies.json').read_text())
    products = json.loads((FIXTURES / 'products.json').read_text())
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE policies (id TEXT PRIMARY KEY, terms_json TEXT NOT NULL, answer TEXT NOT NULL)')
        db.execute('CREATE TABLE products (id TEXT PRIMARY KEY, name TEXT NOT NULL, terms_json TEXT NOT NULL, answer TEXT NOT NULL)')
        db.executemany('INSERT INTO policies VALUES (?, ?, ?)', [(r['id'], json.dumps(r['terms']), r['answer']) for r in policies])
        db.executemany('INSERT INTO products VALUES (?, ?, ?, ?)', [(r['id'], r['name'], json.dumps(r['terms']), r['answer']) for r in products])
        db.commit()
