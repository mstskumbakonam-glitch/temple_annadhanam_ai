"""YOLO model loading.

Rules enforced here:

* The model path comes only from configuration (AI_MODEL_PATH). There is no
  built-in default, and nothing is downloaded automatically: Ultralytics will
  fetch weights by name if the file is missing, so a missing file is turned into
  a clear ConfigurationError *before* Ultralytics is ever asked to load it.
* The model is loaded once per process. Cameras share one ModelHandle, and every
  inference call is serialised by a lock because an Ultralytics model keeps
  mutable predictor state and is not safe to call from several threads at once.
* Ultralytics is put in offline mode (no auto-install of packages, no analytics
  sync). A temple CCTV system must not phone home or pip-install at runtime.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.utils.logs import log_event

logger = logging.getLogger("temple_annadhanam.ai")

# backend/ - relative model paths resolve against this, never the process cwd.
BACKEND_DIR = Path(__file__).resolve().parents[2]


class ModelConfigurationError(RuntimeError):
    """The model cannot be used because of a configuration problem."""


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
def resolve_model_path(raw_path: str | None, base_dir: Path = BACKEND_DIR) -> Path:
    """Turn AI_MODEL_PATH into an existing file, or raise a clear error."""
    if raw_path is None or not raw_path.strip():
        raise ModelConfigurationError(
            "AI_MODEL_PATH is not set. Point it at a YOLO weights file "
            "(for example models/yolov8n.pt). Weights are never downloaded "
            "automatically."
        )
    path = Path(raw_path.strip()).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    if not path.exists():
        raise ModelConfigurationError(
            f"YOLO model file not found: '{raw_path}'. Place the weights there or "
            "correct AI_MODEL_PATH. Weights are never downloaded automatically."
        )
    if not path.is_file():
        raise ModelConfigurationError(f"AI_MODEL_PATH '{raw_path}' is not a file.")
    return path


# ---------------------------------------------------------------------------
# Handle: one loaded model, safe to share between camera threads
# ---------------------------------------------------------------------------
class ModelHandle:
    """A loaded model plus the lock that serialises inference on it."""

    def __init__(
        self,
        model: Any,
        *,
        path: Path,
        device: str,
        names: Mapping[int, str],
    ) -> None:
        self._model = model
        self._lock = threading.Lock()
        self.path = path
        self.device = device
        self.names: dict[int, str] = dict(names)
        self.loaded_at = time.time()
        self.inference_calls = 0

    @property
    def model_name(self) -> str:
        """File name only: the directory layout of the host is not disclosed."""
        return self.path.name

    def predict(
        self,
        frame: Any,
        *,
        conf: float,
        iou: float,
        imgsz: int,
        classes: list[int] | None,
    ) -> Any:
        with self._lock:
            self.inference_calls += 1
            return self._model.predict(
                frame,
                conf=conf,
                iou=iou,
                imgsz=imgsz,
                classes=classes,
                device=self.device,
                verbose=False,
            )


# ---------------------------------------------------------------------------
# Real loader (Ultralytics) - injectable so tests never need weights or a GPU
# ---------------------------------------------------------------------------
ModelLoader = Callable[[Path, str], Any]


def configure_ultralytics_offline() -> None:
    """Disable runtime pip installs and analytics before Ultralytics is imported."""
    os.environ.setdefault("YOLO_AUTOINSTALL", "False")
    os.environ.setdefault("YOLO_VERBOSE", "False")


def ultralytics_loader(path: Path, device: str) -> Any:
    configure_ultralytics_offline()
    try:
        from ultralytics import YOLO, settings as ultralytics_settings
    except ImportError as exc:
        raise ModelConfigurationError(
            "The 'ultralytics' package is not installed. "
            "Install backend/requirements.txt."
        ) from exc

    try:
        ultralytics_settings.update({"sync": False})  # no analytics upload
    except Exception:  # settings storage problems must not block loading
        logger.debug("Could not update Ultralytics settings", exc_info=True)

    if device.startswith("cuda"):
        import torch

        if not torch.cuda.is_available():
            raise ModelConfigurationError(
                f"AI_DEVICE='{device}' but CUDA is not available on this machine. "
                "Use AI_DEVICE=cpu or install a CUDA-enabled PyTorch."
            )

    try:
        return YOLO(str(path), task="detect")
    except Exception as exc:
        raise ModelConfigurationError(
            f"Could not load YOLO weights '{path.name}': {type(exc).__name__}: {exc}"
        ) from exc


def _extract_names(model: Any) -> dict[int, str]:
    names = getattr(model, "names", None)
    if isinstance(names, Mapping):
        result = {int(k): str(v) for k, v in names.items()}
    elif isinstance(names, (list, tuple)):
        result = {i: str(v) for i, v in enumerate(names)}
    else:
        result = {}
    if not result:
        raise ModelConfigurationError("The loaded model does not expose class names.")
    return result


# ---------------------------------------------------------------------------
# Registry: load once per process
# ---------------------------------------------------------------------------
class ModelRegistry:
    """Caches loaded models so every camera reuses one instance."""

    def __init__(
        self,
        loader: ModelLoader = ultralytics_loader,
        *,
        warmup: bool = True,
        base_dir: Path = BACKEND_DIR,
    ) -> None:
        self._loader = loader
        self._warmup = warmup
        self._base_dir = base_dir
        self._handles: dict[tuple[str, str], ModelHandle] = {}
        self._lock = threading.Lock()
        self.load_count = 0

    def get(self, raw_path: str | None, device: str, imgsz: int = 640) -> ModelHandle:
        """Return the shared handle, loading the model on first use only."""
        path = resolve_model_path(raw_path, self._base_dir)
        key = (str(path), device)

        # The lock is held while loading so concurrent callers wait for the one
        # load instead of each loading their own copy.
        with self._lock:
            handle = self._handles.get(key)
            if handle is not None:
                return handle

            started = time.monotonic()
            model = self._loader(path, device)
            handle = ModelHandle(model, path=path, device=device, names=_extract_names(model))
            if self._warmup:
                self._run_warmup(handle, imgsz)

            self._handles[key] = handle
            self.load_count += 1
            log_event(
                logger, logging.INFO, "AI", None, "model_loaded",
                model=handle.model_name, device=device, classes=len(handle.names),
                seconds=time.monotonic() - started,
            )
            return handle

    @staticmethod
    def _run_warmup(handle: ModelHandle, imgsz: int) -> None:
        """One blank inference so the first real frame is not slow, and a broken
        model fails at load time rather than on a live camera."""
        try:
            blank = np.zeros((imgsz, imgsz, 3), dtype=np.uint8)
            handle.predict(blank, conf=0.99, iou=0.5, imgsz=imgsz, classes=None)
        except Exception as exc:
            raise ModelConfigurationError(
                f"Model '{handle.model_name}' failed its warm-up inference: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        handle.inference_calls = 0  # the warm-up is not real work

    def loaded_handles(self) -> list[ModelHandle]:
        with self._lock:
            return list(self._handles.values())

    def clear(self) -> None:
        with self._lock:
            self._handles.clear()


default_registry = ModelRegistry()
