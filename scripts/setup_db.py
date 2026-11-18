"""Create the tables in DATABASE_URL (idempotent).   python scripts/setup_db.py"""
import os, sys
from pathlib import Path
import psycopg

url = os.environ.get("DATABASE_URL") or sys.exit("Set DATABASE_URL")
with psycopg.connect(url) as conn:
    conn.execute((Path(__file__).parent.parent / "schema.sql").read_text())
print("schema ready")
