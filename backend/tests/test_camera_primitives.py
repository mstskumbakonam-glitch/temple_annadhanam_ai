"""Frame buffer, reconnect policy, rate meter and processing pacer."""

import threading
import time

import numpy as np
import pytest

from app.camera.frame_buffer import LatestFrameBuffer
from app.camera.reconnect import ReconnectPolicy
from app.camera.worker import ProcessingPacer
from app.utils.rate import RateMeter
from tests.helpers import frame


# ------------------------------------------------------------- frame buffer
def test_empty_buffer_returns_nothing():
    buf = LatestFrameBuffer("CAM-1")
    assert buf.get_latest() is None
    assert buf.get_newer_than(0) is None
    assert len(buf) == 0
    assert buf.sequence == 0


def test_write_then_read_returns_the_frame_with_metadata():
    buf = LatestFrameBuffer("CAM-1")
    img = frame(value=42)
    written = buf.put(img)
    got = buf.get_latest()
    assert got is written
    assert got.camera_id == "CAM-1"
    assert got.sequence == 1
    assert got.timestamp.tzinfo is not None
    assert got.monotonic > 0
    assert int(got.frame[0, 0, 0]) == 42


def test_overwrite_keeps_only_the_newest_frame():
    buf = LatestFrameBuffer("CAM-1")
    for value in (1, 2, 3):
        buf.put(frame(value=value))
    assert len(buf) == 1
    assert int(buf.get_latest().frame[0, 0, 0]) == 3


def test_sequence_increments_by_one_per_frame():
    buf = LatestFrameBuffer("CAM-1")
    assert [buf.put(frame()).sequence for _ in range(5)] == [1, 2, 3, 4, 5]
    assert buf.sequence == 5


def test_buffer_never_grows_beyond_capacity():
    """No unbounded queue: 10,000 writes still hold exactly one frame."""
    buf = LatestFrameBuffer("CAM-1")
    for i in range(10_000):
        buf.put(frame(8, 8, i % 250))
    assert len(buf) == 1
    assert buf.capacity == 1


def test_capacity_is_configurable_but_bounded():
    buf = LatestFrameBuffer("CAM-1", capacity=3)
    for _ in range(50):
        buf.put(frame(8, 8))
    assert len(buf) == 3
    assert buf.get_latest().sequence == 50
    with pytest.raises(ValueError):
        LatestFrameBuffer("CAM-1", capacity=0)


def test_get_newer_than_only_returns_unseen_frames():
    buf = LatestFrameBuffer("CAM-1")
    first = buf.put(frame())
    assert buf.get_newer_than(first.sequence) is None
    second = buf.put(frame())
    assert buf.get_newer_than(first.sequence) is second


def test_dropped_counts_frames_overwritten_before_being_read():
    buf = LatestFrameBuffer("CAM-1")
    buf.put(frame())            # never read
    buf.put(frame())            # overwrites it -> 1 dropped
    buf.get_latest()            # reads seq 2
    buf.put(frame())            # overwrites a frame that WAS read -> not a drop
    assert buf.dropped == 1


def test_frames_are_read_only_so_threads_cannot_corrupt_them():
    buf = LatestFrameBuffer("CAM-1")
    stored = buf.put(frame()).frame
    with pytest.raises(ValueError):
        stored[0, 0, 0] = 255


def test_clear_empties_buffer_but_keeps_sequence_monotonic():
    buf = LatestFrameBuffer("CAM-1")
    buf.put(frame())
    buf.put(frame())
    buf.clear()
    assert buf.get_latest() is None
    assert buf.put(frame()).sequence == 3


def test_wait_for_frame_times_out_and_wakes_on_write():
    buf = LatestFrameBuffer("CAM-1")
    assert buf.wait_for_frame(0, timeout=0.05) is None

    def writer():
        time.sleep(0.05)
        buf.put(frame())

    threading.Thread(target=writer).start()
    got = buf.wait_for_frame(0, timeout=2.0)
    assert got is not None and got.sequence == 1


def test_concurrent_writers_and_readers_are_consistent():
    buf = LatestFrameBuffer("CAM-1")
    errors: list[str] = []
    stop = threading.Event()
    per_writer = 500

    def writer():
        for _ in range(per_writer):
            buf.put(frame(8, 8))

    def reader():
        last = 0
        while not stop.is_set():
            packet = buf.get_latest()
            if packet is None:
                continue
            if packet.sequence < last:
                errors.append(f"sequence went backwards: {last} -> {packet.sequence}")
            last = packet.sequence
            if len(buf) > buf.capacity:
                errors.append("buffer exceeded capacity")

    readers = [threading.Thread(target=reader) for _ in range(3)]
    writers = [threading.Thread(target=writer) for _ in range(4)]
    for t in readers + writers:
        t.start()
    for t in writers:
        t.join()
    stop.set()
    for t in readers:
        t.join()

    assert errors == []
    assert buf.sequence == 4 * per_writer      # no increment lost under contention
    assert len(buf) == 1


