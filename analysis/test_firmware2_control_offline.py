"""Protocol/lifecycle regression checks; all serial I/O is simulated."""
import argparse
import contextlib
import io
import unittest
from unittest.mock import patch

import test_firmware2_control as control


class FakeSerial:
    def __init__(self, slave, fail_start=False, bad_identity=False):
        self.slave = slave
        self.fail_start = fail_start
        self.frames = []
        self.buffer = b""
        self.words = [0] * 36
        self.words[7] = 1
        self.words[8] = 1
        self.words[0xA] = 732
        self.words[0x19] = 2
        self.words[0x1B] = 0x67C6
        self.words[0x1C] = 0 if bad_identity else 0xD81D

    def reset_input_buffer(self):
        self.buffer = b""

    def write(self, frame):
        self.frames.append(frame)
        assert frame[0] == self.slave
        assert int.from_bytes(frame[-2:], "little") == control.crc16_modbus(frame[:-2])
        if frame[1] == 3:
            assert frame[2:6] == bytes((0, 0, 0, 36))
            body = bytes((self.slave, 3, 72)) + control.struct.pack(">36H", *self.words)
        else:
            assert frame[1] == 0x10
            reg = int.from_bytes(frame[2:4], "big")
            value = int.from_bytes(frame[7:9], "big")
            assert reg in (8, 0xA, 0xB)
            self.words[reg] = value
            if reg == 0xB:
                self.words[8] = 1
                if self.fail_start:
                    raise OSError("Simulated lost start acknowledgement")
            body = frame[:6]
        self.buffer = body + control.crc16_modbus(body).to_bytes(2, "little")
        return len(frame)

    def read(self, count):
        data, self.buffer = self.buffer[:count], self.buffer[count:]
        return data


class ControlTests(unittest.TestCase):
    def args(self, slave):
        return argparse.Namespace(slave=slave, seconds=2.0, sample_hz=10.0,
                                  delta=-100, speed=366, direction=0)

    def execute(self, slave, **kwargs):
        ser = FakeSerial(slave, **kwargs)
        clock = [0.0]
        with (patch.object(control.time, "monotonic", side_effect=lambda: clock[0]),
              patch.object(control.time, "sleep", side_effect=lambda t: clock.__setitem__(0, clock[0] + t)),
              patch.object(control, "beep"), contextlib.redirect_stdout(io.StringIO())):
            trial = control.Trial(self.args(slave), ser)
            try:
                trial.run()
                error = None
            except Exception as exc:
                error = exc
        return trial, ser, error

    def test_slave2_single_direction_then_disable(self):
        trial, ser, error = self.execute(2)
        self.assertIsNone(error)
        self.assertEqual([(c["reg"], c["value"]) for c in trial.commands], [(8, 0), (11, 0), (8, 0)])
        self.assertAlmostEqual(trial.commands[-1]["elapsed_tx_begin"] - trial.commands[1]["elapsed_tx_begin"], 2)
        self.assertTrue(trial.disable_acknowledged)
        self.assertEqual(trial.disable_readback, 0)

    def test_slave3_small_position_and_real_speed_register(self):
        trial, ser, error = self.execute(3)
        self.assertIsNone(error)
        self.assertEqual([(c["reg"], c["value"]) for c in trial.commands],
                         [(8, 0), (10, 366), (11, 0xFF9C), (8, 0)])
        self.assertEqual(trial.target, -100)
        self.assertTrue(any(r["phase"] == "post" for r in trial.samples))

    def test_lost_start_ack_still_disables(self):
        trial, ser, error = self.execute(3, fail_start=True)
        self.assertIsInstance(error, OSError)
        self.assertEqual(trial.commands[-1]["reg"], 8)
        self.assertEqual(trial.disable_readback, 0)

    def test_wrong_firmware_is_read_only(self):
        trial, ser, error = self.execute(2, bad_identity=True)
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(trial.commands, [])
        self.assertTrue(all(f[1] == 3 for f in ser.frames))

    def test_speed_mode_rejected_without_writes(self):
        ser = FakeSerial(3)
        ser.words[7] = 2
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "position mode"):
                control.Trial(self.args(3), ser).run()
        self.assertTrue(all(f[1] == 3 for f in ser.frames))

    def test_dry_run_does_not_open_port(self):
        with patch.object(control.serial, "Serial") as opening, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(control.main(["--slave", "2"]), 0)
            self.assertEqual(control.main(["--slave", "3"]), 0)
            opening.assert_not_called()


if __name__ == "__main__":
    unittest.main()
