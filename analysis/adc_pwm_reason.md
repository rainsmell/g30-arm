# Why PWM duty uses ADC data

## What is statically confirmed

The firmware uses ADC0 with DMA to fill a small RAM sample buffer at `0x2000010C`.

DMA setup evidence:

```asm
0800A900: ...                         ; DMA setup
0800A9A0 literal: 40020000            ; DMA0 base
0800A9A4 literal: 4001244C            ; ADC0 regular data register area
0800A9A8 literal: 2000010C            ; destination buffer
```

ADC setup evidence:

```asm
0800A568: ldr      r4, [pc, #0x94]    ; r4 = 0x40012400, ADC0
...
0800A594: movs     r3, #5
0800A596: movs     r2, #4
0800A598: movs     r1, #0
0800A59C: bl       #0x800a6dc         ; rank 0, channel 4
0800A5A0: movs     r3, #5
0800A5A2: mov      r2, r3
0800A5A4: movs     r1, #1
0800A5A8: bl       #0x800a6dc         ; rank 1, channel 5
0800A5AC: movs     r3, #5
0800A5AE: movs     r2, #0
0800A5B0: movs     r1, #2
0800A5B4: bl       #0x800a6dc         ; rank 2, channel 0
0800A5B8: movs     r3, #5
0800A5BA: movs     r2, #0x11
0800A5BC: movs     r1, #3
0800A5C0: bl       #0x800a6dc         ; rank 3, channel 17
```

On GD32F103-class parts, the likely channel mapping is:

- ADC channel 0: PA0
- ADC channel 4: PA4
- ADC channel 5: PA5
- ADC channel 17: internal reference voltage (`VREFINT`) or internal channel

The periodic task reads the buffer:

```asm
08009E9A: ldr      r0, [pc, #0x88]    ; r0 = 0x2000010C
08009EA0: ldrh     r1, [r0, #2]       ; sample[1]
...
08009EC0: ldrh     r0, [r0]           ; sample[0]
08009EE2: str      r0, [0x20000130]   ; state+0x18 = PWM denominator
```

The PWM duty helper later uses:

```asm
08008E84: ldr      r1, [state, #0x18] ; denominator, ADC-derived
08008E86: ldr      r0, [state, #0x20] ; numerator
08008E88: bl       #0x800B004
```

## Why use ADC in PWM calculation?

Most likely it is not measuring motor angle.  This joint has no encoder, and
the ADC value is used as a PWM scaling denominator rather than as a position
feedback term.

More plausible uses:

1. **Supply/bus voltage compensation** — if motor supply voltage changes, the
   same duty cycle produces different motor voltage/torque.  Using an ADC-derived
   denominator lets the firmware keep approximate motor drive strength stable.
2. **Current/torque sensing** — possible if the board has a shunt amplifier, but
   this firmware path looks more like voltage scaling than fast current limiting.
3. **Board/potentiometer/config input** — possible for analog calibration or
   model detection, but less consistent with the direct PWM denominator use.
4. **Internal VREF calibration** — channel 17 is sampled, probably to help ADC
   voltage calibration or health checking.

Current best hypothesis: `sample[0]`, probably ADC channel 4, measures a supply
or analog scaling voltage used to normalize PWM duty.

## How to verify on hardware

Without sending risky commands:

- Measure MCU pins PA4/PA5/PA0 with a multimeter/scope.
- Check whether one pin is connected through a resistor divider to motor/bus
  supply or board input voltage.
- Change supply voltage slightly within safe range and read registers around
  `0x0002`, `0x0022/0x0023`, or values affected by `state+0x18` if readable.
- If a channel voltage follows supply, it is almost certainly voltage
  compensation.  If it changes with motor load, it may be current sensing.
