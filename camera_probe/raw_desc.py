import ctypes as C
from ctypes import POINTER, byref, c_uint8, c_uint16, c_int, c_void_p, c_char_p
import uuid

lib = C.WinDLL("libusb-1.0.dll")

class DeviceDescriptor(C.Structure):
    _fields_ = [("bLength", c_uint8), ("bDescriptorType", c_uint8), ("bcdUSB", c_uint16),
                ("bDeviceClass", c_uint8), ("bDeviceSubClass", c_uint8), ("bDeviceProtocol", c_uint8),
                ("bMaxPacketSize0", c_uint8), ("idVendor", c_uint16), ("idProduct", c_uint16),
                ("bcdDevice", c_uint16), ("iManufacturer", c_uint8), ("iProduct", c_uint8),
                ("iSerialNumber", c_uint8), ("bNumConfigurations", c_uint8)]

class EndpointDescriptor(C.Structure):
    _fields_ = [("bLength", c_uint8), ("bDescriptorType", c_uint8), ("bEndpointAddress", c_uint8),
                ("bmAttributes", c_uint8), ("wMaxPacketSize", c_uint16), ("bInterval", c_uint8),
                ("bRefresh", c_uint8), ("bSynchAddress", c_uint8),
                ("extra", POINTER(c_uint8)), ("extra_length", c_int)]

class InterfaceDescriptor(C.Structure):
    _fields_ = [("bLength", c_uint8), ("bDescriptorType", c_uint8), ("bInterfaceNumber", c_uint8),
                ("bAlternateSetting", c_uint8), ("bNumEndpoints", c_uint8), ("bInterfaceClass", c_uint8),
                ("bInterfaceSubClass", c_uint8), ("bInterfaceProtocol", c_uint8), ("iInterface", c_uint8),
                ("endpoint", POINTER(EndpointDescriptor)), ("extra", POINTER(c_uint8)), ("extra_length", c_int)]

class Interface(C.Structure):
    _fields_ = [("altsetting", POINTER(InterfaceDescriptor)), ("num_altsetting", c_int)]

class ConfigDescriptor(C.Structure):
    _fields_ = [("bLength", c_uint8), ("bDescriptorType", c_uint8), ("wTotalLength", c_uint16),
                ("bNumInterfaces", c_uint8), ("bConfigurationValue", c_uint8), ("iConfiguration", c_uint8),
                ("bmAttributes", c_uint8), ("bMaxPower", c_uint8),
                ("interface", POINTER(Interface)), ("extra", POINTER(c_uint8)), ("extra_length", c_int)]

lib.libusb_init.argtypes = [POINTER(c_void_p)]
lib.libusb_init.restype = c_int
lib.libusb_get_device_list.argtypes = [c_void_p, POINTER(POINTER(c_void_p))]
lib.libusb_get_device_list.restype = C.c_ssize_t
lib.libusb_get_device_descriptor.argtypes = [c_void_p, POINTER(DeviceDescriptor)]
lib.libusb_get_device_descriptor.restype = c_int
lib.libusb_get_config_descriptor.argtypes = [c_void_p, c_uint8, POINTER(POINTER(ConfigDescriptor))]
lib.libusb_get_config_descriptor.restype = c_int
lib.libusb_free_device_list.argtypes = [POINTER(c_void_p), c_int]
lib.libusb_free_config_descriptor.argtypes = [POINTER(ConfigDescriptor)]
lib.libusb_get_bus_number.argtypes = [c_void_p]
lib.libusb_get_bus_number.restype = c_uint8
lib.libusb_get_device_address.argtypes = [c_void_p]
lib.libusb_get_device_address.restype = c_uint8

VC_SUBTYPE = {1: "VC_HEADER", 2: "VC_INPUT_TERMINAL", 3: "VC_OUTPUT_TERMINAL",
              4: "VC_SELECTOR_UNIT", 5: "VC_PROCESSING_UNIT", 6: "VC_EXTENSION_UNIT",
              7: "VC_ENCODING_UNIT"}
TERM_TYPES = {0x0101: "ITT_VENDOR_SPECIFIC", 0x0201: "ITT_CAMERA",
              0x0401: "OTT_VENDOR_SPECIFIC", 0x0501: "OTT_STREAMING",
              0x0301: "STT_VENDOR_SPECIFIC", 0x0302: "STT_STREAMING"}
