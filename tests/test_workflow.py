"""Worker-to-disk integration checks using controlled device responses, no phone."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QSignalSpy

from phonetrace import controller
from phonetrace.controller import Controller, RecordingWorker
from phonetrace.models import Sample, SessionConfig
from phonetrace.recorder import list_sessions, load_session


@pytest.fixture(scope="module")
def qt_app():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


class Clock:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now


class FastStopEvent:
    """Advance a virtual sampling clock in place of waiting in worker tests."""
    def __init__(self, clock):
        self.clock = clock
        self.stopped = False

    def set(self):
        self.stopped = True

    def is_set(self):
        return self.stopped

    def wait(self, duration):
        if not self.stopped:
            self.clock.now += duration
        return self.stopped


class ClientStub:
    def __init__(self, clock, setup_delay=0, error=None):
        self.clock = clock
        self.setup_delay = setup_delay
        self.error = error

    def device_info(self, serial):
        assert serial == "stub-phone"
        self.clock.now += self.setup_delay
        if self.error:
            raise RuntimeError(self.error)
        return {"model": "Fixture Android", "transport": "Wi-Fi"}


def make_worker(monkeypatch, root, *, duration=3, setup_delay=0, failures=(), stop_after=None, startup_error=None):
    clock = Clock()
    monkeypatch.setattr(controller.time, "monotonic", clock.monotonic)
    collectors = []
    worker_ref = []

    class CollectorStub:
        def __init__(self, client, config):
            self.started = clock.monotonic()
            self.calls = 0
            self.closed = False
            collectors.append(self)

        def sample(self):
            self.calls += 1
            if stop_after == self.calls:
                worker_ref[0].stop()
            if self.calls in failures:
                raise RuntimeError("ADB device disconnected")
            return Sample(
                timestamp="2026-09-28T12:00:00+08:00",
                elapsed_s=clock.monotonic() - self.started,
                fps=60 if self.calls > 1 else None,
                frame_intervals_ms=[1000 / 60] * 60 if self.calls > 1 else [],
                power_w=4.0 + self.calls,
                plugged=False,
                fps_source="fixture.SurfaceFlinger",
                power_source="fixture.discharge",
            )

        def close(self):
            self.closed = True

    monkeypatch.setattr(controller, "Collector", CollectorStub)
    config = SessionConfig("stub-phone", "com.fixture.game", interval_s=1,
                           duration_s=duration, title="工作流验证")
    worker = RecordingWorker(ClientStub(clock, setup_delay, startup_error), config, root)
    worker.stop_event = FastStopEvent(clock)
    worker_ref.append(worker)
    return worker, collectors


def run_worker(worker, qt_app):
    report = QSignalSpy(worker.report_ready)
    problem = QSignalSpy(worker.problem)
    samples = QSignalSpy(worker.sample_ready)
    finished = QSignalSpy(worker.finished)
    worker.start()
    if not worker.wait(5000):
        worker.stop()
        worker.wait(2000)
        pytest.fail("Worker failed to stop within its bounded integration test")
    qt_app.processEvents()
    assert finished.count() == 1
    return report, problem, samples


def test_timed_session_finishes_closes_collector_and_persists(monkeypatch, tmp_path, qt_app):
    worker, collectors = make_worker(monkeypatch, tmp_path)
    reports, problems, emitted = run_worker(worker, qt_app)
    assert reports.count() == 1
    assert problems.count() == 0
    assert emitted.count() == 3
    assert collectors[0].closed
    report = reports.at(0)[0]
    assert report["status"] == "completed"
    assert report["demo"] is False
    assert report["sample_count"] == 3
    saved, samples = load_session(Path(report["path"]))
    assert [sample.elapsed_s for sample in samples] == [0, 1, 2]
    assert saved["avg_fps"] == pytest.approx(60)
    assert saved["avg_power_w"] == pytest.approx(6)
    assert saved["power_valid_duration_s"] == 2
    assert (Path(report["path"]) / "report.html").is_file()
    assert list_sessions(tmp_path)[0]["status"] == "completed"


def test_requested_stop_saves_last_observation(monkeypatch, tmp_path, qt_app):
    worker, collectors = make_worker(monkeypatch, tmp_path, duration=0, stop_after=2)
    reports, problems, emitted = run_worker(worker, qt_app)
    assert reports.count() == 1
    assert problems.count() == 0
    assert emitted.count() == 2
    assert collectors[0].closed
    report = reports.at(0)[0]
    assert report["status"] in {"completed", "stopped"}
    assert report["sample_count"] == 2
    assert report["error"] == ""
    assert len(load_session(Path(report["path"]))[1]) == 2


def test_three_disconnects_preserve_null_rows_and_interrupted_summary(monkeypatch, tmp_path, qt_app):
    worker, collectors = make_worker(monkeypatch, tmp_path, duration=0, failures=(3, 4, 5))
    reports, problems, emitted = run_worker(worker, qt_app)
    assert reports.count() == 1
    assert problems.count() == 1
    assert "连续三次" in problems.at(0)[0]
    assert emitted.count() == 5
    assert collectors[0].closed
    report = reports.at(0)[0]
    assert report["status"] == "interrupted"
    saved, samples = load_session(Path(report["path"]))
    assert saved["status"] == "interrupted"
    assert saved["sample_count"] == 5
    assert saved["avg_power_w"] == pytest.approx(5.5)
    assert saved["power_valid_duration_s"] == 1
    assert saved["power_coverage_pct"] == 25
    assert all(sample.fps is None and sample.power_w is None for sample in samples[-3:])
    assert all("ADB device disconnected" in sample.notes[0] for sample in samples[-3:])
    records = [json.loads(line) for line in (Path(report["path"]) / "samples.jsonl").read_text(encoding="utf-8").splitlines()]
    assert records[-1]["power_w"] is None
    assert list_sessions(tmp_path)[0]["status"] == "interrupted"


def test_startup_exception_emits_problem_and_finished_without_fake_report(monkeypatch, tmp_path, qt_app):
    worker, collectors = make_worker(monkeypatch, tmp_path, startup_error="Phone authorization rejected")
    reports, problems, emitted = run_worker(worker, qt_app)
    assert problems.count() == 1
    assert "Phone authorization rejected" in problems.at(0)[0]
    assert reports.count() == 0
    assert emitted.count() == 0
    assert collectors == []
    assert list_sessions(tmp_path) == []


def test_setup_latency_does_not_consume_requested_recording_duration(monkeypatch, tmp_path, qt_app):
    worker, _ = make_worker(monkeypatch, tmp_path, duration=3, setup_delay=10)
    reports, problems, emitted = run_worker(worker, qt_app)
    assert reports.count() == 1
    assert problems.count() == 0
    assert emitted.count() == 3, "Device setup must occur before the timed recording starts"


def test_transient_disconnect_uses_same_elapsed_clock_after_recovery(monkeypatch, tmp_path, qt_app):
    worker, _ = make_worker(monkeypatch, tmp_path, duration=0, setup_delay=5,
                            failures=(2,), stop_after=4)
    reports, problems, emitted = run_worker(worker, qt_app)
    assert problems.count() == 0
    assert reports.count() == 1
    assert emitted.count() == 4
    report = reports.at(0)[0]
    saved, samples = load_session(Path(report["path"]))
    assert [sample.elapsed_s for sample in samples] == [0, 1, 2, 3]
    assert saved["duration_s"] == 3
    assert saved["error"] == ""
    assert saved["power_valid_duration_s"] == 1


def test_history_does_not_treat_live_recording_as_interrupted(tmp_path):
    scheduled = []
    fake_controller = SimpleNamespace(
        worker=object(), data_dir=tmp_path,
        job=lambda *args: scheduled.append(args),
    )
    Controller.history(fake_controller)
    assert scheduled == [], "Active session metadata says recording and recovery loading would label it interrupted"


def test_late_history_result_is_ignored_if_a_recording_has_started():
    received = []
    fake_controller = SimpleNamespace(
        worker=object(), window=SimpleNamespace(set_history=received.append),
    )
    Controller.job_result(fake_controller, ("history", "", [{"status": "interrupted"}]))
    assert received == []
