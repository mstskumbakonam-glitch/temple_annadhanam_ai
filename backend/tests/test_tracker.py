"""ByteTrack tracking: real Ultralytics ByteTrack driven by scripted detections."""

import random
import threading

import pytest

pytest.importorskip("ultralytics")

from app.ai.tracker import ByteTrackTracker, TrackerSettings
from app.ai.types import Detection, TrackEventType, TrackState
from tests.helpers import person_box, ts

NAMES = {0: "person"}
SETTINGS = TrackerSettings(high_confidence=0.4, low_confidence=0.1, max_lost_frames=4)


def det(cx, conf=0.9, cy=200.0, when=0.0):
    x1, y1, x2, y2 = person_box(cx, cy)
    return Detection(0, "person", conf, x1, y1, x2, y2, ts(when))


def feed(tracker, centres_per_frame, conf=0.9, start=0):
    """Feed successive frames; returns the list of TrackingResults."""
    results = []
    for i, centres in enumerate(centres_per_frame, start=start):
        when = i * 0.2
        results.append(tracker.update([det(c, conf, when=when) for c in centres], ts(when)))
    return results


@pytest.fixture
def tracker():
    return ByteTrackTracker("CAM-A", SETTINGS, NAMES)


# ------------------------------------------------------------- id assignment
def test_each_person_gets_a_track_id_and_a_new_event(tracker):
    first = feed(tracker, [[100, 400]])[0]
    assert len(first.tracks) == 2
    assert sorted(t.track_id for t in first.tracks) == [1, 2]
    assert [e.type for e in first.events] == [TrackEventType.NEW, TrackEventType.NEW]
    assert all(t.state is TrackState.NEW for t in first.tracks)


def test_ids_are_stable_while_people_move(tracker):
    results = feed(tracker, [[100 + 4 * i, 400 + 4 * i] for i in range(10)])
    id_sets = [frozenset(t.track_id for t in r.tracks) for r in results]
    assert len(set(id_sets)) == 1                       # same two ids in every frame
    assert all(t.state is TrackState.TRACKED for t in results[-1].tracks)
    assert not any(e.type is TrackEventType.NEW for r in results[1:] for e in r.events)


def test_multiple_people_get_distinct_ids(tracker):
    result = feed(tracker, [[100, 250, 400, 550, 700]] * 3)[-1]
    ids = [t.track_id for t in result.tracks]
    assert len(ids) == 5 and len(set(ids)) == 5


def test_track_output_carries_camera_class_bbox_confidence_and_timestamp(tracker):
    result = feed(tracker, [[300]])[0]
    track = result.tracks[0]
    assert track.camera_id == "CAM-A"
    assert track.class_name == "person" and track.class_id == 0
    assert track.confidence == pytest.approx(0.9, abs=1e-3)
    assert track.bbox == pytest.approx(person_box(300), abs=1.0)
    assert track.bottom_center == pytest.approx((300, 260), abs=1.0)
    assert track.timestamp == ts(0)
    assert track.hits == 1 and track.first_seen == track.last_seen == ts(0)


def test_track_key_is_scoped_to_camera_and_session(tracker):
    track = feed(tracker, [[100]])[0].tracks[0]
    assert track.track_key == f"CAM-A:{tracker.session_id}:{track.track_id}"


# ----------------------------------------------------------------- lifecycle
def test_lost_then_expired_lifecycle(tracker):
    feed(tracker, [[100, 400]] * 3)
    results = feed(tracker, [[100]] * 8, start=3)       # second person leaves

    lost_frames = [r for r in results if any(e.type is TrackEventType.LOST for e in r.events)]
    expired_frames = [r for r in results if any(e.type is TrackEventType.EXPIRED for e in r.events)]
    assert len(lost_frames) == 1, "LOST must fire once, on the transition"
    assert len(expired_frames) == 1, "EXPIRED must fire once"
    assert results.index(lost_frames[0]) < results.index(expired_frames[0])

    lost_frame = lost_frames[0]
    assert len(lost_frame.lost) == 1 and lost_frame.lost[0].state is TrackState.LOST
    assert len(lost_frame.tracks) == 1                  # the other person is still visible
    assert len(expired_frames[0].expired) == 1
    assert expired_frames[0].expired[0].state is TrackState.EXPIRED


def test_expired_tracks_are_forgotten(tracker):
    feed(tracker, [[100, 400]] * 3)
    assert tracker.live_track_count == 2
    feed(tracker, [[100]] * 8, start=3)
    assert tracker.live_track_count == 1


def test_a_track_that_returns_within_the_buffer_keeps_its_id(tracker):
    first = feed(tracker, [[100, 400]] * 3)[-1]
    person_b = next(t.track_id for t in first.tracks if t.center[0] > 300)
    feed(tracker, [[100]] * 2, start=3)                 # B disappears briefly (within buffer)
    back = feed(tracker, [[100, 400]] * 2, start=5)[-1]
    assert person_b in {t.track_id for t in back.tracks}
    assert all(e.type is not TrackEventType.NEW for r in [back] for e in r.events)


def test_empty_frames_still_age_tracks_toward_expiry(tracker):
    feed(tracker, [[100]] * 3)
    results = feed(tracker, [[]] * 8, start=3)          # nobody in view at all
    assert any(e.type is TrackEventType.LOST for r in results for e in r.events)
    assert any(e.type is TrackEventType.EXPIRED for r in results for e in r.events)
    assert tracker.live_track_count == 0
    assert results[-1].tracks == () and results[-1].active_count == 0


