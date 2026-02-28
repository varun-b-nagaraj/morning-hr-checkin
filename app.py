import base64
import io
import os
import re
from datetime import datetime
from pathlib import Path

from flask import (
    Flask, jsonify, render_template, request,
    redirect, url_for, session, send_file, make_response
)
from dotenv import load_dotenv
from PIL import Image
from supabase import create_client, Client
from zoneinfo import ZoneInfo
# Config
APP_DIR = Path(__file__).parent.resolve()
ENV_DIR = APP_DIR / ".env"
ENV_PATH = ENV_DIR / "supabase.env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)
# update from system env as well
SUPABASE_PHOTOS_BUCKET = os.getenv("SUPABASE_PHOTOS_BUCKET", "checkin-photos")
_supabase_client = None
_supabase_url = None

# ---- Admin password + session secret
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "abhiMora1!")
SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "change-me-please")
# -------------------------------------

app = Flask(
    __name__,
    template_folder=str(APP_DIR / "templates"),
    static_folder=str(APP_DIR / "static"),
)
app.secret_key = SECRET_KEY


def get_supabase_client():
    global _supabase_client, _supabase_url
    if _supabase_client and _supabase_url:
        return _supabase_client, _supabase_url

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not supabase_url or not supabase_key:
        raise RuntimeError("Supabase not configured. Set SUPABASE_URL and SUPABASE_SERVICE_KEY or SUPABASE_ANON_KEY.")

    _supabase_url = supabase_url
    _supabase_client = create_client(supabase_url, supabase_key)
    return _supabase_client, _supabase_url


def normalize_s_number(raw_value: str) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    if value[:1].lower() == "s":
        value = value[1:]
    return re.sub(r"\D", "", value)


def format_timestamp(value) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value)
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("America/Chicago"))
    local = dt.astimezone(ZoneInfo("America/Chicago"))
    return local.strftime("%Y-%m-%d %H:%M:%S %Z")


def require_supabase_data(result, context: str):
    if getattr(result, "error", None):
        raise RuntimeError(f"{context}: {result.error}")
    return result.data or []


def fetch_students():
    client, _ = get_supabase_client()
    page_size = 1000
    rows = []
    start = 0
    while True:
        result = (
            client.table("students")
            .select("name, s_number")
            .order("name")
            .range(start, start + page_size - 1)
            .execute()
        )
        batch = require_supabase_data(result, "students select failed")
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return rows


def fetch_attendance_for_date(date_str: str):
    client, _ = get_supabase_client()
    date_str = normalize_date_string(date_str)
    page_size = 1000
    rows = []
    start = 0
    while True:
        result = (
            client.table("attendance")
            .select("s_number, name, checkin_ts, photo_path")
            .eq("checkin_date", date_str)
            .order("checkin_ts")
            .range(start, start + page_size - 1)
            .execute()
        )
        batch = require_supabase_data(result, "attendance select failed")
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return rows


def fetch_attendance_basic():
    client, _ = get_supabase_client()
    page_size = 1000
    rows = []
    start = 0
    while True:
        result = (
            client.table("attendance")
            .select("s_number, checkin_date")
            .order("checkin_date", desc=True)
            .order("s_number")
            .range(start, start + page_size - 1)
            .execute()
        )
        batch = require_supabase_data(result, "attendance analytics select failed")
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return rows


def fetch_attendance_rows(date_from: str = "", date_to: str = ""):
    client, _ = get_supabase_client()
    date_from = normalize_date_string(date_from)
    date_to = normalize_date_string(date_to)

    page_size = 1000
    rows = []
    start = 0
    while True:
        query = client.table("attendance").select("s_number, name, checkin_ts, checkin_date, photo_path")
        if date_from:
            query = query.gte("checkin_date", date_from)
        if date_to:
            query = query.lte("checkin_date", date_to)
        query = (
            query.order("checkin_date", desc=True)
            .order("checkin_ts")
            .range(start, start + page_size - 1)
        )
        result = query.execute()
        batch = require_supabase_data(result, "attendance select failed")
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return rows


def get_today_str():
    # Explicit US Central (handles CST/CDT correctly)
    return datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")


