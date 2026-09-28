from __future__ import annotations

from datetime import datetime
import shlex
import unittest
from unittest.mock import patch

from phonetrace.adb import AdbError
from phonetrace.collector import Collector, parse_frame_timestamps
from phonetrace.models import Sample, SessionConfig


PACKAGE = "com.example.game"
LAYER = "SurfaceView[com.example.game/com.example.Main](BLAST)#17"
BATTERY = """Current Battery Service state:
  AC powered: false
  USB powered: false
  Wireless powered: false
  status: 3
  level: 80
  scale: 100
  voltage: 4000
  temperature: 375
"""


def frames(*ms):
    return "16666666\n" + "\n".join(f"{int(t * 1_000_000)}\t{int(t * 1_000_000)}\t{int(t * 1_000_000)}" for t in ms)


def batch(frame_text="", battery=BATTERY, current="-1500000", voltage="4000000", cpu="cpu 100 0 40 800 60 0 0 0 20 0", memory="TOTAL PSS: 262144 TOTAL RSS: 700000", status="Discharging", service_current=""):
    sections = {"FRAMES": frame_text, "BATTERY": battery, "CURRENT": current,
                "VOLTAGE": voltage, "SYS_STATUS": status, "CPU": cpu, "MEMORY": memory,
                "SERVICE_CURRENT": service_current}
    return "\n".join("__PHONETRACE_" + key + "__\n" + value for key, value in sections.items())


class FakeClient:
    def __init__(self, outputs, layers=None, layer_frames=None):
        self.outputs = iter(outputs)
        self.layer_list = layers or []
        self.layer_frames = layer_frames or {}
        self.commands = []

    def layers(self, serial, package):
        return list(self.layer_list)

    def shell(self, serial, command, timeout=8):
        self.commands.append(command)
        if command.startswith("dumpsys SurfaceFlinger --latency "):
            return self.layer_frames.get(shlex.split(command)[3], "")
        result = next(self.outputs)
        if isinstance(result, Exception):
            raise result
        return result


def collector(outputs, **kwargs):
    return Collector(FakeClient(outputs), SessionConfig(serial="fixture", package=PACKAGE, layer=LAYER, **kwargs))


