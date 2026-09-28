from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from phonetrace.models import Sample, SessionConfig
from phonetrace.recorder import SessionRecorder, build_summary, list_sessions, load_session


def sample(elapsed: float, **kwargs) -> Sample:
    return Sample(timestamp=f"2026-09-28T12:00:{int(elapsed):02d}+08:00", elapsed_s=elapsed, **kwargs)


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_streamed_files_are_readable_before_finish_and_missing_is_blank(self):
        recorder = SessionRecorder(self.root, SessionConfig("phone", "com.game", title="游戏中文"), {"model": "Test"})
        try:
            recorder.append(sample(0, notes=["电流不可读取"], layer='=HYPERLINK("https://invalid.local")'))
            recorder.append(sample(1, fps=60, frame_intervals_ms=[16.5, 16.7], plugged=False,
                                   power_w=4.2, current_ma=-1050, voltage_v=4))
            raw = (recorder.path / "samples.csv").read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
            rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
            self.assertEqual(rows[0]["power_w"], "")
            self.assertEqual(rows[0]["fps"], "")
            self.assertEqual(rows[1]["current_ma"], "-1050")
            self.assertIn("电流不可读取", rows[0]["notes"])
            self.assertTrue(rows[0]["layer"].startswith("'="))
            with (recorder.path / "frame_intervals.csv").open(encoding="utf-8-sig", newline="") as handle:
                frames = list(csv.DictReader(handle))
            self.assertEqual([float(frame["frame_interval_ms"]) for frame in frames], [16.5, 16.7])
            summary, loaded = load_session(recorder.path)
            self.assertEqual(summary["status"], "interrupted")
            self.assertEqual(summary["sample_count"], 2)
            self.assertIsNone(loaded[0].fps)
            self.assertIsNone(loaded[0].power_w)
            # A single valid power point cannot establish an energy or average.
            self.assertIsNone(summary["avg_power_w"])
            self.assertIsNone(summary["energy_wh"])
        finally:
            recorder.finish()
        final, loaded = load_session(recorder.path)
        self.assertEqual(final["status"], "completed")
        self.assertEqual(len(loaded), 2)
        history = list_sessions(self.root)
        self.assertEqual(history[0]["id"], recorder.id)
        self.assertEqual(history[0]["title"], "游戏中文")

    def test_power_integrates_valid_duration_and_does_not_fill_gaps(self):
        samples = [
            sample(0, power_w=2, plugged=False, fps=999, frame_intervals_ms=[10, 10]),
            sample(1, power_w=4, plugged=False, frame_intervals_ms=[20]),
            sample(3, power_w=6, plugged=False, frame_intervals_ms=[100]),
            sample(4),
            sample(5, power_w=8, plugged=False),
            sample(10, power_w=8, plugged=False),
            sample(11, power_w=10, plugged=True),
            sample(12, power_w=6, plugged=False),
            sample(13, power_w=10, plugged=False),
        ]
        summary = build_summary(samples, {"config": {"interval_s": 1.0}})
        self.assertEqual(summary["duration_s"], 13)
        self.assertEqual(summary["sample_count"], 9)
        self.assertEqual(summary["power_valid_duration_s"], 4)
        self.assertAlmostEqual(summary["avg_power_w"], 21 / 4)
        self.assertAlmostEqual(summary["energy_wh"], 21 / 3600)
        self.assertAlmostEqual(summary["power_coverage_pct"], 400 / 13)
        self.assertAlmostEqual(summary["avg_fps"], 4 / .14)
        self.assertEqual(summary["p95_frame_time_ms"], 100)
        self.assertEqual(summary["slow_frames"], 1)
        self.assertAlmostEqual(summary["frame_observed_duration_s"], .14)

    def test_reopen_disconnected_session_and_recover_incomplete_tail(self):
        recorder = SessionRecorder(self.root, SessionConfig("phone", "com.game"), {})
        recorder.append(sample(0, plugged=False, power_w=4))
        recorder.append(sample(1, plugged=False, power_w=6, frame_intervals_ms=[16, 17]))
        recorder.append(sample(2, notes=["设备已断开"]))
        result = recorder.finish("error", "设备已断开")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["avg_power_w"], 5)
        self.assertEqual(result["power_coverage_pct"], 50)
        with (recorder.path / "samples.jsonl").open("a", encoding="utf-8") as handle:
            handle.write('{"timestamp":"half-written", "elapsed_s":')
        reopened, values = load_session(recorder.path)
        self.assertEqual(len(values), 3)
        self.assertEqual(reopened["recovery_skipped_rows"], 1)
        self.assertEqual(reopened["status"], "error")
        self.assertEqual(reopened["avg_power_w"], 5)
        self.assertIsNone(values[2].power_w)
        self.assertTrue((recorder.path / "report.html").exists())
        with self.assertRaises(RuntimeError):
            recorder.append(sample(4))

    def test_history_recovers_recording_and_malformed_middle_without_erasing_data(self):
        folder = self.root / "interrupted"
        folder.mkdir()
        (folder / "metadata.json").write_text(json.dumps({"status": "recording", "title": "Interrupted"}), encoding="utf-8")
        (folder / "samples.jsonl").write_text(
            json.dumps(sample(0).to_dict()) + "\nnot JSON\n" +
            json.dumps(sample(1, battery_temp_c=40).to_dict()) +
            '\n{"timestamp":"bad","elapsed_s":null}\n', encoding="utf-8")
        original = (folder / "samples.jsonl").read_bytes()
        history = list_sessions(self.root)
        self.assertEqual(history[0]["status"], "interrupted")
        self.assertEqual(history[0]["sample_count"], 2)
        self.assertEqual(history[0]["recovery_skipped_rows"], 2)
        self.assertIsNone(history[0]["avg_fps"])
        self.assertIsNone(history[0]["avg_power_w"])
        self.assertEqual((folder / "samples.jsonl").read_bytes(), original)

    def test_collision_does_not_overwrite_and_report_escapes_text_and_marks_demo(self):
        config = SessionConfig("phone", '<img src=x onerror="alert(1)">', title="<script>alert(1)</script>", demo=True)
        fixed = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
        # UUIDs consumed by atomic writes are patched separately from construction.
        with patch("phonetrace.recorder.datetime") as fake_datetime:
            fake_datetime.now.return_value = fixed
            with patch("phonetrace.recorder.uuid.uuid4", return_value=SimpleNamespace(hex="a" * 32)):
                first = SessionRecorder(self.root, config, {})
            with patch("phonetrace.recorder.uuid.uuid4", side_effect=[
                SimpleNamespace(hex="a" * 32), SimpleNamespace(hex="b" * 32), SimpleNamespace(hex="c" * 32)
            ]):
                second = SessionRecorder(self.root, config, {})
        try:
            self.assertNotEqual(first.path, second.path)
            self.assertTrue(first.path.exists())
            self.assertTrue(second.path.exists())
            first.append(sample(0, fps=60))
            first.append(sample(1))
            first.append(sample(2, fps=61))
        finally:
            one = first.finish()
            second.finish()
        report = (first.path / "report.html").read_text(encoding="utf-8")
        self.assertIn("演示数据", report)
        self.assertIn("不代表真实手机性能", report)
        self.assertIn("&lt;script&gt;", report)
        self.assertNotIn("<script>", report)
        self.assertNotIn("<img src=x", report)
        self.assertNotIn("<script src=", report)
        # Missing FPS creates isolated points, not a line bridging the missing row.
        self.assertEqual(report.count("<circle "), 2)
        self.assertIsNone(one["avg_fps"])
        self.assertEqual(first.finish(), one)

    def test_failed_secondary_write_preserves_durable_jsonl(self):
        recorder = SessionRecorder(self.root, SessionConfig("phone", "com.game"), {})
        try:
            with patch.object(recorder._writer, "writerow", side_effect=OSError("Disk full")):
                with self.assertRaises(OSError):
                    recorder.append(sample(1, battery_temp_c=40))
            summary, recovered = load_session(recorder.path)
            self.assertEqual(summary["sample_count"], 1)
            self.assertEqual(recovered[0].battery_temp_c, 40)
        finally:
            recorder.finish("error", "Disk full")


if __name__ == "__main__":
    unittest.main()