def normalize_date_string(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    # Accept ISO date or datetime; keep YYYY-MM-DD
    if "T" in text:
        return text.split("T", 1)[0]
    if " " in text:
        return text.split(" ", 1)[0]
    return text


def first_name(full_name: str) -> str:
    return (full_name or "").split()[0] if full_name else ""


def save_photo(data_url: str, s_number: str) -> str:
    client, supabase_url = get_supabase_client()
    _, b64data = data_url.split(",", 1)
    binary = base64.b64decode(b64data)
    pil_img = Image.open(io.BytesIO(binary))

    # Resize to keep uploads light
    max_w, max_h = 240, 180
    pil_img.thumbnail((max_w, max_h))

    buffer = io.BytesIO()
    pil_img.save(buffer, format="PNG")
    buffer.seek(0)

    date_str = get_today_str()
    ts = datetime.now(ZoneInfo("America/Chicago")).strftime("%H%M%S%f")
    filename = f"{s_number}_{ts}.png"
    storage_path = f"{date_str}/{filename}"

    upload_result = client.storage.from_(SUPABASE_PHOTOS_BUCKET).upload(
        storage_path,
        buffer.getvalue(),
        {"content-type": "image/png", "upsert": "true"},
    )
    if getattr(upload_result, "error", None):
        raise RuntimeError(f"photo upload failed: {upload_result.error}")

    base_url = supabase_url.rstrip("/")
    public_url = f"{base_url}/storage/v1/object/public/{SUPABASE_PHOTOS_BUCKET}/{storage_path}"
    return public_url


def calculate_analytics(students, attendance_rows, available_dates):
    """Calculate attendance analytics across all sessions"""
    total_sessions = len(available_dates)
    total_students = len(students)

    student_records = {
        str(student["s_number"]): {
            "name": student["name"],
            "s_number": str(student["s_number"]),
            "present_count": 0,
            "absent_count": 0,
        }
        for student in students
    }

    seen = set()
    for row in attendance_rows:
        s_num = str(row.get("s_number", "")).strip()
        date_str = normalize_date_string(row.get("checkin_date") or "")
        if not s_num or not date_str:
            continue
        key = (s_num, date_str)
        if key in seen:
            continue
        seen.add(key)
        if s_num in student_records:
            student_records[s_num]["present_count"] += 1

    students_list = []
    total_attendance = 0

    for record in student_records.values():
        if total_sessions > 0:
            attendance_rate = (record["present_count"] / total_sessions) * 100
        else:
            attendance_rate = 0

        record["absent_count"] = total_sessions - record["present_count"]
        record["attendance_rate"] = attendance_rate
        students_list.append(record)
        total_attendance += attendance_rate

    avg_attendance = total_attendance / total_students if total_students > 0 else 0
    students_list.sort(key=lambda x: x["name"])

    return {
        "total_sessions": total_sessions,
        "total_students": total_students,
        "avg_attendance": avg_attendance,
        "students": students_list,
    }


def _parse_csv_set(value: str):
    items = (value or "").split(",")
    return {item.strip().lower() for item in items if item.strip()}


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_date_param(value: str, field_name: str):
    text = normalize_date_string(value)
    if not text:
        return ""
    if not _DATE_RE.match(text):
        raise ValueError(f"Invalid {field_name}. Expected YYYY-MM-DD.")
    return text


def _check_api_token():
    expected = os.getenv("API_TOKEN") or os.getenv("REPORT_API_TOKEN") or ""
    if not expected:
        return True
    auth = (request.headers.get("Authorization") or "").strip()
    provided = ""
    if auth.lower().startswith("bearer "):
        provided = auth[7:].strip()
    if not provided:
        provided = (request.args.get("token") or "").strip()
    return provided == expected


def _maybe_add_cors(resp):
    allowed = (os.getenv("CORS_ALLOW_ORIGIN") or "").strip()
    if not allowed:
        return resp
    resp.headers["Access-Control-Allow-Origin"] = allowed
    resp.headers["Access-Control-Allow-Methods"] = "GET,OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Authorization,Content-Type"
    return resp


@app.route("/api/report", methods=["GET", "OPTIONS"])
def api_report():
    if request.method == "OPTIONS":
        return _maybe_add_cors(make_response(("", 204)))

    if not _check_api_token():
        return _maybe_add_cors(jsonify({"ok": False, "error": "unauthorized"})), 401

    exclude = _parse_csv_set(request.args.get("exclude", ""))
    date_str = request.args.get("date", "")
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")

    try:
        date_str = _parse_date_param(date_str, "date")
        date_from = _parse_date_param(date_from, "from")
        date_to = _parse_date_param(date_to, "to")
    except ValueError as exc:
        return _maybe_add_cors(jsonify({"ok": False, "error": str(exc)})), 400

    if date_str:
        date_from = date_str
        date_to = date_str
    elif date_from and not date_to:
        date_to = date_from
    elif date_to and not date_from:
        date_from = date_to

    if date_from and date_to and date_from > date_to:
        return _maybe_add_cors(jsonify({"ok": False, "error": "Invalid date range: from must be <= to."})), 400

    include_photos = "photos" not in exclude and "photo" not in exclude
    include_formatted_ts = "formatted_timestamps" not in exclude and "timestamps" not in exclude and "times" not in exclude
    include_absent_lists = "absent" not in exclude and "absences" not in exclude

    include_roster = "roster" not in exclude and "students" not in exclude
    include_dates = "dates" not in exclude
    include_attendance_rows = "attendance_rows" not in exclude and "attendance" not in exclude
    include_sessions = "sessions" not in exclude
    include_analytics = "analytics" not in exclude

    # Fetch core data needed for any calculations.
    roster = fetch_students()
    attendance_rows = fetch_attendance_rows(date_from=date_from, date_to=date_to)

    available_dates = sorted(
        {normalize_date_string(row.get("checkin_date")) for row in attendance_rows if row.get("checkin_date")},
        reverse=True,
    )
    if not available_dates and date_from and date_to and date_from == date_to:
        available_dates = [date_from]

    roster_ids = {str(s.get("s_number", "")).strip() for s in roster if s.get("s_number")}

    # Flat attendance rows (normalized for API)
    api_attendance_rows = []
    if include_attendance_rows:
        for row in attendance_rows:
            s_num = str(row.get("s_number", "")).strip()
            checkin_date = normalize_date_string(row.get("checkin_date") or "")
            if not checkin_date:
                continue
            item = {
                "s_number": s_num,
                "name": row.get("name") or "",
                "checkin_date": checkin_date,
                "checkin_ts": row.get("checkin_ts") or "",
            }
            if include_formatted_ts:
                item["checkin_ts_local"] = format_timestamp(row.get("checkin_ts"))
            if include_photos:
                item["photo_url"] = row.get("photo_path") or ""
            api_attendance_rows.append(item)

    # Sessions (grouped by date)
    sessions = []
    if include_sessions:
        by_date = {}
        for row in attendance_rows:
            d = normalize_date_string(row.get("checkin_date") or "")
            if not d:
                continue
            by_date.setdefault(d, []).append(row)

        for d in available_dates:
            present = []
            present_ids = set()
            unmatched = []
            for row in by_date.get(d, []):
                s_num = str(row.get("s_number", "")).strip()
                present_ids.add(s_num)
                rec = {
                    "s_number": s_num,
                    "name": row.get("name") or "",
                    "checkin_ts": row.get("checkin_ts") or "",
                }
                if include_formatted_ts:
                    rec["checkin_ts_local"] = format_timestamp(row.get("checkin_ts"))
                if include_photos:
                    rec["photo_url"] = row.get("photo_path") or ""
                present.append(rec)
                if s_num and s_num not in roster_ids:
                    unmatched.append(rec)

            present_roster_ids = {s for s in present_ids if s in roster_ids}
            absent_ids = roster_ids - present_roster_ids
            total_students = len(roster_ids)
            present_count = len(present_roster_ids)
            absent_count = len(absent_ids)
            attendance_rate = (present_count / total_students * 100) if total_students else 0

            session_obj = {
                "date": d,
                "present_count": present_count,
                "absent_count": absent_count,
                "total_students": total_students,
                "attendance_rate": attendance_rate,
                "present": present,
            }

            if include_absent_lists:
                absent_list = []
                for student in roster:
                    s_num = str(student.get("s_number", "")).strip()
                    if s_num and s_num in absent_ids:
                        absent_list.append({
                            "s_number": s_num,
                            "name": student.get("name") or "",
                        })
                absent_list.sort(key=lambda x: x.get("name") or "")
                session_obj["absent"] = absent_list

            if unmatched:
                session_obj["unmatched_attendance"] = unmatched

            sessions.append(session_obj)

    analytics = None
    if include_analytics:
        analytics = calculate_analytics(roster, attendance_rows, available_dates)

    payload = {
        "ok": True,
        "meta": {
            "generated_at": datetime.now(ZoneInfo("America/Chicago")).isoformat(),
            "timezone": "America/Chicago",
            "filters": {
                "date": date_str or "",
                "from": date_from or "",
                "to": date_to or "",
                "exclude": sorted(exclude),
            },
        },
    }
    if include_dates:
        payload["dates"] = available_dates
    if include_roster:
        payload["roster"] = [{"name": s.get("name") or "", "s_number": str(s.get("s_number") or "")} for s in roster]
    if include_attendance_rows:
        payload["attendance_rows"] = api_attendance_rows
    if include_sessions:
        payload["sessions"] = sessions
    if include_analytics:
        payload["analytics"] = analytics

    return _maybe_add_cors(jsonify(payload))


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/checkin", methods=["POST"])
def checkin():
    payload = request.get_json(force=True)
    s_number = normalize_s_number(payload.get("s_number", ""))
    photo_data_url = payload.get("image_data_url")

    if not s_number:
        return jsonify({"ok": False, "error": "Missing s-number"}), 400
    if not photo_data_url or not photo_data_url.startswith("data:image/"):
        return jsonify({"ok": False, "error": "Missing or invalid photo"}), 400

    # Lookup roster
    client, _ = get_supabase_client()
    student_result = (
        client.table("students")
        .select("name, s_number")
        .eq("s_number", s_number)
        .limit(1)
        .execute()
    )
    student_data = require_supabase_data(student_result, "student lookup failed")
    if not student_data:
        return jsonify({"ok": False, "error": "S-number not found"}), 404

    full_name = student_data[0]["name"]
    fname = first_name(full_name)

    # Only first check-in counts today
    today = get_today_str()
    existing_result = (
        client.table("attendance")
        .select("id")
        .eq("s_number", s_number)
        .eq("checkin_date", today)
        .limit(1)
        .execute()
    )
    existing_rows = require_supabase_data(existing_result, "attendance lookup failed")
    if existing_rows:
        return jsonify({"ok": True, "status": "already", "first_name": fname})

    # Central time timestamp
    now = datetime.now(ZoneInfo("America/Chicago"))
    timestamp = now.isoformat()

    # Save photo locally and store a relative path in Supabase
    web_path = save_photo(photo_data_url, s_number)
    insert_result = (
        client.table("attendance")
        .insert({
            "s_number": s_number,
            "name": full_name,
            "checkin_ts": timestamp,
            "checkin_date": today,
            "photo_path": web_path,
        })
        .execute()
    )
    require_supabase_data(insert_result, "attendance insert failed")

    return jsonify({"ok": True, "status": "new", "first_name": fname})


# ------------------ HISTORY (password-gated) ------------------

def is_authed():
    return session.get("authed") is True


@app.route("/history", methods=["GET", "POST"])
def history():
    # Password prompt / validation
    if request.method == "POST":
        pwd = request.form.get("password", "")
        if pwd == ADMIN_PASSWORD:
            session["authed"] = True
            return redirect(url_for("history"))
        return render_template("history_login.html", error="Incorrect password")

    if not is_authed():
        return render_template("history_login.html")

    # Get selected date from query param, default to today
    selected_date = normalize_date_string(request.args.get("date", get_today_str()))

    attendance_basic = fetch_attendance_basic()
    available_dates_raw = sorted(
        {normalize_date_string(row["checkin_date"]) for row in attendance_basic if row.get("checkin_date")},
        reverse=True,
    )
    available_dates = available_dates_raw or [get_today_str()]
    if selected_date not in available_dates:
        selected_date = available_dates[0]

    # Load roster
    students = fetch_students()

    # Get data for selected date
    present = []
    present_ids = set()
    for row in fetch_attendance_for_date(selected_date):
        s_num = str(row.get("s_number", "")).strip()
        if not s_num:
            continue
        present_ids.add(s_num)
        present.append({
            "s_number": s_num,
            "name": row.get("name"),
            "timestamp": format_timestamp(row.get("checkin_ts")),
            "photo_path": row.get("photo_path") or "",
        })

    # Absent = roster - present_ids for selected date
    absent = []
    for student in students:
        s_num = str(student.get("s_number", "")).strip()
        if s_num and s_num not in present_ids:
            absent.append({"Name": student.get("name"), "s-number": s_num})
    absent.sort(key=lambda x: x.get("Name") or "")

    # Calculate analytics across all dates
    analytics = calculate_analytics(students, attendance_basic, available_dates_raw)

    return render_template("history.html",
                           present=present,
                           absent=absent,
                           selected_date=selected_date,
                           today=get_today_str(),
                           available_dates=available_dates,
                           analytics=analytics)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("history"))


