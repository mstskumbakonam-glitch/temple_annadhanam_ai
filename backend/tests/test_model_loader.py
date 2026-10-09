"""YOLO model configuration and loading. No weights, GPU or network needed."""

import os
import threading
from pathlib import Path

import pytest

from app.ai.model_loader import (
    ModelConfigurationError,
    ModelRegistry,
    configure_ultralytics_offline,
    resolve_model_path,
    ultralytics_loader,
)
from app.config import Settings
from tests.helpers import FakeYoloModel

DB = "postgresql+psycopg://u:p@localhost:5432/d"


@pytest.fixture
def weights(tmp_path) -> Path:
    path = tmp_path / "model.pt"
    path.write_bytes(b"not-real-weights")
    return path


# ---------------------------------------------------------- configuration
def test_there_is_no_default_model_path(monkeypatch):
    """A default like 'yolov8n.pt' would make Ultralytics download weights.

    The environment variable is cleared so the test checks the code's default,
    not whatever the developer running it happens to have exported."""
    monkeypatch.delenv("AI_MODEL_PATH", raising=False)
    assert Settings(_env_file=None, database_url=DB).ai_model_path is None


def test_model_settings_come_from_environment(monkeypatch):
    monkeypatch.setenv("AI_MODEL_PATH", "models/custom.pt")
    monkeypatch.setenv("AI_CONFIDENCE", "0.55")
    monkeypatch.setenv("AI_IOU", "0.6")
    monkeypatch.setenv("AI_IMAGE_SIZE", "960")
    monkeypatch.setenv("AI_DEVICE", "CUDA:0")
    monkeypatch.setenv("AI_PROCESS_FPS", "8")
    s = Settings(_env_file=None, database_url=DB)
    assert s.ai_model_path == "models/custom.pt"
    assert s.ai_confidence == 0.55
    assert s.ai_iou == 0.6
    assert s.ai_image_size == 960
    assert s.ai_device == "cuda:0"          # normalised
    assert s.ai_process_fps == 8.0


@pytest.mark.parametrize(
    "env, value",
    [("AI_CONFIDENCE", "1.5"), ("AI_PROCESS_FPS", "0"), ("AI_IMAGE_SIZE", "8"),
     ("AI_DEVICE", "  "), ("CAMERA_RTSP_TRANSPORT", "carrier-pigeon")],
)
def test_invalid_ai_settings_are_rejected(monkeypatch, env, value):
    monkeypatch.setenv(env, value)
    with pytest.raises(ValueError):
        Settings(_env_file=None, database_url=DB)


def test_low_threshold_may_not_exceed_high_threshold(monkeypatch):
    monkeypatch.setenv("AI_CONFIDENCE", "0.3")
    monkeypatch.setenv("AI_TRACK_LOW_CONFIDENCE", "0.5")
    with pytest.raises(ValueError, match="AI_TRACK_LOW_CONFIDENCE"):
        Settings(_env_file=None, database_url=DB)


def test_reconnect_delay_ordering_is_validated(monkeypatch):
    monkeypatch.setenv("CAMERA_RECONNECT_DELAY", "30")
    monkeypatch.setenv("CAMERA_MAX_RECONNECT_DELAY", "5")
    with pytest.raises(ValueError, match="CAMERA_MAX_RECONNECT_DELAY"):
        Settings(_env_file=None, database_url=DB)


# ------------------------------------------------------------ missing model
def test_unset_model_path_gives_clear_error():
    with pytest.raises(ModelConfigurationError, match="AI_MODEL_PATH is not set"):
        resolve_model_path(None)
    with pytest.raises(ModelConfigurationError, match="AI_MODEL_PATH is not set"):
        resolve_model_path("   ")


def test_missing_model_file_gives_clear_error_and_never_downloads(tmp_path):
    def loader(path, device):                   # would be Ultralytics fetching weights
        raise AssertionError("loader must not be reached for a missing file")

    registry = ModelRegistry(loader, base_dir=tmp_path)
    with pytest.raises(ModelConfigurationError, match="not found.*never downloaded"):
        registry.get("models/does-not-exist.pt", "cpu")
    assert registry.load_count == 0


def test_directory_is_not_a_model(tmp_path):
    with pytest.raises(ModelConfigurationError, match="not a file"):
        resolve_model_path(str(tmp_path))


def test_relative_paths_resolve_against_base_dir_not_cwd(tmp_path, monkeypatch):
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "m.pt").write_bytes(b"x")
    monkeypatch.chdir("/")                       # cwd deliberately unrelated
    assert resolve_model_path("models/m.pt", tmp_path) == tmp_path / "models" / "m.pt"


