# Phase 5 audit and upgrade summary

Audit date: 9 Oct 2026, commit `507b98e` ("Initial Temple Annadhanam AI Phase 5").
Upgrade branch: `phase5-ai-upgrade`.

## Architecture

```
                  cameras table (PostgreSQL)  <-- REST: /api/cameras, /api/cameras/{id}/analytics
                             |
                     CameraManager (supervisor thread, advisory lock = one owner process)
                             |
         per camera: CameraWorker
           capture thread:  RTSP / demo:// video -> LatestFrameBuffer (1 frame, epoch-tagged)
           AI thread:       paced to process_fps -> DetectionPipeline
                              YoloDetector (shared, locked model)
                              ByteTrackTracker (private per camera)
                              processors: TrackLifecycleProcessor, CrowdAnalyticsProcessor
                             |
               EventSink (DatabaseEventWriter: one thread, bounded queue, batched commits)
                             |
   visitor_events (DETECTED/LOST/ENTERED/EXITED, anonymous) · camera_events · crowd_alerts · crowd_count_snapshots
                             |
   FastAPI (role-checked routers, rate limit, security headers) --> React dashboard (polling)
```

Runtime-only state (live counts, FPS, zone levels, preview frame) lives in
`app.camera.state.runtime_registry`; nothing is written to the database per frame.

## Feature matrix

Legend: **1** implemented and tested · **2** implemented, not tested · **3** partial ·
**4** not implemented · **5** blocked (hardware, weights, camera access, configuration).
"Before" is the audited commit; "After" is this branch.

| Feature | Before | After | Evidence / note |
|---|---|---|---|
| FastAPI CRUD (cameras, staff, seats, visitors read) | 1 | 1 | tests/test_api_*.py |
| PostgreSQL models + Alembic migrations, drift check | 1 | 1 | test_migrations.py incl. new revision `c7d1a2e5f901` |
| YOLO model loading (offline, shared, locked) | 1 | 1 | test_model_loader.py |
| Real YOLO weights | 5 | 1 | 5 real-model tests pass with YOLOv8n and YOLO26n weights (not committed) |
| GPU inference | 5 | 5 | No GPU available; `AI_DEVICE=cuda` path untested |
| ByteTrack per camera | 1 | 1 | test_tracker.py; also exercised by simulation |
| RTSP capture + reconnect back-off | 1 (fake stream) | 1 (fake stream) | Real RTSP camera: **5**, none available |
| Stale-frame skipping / stale flag | 3 | 1 | worker skip existed; API `stale` flag added and tested |
| Tracking continuity across gaps / loops | 3 | 1 | epoch per frame; test_demo_video.py |
| Person count per camera | 1 | 1 | plus `confirmed_count` used by analytics |
| Entry/exit line counting | 4 | 1 | test_analytics.py, crossing simulation |
| Crowd-density zones + thresholds (count or per m²) | 4 | 1 | test_analytics.py |
| Queue length, wait estimate, congestion alert | 4 | 1 | test_analytics.py |
| Debounced alerts, storage, acknowledge | 4 | 1 | test_analytics*.py, test_api_analytics.py |
| Per-minute count history + trends API | 4 | 1 | test_analytics_persistence.py, test_api_analytics.py |
| Per-camera tuning (image size, FPS, confidence) | 4 | 1 | manager tests + API tests |
| Annotated preview with head pixelation | 4 | 1 | test_api_analytics.py (pixelation checked) |
| Demo mode with recorded video, labelled | 4 | 1 | test_demo_video.py; verified with real YOLO11n on a sample clip |
| Dashboard (React) | 3 (health page) | 1 build / 2 behaviour | `npm run build` passes; checked visually on desktop and mobile with a live backend; no automated UI tests |
| Authentication / authorization | 4 | 1 | viewer/admin keys; test_security_auth.py |
| Production safety guards (keys, CORS, docs) | 4 | 1 | test_security_auth.py |
| Rate limiting | 4 | 1 | in-memory per process |
| Input validation of camera URLs | 3 (create only) | 1 | update path was unvalidated (file:// accepted) — fixed |
| Secret redaction in logs/API | 1 | 1 | unchanged + alert messages redacted |
| Face recognition (staff) | 4 | 4 | deliberately not built (Phase 7, needs privacy review) |
| Seat occupancy from video | 4 | 4 | tables exist; AI processor not built (Phase 8) |
| WebSocket push | 4 | 4 | dashboard polls every 2 s instead |
| Push notifications (SMS/WhatsApp) | 4 | 4 | see TECHNOLOGY_REVIEW §9 |
| Cloud deployment | 5 | 5 | not attempted; see DEPLOYMENT.md |

## Defects found during the audit and upgrade

1. `PUT /api/cameras/{id}` stored any `rtsp_url` without validation, so an API caller
   could point a camera at `file:///…` and make the server open local files. Fixed;
   regression tests added.
2. No authentication on any route, including camera creation/deletion. Fixed (keys).
3. The dashboard's `today_entries` reads the `visitors` table, which nothing populates
   yet; line-crossing totals are now provided by `/api/analytics/live` instead.
4. Found while building the demo: a reset signalled by a flag can race with the next
   frame, so the first frame after a video loop (or a reconnect) could be processed by
   the *old* tracker and a track could "jump" across a counting line. Fixed with a
   continuity epoch carried by each frame; regression test added. (A remaining
   entries-vs-exits imbalance on the 2-second sample clip was traced to a different,
   expected cause: people crossing within ~1 s of appearing are not yet confirmed.)
5. CORS allowed credentials with wildcard methods/headers; now restricted (bearer keys,
   no cookies).
