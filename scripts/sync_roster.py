"""
Make the Neon roster match a spreadsheet (.xlsx or .csv; default students.xlsx;
columns "Student"/"name" and "s-number"). Someone whose S-number changed keeps their history:
matched by name, their old check-ins move to the new number. People no longer
on the sheet are removed from the roster (their past check-ins stay).

  DATABASE_URL=... python scripts/sync_roster.py [students.xlsx] [--dry-run]
"""
import os, sys
import openpyxl
import psycopg

args = [a for a in sys.argv[1:] if not a.startswith("--")]
dry = "--dry-run" in sys.argv
path = args[0] if args else "students.xlsx"
url = os.environ.get("DATABASE_URL") or sys.exit("Set DATABASE_URL")
digits = lambda v: "".join(ch for ch in str(v or "") if ch.isdigit())
key = lambda n: " ".join(str(n or "").lower().split())

if path.lower().endswith(".csv"):
    import csv
    with open(path, newline="") as f:
        rows = [tuple(r) for r in csv.reader(f)]
else:
    rows = list(openpyxl.load_workbook(path, read_only=True).active.iter_rows(values_only=True))
head = [str(h or "").strip().lower() for h in rows[0]]
ni = next(i for i, h in enumerate(head) if h in ("student", "name"))
si = next(i for i, h in enumerate(head) if "s-number" in h or "s_number" in h)
sheet = {}
for r in rows[1:]:
    name, sn = str(r[ni] or "").strip(), digits(r[si])
    if name and sn:
        sheet[sn] = name

with psycopg.connect(url) as conn, conn.cursor() as cur:
    cur.execute("SELECT s_number, name FROM students")
    current = dict(cur.fetchall())
    by_name = {key(n): sn for sn, n in current.items()}
    changed, added, renamed = [], [], []
    for sn, name in sheet.items():
        if sn in current:
            if current[sn] != name:
                renamed.append(sn)
                cur.execute("UPDATE students SET name=%s WHERE s_number=%s", (name, sn))
            continue
        old = by_name.get(key(name))
        if old and old not in sheet:  # same person, new S-number
            changed.append((name, old, sn))
            cur.execute("UPDATE students SET s_number=%s WHERE s_number=%s", (sn, old))
            cur.execute("UPDATE attendance SET s_number=%s WHERE s_number=%s AND NOT EXISTS (SELECT 1 FROM attendance x WHERE x.s_number=%s AND x.checkin_date=attendance.checkin_date)", (sn, old, sn))
            current[sn] = current.pop(old)
        else:
            added.append(sn)
            cur.execute("INSERT INTO students (s_number, name) VALUES (%s,%s)", (sn, name))
    # check-ins saved under an old S-number (e.g. copied from Supabase later) follow the person by name
    moved = 0
    for sn, name in sheet.items():
        cur.execute(
            """UPDATE attendance a SET s_number=%s
               WHERE lower(regexp_replace(a.name, '\\s+', ' ', 'g')) = %s AND a.s_number <> %s AND NOT (a.s_number = ANY(%s))
                 AND NOT EXISTS (SELECT 1 FROM attendance x WHERE x.s_number=%s AND x.checkin_date=a.checkin_date)""",
            (sn, key(name), sn, list(sheet), sn),
        )
        moved += cur.rowcount
    gone = [sn for sn in current if sn not in sheet]
    if gone:
        cur.execute("DELETE FROM students WHERE s_number = ANY(%s)", (gone,))
    print(f"sheet {len(sheet)} | new S-number {len(changed)} | added {len(added)} | renamed {len(renamed)} | removed from roster {len(gone)} | check-ins moved to new S-numbers {moved}")
    if dry:
        conn.rollback()
        print("dry run - nothing saved")
