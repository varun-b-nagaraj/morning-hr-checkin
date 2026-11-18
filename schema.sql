-- HR morning check-in: Neon Postgres schema (safe to run more than once).
CREATE TABLE IF NOT EXISTS students (
	s_number  text PRIMARY KEY CHECK (s_number ~ '^[0-9]+$'),
	name      text NOT NULL
);

CREATE TABLE IF NOT EXISTS attendance (
	id            bigserial PRIMARY KEY,
	s_number      text NOT NULL,
	name          text,
	checkin_ts    timestamptz NOT NULL,
	checkin_date  date NOT NULL,
	photo_path    text,
	UNIQUE (s_number, checkin_date)   -- only the first check-in of the day counts
);
CREATE INDEX IF NOT EXISTS attendance_date_idx ON attendance (checkin_date);

-- Check-in photos (small JPEGs). Served only to signed-in admins via /photo/<id>.
CREATE TABLE IF NOT EXISTS checkin_photos (
	id          bigserial PRIMARY KEY,
	s_number    text NOT NULL,
	data        bytea NOT NULL,
	created_at  timestamptz NOT NULL DEFAULT now()
);
