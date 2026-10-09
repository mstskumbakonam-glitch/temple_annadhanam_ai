# Temple Annadhanam Hall - AI CCTV Management System

A standalone system that uses CCTV cameras and AI to manage an annadhanam (community meal) hall:
visitor counting, staff attendance, seat occupancy, and a live dashboard.

**Status: Phase 5 of 12 - real-time AI camera pipeline (YOLO + ByteTrack).**

## Tech stack

| Layer     | Technology |
|-----------|------------|
| Backend   | Python 3.12+, FastAPI, Uvicorn, SQLAlchemy 2.x, Alembic, Pydantic, psycopg 3 |
| Database  | PostgreSQL 16+ (no SQLite, anywhere) |
| AI        | OpenCV, Ultralytics YOLO, ByteTrack |
| Frontend  | React + Vite |
| Realtime  | WebSocket (`/ws/dashboard`) |

## Privacy rules built into the design

- Normal visitors are **anonymous**. They get a display ID (`VIS-000001`) and are never identified by face.
- Face recognition runs **only** against registered staff. Unknown faces stay `UNKNOWN`.
- Visitor face embeddings are **never stored**.

## Prerequisites

- Python 3.12+
- Node.js 20.19+ (or 22+)
- Docker (for PostgreSQL) **or** a native PostgreSQL 16+ install

## 1. Configure environment

```bash
cd backend
cp .env.example .env        # Windows: copy .env.example .env
# edit .env and replace change_me with a real password (in POSTGRES_PASSWORD and both URLs)
```

## 2. Start PostgreSQL

**Option A - Docker (from the project root):**

```bash
docker compose --env-file backend/.env up -d postgres
docker compose ps
```

Create the separate test database used by pytest:

```bash
docker exec -it temple_annadhanam_postgres psql -U postgres -c "CREATE DATABASE temple_annadhanam_test;"
```

**Option B - native PostgreSQL:**

```bash
psql -U postgres -h localhost -c "CREATE DATABASE temple_annadhanam;"
psql -U postgres -h localhost -c "CREATE DATABASE temple_annadhanam_test;"
```

Verify:

```bash
psql "postgresql://postgres@localhost:5432/temple_annadhanam" -c "SELECT version();"
```

## 3. Run the backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- Health check: http://localhost:8000/api/health
- Database health: http://localhost:8000/api/health/db
- API docs: http://localhost:8000/docs (Swagger) and /redoc

### API layout

Requests flow router -> Pydantic schema -> service -> SQLAlchemy -> PostgreSQL.
Routers stay thin; queries and transactions live in `app/services`.

| Area | Endpoints |
|---|---|
| Dashboard | `GET /api/dashboard/summary`, `GET /api/halls/{hall_id}/occupancy` |
| Cameras | `GET|POST /api/cameras`, `GET /api/cameras/status`, `GET|PUT|DELETE /api/cameras/{camera_id}`, `GET /api/cameras/{camera_id}/events` |
| Visitors | `GET /api/visitors`, `/current`, `/today`, `/{visitor_code}`, `/{visitor_code}/events` |
| Staff | `GET|POST /api/staff`, `GET /api/staff/attendance`, `GET|PUT|DELETE /api/staff/{staff_code}`, `GET /api/staff/{staff_code}/attendance` |
| Seats | `GET|POST /api/seats`, `GET /api/seats/status`, `GET /api/seats/history`, `GET|PUT|DELETE /api/seats/{seat_id}` |

List endpoints return `{items, page, page_size, total}` with `page_size` capped at 200.
Errors return `{detail, code}`; SQLAlchemy messages are logged, never returned.

### Apply migrations

Run from `backend/` with the virtualenv active:

```bash
alembic current                                   # show applied revision
alembic upgrade head                              # apply migrations
alembic revision --autogenerate -m "description"  # create a migration (Phase 3+)
alembic downgrade -1                              # roll back one revision
```

The database URL comes from `.env`; `alembic.ini` holds no credentials.

### Optional development seed data

```bash
python ../scripts/seed_dev.py           # sample cameras and seats S01-S06
python ../scripts/seed_dev.py --reset   # remove and recreate them
```

Development only. It is never run by Alembic, refuses to run when `APP_ENV=production`,
and is safe to re-run. Replace the placeholder seat polygons with real camera coordinates.

