import usb.core, usb.util, struct, uuid

dev = usb.core.find(idVendor=0x0c45, idProduct=0x636b)
print("device:", hex(dev.idVendor), hex(dev.idProduct), "bcdUSB", hex(dev.bcdUSB),
      "class", hex(dev.bDeviceClass), hex(dev.bDeviceSubClass), hex(dev.bDeviceProtocol))

VC_SUBTYPE = {
    1: "VC_HEADER (Input Header)",
    2: "VC_INPUT_TERMINAL",
    3: "VC_OUTPUT_TERMINAL",
    4: "VC_SELECTOR_UNIT",
    5: "VC_PROCESSING_UNIT",
    6: "VC_EXTENSION_UNIT",
    7: "VC_ENCODING_UNIT",
}
TERM_TYPES = {
    0x0101: "ITT_VENDOR_SPECIFIC",
    0x0201: "ITT_CAMERA",
    0x0202: "ITT_MEDIA_TRANSPORT_INPUT",
    0x0401: "OTT_VENDOR_SPECIFIC",
    0x0501: "OTT_STREAMING",
    0x0301: "STT_VENDOR_SPECIFIC",
    0x0302: "STT_STREAMING",
}
STREAM_SUBTYPE = {
    1: "VS_INPUT_HEADER",
    2: "VS_OUTPUT_HEADER",
    3: "VS_STILL_IMAGE_FRAME",
    4: "VS_FORMAT_UNCOMPRESSED",
    5: "VS_FRAME_UNCOMPRESSED",
    6: "VS_FORMAT_MJPEG",
    7: "VS_FRAME_MJPEG",
}

cfg = dev[0]
print("config: value", cfg.bConfigurationValue, "bNumInterfaces", cfg.bNumInterfaces)

for intf in cfg:
    if intf.bAlternateSetting != 0:
        continue
    print(f"\n=== Interface {intf.bInterfaceNumber} alt {intf.bAlternateSetting} "
          f"class={intf.bInterfaceClass:#x}/{intf.bInterfaceSubClass:#x}/{intf.bInterfaceProtocol:#x}")
    extra = bytes(intf.extra)
    if not extra:
        print("  (no class-specific extra)")
        continue
    print("  raw extra:", extra.hex())
    i = 0
    while i + 2 <= len(extra):
        blen = extra[i]
        btype = extra[i + 1]
        if blen == 0:
            break
        chunk = extra[i:i + blen]
        if btype == 0x24:  # CS_INTERFACE
            st = chunk[2]
            name = VC_SUBTYPE.get(st, f"subtype {st}")
            print(f"  CS_INTERFACE {name}: {chunk.hex()}")
            if st == 2:  # input terminal
                tid = chunk[3]
                ttype = struct.unpack_from("<H", chunk, 4)[0]
                print(f"      -> terminal id={tid} type={TERM_TYPES.get(ttype, hex(ttype))}")
            elif st == 6:  # extension unit
                uid = chunk[3]
                guid = uuid.UUID(bytes_le=chunk[4:20])
                nctl = chunk[20]
                nr_in = chunk[21]
                srcs = list(chunk[22:22 + nr_in])
                ctrl_size = chunk[22 + nr_in]
                ctrls = chunk[23 + nr_in:23 + nr_in + ctrl_size]
                print(f"      -> XU unit id={uid}")
                print(f"      -> XU GUID = {guid}")
                print(f"      -> bNumControls={nctl} bNrInPins={nr_in} sources={srcs}")
                print(f"      -> bControlSize={ctrl_size} bmControls={ctrls.hex()}")
        elif btype == 0x25:  # CS_ENDPOINT
            print(f"  CS_ENDPOINT: {chunk.hex()}")
        else:
            print(f"  desc type {btype:#x}: {chunk.hex()}")
        i += blen

# also dump VS interface 1 alt 0 extra (still image frame etc)
print("\n=== VS interface 1 alt 1..n frame descriptors ===")
for intf in cfg:
    if intf.bInterfaceNumber == 1:
        ex = bytes(intf.extra)
        if ex:
            print(f"  alt {intf.bAlternateSetting} extra: {ex.hex()}")