@app.route("/verify_password", methods=["POST"])
def verify_password():
    """Simple JSON endpoint to verify admin password for client-side actions.
    The history login still uses the form POST to /history; this endpoint
    only returns a JSON-OK result so the front-end can verify the same
    ADMIN_PASSWORD without reusing the login form.
    """
    payload = request.get_json(silent=True) or {}
    pwd = payload.get("password", "")
    if pwd == ADMIN_PASSWORD:
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 403


# ---------- ROSTER management endpoints (admin only) ----------
@app.route('/roster', methods=['GET'])
def get_roster():
    if not is_authed():
        return jsonify({'ok': False, 'error': 'unauthorized'}), 403
    students = fetch_students()
    payload = [{"Name": s["name"], "s-number": s["s_number"]} for s in students]
    return jsonify({'ok': True, 'students': payload})


@app.route('/roster/public', methods=['GET'])
def get_roster_public():
    students = fetch_students()
    payload = [{"name": s["name"], "s_number": s["s_number"]} for s in students]
    return jsonify({'ok': True, 'students': payload})


@app.route('/roster/add', methods=['POST'])
def add_roster():
    if not is_authed():
        return jsonify({'ok': False, 'error': 'unauthorized'}), 403
    payload = request.get_json(force=True)
    # accept single or bulk
    entries = payload.get('entries') or []
    # entries may be a single dict
    if isinstance(entries, dict):
        entries = [entries]

    if not entries:
        # try single name/s_number
        name = payload.get('name')
        s_number = payload.get('s_number')
        if name and s_number:
            entries = [{'Name': name, 's-number': str(s_number).strip()}]

    if not entries:
        return jsonify({'ok': False, 'error': 'no entries provided'}), 400

    existing_students = fetch_students()
    existing_numbers = {str(s["s_number"]).strip() for s in existing_students if s.get("s_number")}

    added = []
    to_insert = []
    for ent in entries:
        n = (ent.get('Name') or ent.get('name') or '').strip()
        s = normalize_s_number(ent.get('s-number') or ent.get('s_number') or '')
        if not n or not s:
            continue
        # check for duplicates by s-number
        if s in existing_numbers:
            continue
        existing_numbers.add(s)
        to_insert.append({"name": n, "s_number": s})
        added.append({'Name': n, 's-number': s})

    if to_insert:
        client, _ = get_supabase_client()
        insert_result = client.table("students").insert(to_insert).execute()
        require_supabase_data(insert_result, "students insert failed")

    return jsonify({'ok': True, 'added': added})


