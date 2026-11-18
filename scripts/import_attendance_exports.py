"""Import local attendance_*.xlsx exports and their photos into Neon.

The import is safe to rerun. Each student/date pair is inserted once, and an
existing row with a Neon photo URL is left alone.

  DATABASE_URL=... python scripts/import_attendance_exports.py
"""

import glob
import io
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import openpyxl
import psycopg
from PIL import Image


ROOT = Path(__file__).resolve().parent.parent
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise SystemExit("Set DATABASE_URL")


def digits(value):
    return re.sub(r"\D", "", str(value or ""))


def parse_timestamp(value):
    text = str(value or "").strip()
    text = re.sub(r"\s+(CST|CDT)$", "", text)
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=ZoneInfo("America/Chicago")
    )


def jpeg_bytes(path):
    with Image.open(path) as image:
        image.thumbnail((240, 180))
        output = io.BytesIO()
        image.convert("RGB").save(output, format="JPEG", quality=70)
        return output.getvalue()


workbooks = sorted(glob.glob(str(ROOT / "attendance_*.xlsx")))
if not workbooks:
    raise SystemExit("No attendance_*.xlsx exports found")

inserted = updated_photos = skipped = 0
with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
    for workbook in workbooks:
        sheet = openpyxl.load_workbook(
            workbook, read_only=True, data_only=True
        ).active
        rows = sheet.iter_rows(values_only=True)
        headers = [str(value or "").strip().lower() for value in next(rows)]
        column = {name: index for index, name in enumerate(headers)}

        for row in rows:
            s_number = digits(row[column["s-number"]])
            name = str(row[column["name"]] or "").strip()
            timestamp = parse_timestamp(row[column["timestamp"]])
            relative_photo = str(row[column["photopath"]] or "").strip()
            if not s_number or not name:
                continue

            cur.execute(
                "SELECT id, photo_path FROM attendance WHERE s_number = %s AND checkin_date = %s",
                (s_number, timestamp.date()),
            )
            existing = cur.fetchone()
            if existing and str(existing[1] or "").startswith("/photo/"):
                skipped += 1
                continue

            photo_url = None
            if relative_photo:
                photo_file = ROOT / "photos" / relative_photo
                if photo_file.is_file():
                    cur.execute(
                        "INSERT INTO checkin_photos (s_number, data) VALUES (%s, %s) RETURNING id",
                        (s_number, jpeg_bytes(photo_file)),
                    )
                    photo_url = f"/photo/{cur.fetchone()[0]}"

            if existing:
                cur.execute(
                    "UPDATE attendance SET name = %s, checkin_ts = %s, photo_path = COALESCE(%s, photo_path) WHERE id = %s",
                    (name, timestamp, photo_url, existing[0]),
                )
                updated_photos += 1
            else:
                cur.execute(
                    "INSERT INTO attendance (s_number, name, checkin_ts, checkin_date, photo_path) VALUES (%s, %s, %s, %s, %s)",
                    (s_number, name, timestamp, timestamp.date(), photo_url),
                )
                inserted += 1

print(
    f"Imported {inserted} check-ins, repaired {updated_photos} photos, "
    f"skipped {skipped} existing rows from {len(workbooks)} exports"
)
