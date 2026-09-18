# Protocol update (2026-09-06)

## New static findings

- `AA 06 00 05 AA 55` is a true reset path.
  - It lands at `0x08008ed8`, which looks like an `AIRCR/SYSRESETREQ` style system reset.
- `func=0x06` is not a flat write handler.
  - It dispatches by register low byte.
  - `reg 0x0005` is the special motion branch.
  - `reg 0x0009` is the clear/unlock branch.
  - `reg 0x000B` is the direction/phase branch.
- The earlier `AA11` / `22AA` special values are in the `reg 0x0005` branch, not the `reg 0x000B` branch.
  - `0xAA11` feeds the motion start path and negates the computed PWM sign.
  - `0x22AA` enters a different maintenance-style path and is higher risk.
- `func=0x03` read access is gated.
  - When the internal state flag at `state[0x12]` is nonzero, only a small whitelist of registers can be read.
- `func=0x10` can also trigger motion-state changes.
  - It writes a register block first, then checks `reg 0x000B` and `reg 0x0001` for motion decisions.
  - live test after reboot confirmed `01 10 00 0B 00 01 02 AA 11 19 87` can start motion.
  - user-side retest after power adjustment confirmed this starts counterclockwise motion.
  - follow-up `01 10 00 0B 00 01 02 00 00 A7 2B` reversed direction and reduced duty, so `0x0000` is not a stop word.

## Practical interpretation

- The cleanest model is now:
  1. `func=0x06` writes command words into the register map.
  2. The periodic service at `0x08008c30` turns those words into PWM.
  3. `0x0800b220` only handles signed PWM direction/stop.
- So the protocol is closer to a command/state machine than a single "move motor" opcode.

## Safer working hypothesis

- `reg 0x0009 = 0x1234` is the best clear/unlock candidate.
- `reg 0x0005 = 0xAA11` is the best confirmed motion-start candidate.
  - live check on a stopped board did **not** move the motor by itself, so it may be an arming/state-seed word rather than a direct start.
- `reg 0x000B = 0x0000` is now best interpreted as clockwise start; reboot retests did not confirm low-speed behavior.
- `reg 0x000B = 0x22AA` is not stop; retest showed no effect while already counterclockwise, and clockwise start after reboot.
- `reg 0x001F = 0x0001` is a confirmed inhibit/stop condition.
  - It stopped a running motor.
  - If sent as the first command after power-up, later known direction/start commands do not move the motor.
  - Static code supports this: `0x0800b004` checks `reg 0x001F` and uses nonzero value as an override in the PWM duty calculation.
- `reg 0x001F = 0x0000` clears the inhibit and lets the motor resume the previously commanded running state.
- `AA 06 00 05 AA 55` should be treated as reset/maintenance, not normal control.
