# Tests for the recovered slave-2 / slave-3 control paths

Use `analysis/test_firmware2_control.py`, not the previous `test_joint2.py`
or `profile_motion_registers.py` for these new control-logic tests.
The latter scripts retain historical command assumptions for prior experiments.

No port is opened without `--run`. One invocation operates on only one slave.
Default port COM8, 115200 8N1, nominal 10 Hz, maximum motion window 2 seconds.
Polling and host scheduling are best effort, not a hardware cutoff timer.
Support gravity-loaded structures: output disable may remove holding torque.
Direction-code to physical up/down mapping for slave 2 remains unconfirmed.
Observe the first trial with power cutoff accessible; ACK is not a physical stop.

## Joint 2: one direction per trial

```powershell
python analysis/test_firmware2_control.py --slave 2 --direction 0 --run --plot
python analysis/test_firmware2_control.py --slave 2 --direction 1 --run --plot
```

Run separately and observe before trying the other direction. Sequence:
read preflight -> reg8=0 -> readback -> regB=direction -> monitor <=2s ->
reg8=0 -> readback -> 1 second post observation. The regB write flag itself
sets run state (0x0800BC12), so no AA11 command is needed. Firmware selects
negative PWM for direction 0 and positive PWM for 1; physical direction unknown.
No claimed speed-setting register for joint 2 is written.

## Joint 3: small relative position move

```powershell
python analysis/test_firmware2_control.py --slave 3 --delta -100 --speed 366 --run --plot
python analysis/test_firmware2_control.py --slave 3 --delta 100 --speed 366 --run --plot
```

It must already be in position mode (reg7=1). Initial disable resets the internal
trajectory toward the measured position. The script reads current int16 position,
sets regA=speed, then writes regB=current+delta. Position target enables outputs.
Only raw counts are known; delta is not degrees. Position is exposed only as low16
of an internal int32. Use a known near-startup position after a fresh power cycle;
the script cannot detect a hidden high-word wrap. It rejects an int16 target overflow.

Default delta=-100, allowable absolute delta 1..300. Default speed=366, range
1..732. Zero is disallowed because position mode retains the previous speed
envelope for a zero setting. Continuous speed mode is deliberately not tested:
it can re-enable outputs despite reg8=0.

To compare speed while allowing a longer constant-speed section, a later pair
can use the same delta of -300 with speed 732 versus 366, starting from equivalent
safe poses. Small 100-count moves may mainly show acceleration/deceleration.
This is a future comparison, not an automatically executed sequence.

## Checks and logs

- Reads exactly 0x0000..0x0023; does not cross to the next bank.
- Requires reg19=2, reg1B=67C6, reg1C=D81D (analyzed build fingerprint).
  A mismatch aborts before any write; it does not guess another map.
- Requires no reported fault, no reg5 special command, and position mode for 3.
- Motion read errors, a newly asserted limit signal or fault end the window early.
- Every exit after preparation attempts reg8=0. At most one disable retry is used.
- No maintenance/fault-clear writes, no automatic reverse, no restore-to-running
  settings, no writes to the other slave. No use of reg1F or target AA11.
- JSON logs exact TX/RX, samples, resolved target and errors. CSV retains unsigned
  raw words; plots show signed feedback for slave 3. New timestamped names prevent
  overwriting earlier experiments. Files are under analysis/motion_logs.
- Disable ACK and reg8 readback are reported separately from physical stopping.
  Holding, coasting and gravity motion must still be observed by the operator.

Offline verification:

```text
python -m unittest discover -s analysis -p test_firmware2_control_offline.py
```
