# Firmware feature summary

## Practical user-facing capabilities found

1. **RS485/Modbus-like slave**
   - Normal slave address: `0x01`
   - Functions: `0x03` read registers, `0x06` single write/control,
     `0x10` multi-register write/control.

2. **Open-loop DC motor drive**
   - Direction/start: `reg 0x000B`
   - PWM magnitude/speed override: `reg 0x001F`
   - PWM output: TIMER2 CH2/CH3, signed PWM helper at `0x0800B220`.
   - No encoder/position-loop code found.

3. **Limit-switch / sensor-gated motion**
   - Debounced input state stored around `state[0x0A]/state[0x0B]`.
   - Readback:
     - `reg 0x0002`: raw/inverted input bitfield
     - `reg 0x0018`: debounced/latched limit state
   - Limit input is used inside `0x08008C30`, not just exposed as status.

4. **Timed/state-machine motion phases**
   - `state[1]` and `state[5]` select motion phases/directions.
   - Internal counters include `0x32`, `0xC8`, `0x12C` thresholds.
   - This means a command is not simply "set PWM forever"; it seeds a state
     machine that may pause/run/transition depending on limit and status.

5. **Analog compensation / protection**
   - ADC0 DMA samples channels 4, 5, 0, 17 into `0x2000010C`.
   - `sample[0]` feeds PWM denominator, probably supply-voltage compensation.
   - `sample[1]` is scaled to `state+0x14` and compared with `state+0x0E`
     (`reg 0x0013`, default 180).  This may be load/current/force or another
     analog threshold input.

6. **Configurable threshold/timing**
   - `reg 0x0013` -> copied to `state+0x0E`, threshold-like.
   - `reg 0x0014` -> divided by 10, copied to `state+0x10`, timeout/timing-like.
   - These affect stop/error behavior rather than direct speed.

7. **Status/error reporting and unlock**
   - `reg 0x0001`: phase/status guard.
   - `reg 0x0017`: error/lock code; e.g. motion service writes `0xFC1A`.
   - `reg 0x0020/0x0021/0x0024`: runtime motion status.
   - `reg 0x0009 = 0x1234`: clears an error/lock latch.

8. **Maintenance/reset/config storage**
   - `AA 06 00 05 AA 55`: software reset path.
   - `reg 0x0005 = 0x22AA`: flash/maintenance-adjacent path; avoid for normal
     control.

## What has not been found

- No encoder interface or angle feedback loop found.
- No absolute angle setpoint register found.
- No trajectory planner / multi-axis interpolation found in this joint firmware.
- No evidence that it can move to arbitrary angles by command alone.

## Current best mechanical interpretation

This joint is probably a low-cost open-loop endpoint/homing actuator:

- the master commands direction and speed;
- firmware drives the DC motor through PWM;
- limit switch and analog threshold/timing logic gate or terminate phases;
- higher-level angle/sequence control, if any, lives in the master controller,
  not in this joint MCU.

So the user's intuition is mostly right: this board likely cannot do closed-loop
angle positioning by itself.  It can rotate in either direction at controlled
speed and respond to limit/error conditions.

## Direction test confirmation — 2026-09-12

Slave 3 was tested in both directions with the same moderate PWM override:

- `0x000B=0xAA11`: contraction/tightening confirmed.
- `0x000B=0x0000`: expansion confirmed.

This strengthens the open-loop interpretation: the MCU selects a direction and
drives PWM, while the limit switch and internal state machine provide the
available endpoint/safety handling.  No angle feedback was observed.

## Stepper-vs-DC assessment

The firmware does not show a step-count output, STEP/DIR interface, phase
commutation table, or position accumulator.  The motor helper instead applies a
signed duty value to TIMER2 CH2/CH3, with one channel active and the other
disabled for each direction.  Since changing `0x001F` changes the measured
motor speed while the timer carrier configuration remains fixed, the current
evidence is much more consistent with an open-loop brushed DC motor plus H-bridge
than with a stepper motor whose position is estimated from pulse count.

Time-based position estimation by the higher-level master remains possible, but
it is not a reliable angle measurement and has not been found as an internal
angle estimator in this firmware.

## Fixed small expansion range observed on slaves 3 and 4 — 2026-09-12

Both joints were tested with contraction for about one second followed by
expansion for ten seconds.  The operator confirmed that both moved in the
expected directions, but the expansion range remained small and did not grow
when the expansion interval was increased from a few seconds to ten seconds.

This suggests that a direction write starts an internal finite state-machine
phase; it is not a direct command to run the motor continuously for the entire
host-side delay.  The shared behavior across joints 3 and 4 makes a common
firmware phase/threshold/termination condition more likely than an isolated
motor fault.  The exact termination condition remains unresolved.
