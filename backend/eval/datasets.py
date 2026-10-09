"""Loaders for public benchmark data. Data is never bundled or downloaded here.

* YOLO-format detection sets (e.g. COCO128: images/<split>/*.jpg + labels/<split>/*.txt,
  each label line `class cx cy w h` normalised to [0,1]).
* MOTChallenge sequences (MOT17/MOT20: <seq>/img1/*.jpg, <seq>/gt/gt.txt,
  <seq>/seqinfo.ini).

MOT17 gt.txt columns: frame, id, x, y, w, h, consider, class, visibility.
Class 1 = pedestrian (scored). Classes 2 (person on vehicle), 7 (static person),
8 (distractor) and 12 (reflection) are treated as don't-care regions, following
the MOTChallenge evaluation protocol.
"""

from __future__ import annotations

import configparser
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}
MOT_PEDESTRIAN = 1
MOT_IGNORE_CLASSES = {2, 7, 8, 12}


@dataclass(frozen=True)
class Sample:
    name: str
    image_path: Path
    gt: np.ndarray        # (N,4) xyxy pixels: boxes to be detected / counted
    ignore: np.ndarray    # (M,4) xyxy pixels: don't-care regions
    gt_ids: tuple[int, ...] = ()   # MOT identities aligned with gt (empty for COCO)
    frame_index: int = 0


def load_yolo_split(root: Path, split: str = "train2017", class_id: int = 0) -> list[Sample]:
    """Images of a YOLO-format dataset with boxes of one class (0 = person in COCO)."""
    import cv2

    image_dir, label_dir = root / "images" / split, root / "labels" / split
    if not image_dir.is_dir():
        raise FileNotFoundError(f"no image directory at {image_dir}")
    samples = []
    for image_path in sorted(p for p in image_dir.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES):
        img = cv2.imread(str(image_path))
        if img is None:
            continue
        h, w = img.shape[:2]
        boxes = []
        label_path = label_dir / f"{image_path.stem}.txt"
        if label_path.exists():
            for line in label_path.read_text().split("\n"):
                parts = line.split()
                if len(parts) < 5 or int(float(parts[0])) != class_id:
                    continue
                cx, cy, bw, bh = (float(v) for v in parts[1:5])
                boxes.append([(cx - bw / 2) * w, (cy - bh / 2) * h, (cx + bw / 2) * w, (cy + bh / 2) * h])
        samples.append(Sample(image_path.stem, image_path, np.array(boxes).reshape(-1, 4), np.zeros((0, 4))))
    return samples


def read_seqinfo(seq_dir: Path) -> dict[str, str]:
    parser = configparser.ConfigParser()
    parser.read(seq_dir / "seqinfo.ini")
    return dict(parser["Sequence"]) if parser.has_section("Sequence") else {}


def load_mot_sequence(seq_dir: Path, min_visibility: float = 0.0) -> list[Sample]:
    """Frames of one MOT sequence that exist on disk, with per-frame GT."""
    info = read_seqinfo(seq_dir)
    image_dir = seq_dir / info.get("imdir", "img1")
    rows = np.loadtxt(seq_dir / "gt" / "gt.txt", delimiter=",", ndmin=2)
    by_frame: dict[int, list[np.ndarray]] = defaultdict(list)
    for row in rows:
        by_frame[int(row[0])].append(row)

    samples = []
    for image_path in sorted(p for p in image_dir.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES):
        frame_no = int(image_path.stem)
        gt, ids, ignore = [], [], []
        for row in by_frame.get(frame_no, []):
            _, tid, x, y, bw, bh, consider, cls, vis = row[:9]
            box = [x, y, x + bw, y + bh]
            if int(cls) == MOT_PEDESTRIAN and int(consider) == 1 and vis >= min_visibility:
                gt.append(box)
                ids.append(int(tid))
            elif int(cls) == MOT_PEDESTRIAN or int(cls) in MOT_IGNORE_CLASSES:
                ignore.append(box)   # non-considered or too-occluded pedestrians: don't care
        samples.append(Sample(
            f"{seq_dir.name}/{image_path.stem}", image_path,
            np.array(gt).reshape(-1, 4), np.array(ignore).reshape(-1, 4),
            tuple(ids), frame_no,
        ))
    return samples


def iter_mot_sequences(root: Path) -> Iterator[Path]:
    for seq in sorted(p for p in root.iterdir() if (p / "gt" / "gt.txt").exists()):
        yield seq
