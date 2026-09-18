# Motion-control semantics

## Evidence

- `analysis/protocol_report.md`
  - `0x080090fa` / `0x080091b6`: `func=0x06` control path
  - `0x080091b6`: compares value against `0xAA11` and `0xAA55`
- `0x0800b220`
  - signed `r1 > 0` => drive one direction
  - signed `r1 < 0` => drive the opposite direction
  - `r1 == 0` => stop
- `0x08008c30`
  - periodic motor state machine
  - `state[5]` is the motion-state flag
  - hardware PWM path is TIMER2 CH2/CH3
  - likely default pins are PB0/PB1

## Live

- `01 06 00 0B ...` => direction-control path; zero/nonzero selects opposite directions
- `01 06 00 05 AA 11` => special motion-control path
- `01 06 00 05 22 AA` => special maintenance/reset-like path
- No direct stop frame confirmed yet
- Rebooted live test: `01 06 00 05 AA 11` did not move the motor
- Limit switch press while running causes a brief stop/pause, so the periodic motion loop is definitely gating on the limit input

## Static correction

- `0x08008C30` is the only place I found a true PWM stop call (`0x0800B220` with speed 0)
- The dispatcher paths we tested do not directly call that stop branch
- `0x0800b220` clamps speed to `±100`
