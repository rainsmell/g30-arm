"""Dump every USB descriptor byte of the arm camera to a text file (raw hex)."""
import ctypes as C, uuid, io, sys
from ctypes import POINTER, byref, c_uint8, c_uint16, c_int, c_void_p

lib = C.WinDLL("libusb-1.0.dll")
class DD(C.Structure):
    _fields_=[("bLength",c_uint8),("bDescriptorType",c_uint8),("bcdUSB",c_uint16),("bDeviceClass",c_uint8),
              ("bDeviceSubClass",c_uint8),("bDeviceProtocol",c_uint8),("bMaxPacketSize0",c_uint8),
              ("idVendor",c_uint16),("idProduct",c_uint16),("bcdDevice",c_uint16),("iManufacturer",c_uint8),
              ("iProduct",c_uint8),("iSerialNumber",c_uint8),("bNumConfigurations",c_uint8)]
class EP(C.Structure):
    _fields_=[("bLength",c_uint8),("bDescriptorType",c_uint8),("bEndpointAddress",c_uint8),("bmAttributes",c_uint8),
              ("wMaxPacketSize",c_uint16),("bInterval",c_uint8),("bRefresh",c_uint8),("bSynchAddress",c_uint8),
              ("extra",POINTER(c_uint8)),("extra_length",c_int)]
class ID(C.Structure):
    _fields_=[("bLength",c_uint8),("bDescriptorType",c_uint8),("bInterfaceNumber",c_uint8),("bAlternateSetting",c_uint8),
              ("bNumEndpoints",c_uint8),("bInterfaceClass",c_uint8),("bInterfaceSubClass",c_uint8),
              ("bInterfaceProtocol",c_uint8),("iInterface",c_uint8),("endpoint",POINTER(EP)),
              ("extra",POINTER(c_uint8)),("extra_length",c_int)]
class IF(C.Structure): _fields_=[("altsetting",POINTER(ID)),("num_altsetting",c_int)]
class CD(C.Structure):
    _fields_=[("bLength",c_uint8),("bDescriptorType",c_uint8),("wTotalLength",c_uint16),("bNumInterfaces",c_uint8),
              ("bConfigurationValue",c_uint8),("iConfiguration",c_uint8),("bmAttributes",c_uint8),("bMaxPower",c_uint8),
              ("interface",POINTER(IF)),("extra",POINTER(c_uint8)),("extra_length",c_int)]
for f,a,r in [("libusb_init",[POINTER(c_void_p)],c_int),("libusb_get_device_list",[c_void_p,POINTER(POINTER(c_void_p))],C.c_ssize_t),
              ("libusb_get_device_descriptor",[c_void_p,POINTER(DD)],c_int),
              ("libusb_get_config_descriptor",[c_void_p,c_uint8,POINTER(POINTER(CD))],c_int),
              ("libusb_get_device_speed",[c_void_p],c_int),
              ("libusb_free_device_list",[POINTER(c_void_p),c_int],None),("libusb_free_config_descriptor",[POINTER(CD)],None)]:
    fn=getattr(lib,f); fn.argtypes=a; fn.restype=r

def hx(ptr, n):
    return bytes(bytearray(ptr[i] for i in range(n))).hex() if ptr and n else ""

out = io.StringIO()
ctx=c_void_p(); lib.libusb_init(byref(ctx))
lst=POINTER(c_void_p)(); n=lib.libusb_get_device_list(ctx,byref(lst))
SPEED={0:"unknown",1:"Low",2:"Full",3:"High",4:"Super"}
for i in range(n):
    dd=DD(); lib.libusb_get_device_descriptor(lst[i],byref(dd))
    if dd.idVendor!=0x0c45 or dd.idProduct!=0x636b: continue
    out.write("### DEVICE DESCRIPTOR (18 bytes)\n")
    out.write("bcdUSB=0x%04x bDeviceClass=0x%02x bDeviceSubClass=0x%02x bDeviceProtocol=0x%02x bMaxPacketSize0=%d\n"
              % (dd.bcdUSB, dd.bDeviceClass, dd.bDeviceSubClass, dd.bDeviceProtocol, dd.bMaxPacketSize0))
    out.write("idVendor=0x%04x idProduct=0x%04x bcdDevice=0x%04x iMan=%d iProd=%d iSer=%d bNumConfigurations=%d\n"
              % (dd.idVendor, dd.idProduct, dd.bcdDevice, dd.iManufacturer, dd.iProduct, dd.iSerialNumber, dd.bNumConfigurations))
    out.write("USB speed: %s\n\n" % SPEED.get(lib.libusb_get_device_speed(lst[i]),"?"))
    cp=POINTER(CD)(); lib.libusb_get_config_descriptor(lst[i],0,byref(cp)); c=cp.contents
    out.write("### CONFIGURATION 1\n")
    out.write("wTotalLength=%d bNumInterfaces=%d bmAttributes=0x%02x bMaxPower=%dmA\n\n"
              % (c.wTotalLength, c.bNumInterfaces, c.bmAttributes, c.bMaxPower*2))
    for ii in range(c.bNumInterfaces):
        iface=c.interface[ii]
        for ai in range(iface.num_altsetting):
            d=iface.altsetting[ai]
            # rebuild standard interface descriptor bytes
            std=bytes([d.bLength,d.bDescriptorType,d.bInterfaceNumber,d.bAlternateSetting,d.bNumEndpoints,
                       d.bInterfaceClass,d.bInterfaceSubClass,d.bInterfaceProtocol,d.iInterface])
            ex=bytes(bytearray(d.extra[k] for k in range(d.extra_length))) if d.extra_length else b""
            out.write("--- INTERFACE %d alt %d : class=0x%02x sub=0x%02x proto=0x%02x nEndpoints=%d ---\n"
                      % (d.bInterfaceNumber, d.bAlternateSetting, d.bInterfaceClass, d.bInterfaceSubClass,
                         d.bInterfaceProtocol, d.bNumEndpoints))
            out.write("std: %s\n" % std.hex())
            for e in range(d.bNumEndpoints):
                ep=d.endpoint[e]
                estd=bytes([ep.bLength,ep.bDescriptorType,ep.bEndpointAddress,ep.bmAttributes,
                            ep.wMaxPacketSize & 0xFF, (ep.wMaxPacketSize>>8)&0xFF, ep.bInterval,
                            ep.bRefresh, ep.bSynchAddress])
                out.write("  ep: %s   (addr=0x%02x attr=0x%02x wMaxPacketSize=%d int=%d)\n"
                          % (estd.hex(), ep.bEndpointAddress, ep.bmAttributes, ep.wMaxPacketSize, ep.bInterval))
            if ex:
                out.write("class-specific (%d bytes):\n%s\n" % (len(ex), ex.hex()))
            out.write("\n")
    lib.libusb_free_config_descriptor(cp)
    break
lib.libusb_free_device_list(lst,1)

txt = out.getvalue()
print(txt)
open("descriptors.txt","w",encoding="utf-8").write(txt)
