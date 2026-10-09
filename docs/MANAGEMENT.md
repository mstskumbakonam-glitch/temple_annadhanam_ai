# Temple and annadhanam hall management

The application manages several temples, each with one or more annadhanam halls,
their seats, the session calendar and staff. The AI / CCTV features remain
available as an optional "AI / CCTV monitoring" page.

## Data model

```
temples 1 ──< annadhanam_halls 1 ──< seats 1 ──< seat_status_events
                     │   1                 │
                     │                     └── reserved_session_id ──> annadhanam_sessions
                     └──< annadhanam_sessions >── responsible_staff_id ──> staff
staff >── temple_id / hall_id (assignment)          staff 1 ──< staff_daily_attendance
```

| Table | Key rules |
|---|---|
| `temples` | `temple_code` unique; `active`; `is_demo` marks sample rows |
| `annadhanam_halls` | `hall_code` globally unique; belongs to exactly one temple (`ON DELETE RESTRICT`) |
| `seats` | `hall_id` holds the hall **code** (historic column name) and is a foreign key to `annadhanam_halls.hall_code` (`ON UPDATE CASCADE`); seat code unique per hall; one `status` per seat; `status_version` for optimistic locking; reservation fields allowed only while `RESERVED` (CHECK constraint) |
| `seat_status_events` | append-only history of every confirmed change, with the time spent in the previous status |
| `annadhanam_sessions` | one hall; UTC `starts_at`/`ends_at` plus local `session_date`; **EXCLUDE constraint** (btree_gist) forbids overlapping non-cancelled sessions in one hall; cancel needs a reason |
| `staff` | adds phone, designation, shift, temple and hall assignment (hall must belong to the temple) |
| `staff_daily_attendance` | one row per person per day (unique), status PRESENT / HALF_DAY / ABSENT / LEAVE |

Migration: `alembic/versions/20261009_1800_d4e8f2a61b37_phase5c_temple_hall_management.py`.
If seats already exist, it places their hall codes under one placeholder temple named
"Unassigned (migrated)" so no row is lost; move those halls to their real temple and
delete the placeholder. An empty database gets no placeholder.

## Seat counting rules

| Figure | Rule |
|---|---|
| Installed | enabled seats (disabled seats are removed from service entirely) |
| Capacity | installed seats that are not OUT_OF_SERVICE |
| Occupied | status OCCUPIED |
| Reserved | status RESERVED (a reserved seat is not available) |
| Available | status AVAILABLE = capacity − occupied − reserved |
| Occupancy % | occupied ÷ capacity × 100, 0 when capacity is 0 |
| Temple / dashboard totals | only active halls of active temples |

A seat has exactly one status, so nothing is counted twice. Capacity is always
counted from seat rows, never stored separately.

**Allowed changes:** Available → Occupied / Reserved / Out of service;
Reserved → Occupied / Available / Out of service; Occupied → Available;
Out of service → Available. Anything else returns 409.

**Two users, one seat:** the change locks the seat row (`SELECT … FOR UPDATE`) and
re-checks the status inside the transaction. Eight simultaneous "occupy" requests
produce exactly one success and seven 409s (tested with real threads and separate
connections; the test fails if the lock is removed). Clients can also send
`expected_version` so a stale screen cannot overwrite someone else's change.

**AI is advisory.** Camera detections never set a seat status. The older
`seat_occupancy` table stays as AI observations; a confirmed status from an AI
suggestion is recorded with `status_source = AI_CONFIRMED`.

## Sessions

* Entered and displayed in Asia/Kolkata (`SITE_TIMEZONE`), stored in UTC.
* Same hall, overlapping times → 409 naming the clashing session. Back-to-back is fine.
  The database constraint also stops two people saving at the same instant.
* Cancelled sessions free their slot. Completed sessions accept only the actual
  number served and notes.
* A capacity warning is shown when expected devotees exceed the hall's capacity.
* A scheduled session whose end time has passed is shown as "Not closed".

## Attendance

The daily register is marked by supervisors. Staff with no entry are **not marked**,
which is reported separately and never counted as absent. Until any attendance has
been recorded, the dashboard says attendance is not being recorded rather than
showing zeros. Future dates cannot be marked.

## Roles

| Action | Viewer | Operator | Admin |
|---|---|---|---|
| Dashboards, temples, halls, seats, calendar, capacity/occupancy/session reports | ✓ | ✓ | ✓ |
| Change seat status, schedule/edit/cancel sessions, record actual devotees | | ✓ | ✓ |
| Attendance register, staff list (without phone numbers), staff-attendance report | | ✓ | ✓ |
| Add/edit/delete temples, halls, seat layouts; register/edit staff; see phone numbers; cameras | | | ✓ |

Keys: `API_VIEWER_KEYS`, `API_OPERATOR_KEYS`, `API_ADMIN_KEYS` in `backend/.env`.

## API summary

| Area | Endpoints |
|---|---|
| Session info | `GET /api/auth/me` |
| Dashboard | `GET /api/dashboard/overview?temple_code=` |
| Temples | `GET/POST /api/temples`, `GET /api/temples/districts`, `GET/PUT/DELETE /api/temples/{code}` |
| Halls | `GET/POST /api/halls`, `GET/PUT/DELETE /api/halls/{code}`, `POST /api/halls/{code}/seats/generate` |
| Seats | `GET /api/halls/{code}/seats`, `POST /api/halls/{code}/seats/{seat}/status`, `GET /api/halls/{code}/seats/history` (seat CRUD stays at `/api/seats`) |
| Sessions | `GET/POST /api/sessions`, `GET/PUT /api/sessions/{id}`, `POST /api/sessions/{id}/cancel` |
| Staff | `GET/POST /api/staff`, `GET/PUT/DELETE /api/staff/{code}` (search, temple, hall, shift filters) |
| Attendance | `GET /api/attendance?date=`, `PUT /api/attendance` |
| Reports (`?format=csv`) | `/api/reports/temple-capacity`, `hall-occupancy`, `sessions`, `staff-attendance`, `seat-occupancy` |

CSV exports neutralise cells starting with `= + - @` so a spreadsheet will not run them as formulas.

## Sample data

`python ../scripts/seed_management_demo.py` (only with `DEMO_MODE=true`, never in production)
creates two clearly named sample temples ("Sample Temple A (demo)"), three halls, 162 seats,
six "Sample Staff" members, sessions and one day of attendance. Every row is flagged
`is_demo` and shown with a "Sample data" badge. `--remove` deletes them; `--reset` recreates them.
