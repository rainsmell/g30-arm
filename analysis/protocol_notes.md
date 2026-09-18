# GD32F103 RS485 firmware notes

## Confirmed

- Flash image size: 131072 bytes.
- ARM Thumb firmware.
- CRC helper at `0x08008b34` is a table-driven CRC16 with initial value `0xFFFF`.
- TX helper at `0x0800b2cc` writes a buffer to the UART/RS485 transmit path.
- Main protocol parser/dispatcher is `0x08008ffc`.
- Invalid frames print `FunCode Wrong!` / `CRC Wrong!`.
- `func=0x03` reads registers.
- `func=0x06` writes a single register / triggers control branches.
- `func=0x10` writes multiple registers.

## Frame format

- Normal address byte: `0x01`.
- Special maintenance/reset frame address: `0xAA`.
- CRC is appended as 2 bytes at the end.
- The code treats the protocol as Modbus-RTU-like, but several opcodes are custom.

## Function codes seen

- `0x03` read registers
- `0x06` write single register / control
- `0x10` write multiple registers

## Single-write register numbers seen under `func=0x06`

- `0x0005`
- `0x0009`
- `0x000B`
- `0x0014`
- `0x001D`
- `0x001E`
- `0x001F`

## Special values

- `0x1234` is checked as an unlock / clear-state marker.
- `0xAA55` appears in the `address=0xAA, func=0x06` reset path.
- `0xAA11` appears in a single-write control path.
- `reg=0x0009, value=0x1234` clears a latched state word.

## Memory blocks

- `0x20000115`: ready/armed flag
- `0x20000114`: received length byte
- `0x200001A2`: RX scratch buffer
- `0x20000118`: state/config block, includes the `+0x12` mode flag
- `0x20000158`: register map used by read/write commands
- `0x200002A2`: TX buffer

## Current best interpretation

1. `0x03` returns register values from `0x20000158`.
2. `0x10` writes multiple registers into `0x20000158`.
3. Several other function codes write a single register or trigger control actions.
4. A special `AA 06 00 05 AA 55` frame triggers reset / bootloader-style action.
5. `0x06 / reg 0x0009 / value=0x1234` appears to clear an internal latch.

## Bench probe result

- Tried safe read-only `0x03` probes on COM4.
- Scanned slave IDs `1..32` and `1..247`.
- Tested common UART settings: `9600/19200/38400/57600/115200`, `8N1` and `8E1`.
- No response observed.

This usually means one of:

1. The physical RS485 wiring / adapter direction control is wrong.
2. The board is not powered or not on that bus.
3. The protocol framing is not the simple `0x03` read we inferred.
4. The device uses another serial port than the one currently exposed on COM4.

## Live slave-1 reads

Observed stable values with working GND:

- `0x0013 = 0x00B4`
- `0x0014 = 0x03E8`
- `0x0019 = 0x0001`
- `0x001A = 0x0001`
- `0x001B = 0x67C6`
- `0x001C = 0xD7FB`
- `0x0022 = 0x0BCD`
- `0x0023 = 0x461F`
- `0x0025 = 0x0301`

## Limit switch result

Comparing open vs pressed snapshots:

- `0x0002`: `0x0002 -> 0x0000`
- `0x0018`: `0x0000 -> 0x0001`

This strongly suggests:

- `0x0002` is the raw limit input bitfield
- `0x0018` is the debounced/latched limit state

So the limit switch is active-low or inverted in firmware.

## Motion command candidate

Observed accepted frame:

```
01 06 00 05 00 01 58 0B
```

Effect:

- frame echoed back unchanged
- the earlier observed rotation may have been delayed or correlated with a different command

Current interpretation:

- register `0x000B` with value `0xAA11` is the confirmed motion trigger path
- `0x0005` was a false lead for motion control
- value `0x0001` on `0x0005` is not the actual start command
- value `0x0000` is the current best stop candidate
- value `0x0002` is the current best reverse/CW candidate
- latest single-frame test with `0x0001` did not visibly re-start motion, so the earlier rotation was likely delayed or correlated

## Static motor-control evidence

- `0x0800b220` is the PWM direction helper.
  - `r0 != 1` => reject
  - signed `r1 > 0` => drive channel 2, clear channel 3, return `1`
  - signed `r1 < 0` => clear channel 2, drive channel 3, return `2`
  - `r1 == 0` => clear both channels, return `0`
  - clamps magnitude to `±100`
  - this means reverse is ultimately sign-based; a raw register value of `0x0002` is only a candidate until the live motor response is confirmed
- `0x08008c30` is the periodic motion state machine.
  - It uses the state block at `0x20000118`.
  - `state[5]` is the active motion state flag; `1`/`2` mean motion in progress, `0` means idle/stop.
  - The loop calls `0x0800b220` after computing a signed duty value, so reverse is implemented by sign rather than a separate peripheral bit.