### Run the tests

```bash
pytest
```

Tests use `TEST_DATABASE_URL` (the `temple_annadhanam_test` database) and never
touch production data. There is no SQLite fallback: if the test database is
unreachable the suite skips loudly rather than switching engines.

## 4. Run the frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The Vite dev server proxies `/api` and `/ws` to the backend.

## AI camera pipeline (Phase 5)

```
RTSP camera -> LatestFrameBuffer -> YOLO person detection -> ByteTrack -> processors -> PostgreSQL
 (continuous)   (newest frame only)   (paced to AI_PROCESS_FPS)  (per camera)   (events)
```

Each camera runs two threads: capture never waits for inference, and inference never
runs faster than `AI_PROCESS_FPS`. Cameras are read **only** from the `cameras` table
(enabled, with an `rtsp_url`) and are picked up, dropped or restarted automatically
when they change through the API.

### Set up the model (weights are never downloaded automatically)

```bash
mkdir -p backend/models
cp /path/to/yolov8n.pt backend/models/          # you supply the weights
# backend/.env
AI_MODEL_PATH=models/yolov8n.pt                 # relative paths resolve against backend/
AI_DEVICE=cpu                                   # or cuda / cuda:0
AI_AUTOSTART=true
```

Run **one** uvicorn worker when `AI_AUTOSTART=true`. A PostgreSQL advisory lock makes a
second process report `not_owner` instead of writing every event twice.

```bash
uvicorn app.main:app --port 8000          # not --workers N
curl localhost:8000/api/ai/status
curl localhost:8000/api/cameras/ANN-ENT-01/ai-status
```

CPU-only installs can use a much smaller PyTorch:
`pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu`

### What is stored

| Event | Where | Notes |
|---|---|---|
| Connected / disconnected / stream error / recovered | `camera_events` (`ONLINE`, `OFFLINE`, `ERROR`, `RECOVERED`) | one row per **transition**, not per retry |
| AI started / stopped | `camera_events` (`AI_STARTED`, `AI_STOPPED`) | added by migration `ab4842faaacb` |
| Track confirmed / track ended | `visitor_events` (`DETECTED`, `LOST`) | `visitor_id` is **NULL**: a ByteTrack id is temporary and is not a visitor |
| `cameras.status`, `fps`, `last_frame_time` | `cameras` | updated on transitions and a periodic heartbeat, never per frame |

Live counts (`person_count`, `active_tracks`, FPS) are runtime state only. They are `null`,
not `0`, whenever a camera is not connected or AI is not running.

### Privacy and secrets

* No face detection or recognition exists in this phase; visitors remain anonymous.
* RTSP passwords are redacted from every log record in the process, from stored event
  messages and from API responses. FFmpeg's own native logging (which bypasses Python
  logging) is silenced.
* Ultralytics runs offline: no runtime `pip install`, no analytics sync.

### Tests

```bash
pytest -q                                          # no weights or GPU needed
AI_MODEL_PATH=models/yolov8n.pt pytest -q          # also runs the 5 real-model tests
```

The default suite uses a deterministic fake YOLO model and a scripted stream, with the
real Ultralytics ByteTrack. Real-model tests are skipped, and reported as skipped, when
no weights are configured.

## Project layout

```
backend/app/
  main.py        FastAPI entry point
  config.py      Settings from .env
  database.py    Engine, session factory, connectivity check
  models/base.py Declarative base, UTC timestamp mixin
  models/        SQLAlchemy models: cameras, visitors, staff, seats, occupancy, events
  api/           REST routers, dependencies, error handlers
  schemas/       Pydantic request/response models
  services/      Query and transaction logic
  ai/            model loader, detector, ByteTrack tracker, pipeline, processors
  camera/        RTSP source, frame buffer, worker, manager, runtime state
  utils/         Helpers
backend/alembic/ Migration environment (env.py reads .env)
backend/tests/   pytest, PostgreSQL only
frontend/src/    React dashboard (Phase 10)
```

## Roadmap

1. Project structure
2. PostgreSQL + SQLAlchemy + Alembic
3. Models and first migration
4. FastAPI REST endpoints
5. YOLO + ByteTrack  <- current
6. Visitor management
7. Staff + face recognition
8. Seat occupancy
9. WebSocket events
10. React dashboard
11. Reports
12. Full test run