def test_visible_and_active_counts_differ_while_a_track_is_lost(tracker):
    feed(tracker, [[100, 400]] * 3)
    result = feed(tracker, [[100]], start=3)[0]
    assert result.visible_count == 1
    assert result.active_count == 2                     # the missing person is still alive


def test_low_confidence_detections_sustain_a_track_but_cannot_start_one(tracker):
    """ByteTrack's second association: 0.1 < conf < 0.4 keeps a track, never starts one."""
    solo = ByteTrackTracker("CAM-Z", SETTINGS, NAMES)
    assert feed(solo, [[300]] * 5, conf=0.25)[-1].tracks == ()       # weak alone: no track
    assert solo.live_track_count == 0

    feed(tracker, [[300]] * 4, conf=0.9)                              # confident: track starts
    tid = feed(tracker, [[300]], conf=0.9, start=4)[0].tracks[0].track_id
    weak = feed(tracker, [[300]] * 3, conf=0.25, start=5)             # then only weak scores
    assert [t.track_id for t in weak[-1].tracks] == [tid]


def test_detections_below_the_low_threshold_are_ignored(tracker):
    assert feed(tracker, [[300]] * 5, conf=0.05)[-1].tracks == ()


# ----------------------------------------------------------- session / reset
def test_reset_expires_every_live_track_and_starts_a_new_session(tracker):
    before = feed(tracker, [[100, 400]] * 3)[-1]
    old_session = tracker.session_id

    result = tracker.reset(ts(9))

    assert tracker.session_id != old_session
    assert result.tracker_session == old_session
    assert len(result.expired) == 2
    assert {e.type for e in result.events} == {TrackEventType.EXPIRED}
    assert all(t.tracker_session == old_session for t in result.expired)
    assert {t.track_id for t in result.expired} == {t.track_id for t in before.tracks}
    assert tracker.live_track_count == 0


def test_ids_restart_in_a_new_session_and_are_scoped_by_session(tracker):
    first_ids = {t.track_id for t in feed(tracker, [[100]] * 3)[-1].tracks}
    old_session = tracker.session_id
    tracker.reset(ts(9))
    second = feed(tracker, [[100]] * 3, start=50)[-1].tracks[0]
    assert second.track_id in first_ids                 # same number reused ...
    assert second.tracker_session != old_session        # ... but a different session
    assert second.track_key != f"CAM-A:{old_session}:{second.track_id}"


def test_every_tracker_has_its_own_session_id():
    a, b = ByteTrackTracker("CAM-A", SETTINGS, NAMES), ByteTrackTracker("CAM-B", SETTINGS, NAMES)
    assert a.session_id != b.session_id


# ------------------------------------------------------------ camera isolation
def test_trackers_for_different_cameras_do_not_interfere():
    """Camera A's output is identical whether or not camera B is running beside it."""
    def run_a(with_b):
        a = ByteTrackTracker("CAM-A", SETTINGS, NAMES)
        b = ByteTrackTracker("CAM-B", SETTINGS, NAMES) if with_b else None
        out = []
        for i in range(25):
            a_centres = [100 + 3 * i, 500 - 2 * i] + ([700] if i > 10 else [])
            when = i * 0.2
            if b:
                b.update([det(900 - 5 * i, when=when), det(50 + 7 * i, when=when)], ts(when))
                if i == 12:
                    ByteTrackTracker("CAM-C", SETTINGS, NAMES)   # a third tracker appears mid-run
            r = a.update([det(c, when=when) for c in a_centres], ts(when))
            out.append(sorted(t.track_id for t in r.tracks))
        return out

    assert run_a(with_b=True) == run_a(with_b=False)


def test_track_ids_are_unique_for_the_life_of_a_camera_session():
    """Once an id has been used in a session it is never handed to a different person,
    even with other trackers being created and people constantly arriving and leaving."""
    rng = random.Random(7)
    for seed in range(20):
        rng.seed(seed)
        a = ByteTrackTracker("CAM-A", SETTINGS, NAMES)
        people = {}                       # slot -> x position
        seen_new = []
        for step in range(60):
            if rng.random() < 0.2:
                ByteTrackTracker(f"OTHER-{step}", SETTINGS, NAMES)   # others being built
            if rng.random() < 0.25 and len(people) < 6:
                people[step] = rng.randint(50, 1200)
            if rng.random() < 0.15 and people:
                people.pop(rng.choice(list(people)))
            for k in people:
                people[k] += rng.choice([-2, 0, 2])
            when = step * 0.2
            r = a.update([det(x, when=when) for x in people.values()], ts(when))
            seen_new += [e.track.track_id for e in r.events if e.type is TrackEventType.NEW]
        assert len(seen_new) == len(set(seen_new)), f"seed {seed}: an id was reused {seen_new}"


def test_two_threads_can_drive_two_trackers_concurrently():
    errors = []
    outcomes = {}

    def drive(name, base):
        try:
            t = ByteTrackTracker(name, SETTINGS, NAMES)
            last = None
            for i in range(150):
                when = i * 0.2
                last = t.update([det(base + 2 * i % 300, when=when), det(base + 500, when=when)], ts(when))
            outcomes[name] = sorted(x.track_id for x in last.tracks)
        except Exception as exc:                                  # pragma: no cover
            errors.append(repr(exc))

    threads = [threading.Thread(target=drive, args=(f"CAM-{i}", 100 + i * 10)) for i in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    assert all(len(ids) == 2 for ids in outcomes.values())