# ---------- EXPORT endpoints (admin only) ----------
def _make_attendance_export(selected_date: str):
    # Build a workbook combining roster and attendance for selected_date
    from openpyxl import Workbook
    from openpyxl.styles import PatternFill

    selected_date = normalize_date_string(selected_date)
    roster = fetch_students()

    wb = Workbook()
    ws = wb.active
    ws.title = f"Attendance_{selected_date}"

    headers = ["S-Number", "Name", "Timestamp", "Present", "PhotoPath"]
    ws.append(headers)

    present_ids = set()
    attendance_map = {}
    for row in fetch_attendance_for_date(selected_date):
        s_num = str(row.get("s_number", "")).strip()
        if not s_num:
            continue
        present_ids.add(s_num)
        attendance_map[s_num] = {
            'timestamp': format_timestamp(row.get("checkin_ts")),
            'photo_path': row.get("photo_path") or ''
        }

    red_fill = PatternFill(start_color='FFEFEF', end_color='FFEFEF', fill_type='solid')

    # Split roster so absentees appear first
    absent_list = []
    present_list = []
    for student in roster:
        s = str(student.get('s_number', '')).strip()
        name = student.get('name', '')
        present = 'Yes' if s in present_ids else 'No'
        ts = attendance_map.get(s, {}).get('timestamp', '')
        photo = attendance_map.get(s, {}).get('photo_path', '')
        record = { 's': s, 'name': name, 'present': present, 'ts': ts, 'photo': photo }
        if present == 'No':
            absent_list.append(record)
        else:
            present_list.append(record)

    for record in (absent_list + present_list):
        s = record['s']
        name = record['name']
        present = record['present']
        ts = record['ts']
        photo = record['photo']

        # Store the public photo URL in the export
        ws.append([s, name, ts, present, photo or ""])

        # Highlight absentees (they are at top already)
        if present == 'No':
            row_idx = ws.max_row
            for col in range(1, len(headers) + 1):
                ws.cell(row=row_idx, column=col).fill = red_fill

    return wb


