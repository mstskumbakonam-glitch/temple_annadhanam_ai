"""Annotated preview JPEG of a camera's last processed frame.

Draws what the AI saw (boxes, temporary track ids), the configured counting
lines (with an arrow pointing in the ENTRY direction) and zones. With
PREVIEW_ANONYMIZE (default on) the head area of every person box is pixelated,
so the preview shows crowd movement without showing faces. Recorded demo video
is stamped "RECORDED VIDEO - NOT LIVE".

Only enabled with PREVIEW_ENABLED=true; the frame is never stored on disk.
"""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np

from app.ai.analytics_config import AnalyticsConfig, DensityLevel
from app.ai.types import TrackedObject

LEVEL_COLOURS = {  # BGR
    DensityLevel.LOW: (90, 170, 60),
    DensityLevel.MEDIUM: (40, 180, 230),
    DensityLevel.HIGH: (30, 120, 245),
    DensityLevel.CRITICAL: (40, 40, 220),
}


def _pixelate(img: np.ndarray, x1: int, y1: int, x2: int, y2: int, block: int = 10) -> None:
    """Replace a region with coarse `block`-pixel squares (irreversible at JPEG quality)."""
    import cv2

    region = img[y1:y2, x1:x2]
    if region.size == 0:
        return
    h, w = region.shape[:2]
    small = cv2.resize(region, (max(1, w // block), max(1, h // block)),
                       interpolation=cv2.INTER_AREA)
    img[y1:y2, x1:x2] = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)


def render_preview(
    frame: np.ndarray,
    tracks: tuple[TrackedObject, ...],
    *,
    config: AnalyticsConfig,
    zone_levels: dict[str, DensityLevel],
    recorded: bool,
    timestamp: datetime,
    anonymize: bool = True,
    max_width: int = 960,
    quality: int = 70,
) -> bytes:
    import cv2

    img = np.array(frame, copy=True)          # the shared frame is read-only
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    h, w = img.shape[:2]

    if anonymize:
        for t in tracks:
            x1, y1 = max(0, int(t.x1)), max(0, int(t.y1))
            x2 = min(w, int(t.x2))
            y2 = min(h, int(t.y1 + 0.28 * (t.y2 - t.y1)))
            _pixelate(img, x1, y1, x2, y2)

    overlay = img.copy()
    for zone in config.zones:
        pts = np.array([[int(x * w), int(y * h)] for x, y in zone.polygon], dtype=np.int32)
        colour = LEVEL_COLOURS.get(zone_levels.get(zone.id, DensityLevel.LOW))
        cv2.fillPoly(overlay, [pts], colour)
    cv2.addWeighted(overlay, 0.18, img, 0.82, 0, img)
    thick = max(1, round(w / 640))
    for zone in config.zones:
        pts = np.array([[int(x * w), int(y * h)] for x, y in zone.polygon], dtype=np.int32)
        colour = LEVEL_COLOURS.get(zone_levels.get(zone.id, DensityLevel.LOW))
        cv2.polylines(img, [pts], True, colour, thick)
        x0, y0 = int(pts[:, 0].min()) + 6, int(pts[:, 1].min()) + 20 * thick
        y0 = max(y0, 46 * thick)                  # keep clear of the banner
        cv2.putText(img, zone.name or zone.id, (x0, y0),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55 * thick, colour, thick, cv2.LINE_AA)

    for line in config.lines:
        s = (int(line.start[0] * w), int(line.start[1] * h))
        e = (int(line.end[0] * w), int(line.end[1] * h))
        cv2.line(img, s, e, (255, 220, 0), 2 * thick, cv2.LINE_AA)
        # Arrow from the midpoint along the normal that points to the ENTRY side.
        dx, dy = e[0] - s[0], e[1] - s[1]
        n = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / n, dx / n
        if line.invert:
            nx, ny = -nx, -ny
        mid = ((s[0] + e[0]) // 2, (s[1] + e[1]) // 2)
        tip = (int(mid[0] + nx * 40 * thick), int(mid[1] + ny * 40 * thick))
        cv2.arrowedLine(img, mid, tip, (255, 220, 0), 2 * thick, cv2.LINE_AA, tipLength=0.35)
        cv2.putText(img, f"{line.name or line.id} IN", (tip[0] + 4, tip[1]),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5 * thick, (255, 220, 0), thick, cv2.LINE_AA)

    for t in tracks:
        p1, p2 = (int(t.x1), int(t.y1)), (int(t.x2), int(t.y2))
        cv2.rectangle(img, p1, p2, (80, 220, 80), thick)
        cv2.circle(img, (int((t.x1 + t.x2) / 2), int(t.y2)), 3 * thick, (80, 220, 80), -1)
        cv2.putText(img, str(t.track_id), (p1[0], max(12, p1[1] - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45 * thick, (80, 220, 80), thick, cv2.LINE_AA)

    stamp = timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
    label = f"RECORDED VIDEO - NOT LIVE   {stamp}" if recorded else f"LIVE   {stamp}"
    cv2.rectangle(img, (0, 0), (w, 26 * thick), (0, 0, 0), -1)
    cv2.putText(img, label, (8, 18 * thick), cv2.FONT_HERSHEY_SIMPLEX, 0.55 * thick,
                (0, 200, 255) if recorded else (255, 255, 255), thick, cv2.LINE_AA)

    if w > max_width:
        img = cv2.resize(img, (max_width, int(h * max_width / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return buf.tobytes()
