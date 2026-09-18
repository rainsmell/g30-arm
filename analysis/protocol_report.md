# Protocol report
Program: gd32_firmware.bin

## Function @ 08008b34
name: FUN_08008b34
entry: 08008b34
signature: undefined FUN_08008b34(void)
body: 08008b34 - 08008b59

08008b34: push     {r4,r5,r6,lr}
08008b36: ldr      r5
08008b38: movs     r2
08008b3a: mov      r3
08008b3c: add.w    r6
08008b40: b        0x08008b4e
08008b42: ldrb.w   r4
08008b46: eors     r4
08008b48: ldrb     r2
08008b4a: eors     r2
08008b4c: ldrb     r3
08008b4e: subs     r1
08008b50: uxth     r1
08008b52: bcs      0x08008b42
08008b54: orr.w    r0
08008b58: pop      {r4,r5,r6,pc}

## Function @ 08008ffc
name: FUN_08008ffc
entry: 08008ffc
signature: undefined FUN_08008ffc(void)
body: 08008ffc - 08009393

08008ffc: push     {r4,r5,r6,r7,r8,r9,r10,r11,r12,lr}
08009000: ldr.w    r10
08009004: movs     r4
08009006: ldrb.w   r0
0800900a: cmp      r0
0800900c: bne      0x080090f2
0800900e: ldr      r7
08009010: ldr      r5
08009012: mov      r0
08009014: ldrb     r2
08009016: ldrb     r1
08009018: ldrb     r0
0800901a: movs     r6
0800901c: subs     r0
0800901e: mov.w    r11
08009022: cmp      r2
08009024: beq      0x0800902c
08009026: cmp      r2
08009028: beq      0x08009118
0800902a: b        0x08009342
0800902c: mov      r9
0800902e: ldr.w    r8
08009032: ldr      r7
08009034: cmp      r1
08009036: beq      0x08009048
08009038: mov      r2
0800903a: cmp      r1
0800903c: ldrsh.w  r2
08009040: beq      0x080090fa
08009042: cmp      r1
08009044: bne      0x08009128
08009046: b        0x08009266
08009048: uxth     r1
0800904a: ldr      r0
0800904c: bl       0x08008b34
08009050: ldrb     r1
08009052: mov      r2
08009054: add      r1
08009056: ldrh.w   r1
0800905a: rev16    r1
0800905c: cmp      r0
0800905e: bne      0x0800915a
08009060: ldrh     r0
08009062: ldrh.w   r2
08009066: rev16    r1
08009068: ldrh.w   r0
0800906c: rev16    r0
0800906e: cbz      r2
08009070: cmp      r0
08009072: beq      0x08009080
08009074: cmp      r0
08009076: beq      0x08009080
08009078: cmp      r0
0800907a: beq      0x08009080
0800907c: cmp      r0
0800907e: bne      0x0800902a
08009080: ldr.w    r8
08009084: movs     r2
08009086: strb.w   r2
0800908a: movs     r2
0800908c: strb.w   r2
08009090: lsls     r2
08009092: strb.w   r2
08009096: b        0x080090b8
08009098: ldrh.w   r2
0800909c: add.w    r12
080090a0: lsrs     r3
080090a2: strb.w   r3
080090a6: adds     r4
080090a8: uxtb     r3
080090aa: add.w    r4
080090ae: adds     r0
080090b0: strb     r2
080090b2: adds     r3
080090b4: uxth     r0
080090b6: uxtb     r4
080090b8: subs     r1
080090ba: uxth     r1
080090bc: bcs      0x08009098
080090be: ldrb.w   r1
080090c2: ldr      r0
080090c4: adds     r1
080090c6: bl       0x08008b34
080090ca: add.w    r2
080090ce: lsrs     r1
080090d0: strb     r1
080090d2: adds     r4
080090d4: uxtb     r1
080090d6: add      r1
080090d8: strb     r0
080090da: ldrb.w   r0
080090de: adds     r0
080090e0: uxtb     r1
080090e2: ldr      r0
080090e4: bl       0x0800b2cc
080090e8: mov      r1
080090ea: ldr      r0
080090ec: bl       0x080088ce
080090f0: b        0x08009342
080090f2: b        0x08009350
080090f4: bl       0x0800a02c
080090f8: b        0x08009342
080090fa: ldrb.w   r1
080090fe: mov      r4
08009100: cmp      r1
08009102: beq      0x08009116
08009104: bgt      0x0800911c
08009106: cmp      r1
08009108: beq      0x080091b6
0800910a: cmp      r1
0800910c: beq      0x0800912c
0800910e: cmp      r1
08009110: beq      0x08009164
08009112: cmp      r1
08009114: bne      0x08009128
08009116: b        0x08009242
08009118: b        0x08009308
0800911c: cmp      r1
0800911e: beq      0x0800920e
08009120: cmp      r1
08009122: beq      0x0800920e
08009124: cmp      r1
08009126: beq      0x0800920e
08009128: adr      r0
0800912a: b        0x080090f4
0800912c: uxth     r1
0800912e: ldr      r0
08009130: bl       0x08008b34
08009134: ldrb     r1
08009136: add      r1
08009138: ldrh.w   r1
0800913c: rev16    r1
0800913e: cmp      r0
08009140: bne      0x0800905e
08009142: ldrh     r0
08009144: ldrb     r1
08009146: rev16    r0
08009148: strh.w   r0
0800914c: ldrh     r0
0800914e: sub.w    r1
08009152: subs     r1
08009154: bne      0x0800923c
08009156: strh     r6
08009158: b        0x0800915c
0800915a: b        0x08009264
0800915c: strh.w   r6
08009160: strh     r6
08009162: b        0x0800923c
08009164: cmp      r2
08009166: bne      0x080090f0
08009168: uxth     r1
0800916a: ldr      r0
0800916c: bl       0x08008b34
08009170: ldrb     r1
08009172: add      r1
08009174: ldrh.w   r1
08009178: rev16    r1
0800917a: cmp      r0
0800917c: bne      0x08009264
0800917e: ldrh     r0
08009180: ldrb     r1
08009182: rev16    r0
08009184: strh.w   r0
08009188: cbz      r0
0800918a: strb.w   r6
0800918e: str.w    r6
08009192: mov      r4
08009194: strh.w   r6
08009198: strh.w   r6
0800919c: ldrd     r1
080091a0: bl       0x0800b004
080091a4: strb     r0
080091a6: sxtb     r1
080091a8: movs     r0
080091aa: bl       0x0800b220
080091ae: bl       0x0800b2a8
080091b2: str      r0
080091b4: b        0x0800923c
080091b6: cmp      r2
080091b8: bne      0x08009240
080091ba: uxth     r1
080091bc: ldr      r0
080091be: bl       0x08008b34
080091c2: ldrb     r1
080091c4: add      r1
080091c6: ldrh.w   r1
080091ca: rev16    r1
080091cc: cmp      r0
080091ce: bne      0x08009264
080091d0: ldrh     r0
080091d2: ldrb     r1
080091d4: rev16    r0
080091d6: strh.w   r0
080091da: sub.w    r1
080091de: subs     r1
080091e0: beq      0x080091ee
080091e2: sub.w    r1
080091e6: subs     r1
080091e8: beq      0x08009210
080091ea: b        0x0800923c
080091ec: b        0x080091ee
080091ee: strb.w   r6
080091f2: str.w    r6
080091f6: mov      r4
080091f8: strh.w   r6
080091fc: strh.w   r6
08009200: ldrd     r1
08009204: bl       0x0800b004
08009208: strb     r0
0800920a: rsbs     r0
0800920c: b        0x080091a6
0800920e: b        0x08009242
08009210: cpsid    i
08009212: ldrh     r0
08009214: movw     r4
08009218: cmp      r0
0800921a: beq      0x08009228
0800921c: ldr      r1
0800921e: movs     r2
08009220: adds     r1
... truncated ...