@app.route('/export/students')
def export_students():
    if not is_authed():
        return redirect(url_for('history'))
    from openpyxl import Workbook

    students = fetch_students()
    wb = Workbook()
    ws = wb.active
    ws.title = 'Students'
    ws.append(['Name', 'S-Number'])
    for student in students:
        ws.append([student.get('name', ''), student.get('s_number', '')])

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return send_file(bio, as_attachment=True, download_name="students.xlsx", mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@app.route('/export/attendance')
def export_attendance():
    if not is_authed():
        return redirect(url_for('history'))
    selected_date = normalize_date_string(request.args.get('date', get_today_str()))
    wb = _make_attendance_export(selected_date)
    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return send_file(bio, as_attachment=True, download_name=f"attendance_{selected_date}.xlsx", mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@app.route('/export/analytics')
def export_analytics():
    if not is_authed():
        return redirect(url_for('history'))
    # Generate analytics workbook (simple CSV-like sheet + bar chart)
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference

    attendance_basic = fetch_attendance_basic()
    available_dates = sorted(
        {normalize_date_string(row["checkin_date"]) for row in attendance_basic if row.get("checkin_date")},
        reverse=True,
    )
    students = fetch_students()
    analytics = calculate_analytics(students, attendance_basic, available_dates)

    wb = Workbook()
    ws = wb.active
    ws.title = 'Analytics'
    ws.append(['Name', 'S-Number', 'Present Count', 'Absent Count', 'Attendance Rate'])
    for s in analytics['students']:
        ws.append([s['name'], s['s_number'], s['present_count'], s['absent_count'], s['attendance_rate']])

    # Add a simple bar chart for present_count
    chart = BarChart()
    chart.title = 'Present Count per Student'
    chart.y_axis.title = 'Present Count'
    chart.x_axis.title = 'Student'
    data = Reference(ws, min_col=3, min_row=1, max_row=ws.max_row)
    cats = Reference(ws, min_col=1, min_row=2, max_row=ws.max_row)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(cats)
    ws.add_chart(chart, 'H2')

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return send_file(bio, as_attachment=True, download_name='analytics.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


if __name__ == "__main__":
    # For Chromebook local testing
    app.run(host="0.0.0.0", port=5005, debug=True)