- `0x080090fa` / `0x080091b6` gate write access with `state[0x12] == 0`.
  - If that word is nonzero, motion writes are rejected.
  - This is the main runtime precondition I found for motion commands.

## Safe control frames tried

- Stop candidate: `01 06 00 05 00 00 99 CB`
- Reverse candidate: `01 06 00 05 00 02 18 0A`
- `01 0B 00 05 AA 11 0A A6` did **not** visibly move the motor
- Confirmed motion trigger: `01 06 00 0B AA 11 46 A4`
- Stop candidate re-tested while running: `01 06 00 05 00 00 99 CB`

## Maintenance path

- `0x08009308` handles function code `0x06`
- In the `0x06` path, value `0xAA11` hits the motion/control branch
- `0xAA55` branches to `0x08008ED8`
- This looks like a maintenance/reset path, so I have not used it yet
- After sending `AA 06 00 05 AA 55`, the readable status registers I checked did not visibly change

## Latest confirmed behavior

- `01 06 00 0B AA 11 46 A4` caused counterclockwise rotation
- `01 06 00 0B 00 00 F8 08` was observed to cause clockwise rotation
- `01 06 00 0B 22 AA 60 D7` caused a direction change / reverse
- The `0x000B` register is now the confirmed direction-control path
- `01 06 00 05 AA 11 27 67` was accepted but produced no visible motion by itself
- After reboot, `01 10 00 0B 00 01 02 AA 11 19 87` did trigger motion again
- User-side retest after power/power-supply adjustment: `01 10 00 0B 00 01 02 AA 11 19 87` started counterclockwise motion
- `01 10 00 0B 00 01 02 00 00 A7 2B` did **not** stop; later reboot retests show it consistently starts clockwise, with no clear low-speed behavior
- `01 10 00 0B 00 01 02 22 AA 3F F4` is not a universal stop command:
  - from one previous state it appeared to stop
  - from current counterclockwise state it produced no visible change
  - after reboot, sending it immediately started clockwise rotation
- `01 10 00 1F 00 01 02 00 01 65 FF` stopped the motor.
  - User also observed that if this is the first command after power-up, the motor no longer responds to the known start/direction frames and remains stopped.
  - This makes `reg 0x001F` look like a PWM override / inhibit / soft-lock parameter rather than a simple one-shot stop.
- `01 10 00 1F 00 01 02 00 00 A4 3F` cleared that inhibit/soft-lock condition and restored motor running.
- `01 06 00 05 00 00 99 CB` changed internal state (`0x0002` settled to `0x000B`) but physical stop still needs confirmation
- New live test after reboot:
  - `01 06 00 05 AA 11 27 67`
  - echoed back unchanged
  - readback immediately after: `0x0005 = 0xAA11`, `0x0002 = 0x0004`
  - visual motion: none
- New live observation:
  - while the motor is running, pressing the limit switch makes it pause briefly
  - this strongly suggests the limit input is sampled inside the periodic motion service, not just in the serial parser

## Correction from deeper static analysis

- The earlier `0x0005/0x000B` stop guess was wrong.
- In `func=0x06`, the `reg=0x000B` path is a direction/start path, not a direct stop opcode.
- In the same function, `reg=0x0005` uses special values `0xAA11` and `0x22AA` for another control branch, but it still does not expose a simple stop frame.
- The actual stop action I found is internal to the periodic state machine at `0x08008C30`, where it calls the PWM helper with `speed=0`.
- I have not yet found a serial frame that directly reaches that stop call.
- Current live evidence suggests `0x0005 = 0xAA11` alone is not enough to move the motor; it may be a state-seed or arming word that needs a follow-up command.
- `func=0x10` on `reg 0x000B` looks more reliable than the `func=0x06` path for starting motion after a clean reboot.
- `0x10 / reg 0x000B / value 0x0000` is not stop; it is a clockwise-start command in reboot retests.
- `0x10 / reg 0x000B / value 0x22AA` is now best interpreted as a direction/state command, not stop. It can start clockwise motion after reboot.
- `0x10 / reg 0x001F / value 0x0001` is a confirmed motor inhibit/stop condition in live testing.
  - Static backing: helper `0x0800b004` reads register-map offset `0x3E` (`reg 0x001F`) and, when nonzero, uses it as an override in PWM calculation.
  - Therefore nonzero `0x001F` can suppress effective PWM for later direction commands too.
- `0x10 / reg 0x001F / value 0x0000` clears the inhibit and allows the already-commanded motor state to resume.

## Code artifacts

- Raw disassembly/report: `analysis/protocol_report.md`
- Scan script: `analysis/protocol_scan.py`
- Ghidra script: `analysis/scripts/DumpProtocol.java`