class CollectorTests(unittest.TestCase):
    def test_end_to_end_baseline_dedup_new_intervals_and_units(self):
        monitor = collector([
            batch(frames(1000, 1020, 1040)),
            batch(frames(1000, 1020, 1040, 1060, 1080), cpu="cpu 140 0 60 920 80 0 0 0 40 0"),
            batch(frames(1000, 1020, 1040, 1060, 1080)),
            batch(frames(1060, 1080, 1100, 1160)),
        ])
        baseline, sample, stale, resumed = [monitor.sample() for _ in range(4)]
        self.assertIsNone(baseline.fps)
        self.assertEqual(baseline.frame_intervals_ms, [])
        self.assertEqual(sample.frame_intervals_ms, [20, 20])
        self.assertEqual(sample.fps, 50)
        self.assertEqual(sample.power_w, 6)
        self.assertEqual(sample.current_ma, -1500)
        self.assertEqual(sample.voltage_v, 4)
        self.assertEqual(sample.battery_temp_c, 37.5)
        self.assertEqual(sample.battery_pct, 80)
        self.assertEqual(baseline.app_mem_mb, 256)
        self.assertIsNone(sample.app_mem_mb)
        self.assertAlmostEqual(sample.cpu_pct, 30)
        self.assertFalse(sample.plugged)
        self.assertIsNotNone(datetime.fromisoformat(sample.timestamp).tzinfo)
        self.assertIsNone(stale.fps)
        self.assertEqual(stale.frame_intervals_ms, [])
        self.assertEqual(resumed.frame_intervals_ms, [20, 60])
        self.assertEqual(resumed.slow_frames, 1)
        self.assertEqual(resumed.frame_p95_ms, 60)

    def test_charging_never_reports_whole_device_power_even_negative_current(self):
        for key in ("USB", "AC", "Wireless"):
            with self.subTest(key=key):
                charging = BATTERY.replace(f"{key} powered: false", f"{key} powered: true")
                sample = collector([batch(battery=charging)]).sample()
                self.assertTrue(sample.plugged)
                self.assertIsNone(sample.power_w)
                self.assertEqual(sample.current_ma, -1500)

    def test_unverified_battery_status_and_simulated_readings_are_not_power(self):
        variations = ["", BATTERY.replace("status: 3", "status: 1"),
                      BATTERY.replace("Wireless powered: false", "Wireless powered: unknown"),
                      BATTERY + "  Dock powered: true\n", BATTERY + "  Dock powered: unknown\n",
                      BATTERY + "(UPDATES STOPPED -- use reset to restart)\n", BATTERY + "present: false\n"]
        for battery in variations:
            with self.subTest(battery=battery):
                self.assertIsNone(collector([batch(battery=battery)]).sample().power_w)
        self.assertIsNone(collector([batch(status="Charging")]).sample().power_w)

    def test_permission_denied_zero_and_invalid_current_do_not_become_power_zero(self):
        for value in ("Permission denied", "", "0", "NaN", "-100000001"):
            with self.subTest(value=value):
                self.assertIsNone(collector([batch(current=value)]).sample().power_w)
        denied = collector([batch(cpu="", memory="Permission denied", current="", voltage="", battery="")]).sample()
        self.assertIsNone(denied.cpu_pct)
        self.assertIsNone(denied.app_mem_mb)
        self.assertIsNone(denied.voltage_v)
        self.assertIsNone(denied.battery_temp_c)
        self.assertIsNone(denied.battery_pct)

    def test_known_millivolt_fallback_and_positive_current_sign(self):
        sample = collector([batch(current="1500000", voltage="Permission denied")]).sample()
        self.assertEqual(sample.current_ma, 1500)
        self.assertEqual(sample.power_w, 6)

    def test_battery_service_fallback_has_explicit_units_sources_and_discharge_power(self):
        sample = collector([batch(current="", voltage="", status="", service_current="-1500000\n")]).sample()
        self.assertEqual(sample.current_ma, -1500)
        self.assertEqual(sample.voltage_v, 4)
        self.assertEqual(sample.power_w, 6)
        self.assertEqual(sample.current_source, "BatteryService.batteryCurrentMicroamps")
        self.assertEqual(sample.voltage_source, "BatteryService.voltage_mV")
        self.assertIn(sample.current_source, sample.power_source)
        self.assertIn(sample.voltage_source, sample.power_source)

    def test_sysfs_current_has_priority_over_service_and_preserves_driver_sign(self):
        sample = collector([batch(current="1500000", service_current="-5000000")]).sample()
        self.assertEqual(sample.current_ma, 1500)
        self.assertEqual(sample.power_w, 6)
        self.assertEqual(sample.current_source, "sysfs.battery.current_now_uA")
        self.assertEqual(sample.voltage_source, "sysfs.battery.voltage_now_uV")

    def test_service_error_text_decimal_sentinels_and_outliers_are_missing(self):
        invalid = ("", "Unknown get option: current_now", "Permission denied", "NaN", "1.2",
                   "-1500000\nwarning", "-2147483648", "-9223372036854775808", "100000001")
        for value in invalid:
            with self.subTest(value=value):
                sample = collector([batch(current="", service_current=value)]).sample()
                self.assertIsNone(sample.current_ma)
                self.assertIsNone(sample.power_w)
                self.assertEqual(sample.current_source, "unavailable")

    def test_service_zero_is_recorded_but_does_not_validate_zero_load_power(self):
        sample = collector([batch(current="", service_current="0")]).sample()
        self.assertEqual(sample.current_ma, 0)
        self.assertEqual(sample.current_source, "BatteryService.batteryCurrentMicroamps")
        self.assertIsNone(sample.power_w)

    def test_service_positive_current_conflicts_with_discharge_status(self):
        sample = collector([batch(current="", status="", service_current="1500000")]).sample()
        self.assertEqual(sample.current_ma, 1500)
        self.assertIsNone(sample.power_w)
        self.assertTrue(any("读数不一致" in note for note in sample.notes))

    def test_service_current_is_recorded_while_charging_but_never_whole_device_power(self):
        charging = BATTERY.replace("USB powered: false", "USB powered: true").replace("status: 3", "status: 2")
        for current in ("1500000", "-1500000"):
            with self.subTest(current=current):
                sample = collector([batch(battery=charging, current="", status="", service_current=current)]).sample()
                self.assertEqual(sample.current_ma, int(current) / 1000)
                self.assertTrue(sample.plugged)
                self.assertIsNone(sample.power_w)

    def test_service_fallback_does_not_bypass_simulated_or_unverified_state(self):
        for battery in (BATTERY + "(UPDATES STOPPED -- use reset to restart)\n",
                        BATTERY.replace("USB powered: false", "USB powered: unknown"),
                        BATTERY.replace("status: 3", "status: 2")):
            with self.subTest(battery=battery):
                sample = collector([batch(battery=battery, current="", status="", service_current="-1500000")]).sample()
                self.assertEqual(sample.current_ma, -1500)
                self.assertIsNone(sample.power_w)

    def test_current_service_refresh_is_conditional_and_precedes_battery_snapshot(self):
        command = collector([])._command(include_memory=False)
        self.assertIn('if _pt_current=$(cat /sys/class/power_supply/battery/current_now', command)
        self.assertIn('&& [ -n "$_pt_current" ]; then', command)
        self.assertLess(command.index("else printf"), command.index("cmd battery get -f current_now"))
        self.assertLess(command.index("cmd battery get -f current_now"), command.index("dumpsys battery"))
        self.assertNotIn("battery set", command)
        self.assertNotIn("battery unplug", command)

    def test_ring_overflow_does_not_join_unknown_frames(self):
        monitor = collector([batch(frames(1000, 1020)), batch(frames(2000, 2020, 2040))])
        monitor.sample()
        sample = monitor.sample()
        self.assertEqual(sample.frame_intervals_ms, [20, 20])
        self.assertEqual(sample.fps, 50)
        self.assertTrue(any("覆盖" in note for note in sample.notes))

    def test_timestamp_reset_and_missing_history_rebaseline(self):
        monitor = collector([batch(frames(1000, 1020)), batch(frames(20, 40)),
                             batch("16666666\n0 0 0"), batch(frames(60, 80)), batch(frames(60, 80, 100))])
        samples = [monitor.sample() for _ in range(5)]
        self.assertTrue(all(sample.fps is None for sample in samples[:4]))
        self.assertEqual(samples[4].frame_intervals_ms, [20])

    def test_parser_uses_actual_column_and_ignores_fences_and_duplicates(self):
        text = "16666666\n100 200 300\n100 200 300\n100 0 300\n100 9223372036854775807 300\n100 -1 300\n90 150 180"
        self.assertEqual(parse_frame_timestamps(text), [150, 200])

    def test_application_pss_sums_processes_without_rss_or_duplicate_total(self):
        self.assertEqual(Collector._memory("TOTAL 1024 0 0\nTOTAL PSS: 1024 TOTAL RSS: 9999\nTOTAL 2048 0 0\nTOTAL PSS: 2048"), 3)
        self.assertIsNone(Collector._memory("TOTAL RSS: 524288"))
        self.assertIsNone(Collector._memory("TOTAL PSS: 0"))
        self.assertEqual(Collector._memory(" TOTAL 1024 0 0\n TOTAL 2048 0 0"), 3)

    def test_auto_layer_selects_recent_valid_package_layer_and_rebaselines_change(self):
        old = "SurfaceView[com.example.game/.Old]#1"
        new = "SurfaceView[com.example.game/.New]#2"
        client = FakeClient([batch(frames(500, 520)), batch(frames(540, 560))],
                            layers=[old, new], layer_frames={old: frames(100, 120), new: frames(500, 520)})
        monitor = Collector(client, SessionConfig(serial="fixture", package=PACKAGE))
        self.assertIsNone(monitor.sample().fps)
        self.assertEqual(monitor._layer, new)
        client.layer_list = [old]
        client.layer_frames[old] = frames(540, 560)
        monitor._stale_polls = 3
        monitor._last_scan = -float("inf")
        self.assertIsNone(monitor.sample().fps)
        self.assertEqual(monitor._layer, old)

    def test_no_layer_does_not_issue_global_latency(self):
        client = FakeClient([batch()])
        monitor = Collector(client, SessionConfig(serial="fixture", package=PACKAGE))
        self.assertIsNone(monitor.sample().fps)
        self.assertTrue(all("--latency" not in command for command in client.commands))

    def test_pss_is_sampled_every_five_seconds_without_repeating_old_values(self):
        monitor = collector([batch(), batch(), batch()])
        first = monitor.sample()
        second = monitor.sample()
        monitor._last_memory_poll -= 6
        third = monitor.sample()
        self.assertEqual(first.app_mem_mb, 256)
        self.assertIsNone(second.app_mem_mb)
        self.assertEqual(third.app_mem_mb, 256)
        self.assertIn("dumpsys meminfo", monitor.client.commands[0])
        self.assertNotIn("dumpsys meminfo", monitor.client.commands[1])
        self.assertIn("dumpsys meminfo", monitor.client.commands[2])
        self.assertTrue(any("每 5 秒" in note for note in second.notes))

    def test_repeated_stale_then_resume_does_not_count_pause_as_gameplay_frame(self):
        monitor = collector([batch(frames(100, 120)) for _ in range(4)]
                            + [batch(frames(100, 120, 10000, 10020)), batch(frames(10000, 10020, 10040))])
        samples = [monitor.sample() for _ in range(6)]
        self.assertTrue(all(sample.fps is None for sample in samples[:5]))
        self.assertEqual(samples[5].frame_intervals_ms, [20])
        self.assertTrue(any("重新建立基线" in note for note in samples[4].notes))

    def test_auto_selection_prefers_game_surface_over_activity_chrome(self):
        chrome = "com.example.game/com.example.Main#4"
        client = FakeClient([batch(frames(500, 520))], layers=[chrome, LAYER],
                            layer_frames={chrome: frames(1000, 1020), LAYER: frames(500, 520)})
        monitor = Collector(client, SessionConfig(serial="fixture", package=PACKAGE))
        self.assertEqual(monitor.sample().layer, LAYER)

    def test_disconnection_propagates_and_stop_prevents_new_commands(self):
        monitor = collector([AdbError("手机已断开")])
        with self.assertRaisesRegex(AdbError, "断开"):
            monitor.sample()
        monitor.close()
        with self.assertRaisesRegex(AdbError, "停止"):
            monitor.sample()

    def test_manual_layer_cannot_select_another_app_and_shell_values_are_quoted(self):
        with self.assertRaises(AdbError):
            Collector(FakeClient([]), SessionConfig(serial="x", package=PACKAGE, layer="SurfaceView[com.other.game/.Main]"))
        with self.assertRaises(AdbError):
            Collector(FakeClient([]), SessionConfig(serial="x", package="com.example.game; touch /tmp/x"))
        special = "SurfaceView[com.example.game/.Main] $HOME ' ; #12"
        monitor = Collector(FakeClient([]), SessionConfig(serial="x", package=PACKAGE, layer=special))
        self.assertIn(shlex.quote(special), monitor._command())


if __name__ == "__main__":
    unittest.main()
