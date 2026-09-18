# gd32_firmware_2.bin: first static analysis

## Identity and tools

- Size: 131072 bytes. SHA256:
  b006b291ab2c1b9b9973f5f48f111c5f1d5ced39afa043e2be8573b55924edd2.
- Boot vectors at 0x08000000; application vectors at 0x08008000.
  Application SP=0x20007C00, reset=0x08009728 (Thumb pointer has bit 0 set).
- Hardware floating-point instructions are present on executed application paths
  (e.g. 0x0800C49A). This is not compatible with a plain Cortex-M3 GD32F103.
  An FPU-capable Thumb MCU is indicated; exact package marking is still needed.
- Metadata at 0x0801FC00 reports version 0.1.0 and motor ID 3;
  its printer is at 0x0800C582..0x0800C5A2. Metadata does not mean a single bus address.
- Capstone annotated Thumb disassembly, Python struct/hash/CRC checks and direct
  data-flow inspection were used. Ghidra source/build artifacts exist locally,
  but no ready distribution was found; no new installation was needed this pass.
- `python analysis/inspect_firmware2.py` regenerates inventory and linear listing.
  The linear listing includes literal pools/strings interpreted as instructions;
  only traced code paths cited below are evidence. No serial commands were sent.

## One image implements two logical slaves

Dispatcher 0x0800E46C explicitly rejects slave IDs 1,4,5,6 in its 03/06/10 paths.
Ordinary active banks here are slave 2 and slave 3. Register storage is:

    base(slave) = 0x2000063C + (slave - 1) * 0x48
    address(reg) = base(slave) + 2 * reg
    write_flag = 0x200007EC + (slave - 1) * 0x24 + reg

Slave 2 base=0x20000684; slave 3 base=0x200006CC.
Evidence: read paths 0x0800E54C/0x0800E58A and write10 paths
0x0800E8AE/0x0800E8D0. Function 06 also sets write flags, using only the low
register-address byte in the ordinary-bank path; function 10 uses the full start.

Each bank has 0x24 words, i.e. registers 0x0000..0x0023. The read implementation
does not stop at the bank boundary. Reading slave 2 reg24/reg25 actually accesses
slave 3 reg0/reg1. This explains the earlier slave-2 snapshot's reg25=1.
Historical extended reads were read-only, but those last two columns must not be
interpreted as independent registers of that slave. Do not write across banks.

Default slave-2 values at 0x0800BB48 are enable=1, reg13=700, reg14=400.
0x0800B42C installs build words 67C6/D81D in both banks, matching the captured
slave-2 snapshot. Slave-3 init at 0x0800C51C installs mode=1, enable=1,
speed=732, reg13=1500 and reg14=1000, matching the earlier position-controlled family.
This image explains slave 2/3; it does NOT itself respond as slave 4.

## Serial and CRC

- 0x0800AC3C loads baud 0x1C200=115200 for USART at 0x40004800.
- 0x0800A084 receives via DMA, buffer=0x2000043C; length=0x20000108,
  ready flag=0x20000109. Dispatcher runs in the main loop (0x0800C5BA).
- 0x080089F8 uses two 256-byte CRC lookup tables at 0x0800E9E4 and
  0x0800EAE4. Replayed table calculations match Modbus CRC on three sample frames.
  The helper returns swapped representation; on-wire CRC remains low byte first.
- Register payload is big-endian; functions 03,06,10 are present.
- Slave AA/function06/reg5=AA55 reaches system reset at 0x0800E7F0: avoid.
- Error states may suppress read replies except reads starting at 17/19/1B/1C
  (0x0800E66A..0x0800E6A2). A bulk-read timeout does not prove a dead serial link.

## Slave 3 feedback: direct source mapping

Publisher 0x0800C484 runs from SysTick at 0x08009F72.

| Register | Source | Interpretation / confidence |
| --- | --- | --- |
| 0000 | int16 at 200002E8 | Signed speed feedback; direct load plus control-loop use |
| 0001 | low16 of int32 at 200002DC | Position accumulation; high word is not reg0000 |
| 0002 | halfword at 20000298 | Filtered current-related control feedback |
| 0003 | float at 200001B8, converted to integer | ADC-derived temperature-like polynomial output |
| 0004 | same source as 0002 | Separate read of same feedback, explaining rare mismatch |
| 0017 | halfword at 200002BE | Fault code |
| 0018 | byte at 20000111 | Limit input sampled from PA15, polarity depends on board detection |

0x08008C6C samples PB6/PB7/PB8 as a three-bit state. 0x08008A3A maps changes
through a six-state order table, choosing direction +/-1. 0x0800B80C handles
transitions and 0x0800B858 increments the 32-bit position by signed direction.
TIMER3 (0x40000800) interrupt is 0x0800A024. This is strong evidence of
three-Hall feedback, with position counted from commutation transitions rather
than a high-resolution absolute joint encoder. Exact counts/revolution and
gear ratio are not yet decoded. FOC-like transforms and three-phase TIMER0 PWM
exist (e.g. 0x08008A7E, 0x08008FC6, 0x0800C1C0).

