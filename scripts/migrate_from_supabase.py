"""
Copy students + attendance from Supabase into Neon. Read-only on Supabase; safe
to re-run (existing rows are skipped).

  SUPABASE_URL=... SUPABASE_SERVICE_KEY=... DATABASE_URL=... python scripts/migrate_from_supabase.py
"""
import os, sys
import psycopg
from supabase import create_client

sb = create_client(os.environ["SUPABASE_URL"], os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_ANON_KEY"])
url = os.environ.get("DATABASE_URL") or sys.exit("Set DATABASE_URL")


def all_rows(table, cols):
    rows, start = [], 0
    while True:
        batch = sb.table(table).select(cols).order("id" if table == "attendance" else "s_number").range(start, start + 999).execute().data or []
        rows += batch
        if len(batch) < 1000:
            return rows
        start += 1000


students = all_rows("students", "name, s_number")
attendance = all_rows("attendance", "id, s_number, name, checkin_ts, checkin_date, photo_path")
digits = lambda v: "".join(ch for ch in str(v or "") if ch.isdigit())

with psycopg.connect(url) as conn, conn.cursor() as cur:
    s_new = a_new = 0
    cur.execute("SELECT count(*) FROM students")
    roster_exists = cur.fetchone()[0] > 0  # the current roster wins; old students aren't re-added
    for s in ([] if roster_exists else students):
        sn = digits(s.get("s_number"))
        if sn and s.get("name"):
            cur.execute("INSERT INTO students (s_number, name) VALUES (%s,%s) ON CONFLICT (s_number) DO NOTHING", (sn, s["name"].strip()))
            s_new += cur.rowcount
    for a in attendance:
        sn = digits(a.get("s_number"))
        if not sn or not a.get("checkin_date") or not a.get("checkin_ts"):
            continue
        cur.execute(
            """INSERT INTO attendance (s_number, name, checkin_ts, checkin_date, photo_path)
               VALUES (%s,%s,%s,%s,%s) ON CONFLICT (s_number, checkin_date) DO NOTHING""",
            (sn, a.get("name"), a["checkin_ts"], str(a["checkin_date"])[:10], a.get("photo_path")),
        )
        a_new += cur.rowcount
print("Next: python scripts/sync_roster.py  (moves old check-ins onto current S-numbers)")
print(f"Supabase: {len(students)} students, {len(attendance)} check-ins -> Neon: +{s_new} students, +{a_new} check-ins")
