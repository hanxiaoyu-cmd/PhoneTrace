from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from phonetrace.adb import (
    AdbClient, AdbError, layer_matches_package, normalize_layer_name, validate_endpoint,
)


class AdbTests(unittest.TestCase):
    def setUp(self):
        self.client = AdbClient("C:/adb/adb.exe")

    @patch("phonetrace.adb.subprocess.run")
    def test_windows_process_is_bounded_and_host_shell_is_not_used(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "hello", "")
        self.assertEqual(self.client.shell("serial", "dumpsys battery", timeout=7), "hello")
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["C:/adb/adb.exe", "-s", "serial", "shell", "dumpsys battery"])
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["timeout"], 7)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)

    @patch("phonetrace.adb.subprocess.run")
    def test_connection_errors_and_timeouts_are_actionable(self, run):
        for text, expected in [("error: device unauthorized", "授权"), ("error: device offline", "离线")]:
            run.return_value = subprocess.CompletedProcess([], 1, "", text)
            with self.assertRaisesRegex(AdbError, expected):
                self.client.shell("serial", "dumpsys battery")
        run.side_effect = subprocess.TimeoutExpired("adb", 8)
        with self.assertRaisesRegex(AdbError, "超时"):
            self.client.devices()
        run.side_effect = FileNotFoundError()
        with self.assertRaisesRegex(AdbError, "未找到 ADB"):
            self.client.devices()

    def test_parse_device_states_and_transports(self):
        output = """List of devices attached
USB123 device product:foo model:Xiaomi_18 device:foo transport_id:1
192.168.1.5:32900 device product:foo model:Xiaomi_18 device:foo transport_id:2
adb-id-test._adb-tls-connect._tcp device model:Xiaomi_18
OTHER unauthorized transport_id:4
DISCONNECTED offline transport_id:5
"""
        with patch.object(self.client, "_run", return_value=output):
            devices = self.client.devices()
        self.assertEqual(len(devices), 5)
        self.assertEqual(devices[0].model, "Xiaomi 18")
        self.assertEqual([device.transport for device in devices[:3]], ["USB", "Wi-Fi", "Wi-Fi"])
        self.assertEqual(devices[-2].state, "unauthorized")

    def test_invalid_endpoints_and_pair_codes_never_start_a_process(self):
        bad = ["--help", "127.0.0.1", "127.0.0.1:0", "127.0.0.1:65536", "999.0.0.1:1234",
               "127.0.0.1:1234;calc", "host$(x):1234", "foo..bar:23", "[::g]:33"]
        with patch.object(self.client, "_run") as run:
            for endpoint in bad:
                with self.subTest(endpoint=endpoint), self.assertRaises(AdbError):
                    self.client.connect(endpoint)
            with self.assertRaises(AdbError):
                self.client.pair("127.0.0.1:1234", "123456;calc")
            run.assert_not_called()
        self.assertEqual(validate_endpoint("[::1]:1234"), "[::1]:1234")
        self.assertEqual(validate_endpoint("android.local:1234"), "android.local:1234")

    def test_adb_connect_exit_zero_with_failure_is_still_failure(self):
        with patch.object(self.client, "_run", return_value="failed to connect to 127.0.0.1:1234"):
            with self.assertRaises(AdbError):
                self.client.connect("127.0.0.1:1234")
        with patch.object(self.client, "_run", return_value="Successfully paired to 127.0.0.1:1234"):
            self.assertIn("Successfully", self.client.pair("127.0.0.1:1234", "123456"))

    def test_foreground_and_layer_filter_do_not_match_package_prefixes(self):
        activity = "mResumedActivity: ActivityRecord{123 u0 com.example.game/.Main t12}"
        with patch.object(self.client, "shell", return_value=activity):
            self.assertEqual(self.client.foreground_package("serial"), "com.example.game")
        layer_output = "SurfaceView[com.example.game/.Main]#1\nSurfaceView[com.example.game.other/.Main]#2\ncom.android.systemui"
        with patch.object(self.client, "shell", return_value=layer_output):
            self.assertEqual(self.client.layers("serial", "com.example.game"), ["SurfaceView[com.example.game/.Main]#1"])
        self.assertFalse(layer_matches_package("SurfaceView[com.example.game2/.Main]", "com.example.game"))

    def test_device_info_allowlist_preserves_empty_properties_without_shifting(self):
        output = "manufacturer=\nmodel=Xiaomi 18\nandroid_version=16\nsdk=36\nbuild=Build 1"
        with patch.object(self.client, "shell", return_value=output) as shell:
            info = self.client.device_info("serial")
        self.assertEqual(info["manufacturer"], "")
        self.assertEqual(info["model"], "Xiaomi 18")
        self.assertEqual(info["android_version"], "16")
        self.assertNotIn("imei", shell.call_args.args[1].lower())
        self.assertNotIn("serialno", shell.call_args.args[1].lower())

    def test_android16_debug_layer_names_preserve_name_and_sequence(self):
        name = "abc123 SurfaceView[com.example.game/.Main](BLAST)#42"
        suffixes = [
            "", " parentId=41", " z=-2", " relativeParentId=40",
            " parentId=41 relativeParentId=40 mirrorId={2,3,} !handle z=-2 layerStack=1",
        ]
        for suffix in suffixes:
            with self.subTest(suffix=suffix):
                self.assertEqual(normalize_layer_name(f"RequestedLayerState{{{name}{suffix}}}"), name)
        # A name may itself contain braces, spaces and text resembling metadata.
        embedded = "SurfaceView[com.example.game/.Main] parentId=7 {child}#45"
        self.assertEqual(normalize_layer_name(f"RequestedLayerState{{{embedded} parentId=41}}"), embedded)

    def test_layer_list_normalizes_before_filtering_and_deduplicates(self):
        selected = "abc123 SurfaceView[com.example.game/.Main](BLAST)#42"
        output = "\n".join([
            f"RequestedLayerState{{{selected} parentId=41}}", selected,
            "RequestedLayerState{SurfaceView[com.example.game.other/.Main]#44 parentId=43}",
            "RequestedLayerState{SurfaceView[com.example.game2/.Main]#46 parentId=45}",
            "Permission Denial: can't dump SurfaceFlinger", "",
        ])
        with patch.object(self.client, "shell", return_value=output):
            self.assertEqual(self.client.layers("serial", "com.example.game"), [selected])

    def test_plain_names_and_unrecognized_wrappers_are_not_rewritten(self):
        names = [
            "SurfaceView[com.example.game/.Main](BLAST)#42",
            "SurfaceView - com.example.game/.Main@abc123",
            "RequestedLayerState{SurfaceView[com.example.game/.Main]#42 futureField=1}",
            "RequestedLayerState{SurfaceView[com.example.game/.Main]#42 parentId=41",
        ]
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(normalize_layer_name(name), name)


if __name__ == "__main__":
    unittest.main()
