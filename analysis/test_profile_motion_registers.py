"""Offline lifecycle checks: no real serial port is opened."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import profile_motion_registers as profile


class LifecycleTests(unittest.TestCase):
    def run_profile(self, inhibit_error=None):
        clock = [0.0]
        writes = []

        def send(ser, slave, reg, value, timeout):
            writes.append((reg, value, clock[0]))
            return b"ack", inhibit_error if (reg, value) == (0x1F, 1) else None

        def read(*args):
            values = {reg: 0 for reg in profile.REGS}
            values[0x1F] = writes[-1][1] if writes and writes[-1][0] == 0x1F else 0x1482
            return values, []

        with tempfile.TemporaryDirectory() as directory:
            with (patch.object(profile, "serial", MagicMock()),
                  patch.object(profile, "send_write10", side_effect=send),
                  patch.object(profile, "read_register_groups", side_effect=read),
                  patch.object(profile.time, "monotonic", side_effect=lambda: clock[0]),
                  patch.object(profile.time, "sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)),
                  patch("sys.argv", ["probe", "--slave", "4", "--run", "--sample-hz", "10", "--output-dir", directory, "--tag", "test"]),
                  contextlib.redirect_stdout(io.StringIO())):
                result = profile.main()
            metadata = json.loads((Path(directory) / "test.json").read_text())
        return result, metadata, writes

    def test_timing_and_post_inhibit_sampling(self):
        result, metadata, writes = self.run_profile()
        self.assertEqual(result, 0)
        self.assertEqual([(reg, value) for reg, value, _ in writes],
                         [(0x1F, 0x1482), (0x0B, 0xAA11), (0x0B, 0), (0x1F, 1)])
        self.assertGreaterEqual(writes[2][2] - writes[1][2], 10)
        self.assertGreaterEqual(writes[3][2] - writes[2][2], 15)
        self.assertGreaterEqual(metadata["events"][-1]["elapsed_s"] - writes[3][2], 2.999)
        self.assertEqual(metadata["final_inhibit_last_readback"], 1)
        self.assertTrue(metadata["final_inhibit_acknowledged"])
        self.assertFalse(metadata["physical_stop_confirmed"])

    def test_inhibit_failure_is_not_success(self):
        result, metadata, _ = self.run_profile("timeout")
        self.assertEqual(result, 1)
        self.assertFalse(metadata["final_inhibit_acknowledged"])
        self.assertFalse(metadata["physical_stop_confirmed"])


if __name__ == "__main__":
    unittest.main()
