# Stop / direction research

## Rechecked with Capstone

### `func=0x06`, `reg=0x0005`

- `value=0xAA11` -> special motion-control branch
- `value=0x22AA` -> maintenance/reset-like branch
- not a direct stop frame

### `func=0x06`, `reg=0x000B`

- zero/nonzero selects opposite motion directions
- not a direct stop frame

### True stop call

- `0x08008C30` calls `0x0800B220` with `r1=0`
- that is the only clear stop path I found
- I have not yet found a serial frame that directly reaches it

## Practical implication

- the earlier "stop candidate" was a false lead
- current protocol work should focus on finding the write that drives the periodic state machine into its zero-speed branch
