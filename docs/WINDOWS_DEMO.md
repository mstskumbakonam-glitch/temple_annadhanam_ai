# Running the demo on Windows

This runs the full system — PostgreSQL, the AI backend and the dashboard — on one
Windows 10/11 PC, using a **recorded video** instead of a camera. Everything the
dashboard shows is real model output on that video, and it is labelled
"RECORDED VIDEO — not live" everywhere.

Commands are for **PowerShell**. Run them from the project folder.

## 0. Install once

* Python 3.12 or newer — https://www.python.org (tick "Add python.exe to PATH")
* Node.js 22 LTS — https://nodejs.org
* PostgreSQL 16 — either Docker Desktop, or the PostgreSQL Windows installer
* Git

## 1. Database

**With Docker Desktop** (from the project root):

```powershell
copy backend\.env.example backend\.env
notepad backend\.env      # replace every change_me with your own password
docker compose --env-file backend/.env up -d postgres
```

**With the PostgreSQL installer:** create two databases in pgAdmin or `psql`:
`temple_annadhanam` and `temple_annadhanam_test`, then put your password in
`backend\.env` (both URLs and `POSTGRES_PASSWORD`).

## 2. Backend

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
# CPU-only PyTorch first (much smaller download):
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
alembic upgrade head
```

If PowerShell refuses to run `Activate.ps1`, run once:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## 3. Model weights (never downloaded automatically)

Download one file, e.g. **yolo11n.pt** or **yolo26n.pt**, from
https://github.com/ultralytics/assets/releases (look under the v8.3.0 / v8.4.0 releases),
and save it as `backend\models\yolo11n.pt`. (Read the licence note in
TECHNOLOGY_REVIEW.md: Ultralytics models are AGPL-3.0.)

## 4. Demo video

Copy a video you are allowed to use into `backend\demo_videos\`, for example
`backend\demo_videos\queue.mp4`. A few minutes of people walking through a gate or
standing in a queue works best. Video files in this folder are never committed.

Register it as a demo camera with a default gate line and two zones:

```powershell
python ..\scripts\seed_demo.py --video queue.mp4
```

## 5. Settings for the demo

Edit `backend\.env` and set:

```ini
AI_MODEL_PATH=models/yolo11n.pt
AI_DEVICE=cpu
AI_AUTOSTART=true
AI_SYNC_INTERVAL=10
DEMO_MODE=true
PREVIEW_ENABLED=true
PREVIEW_ANONYMIZE=true
SITE_TIMEZONE=Asia/Kolkata

# Keys: generate two different values with
#   python -c "import secrets; print(secrets.token_urlsafe(32))"
API_VIEWER_KEYS=<paste first value>
API_ADMIN_KEYS=<paste second value>
```

Leaving both key lines empty is possible on your own PC (the API is then open and the
server prints a warning), but never on a network others can reach.

## 6. Start

Terminal 1 (backend, ONE worker):

```powershell
cd backend
.venv\Scripts\Activate.ps1
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Terminal 2 (dashboard):

```powershell
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 and paste the **viewer** key when asked. After 10–30 seconds
(model load + first frames) the camera card shows the processed video with pixelated
heads, the gate line with its IN arrow, zone levels, entries/exits and FPS.

## 7. Adjust lines and zones for your video

Coordinates are fractions of the picture (0 = left/top, 1 = right/bottom). Send a new
configuration with the **admin** key, e.g. in PowerShell:

```powershell
$h = @{ Authorization = "Bearer <admin key>" }
$body = @'
{
  "tuning": {"process_fps": 5, "image_size": 640},
  "lines": [{"id": "GATE", "name": "Main gate", "start": [0.1, 0.6], "end": [0.9, 0.6]}],
  "zones": [{"id": "QUEUE", "name": "Food queue", "kind": "queue",
             "polygon": [[0.05,0.1],[0.45,0.1],[0.45,0.95],[0.05,0.95]],
             "queue_alert_length": 15}]
}
'@
Invoke-RestMethod -Method Put -Uri http://127.0.0.1:8000/api/cameras/DEMO-QUEUE/analytics -Headers $h -ContentType "application/json" -Body $body
```

The worker picks the change up within `AI_SYNC_INTERVAL` seconds. Walking from the
line's left-hand side (looking from `start` to `end`) to its right-hand side is an
entry; add `"invert": true` to swap. The preview arrow shows the entry direction.

Tips from the measurements (EVALUATION.md): keep gate cameras at ≥ 5 AI FPS; for a wide
hall use `"image_size": 1280` with `"process_fps": 2`; put lines in the middle of the
frame, not at the edge.

## 8. Run the tests

```powershell
cd backend
pytest -q                                  # uses temple_annadhanam_test
$env:AI_MODEL_PATH="models/yolo11n.pt"; pytest -q tests/test_real_model_optional.py
cd ..\frontend; npm run build
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| Dashboard keeps asking for a key | Use the viewer or admin key exactly as in `.env`; restart the backend after editing `.env`. |
| `model_unavailable` in System health | `AI_MODEL_PATH` wrong; it is relative to `backend\`. |
| Camera says "Error" | Video file name in `demo_videos` must match the seeded `demo://` name; `DEMO_MODE=true`. |
| `SITE_TIMEZONE ... not a known IANA time zone` | `pip install tzdata` (included in requirements). |
| Very low FPS | Lower `image_size`, fewer demo cameras, close other heavy programs. |
