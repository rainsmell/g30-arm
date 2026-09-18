# RS485 protocol map

## Envelope

```
[addr][func][payload...][crc_lo][crc_hi]
```

Observed normal address: `0x01`  
Observed special address: `0xAA`

## Dispatcher

Main parser: `0x08008ffc`

### Read

- `0x03`
- Reads from register map at `0x20000158`
- Response format is `01 03 <bytecount> ... CRC`

### Write / control

- `0x06`

Most of these end by echoing the request frame back.

### Multi-write

- `0x10`
- Writes multiple 16-bit registers into `0x20000158`
- Response is a fixed 8-byte confirmation

## Special actions

- `0x1234`: clear / unlock marker
- `0xAA11`: special motion-control value in the `func=0x06, reg=0x0005` path
- `0x22AA`: special motion-control / maintenance value in the `func=0x06, reg=0x0005` path
- `0xAA 06 00 05 AA 55`: special maintenance/reset frame
- `0x06 / reg 0x0009 / value 0x1234`: clear / unlock latch
- `0x06 / reg 0x000B`: direction-control path; nonzero vs zero selects opposite motion direction, not stop
- `0x06 / reg 0x0005 / value 0xAA11`: special motion-control path
- `0x06 / reg 0x0005 / value 0x22AA`: special maintenance/reset-like path
- live reboot test: `0x06 / reg 0x0005 / value 0xAA11` produced no visible motion

## Motion register

- `0x000B`: confirmed direction-control register
- `0x000B = 0x0000`: opposite direction to the nonzero case, not stop
- `0x0005 = 0xAA11`: special motion-control value
- `0x0005 = 0x22AA`: special maintenance/reset-like value
- Direct stop frame: not found yet

## Motor helper

- `0x0800b220`: signed PWM direction helper
  - positive => channel 2
  - negative => channel 3
  - zero => stop

## PWM/GPIO path

- PWM timer: `TIMER2` (`0x40000400`)
- PWM channels used: `CH2` and `CH3`
- CCR writer: `0x0800c1c6`
  - `r1=2` -> `CCR2`
  - `r1=3` -> `CCR3`
- Timer mode helpers:
  - `0x0800c178`
  - `0x0800c1e4`
- Timer init:
  - `0x0800c3e8`
  - observed config includes prescaler `0x23` and ARR `0x03e7`
- Startup chain:
  - `0x0800b02c` calls `0x0800c234` before the protocol dispatcher
- Clock/reset helper:
  - `0x0800c364`
  - resets/enables the timer base before PWM setup
- GPIO clock helper:
  - `0x0800b524`
- Likely pin pair:
  - default `TIMER2_CH2` / `TIMER2_CH3` map to `PB0` / `PB1`
  - I found no AFIO remap writes in the current binary search, so default mapping is the most likely one
- Boot-time GPIO wrappers:
  - `0x0800b148` uses pin code `0x25`
  - `0x0800b5b0` also touches `0x25` and `0x32`
  - these look like packed GPIO source IDs; `0x25`/`0x32` are plausible `GPIOC5`/`GPIOD2`

## Working buffers

- RX scratch: `0x200001A2`
- RX length byte: `0x20000114`
- Ready flag: `0x20000115`
- State/config: `0x20000118`
- Register map: `0x20000158`
- TX buffer: `0x200002A2`