## Function @ 0800b2cc
name: FUN_0800b2cc
entry: 0800b2cc
signature: undefined FUN_0800b2cc(void)
body: 0800b2cc - 0800b31d

0800b2cc: push     {r4,r5,r6,r7,r8,r9,r10,lr}
0800b2d0: ldr.w    r9
0800b2d4: mov      r5
0800b2d6: mov      r6
0800b2d8: mov.w    r8
0800b2dc: movs     r4
0800b2de: mov      r1
0800b2e0: mov      r0
0800b2e2: bl       0x0800ae80
0800b2e6: movs     r0
0800b2e8: bl       0x0800a85c
0800b2ec: ldr      r7
0800b2ee: b        0x0800b308
0800b2f0: ldrb     r1
0800b2f2: mov      r0
0800b2f4: bl       0x0800c55c
0800b2f8: movs     r1
0800b2fa: mov      r0
0800b2fc: bl       0x0800c5d6
0800b300: cmp      r0
0800b302: beq      0x0800b2f8
0800b304: adds     r4
0800b306: uxtb     r4
0800b308: cmp      r4
0800b30a: bcc      0x0800b2f0
0800b30c: movs     r0
0800b30e: bl       0x0800a85c
0800b312: mov      r1
0800b314: mov      r0
0800b316: pop.w    {r4,r5,r6,r7,r8,r9,r10,lr}
0800b31a: b.w      0x0800ae84


## String xrefs
### 0800936f
text:  FunCode Wrong!

addr: 0800936f
xrefs:

### 08009384
text: CRC Wrong!

addr: 08009384
xrefs:

### 08001528
text: Bootloader Version: %d.%d.%d

addr: 08001528
xrefs:
  0800119a -> 08001528 (PARAM)

### 0800b0e8
text: Firmware Version: %d.%d.%d

addr: 0800b0e8
xrefs:
  0800b09c -> 0800b0e8 (PARAM)

### 08001548
text: Build info:%s, motor ID: %d.

addr: 08001548
xrefs:
  080011b4 -> 08001548 (PARAM)

### 0800b108
text: Build info:%s, motor ID: %d, crc:0x%08X.

addr: 0800b108
xrefs:
  0800b0b6 -> 0800b108 (PARAM)