VS_SUBTYPE = {1: "VS_INPUT_HEADER", 2: "VS_OUTPUT_HEADER", 3: "VS_STILL_IMAGE_FRAME",
              4: "VS_FORMAT_UNCOMPRESSED", 5: "VS_FRAME_UNCOMPRESSED",
              6: "VS_FORMAT_MJPEG", 7: "VS_FRAME_MJPEG", 8: "VS_FORMAT_MPEG2TS",
              9: "VS_FORMAT_FRAME_BASED", 10: "VS_FRAME_FRAME_BASED"}

def extra_bytes(ptr, length):
    return bytes(bytearray(ptr[i] for i in range(length))) if ptr and length else b""

ctx = c_void_p()
lib.libusb_init(byref(ctx))
lst = POINTER(c_void_p)()
n = lib.libusb_get_device_list(ctx, byref(lst))
target = None
for i in range(n):
    dd = DeviceDescriptor()
    lib.libusb_get_device_descriptor(lst[i], byref(dd))
    if dd.idVendor == 0x0c45 and dd.idProduct == 0x636b:
        target = lst[i]
        print(f"FOUND bus={lib.libusb_get_bus_number(lst[i])} addr={lib.libusb_get_device_address(lst[i])} "
              f"bcdUSB={dd.bcdUSB:#06x} class={dd.bDeviceClass:#04x}/{dd.bDeviceSubClass:#04x}/{dd.bDeviceProtocol:#04x} "
              f"bNumConfigurations={dd.bNumConfigurations}")
        break

if target:
    cp = POINTER(ConfigDescriptor)()
    r = lib.libusb_get_config_descriptor(target, 0, byref(cp))
    print("get_config_descriptor ret", r, "wTotalLength", cp.contents.wTotalLength,
          "bNumInterfaces", cp.contents.bNumInterfaces)
    for ii in range(cp.contents.bNumInterfaces):
        iface = cp.contents.interface[ii]
        for ai in range(iface.num_altsetting):
            idsc = iface.altsetting[ai]
            if ai == 0:
                print(f"\n--- Interface {idsc.bInterfaceNumber} alt {idsc.bAlternateSetting} "
                      f"class={idsc.bInterfaceClass:#x}/{idsc.bInterfaceSubClass:#x} "
                      f"nEp={idsc.bNumEndpoints} extraLen={idsc.extra_length}")
            ex = extra_bytes(idsc.extra, idsc.extra_length)
            if ex:
                print(f"    alt{idsc.bAlternateSetting} extra: {ex.hex()}")
                j = 0
                while j + 2 <= len(ex):
                    blen, btype = ex[j], ex[j + 1]
                    if blen == 0:
                        break
                    chunk = ex[j:j + blen]
                    if btype == 0x24:
                        st = chunk[2]
                        print(f"      CS_INTERFACE {VC_SUBTYPE.get(st, VS_SUBTYPE.get(st, st))}: {chunk.hex()}")
                        if st == 2:
                            print(f"         termId={chunk[3]} type={TERM_TYPES.get(int.from_bytes(chunk[4:6],'little'), hex(int.from_bytes(chunk[4:6],'little')))}")
                        elif st == 5:
                            print(f"         procUnitId={chunk[3]} bmControls={chunk[8:8+chunk[7]].hex() if len(chunk)>8 else '?'}")
                        elif st == 6:
                            uid = chunk[3]; g = uuid.UUID(bytes_le=chunk[4:20])
                            nctl = chunk[20]; nrin = chunk[21]
                            srcs = list(chunk[22:22 + nrin]); cs = chunk[22 + nrin]
                            ctrls = chunk[23 + nrin:23 + nrin + cs]
                            print(f"         >>> XU unitId={uid} GUID={g}")
                            print(f"         >>> bNumControls={nctl} bNrInPins={nrin} srcIDs={srcs} bControlSize={cs} bmControls={ctrls.hex()}")
                    elif btype == 0x25:
                        print(f"      CS_ENDPOINT: {chunk.hex()}")
                    else:
                        print(f"      desc {btype:#x}: {chunk.hex()}")
                    j += blen
    lib.libusb_free_config_descriptor(cp)

lib.libusb_free_device_list(lst, 1)
