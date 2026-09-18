# Motor-related register map

This file separates high-confidence motor control registers from status/config
registers.  It is based on static firmware paths plus live hardware tests.

## High-confidence control registers

### `0x000B` ? direction/start command

Best live-tested function-0x10 forms:

```text
01 10 00 0B 00 01 02 AA 11 19 87   ; starts counterclockwise, confirmed
01 10 00 0B 00 01 02 00 00 A7 2B   ; starts clockwise, confirmed
```

Static backing in dispatcher `0x08008FFC`, function `0x10` path:

```asm
08009280: ldrh     r0, [rx, #4]              ; register count
08009282: ldrh     r1, [rx, #2]              ; start register
08009292: strh.w   r3, [regs, r1, lsl #1]   ; write words into reg map
...
080092A4: ldrh     r0, [regs, #0x16]        ; reg 0x000B
080092A6: cmp      r0, #0
080092A8: ldrh     r0, [regs, #2]           ; reg 0x0001 status/phase
080092AA: beq      #0x80092d0               ; reg0B == 0 branch
080092AC: cmp      r0, #0x5a
080092AE: beq      #0x80092fe               ; guard: no immediate start
080092B0: strb.w   #1, [state, #5]
080092C2: ldrd     r1, r0, [state, #0x18]
080092C6: bl       #0x800b004               ; speed magnitude
080092F4: bl       #0x800b220               ; signed PWM
080092D0: cbz      r0, #0x80092fe           ; if reg1==0, no start
080092D2: strb.w   #1, [state, #5]
080092E4: ldrd     r1, r0, [state, #0x18]
080092E8: bl       #0x800b004
080092EE: rsbs     r0, r0, #0               ; opposite direction
080092F4: bl       #0x800b220
```

Interpretation:

- `reg 0x000B != 0` and `reg 0x0001 != 0x005A` starts one signed PWM direction.
- `reg 0x000B == 0` and `reg 0x0001 != 0` starts the opposite signed PWM direction.
- Live mapping on this joint:
  - `0x000B = 0xAA11` => counterclockwise
  - `0x000B = 0x0000` => clockwise
- This also explains why repeated command behavior can depend on current state:
  the function-0x10 path checks `reg 0x0001` before applying a new start.

### `0x001F` ? PWM magnitude override / speed control

Confirmed live: larger values change motor speed.  Static helper `0x0800B004`:

```asm
0800B00A: ldrh     r2, [regs, #0x3e]    ; reg 0x001F
0800B00C: cbz      r2, #0x800b010
0800B00E: mov      r0, r2               ; nonzero override numerator
0800B014: udiv     r0, r0, r1           ; duty = numerator*100/denominator
```

Practical values:

```text
01 10 00 1F 00 01 02 14 82 2B 5E   ; lower/default-ish magnitude
01 10 00 1F 00 01 02 1F 40 AD FF   ; medium-high magnitude
01 10 00 1F 00 01 02 36 B0 B2 2B   ; high magnitude
01 10 00 1F 00 01 02 00 01 65 FF   ; effective inhibit/stop, ~0% duty
01 10 00 1F 00 01 02 00 00 A4 3F   ; clear override, resume normal speed
```

Clockwise and counterclockwise use the same speed register.  The firmware first
computes a magnitude, then negates it for the opposite direction.

## Medium-confidence control / state registers

### `0x0009` ? unlock / clear error latch

Function-0x06 path accepts register `0x0009`.  If the written value is `0x1234`,
the code clears a lock/error word and related status:

```asm
08009148: strh.w   r0, [regs, r1, lsl #1]   ; write reg9
0800914C: ldrh     r0, [regs, #0x12]        ; reg 0x0009
0800914E: sub.w    r1, r0, #0x1200
08009152: subs     r1, #0x34                ; compare 0x1234
08009156: strh     #0, [regs, #0x12]        ; clear reg9
0800915C: strh.w   #0, [state, #0x12]       ; clear lock/error state
08009160: strh     #0, [regs, #0x2e]        ; clear reg 0x0017
```

Candidate frame:

```text
01 06 00 09 12 34 54 BF
```

Use only as an unlock/clear command, not as a normal motion command.

### `0x0005` ? special function-0x06 branch, not recommended for normal control

Function-0x06 dispatch treats `reg 0x0005` specially:

- value `0xAA11` goes into the same negative-signed PWM start branch used by
  one direction path.