The user's 32-bit suspicion was partly right: position IS internally 32-bit,
but only its low half is exposed at reg1; reg0 independently reports speed.

Temperature-like chain: ADC DMA data at 20000280+2 -> 0x0800B508 polynomial ->
float 20000174+44 (0x08008ADE) -> reg3 (0x0800C498).
Physical sensor and temperature unit remain unconfirmed.

## Slave 3 control registers

| Register | Static behavior | Evidence |
| --- | --- | --- |
| 0005=AA11 | Homing-style branch, target -2700, limit/time checks and position reset | C302..C3C4 |
| 0005=A5A5 | Automatic alternating speed test | C3C6, C14C; avoid |
| 0007=1 | Position-target mode | C3E8 |
| 0007=2 | Continuous signed speed mode; re-enables outputs every service | C42C; do not casually switch |
| 0008=0 | Disable branch on write; resets control terms and tracks present position | C2BC..C2F0, C448 |
| 0008=1 | Enable output branch | C2F2..C302 |
| 0009=1234 | Conditional fault clear, reinitialization | C26C..C2B8; not generic start |
| 000A | Position-mode speed envelope uses absolute nonzero value; speed mode uses signed value | C3FA..C444, D260 |
| 000B | int16 target position, sign-extended into int32 target; write also enables outputs | C3DC..C3F6 |
| 0013 | value/1000.0 -> internal float; fallback 1.5 | C4BA..C4EE |
| 0014 | (value<<4) -> internal halfword; fallback 16000 | C4D6..C4FC |
| 001F | No direct PWM override read in traced motor-control paths | Contrast with first image; do not call it a stop |

IMPORTANT: enable=0 is conditional, not an unconditional emergency stop. Pending
target writes, homing/test state or continuous speed mode can re-enable outputs
later in the same service. Validate mode and command state before live use.
Zero speed in position-mode setter retains the previous nonzero envelope; it is
not a reliable stop. Avoid -32768 due to int16 absolute-value overflow.

Previously sent regB=AA11 is a target of -21999, not a direction enum.
regB=22AA is a target of +8874; regB=0 requests zero. These targets are not
calibrated to mechanical travel and can drive the joint into an endpoint.

## Explanation for delayed reversal

Position loop at 0x0800B1DC calls 0x0800E9CA to approach the requested target
with a finite step, storing an internal trajectory at CONTROL+14. It subtracts
measured Hall position, then limits a speed demand (0x0800B1EC..0x0800B28C).
The trajectory can continue toward an unreachable negative target while the
physical motor is stalled. After writing target zero, that internal trajectory
must return past actual position before the requested direction changes.
This is a code-backed explanation for long delayed reversal. Speed-loop PI and
limit logic also exist; exact 8-9 second timing has not yet been reproduced from
decoded update frequencies/parameters. It is not established as a fixed timer.

## Slave 2 is a different motor path

0x0800BBC4 is the second state machine; publisher=0x0800BFE8.
0x0800CD1C accepts motor ID 2 and signed PWM request clamped to +/-100;
positive and negative select TIMER2 channels 1 and 0 (0x40000400).
This supports a bidirectional DC-driver path, distinct from the Hall/FOC path.
Physical up/down mapping remains untested.

- reg5=AA11 seeds its state machine (0x0800BC38), not the same as slave-3 regB.
- reg8=0 leads to signed PWM zero in the normal no-fault path (0x0800BD06).
- regB is compared to 0 or 1 (0x0800BD1E), not treated as int16 position.
- PA11 and PB1 are input paths at 0x0800BB6C and 0x0800BBAC; wiring roles need checking.
- The old `test_joint2.py` AA11-at-regB / reg1F stop assumptions are unsupported
  by this firmware and should not be reused for a motion trial unchanged.

## Frames generated offline, not transmitted or mechanically validated

```text
Slave 2 reg8=0:    02 10 00 08 00 01 02 00 00 B3 E8
Slave 3 reg8=0:    03 10 00 08 00 01 02 00 00 BE 78
Slave 3 regA=366:  03 10 00 0A 00 01 02 01 6E 3F E6
Slave 3 regB=-100: 03 10 00 0B 00 01 02 FF 9C FF D2
```

All use [slave][10][register:2][quantity:0001][bytecount:02][value:2][CRC:2].
The target frame enables outputs; this is not a recommendation to send it now.

## Remaining work

- Confirm actual MCU marking and which physical board supplied the image.
- Decode Hall speed units, PWM/loop rates, trajectory step and current scaling.
- Complete slave-2 startup/direction/stop state guards before rewriting its trial.
- Verify slave-3 small position and speed-envelope commands with short, supervised
  trials only after mechanically safe state is established.
- Dump slave-4 firmware before assigning this image's exact code to slave 4.
- Ghidra decompilation and deeper control-loop reconstruction can extend the
  saved Capstone evidence. No claim is made that the whole firmware is recovered.
