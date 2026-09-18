# Speed control finding

## Static evidence

Function `0x0800b004` computes the PWM duty passed to `0x0800b220`.

Reconstructed logic:

```c
uint8_t pwm_duty_compute(uint32_t numerator, uint32_t denominator)
{
    if (numerator == 0 || denominator == 0) {
        return 0;
    }

    uint16_t override = regs[0x001F];
    if (override != 0) {
        numerator = override;
    }

    duty = numerator * 100 / denominator;
    if (duty > 100) {
        duty = 100;
    }
    return duty;
}
```

So register `0x001F` is not just a lock bit. It is very likely a **speed/PWM numerator override**:

- `0x001F = 0` disables the override and lets the state machine use its normal speed.
- `0x001F != 0` forces the PWM calculation to use this value.
- Very small values such as `1` produce effective zero duty with the current denominator, so the motor stops/inhibits.

## Live evidence

- `01 10 00 1F 00 01 02 00 01 65 FF`
  - wrote `0x001F = 1`
  - stopped/inhibited the motor
  - if sent first after power-up, later direction commands did not move the motor
- `01 10 00 1F 00 01 02 00 00 A4 3F`
  - wrote `0x001F = 0`
  - cleared the override
  - restored/resumed previous motion

## Practical speed candidates

Assuming the denominator is around the configured range value `1000` (`reg 0x0014 = 0x03E8`), approximate duty would be:

| Write value | Expected duty if denominator=1000 | Frame |
|---:|---:|---|
| `0x0001` | ~0% | `01 10 00 1F 00 01 02 00 01 65 FF` |
| `0x0032` / 50 | ~5% | `01 10 00 1F 00 01 02 00 32 25 EA` |
| `0x0064` / 100 | ~10% | `01 10 00 1F 00 01 02 00 64 A5 D4` |
| `0x00C8` / 200 | ~20% | `01 10 00 1F 00 01 02 00 C8 A5 A9` |
| `0x012C` / 300 | initially guessed ~30%, **not confirmed** | `01 10 00 1F 00 01 02 01 2C A4 72` |
| `0x01F4` / 500 | initially guessed ~50%, **not confirmed** | `01 10 00 1F 00 01 02 01 F4 A4 28` |
| `0x0320` / 800 | initially guessed ~80%, **not confirmed** | `01 10 00 1F 00 01 02 03 20 A5 17` |
| `0x03E8` / 1000 | ~100% | `01 10 00 1F 00 01 02 03 E8 A4 81` |

Live correction: user tested the 300/500/800 frames and observed no visible effect.
Deeper static tracing now explains this: the denominator is not `reg 0x0014=1000`,
but `state+0x18`, recomputed from ADC math and likely around 10k~20k.  Therefore
300/500/800 are only a few percent duty.  See `analysis/speed_override_evidence.md`.

## Safety note

Use `0x001F=1` as the current safest software stop/inhibit. Use `0x001F=0` to clear the override. Intermediate values should be tested cautiously because the mapping depends on the firmware's current denominator.