- value `0x22AA` enters a flash/maintenance-related branch.

Live behavior of `0x0005=0xAA11` was not as reliable as `func=0x10 / reg0B`.
For normal control, prefer `0x000B` and `0x001F`.

Avoid `0x0005=0x22AA` unless intentionally analyzing maintenance/storage code.

## Config/status registers involved in motor state machine

These are involved in the motor code but are not primary direction/speed commands.

### `0x0001` ? status/phase guard

The motion service writes values such as:

- `0x002D`
- `0x005A`
- `0x0000`

The function-0x10 start path also reads this register as a guard, which explains
some state-dependent behavior.  It should be treated as status/precondition, not
as the primary user command.

### `0x0013` and `0x0014` ? motion threshold/timing config

At start of `0x08008C30`:

```asm
08008C3E: ldrh     r0, [regs, #0x26]   ; reg 0x0013
08008C42: strh     r0, [state, #0x0e]
08008C44: ldrh     r0, [regs, #0x28]   ; reg 0x0014
08008C4A: udiv     r0, r0, #10
08008C4E: strh     r0, [state, #0x10]
```

Default live values:

- `0x0013 = 180`
- `0x0014 = 1000`, stored internally as `100`

These appear to be sensor threshold / timeout parameters, not direct speed.
Changing them could affect auto-stop/error behavior.

### `0x0017` ? error/lock code

Motion service writes `0xFC1A` into `reg 0x0017` when a threshold/timeout branch
is hit, and `reg 0x0009=0x1234` clears it.  Treat as error/status.

### `0x0020`, `0x0021`, `0x0024` ? motion runtime status

The motion service updates these during movement:

- `0x0020`: elapsed/timing/status value
- `0x0021`: active/transition flag, often set to `1`
- `0x0024`: zero/nonzero completion/status flag

They are useful for diagnostics/readback, not primary control.

### `0x0002` and `0x0018` ? limit switch/input status

Live-confirmed:

- limit open: `reg 0x0002 = 0x0002`, `reg 0x0018 = 0x0000`
- limit pressed: `reg 0x0002 = 0x0000`, `reg 0x0018 = 0x0001`

Pressing the limit while moving briefly pauses the motor.  These are input/status
registers, not motor command registers.

## Not yet fully resolved

The core user-facing motor controls are clear enough for practical use:

- direction/start: `0x000B`
- speed magnitude: `0x001F`
- inhibit/stop by speed override: `0x001F=1`
- clear override: `0x001F=0`
- unlock/error clear: `0x0009=0x1234`

What is not fully resolved yet:

1. exact semantics of every status phase value in `0x0001`, `0x0020`, `0x0021`,
   `0x0024`;
2. exact physical meaning of `0x0013/0x0014` thresholds;
3. whether `0x0005=0xAA11` is used by the original controller as a homing/special
   phase command rather than ordinary run command;
4. safe, explicit "stop and clear motion state" command separate from the
   confirmed PWM inhibit `0x001F=1`.

## 2026-09-11 slave-3 CW no-response clarification

A read-only snapshot taken after a direction test showed:

```text
0x0001 = 0x0002
0x000B = 0x0000
0x0017 = 0x0000
0x0018 = 0x0000
0x001F = 0x0001
```

Therefore the direction register was not sufficient to infer whether the
clockwise path ran.  `0x001F=1` was still active; because the PWM helper treats
this as a nonzero numerator override, the calculated duty is effectively zero.
The absence of current increase is consequently explained by the active PWM
inhibit.  A valid clockwise test must clear `0x001F` first, with the warning
that clearing it can resume a previously latched motion state.

## Direction confirmation update — 2026-09-12

After setting the moderate speed override and issuing:

```text
03 10 00 1F 00 01 02 14 82 32 3E
03 10 00 0B 00 01 02 00 00 BE 4B
```

the operator confirmed physical expansion of slave 3.  Together with the
previous confirmed contraction test, the direction mapping is now established
for this joint:

```text
0x000B = 0xAA11  -> tightening / contraction
0x000B = 0x0000  -> expanding
```

Both directions use the same speed override register `0x001F`; direction is
applied later by the signed PWM path.  The test ended by restoring
`0x001F=0x0001`, so the joint was left PWM-inhibited.

So the important motor controls are identified, but the whole internal state
machine is not 100% named yet.