def test_buffers_of_different_cameras_are_isolated():
    a, b = LatestFrameBuffer("CAM-A"), LatestFrameBuffer("CAM-B")
    a.put(frame(value=1))
    assert b.get_latest() is None
    b.put(frame(value=2))
    assert a.get_latest().camera_id == "CAM-A"
    assert int(a.get_latest().frame[0, 0, 0]) == 1


# ------------------------------------------------------- reconnect policy
def test_backoff_grows_then_caps():
    policy = ReconnectPolicy(1.0, 8.0, factor=2.0, jitter=0.0)
    assert [policy.next_delay() for _ in range(6)] == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0]
    assert policy.attempts == 6


def test_reset_restarts_backoff():
    policy = ReconnectPolicy(1.0, 8.0, jitter=0.0)
    for _ in range(4):
        policy.next_delay()
    policy.reset()
    assert policy.next_delay() == 1.0


def test_jitter_stays_within_bounds_and_never_exceeds_max():
    policy = ReconnectPolicy(2.0, 10.0, jitter=0.5, rng=lambda: 1.0)   # maximum jitter
    delays = [policy.next_delay() for _ in range(10)]
    assert all(0 <= d <= 10.0 for d in delays)


@pytest.mark.parametrize("args", [(0, 5), (5, 1)])
def test_invalid_policy_arguments_rejected(args):
    with pytest.raises(ValueError):
        ReconnectPolicy(*args)


# --------------------------------------------------------------- rate meter
def test_rate_meter_measures_events_per_second():
    now = [0.0]
    meter = RateMeter(window=10, clock=lambda: now[0])
    for _ in range(6):
        meter.tick()
        now[0] += 0.2                      # 5 events per second
    now[0] -= 0.2
    assert meter.rate() == pytest.approx(5.0)


def test_rate_meter_reports_zero_when_stale_or_too_few_events():
    now = [0.0]
    meter = RateMeter(window=10, stale_after=2.0, clock=lambda: now[0])
    assert meter.rate() == 0.0
    meter.tick()
    assert meter.rate() == 0.0             # one event is not a rate
    now[0] += 0.5
    meter.tick()
    assert meter.rate() > 0
    now[0] += 10.0
    assert meter.rate() == 0.0             # stale


# ------------------------------------------------------------ pacing (AI FPS)
def test_pacer_never_exceeds_target_rate_when_frames_are_plentiful():
    """Camera at 25 FPS, AI at 5 FPS: over 10 s inference runs ~50 times, not 250."""
    now = [0.0]
    pacer = ProcessingPacer(5.0, clock=lambda: now[0])
    processed = 0
    while now[0] < 10.0:
        wait = pacer.time_until_due()
        if wait > 0:
            now[0] += wait
            continue
        pacer.mark_started()
        processed += 1
        now[0] += 0.001                     # inference itself is instantaneous
    assert 49 <= processed <= 51


def test_pacer_does_not_burst_after_a_stall():
    now = [0.0]
    pacer = ProcessingPacer(5.0, clock=lambda: now[0])
    pacer.mark_started()
    now[0] += 5.0                           # e.g. a long GC pause or slow inference
    assert pacer.time_until_due() == 0.0
    pacer.mark_started()
    # after the stall it must wait a full interval, not fire back to back
    assert pacer.time_until_due() == pytest.approx(0.2, abs=1e-6)


def test_consecutive_inference_starts_are_never_closer_than_one_interval():
    """The hard guarantee: however irregularly frames and stalls arrive, two inferences
    never start less than 1/fps apart - so the rate can never exceed the configured FPS."""
    import random

    rng = random.Random(3)
    for fps in (2.0, 5.0, 12.0):
        now = [0.0]
        pacer = ProcessingPacer(fps, clock=lambda: now[0])
        starts = []
        for _ in range(400):
            wait = pacer.time_until_due()
            now[0] += wait + rng.choice([0.0, 0.0, 0.003, 0.05, 0.4, 2.0])   # random stalls
            pacer.mark_started()
            starts.append(now[0])
            now[0] += rng.choice([0.0, 0.01, 0.3])                           # inference time
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        assert min(gaps) >= pacer.interval - 1e-9, f"fps={fps}: gap {min(gaps)} < {pacer.interval}"


def test_pacer_rejects_non_positive_fps():
    with pytest.raises(ValueError):
        ProcessingPacer(0)