# ------------------------------------------------------------ load once
def test_model_is_loaded_once_and_shared(weights):
    loads = []

    def loader(path, device):
        loads.append((path, device))
        return FakeYoloModel()

    registry = ModelRegistry(loader)
    handles = [registry.get(str(weights), "cpu") for _ in range(50)]
    assert len(loads) == 1
    assert registry.load_count == 1
    assert all(h is handles[0] for h in handles)


def test_concurrent_first_use_still_loads_only_once(weights):
    loads = []

    def slow_loader(path, device):
        loads.append(1)
        import time
        time.sleep(0.1)                          # widen the race window
        return FakeYoloModel()

    registry = ModelRegistry(slow_loader)
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(registry.get(str(weights), "cpu")))
        for _ in range(16)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(loads) == 1
    assert len({id(h) for h in results}) == 1


def test_different_device_gets_its_own_instance(weights):
    registry = ModelRegistry(lambda p, d: FakeYoloModel())
    assert registry.get(str(weights), "cpu") is not registry.get(str(weights), "cuda:0")
    assert registry.load_count == 2


def test_handle_exposes_file_name_only_not_the_directory(weights):
    handle = ModelRegistry(lambda p, d: FakeYoloModel()).get(str(weights), "cpu")
    assert handle.model_name == "model.pt"
    assert str(weights.parent) not in handle.model_name


def test_warmup_runs_once_and_is_not_counted_as_work(weights):
    model = FakeYoloModel()
    handle = ModelRegistry(lambda p, d: model, warmup=True).get(str(weights), "cpu", imgsz=320)
    assert len(model.calls) == 1                 # the warm-up inference
    assert model.calls[0]["imgsz"] == 320
    assert handle.inference_calls == 0


def test_broken_model_fails_at_load_time_not_on_a_live_camera(weights):
    bad = FakeYoloModel(error=RuntimeError("corrupt weights"))
    with pytest.raises(ModelConfigurationError, match="warm-up"):
        ModelRegistry(lambda p, d: bad).get(str(weights), "cpu")


def test_model_without_class_names_is_rejected(weights):
    class Nameless:
        names = {}

        def predict(self, *a, **k):
            return []

    with pytest.raises(ModelConfigurationError, match="class names"):
        ModelRegistry(lambda p, d: Nameless(), warmup=False).get(str(weights), "cpu")


def test_inference_calls_are_serialised_by_a_lock(weights):
    """One shared model must never be entered by two camera threads at once."""
    model = FakeYoloModel(delay=0.01)
    handle = ModelRegistry(lambda p, d: model, warmup=False).get(str(weights), "cpu")
    import numpy as np

    blank = np.zeros((64, 64, 3), np.uint8)
    threads = [
        threading.Thread(
            target=lambda: [handle.predict(blank, conf=.1, iou=.5, imgsz=64, classes=None)
                            for _ in range(5)]
        )
        for _ in range(6)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(model.calls) == 30
    assert model.max_concurrent == 1


def test_predict_is_always_called_quietly_on_the_configured_device(weights):
    model = FakeYoloModel()
    handle = ModelRegistry(lambda p, d: model, warmup=False).get(str(weights), "cpu")
    import numpy as np

    handle.predict(np.zeros((64, 64, 3), np.uint8), conf=.3, iou=.5, imgsz=64, classes=[0])
    kwargs = model.calls[0]
    assert kwargs["verbose"] is False
    assert kwargs["device"] == "cpu"
    assert kwargs["classes"] == [0]


# --------------------------------------- the real Ultralytics loader (no weights)
def test_ultralytics_is_forced_offline_before_import(monkeypatch):
    monkeypatch.delenv("YOLO_AUTOINSTALL", raising=False)
    monkeypatch.delenv("YOLO_VERBOSE", raising=False)
    configure_ultralytics_offline()
    assert os.environ["YOLO_AUTOINSTALL"] == "False"   # no pip install at runtime
    assert os.environ["YOLO_VERBOSE"] == "False"


def test_cuda_requested_but_unavailable_is_a_clear_config_error(weights):
    torch = pytest.importorskip("torch")
    if torch.cuda.is_available():
        pytest.skip("CUDA is available here")
    with pytest.raises(ModelConfigurationError, match="CUDA is not available"):
        ultralytics_loader(weights, "cuda")


def test_corrupt_weights_file_is_reported_not_downloaded(weights):
    """The file exists but is garbage: Ultralytics must fail, not fetch a replacement."""
    pytest.importorskip("ultralytics")
    with pytest.raises(ModelConfigurationError, match="Could not load YOLO weights 'model.pt'"):
        ultralytics_loader(weights, "cpu")
