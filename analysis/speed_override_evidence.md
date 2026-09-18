# Speed/PWM override evidence update

This note corrects the earlier low-value speed tests (`0x001F=300/500/800`).
Those tests were probably too small, not evidence that speed control is absent.

## Static evidence

The PWM percentage helper is at `0x0800B004`:

```asm
0800B004: cbz      r0, #0x800b022       ; numerator == 0 => 0%
0800B006: cbz      r1, #0x800b022       ; denominator == 0 => 0%
0800B008: ldr      r2, [pc, #0x1c]      ; r2 = 0x20000158, register map
0800B00A: ldrh     r2, [r2, #0x3e]      ; reg[0x001F]
0800B00C: cbz      r2, #0x800b010
0800B00E: mov      r0, r2               ; nonzero 0x001F overrides numerator
0800B010: movs     r2, #0x64
0800B012: muls     r0, r2, r0
0800B014: udiv     r0, r0, r1           ; duty = numerator*100/denominator
0800B018: uxtb     r0, r0
0800B01A: cmp      r0, #0x64
0800B01E: movs     r0, #0x64            ; clamp to 100
```

So `reg 0x001F` is definitely sampled by the PWM duty calculation.  When it
is nonzero it replaces the normal numerator.

## Denominator is not 1000

The earlier guess used `reg 0x0014 = 1000` as the denominator.  The disassembly
does not support that:

```asm
08008E84: ldr      r1, [r7, #0x18]      ; denominator = state+0x18
08008E86: ldr      r0, [r7, #0x20]      ; normal numerator = state+0x20
08008E88: bl       #0x800b004
```

`state+0x18` is periodically recomputed in `0x08009E68` from ADC-derived math:

```asm
08009EC0: ldrh     r0, [r0]             ; ADC/sample value
08009EC2: movw     r1, #0x0CE4          ; 3300
08009EC6: muls     r0, r1, r0
08009EC8: bl       #0x80089c8
08009ECC: vldr     d1, [pc, #0x64]      ; double 0.002075
08009ED4: bl       #0x80088e4
08009ED8: bl       #0x80089e2
08009EE2: str      r0, [0x20000130]     ; state+0x18
```

Formula from constants:

```c
state_18 = (uint32_t)(adc0 * 3300 * 0.002075);
         = (uint32_t)(adc0 * 6.8475);
```

With a normal 12-bit ADC reading in the 2000~2500 range, the denominator is
roughly `13700~17100`, not `1000`.  That makes low tests tiny:

| override | duty if denominator=15000 |
|---:|---:|
| 300 | 2% |
| 500 | 3% |
| 800 | 5% |
| 5250 | 35% |
| 8000 | 53% |
| 12000 | 80% |
| 14000 | 93% |

This explains why `300/500/800` could look like "no response".

## Default numerator clues

The flash/config image near `0x0801FC40` contains values that line up with the
state structure copied into RAM:

```text
0801FC40: 00 00 B4 00 64 00 00 00 ...
0801FC50: B0 36 00 00 82 14 00 00 ...
```

The layout matches known startup copies:

- `state+0x0E = 0x00B4` -> copied to `reg 0x0013`
- `state+0x10 = 0x0064` -> multiplied by 10 and copied to `reg 0x0014 = 1000`
- likely `state+0x1C = 0x36B0` (14000), used by immediate/start PWM paths
- likely `state+0x20 = 0x1482` (5250), used by the sustained running phase

The first two fields are high-confidence because the code copies them to
readable registers.  The last two are a layout-based inference, but they are
also exactly the fields read by the PWM calculation paths.

## Practical implication

`0x001F=1` stops/inhibits because it makes duty effectively 0%.
Intermediate tests should use thousands, not hundreds.

Suggested non-persistent test sequence while motor is already running:

```text
0x001F = 0x1482 (5250)   about default sustained speed
0x001F = 0x1F40 (8000)   clearly higher
0x001F = 0x36B0 (14000)  near high/start value
0x001F = 0x0001          safe inhibit/stop
0x001F = 0x0000          clear override/resume normal
```

Generated function-0x10 frames:

```text
01 10 00 1F 00 01 02 14 82 2B 5E
01 10 00 1F 00 01 02 1F 40 AD FF
01 10 00 1F 00 01 02 36 B0 B2 2B
01 10 00 1F 00 01 02 00 01 65 FF
01 10 00 1F 00 01 02 00 00 A4 3F
```

Avoid `reg 0x0005 = 0x22AA` / `AA 06 00 05 AA 55` during speed testing; those
hit maintenance/reset paths, not speed control.


## Live confirmation

User confirmed on hardware that writing larger `reg 0x001F` values can adjust
counterclockwise speed.  This validates the static interpretation that
`0x001F` is a PWM magnitude override.

## Clockwise speed

There is no separate clockwise-speed register in the motor path found so far.
The firmware computes one PWM magnitude, then applies direction by sign:

```asm
; sustained running phase
08008E84: ldr      r1, [r7, #0x18]
08008E86: ldr      r0, [r7, #0x20]
08008E88: bl       #0x800b004       ; duty magnitude, may use reg 0x001F
08008E8C: strb     r0, [r7, #4]
08008E8E: ldrb     r1, [r7, #5]    ; direction/state
08008E90: cmp      r1, #1
08008E92: beq      #0x8008e96
08008E94: rsbs     r0, r0, #0      ; state != 1 => negate duty
08008E96: sxtb     r1, r0
08008E9A: bl       #0x800b220      ; signed PWM: +/? chooses CH2/CH3
```

Therefore clockwise speed should be configured by the same `0x001F` value;
only the direction/start command changes.

Known safer clockwise command from live tests:

```text
01 10 00 0B 00 01 02 00 00 A7 2B     ; reg 0x000B = 0x0000, clockwise start
```

Then adjust speed with the same override frames, for example:

```text
01 10 00 1F 00 01 02 14 82 2B 5E     ; lower/default-ish magnitude
01 10 00 1F 00 01 02 1F 40 AD FF     ; medium-high magnitude
01 10 00 1F 00 01 02 36 B0 B2 2B     ; high magnitude
```
