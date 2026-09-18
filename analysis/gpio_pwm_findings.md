# GPIO / PWM findings

## High-confidence chain

- Motor PWM helper: `0x0800b220`
  - signed positive => drive CH2
  - signed negative => drive CH3
  - zero => stop
- Main startup path: `0x0800b02c`
  - calls `0x0800c234` during board init, before entering the protocol loop at `0x08008ffc`
- PWM timer init: `0x0800c3e8`
  - timer base: `TIMER2` (`0x40000400`)
  - prescaler observed: `0x23`
  - auto-reload observed: `0x03E7`
- Channel setup:
  - `0x0800c178` / `0x0800c1e4` configure channel mode / enable bits
  - `0x0800c1c6` writes CCR values
  - `r1=2` => `CCR2`
  - `r1=3` => `CCR3`

## Likely pin mapping

- Default `TIMER2_CH2` / `TIMER2_CH3` pins are `PB0` / `PB1` on the GD32F103 packages I checked.
- I did not find AFIO remap writes in the current binary search, so the default mapping looks most likely.
- There is also a GPIO init wrapper using pin code `0x25`; by the common `port<<4 | pin` encoding, that would be `GPIOC5`.
- Another boot-time helper touches `0x32`; by the same encoding, that would be `GPIOD2`.

## Practical meaning

- The serial protocol is not directly toggling GPIO pins for motion.
- It writes control state; the periodic motion loop turns that into PWM on TIMER2.
- So the cleanest control point is still the register/state machine, not raw GPIO pokes.
