import ctypes as C, uuid, struct
from ctypes import POINTER, byref, c_uint8, c_uint16, c_int, c_void_p

lib = C.WinDLL("libusb-1.0.dll")

class DD(C.Structure):
    _fields_=[("bLength",c_uint8),("bDescriptorType",c_uint8),("bcdUSB",c_uint16),
              ("bDeviceClass",c_uint8),("bDeviceSubClass",c_uint8),("bDeviceProtocol",c_uint8),
              ("bMaxPacketSize0",c_uint8),("idVendor",c_uint16),("idProduct",c_uint16),
              ("bcdDevice",c_uint16),("iManufacturer",c_uint8),("iProduct",c_uint8),
              ("iSerialNumber",c_uint8),("bNumConfigurations",c_uint8)]
class EP(C.Structure):
    _fields_=[("bLength",c_uint8),("bDescriptorType",c_uint8),("bEndpointAddress",c_uint8),
              ("bmAttributes",c_uint8),("wMaxPacketSize",c_uint16),("bInterval",c_uint8),
              ("bRefresh",c_uint8),("bSynchAddress",c_uint8),("extra",POINTER(c_uint8)),("extra_length",c_int)]
class ID(C.Structure):
    _fields_=[("bLength",c_uint8),("bDescriptorType",c_uint8),("bInterfaceNumber",c_uint8),
              ("bAlternateSetting",c_uint8),("bNumEndpoints",c_uint8),("bInterfaceClass",c_uint8),
              ("bInterfaceSubClass",c_uint8),("bInterfaceProtocol",c_uint8),("iInterface",c_uint8),
              ("endpoint",POINTER(EP)),("extra",POINTER(c_uint8)),("extra_length",c_int)]
class IF(C.Structure):
    _fields_=[("altsetting",POINTER(ID)),("num_altsetting",c_int)]
class CD(C.Structure):
    _fields_=[("bLength",c_uint8),("bDescriptorType",c_uint8),("wTotalLength",c_uint16),
              ("bNumInterfaces",c_uint8),("bConfigurationValue",c_uint8),("iConfiguration",c_uint8),
              ("bmAttributes",c_uint8),("bMaxPower",c_uint8),
              ("interface",POINTER(IF)),("extra",POINTER(c_uint8)),("extra_length",c_int)]
for f,a,r in [("libusb_init",[POINTER(c_void_p)],c_int),
              ("libusb_get_device_list",[c_void_p,POINTER(POINTER(c_void_p))],C.c_ssize_t),
              ("libusb_get_device_descriptor",[c_void_p,POINTER(DD)],c_int),
              ("libusb_get_config_descriptor",[c_void_p,c_uint8,POINTER(POINTER(CD))],c_int),
              ("libusb_get_device_speed",[c_void_p],c_int),
              ("libusb_free_device_list",[POINTER(c_void_p),c_int],None),
              ("libusb_free_config_descriptor",[POINTER(CD)],None)]:
    fn=getattr(lib,f); fn.argtypes=a; fn.restype=r

SPEED={0:"unknown",1:"LOW(1.5M)",2:"FULL(12M)",3:"HIGH(480M)",4:"SUPER(5G)"}
ctx=c_void_p(); lib.libusb_init(byref(ctx))
lst=POINTER(c_void_p)(); n=lib.libusb_get_device_list(ctx,byref(lst))
target=None
for i in range(n):
    dd=DD(); lib.libusb_get_device_descriptor(lst[i],byref(dd))
    if dd.idVendor==0x0c45 and dd.idProduct==0x636b:
        target=lst[i]
        print("speed:",SPEED.get(lib.libusb_get_device_speed(lst[i])),"bcdUSB",hex(dd.bcdUSB),
              "bMaxPacketSize0",dd.bMaxPacketSize0,"bcdDevice",hex(dd.bcdDevice))
        break
cp=POINTER(CD)(); lib.libusb_get_config_descriptor(target,0,byref(cp))
c=cp.contents
print("wTotalLength",c.wTotalLength,"bMaxPower",c.bMaxPower*2,"mA")
for ii in range(c.bNumInterfaces):
    iface=c.interface[ii]
    for ai in range(iface.num_altsetting):
        d=iface.altsetting[ai]
        eps=[]
        for e in range(d.bNumEndpoints):
            ep=d.endpoint[e]
            eps.append(f"ep{ep.bEndpointAddress:#04x} attr={ep.bmAttributes:#04x} maxPkt={ep.wMaxPacketSize} ({ep.wMaxPacketSize&0x7ff}x{1+((ep.wMaxPacketSize>>11)&3)}) int={ep.bInterval}")
        print(f"IF{d.bInterfaceNumber} alt{d.bAlternateSetting} sub={d.bInterfaceSubClass} nEp={d.bNumEndpoints} {' | '.join(eps)}")
        ex=bytes(bytearray(d.extra[k] for k in range(d.extra_length))) if d.extra_length else b""
        if ex and d.bInterfaceSubClass==2:
            j=0
            while j+2<=len(ex):
                bl,bt=ex[j],ex[j+1]
                if bl==0: break
                ch=ex[j:j+bl]
                if bt==0x24:
                    st=ch[2]
                    if st==1:
                        nf=ch[3]; tl=int.from_bytes(ch[4:6],'little'); epa=ch[6]
                        print(f"   VS_INPUT_HEADER nFormats={nf} totalLen={tl} ep={epa:#04x} bTerminalLink={ch[8]} trigSup={ch[10]} trigUsage={ch[11]} bmaControls={ch[13:].hex()}")
                    elif st==6:
                        print(f"   VS_FORMAT_MJPEG fmtIdx={ch[3]} nFrame={ch[4]} bmFlags={ch[5]} defFrame={ch[6]} aspect={ch[7]}:{ch[8]} interlace={ch[9]} copy={ch[10]}")
                    elif st==7:
                        w=int.from_bytes(ch[5:7],'little'); h=int.from_bytes(ch[7:9],'little')
                        minbr=int.from_bytes(ch[9:13],'little'); maxbr=int.from_bytes(ch[13:17],'little')
                        mfs=int.from_bytes(ch[17:21],'little'); defi=int.from_bytes(ch[21:25],'little')
                        itype=ch[25]; iv=[]; k=26
                        if itype==0:
                            cnt=int.from_bytes(ch[k:k+4],'little'); k+=4
                            for _ in range(cnt): iv.append(int.from_bytes(ch[k:k+4],'little')); k+=4
                        else:
                            for _ in range(itype): iv.append(int.from_bytes(ch[k:k+4],'little')); k+=4
                        print(f"   VS_FRAME_MJPEG fmt={ch[3]} frame={ch[4]} {w}x{h} caps={ch[4+0]&0} bmCap={ch[4]} minBW={minbr} maxBW={maxbr} maxFrameSize={mfs} defInterval={defi/1e7:.3f}s ({1e7/defi:.1f}fps) intervals={[round(x/1e7,3) for x in iv]}")
                    elif st==4:
                        print(f"   VS_FORMAT_UNCOMPRESSED fmtIdx={ch[3]} nFrame={ch[4]} guid={uuid.UUID(bytes_le=ch[5:21])} bpp={ch[21]} defFrame={ch[22]} aspect={ch[23]}:{ch[24]} interlace={ch[25]} copy={ch[26]}")
                    elif st==5:
                        w=int.from_bytes(ch[5:7],'little'); h=int.from_bytes(ch[7:9],'little')
                        minbr=int.from_bytes(ch[9:13],'little'); maxbr=int.from_bytes(ch[13:17],'little')
                        mfs=int.from_bytes(ch[17:21],'little'); defi=int.from_bytes(ch[21:25],'little')
                        itype=ch[25]; iv=[]; k=26
                        if itype==0:
                            cnt=int.from_bytes(ch[k:k+4],'little'); k+=4
                            for _ in range(cnt): iv.append(int.from_bytes(ch[k:k+4],'little')); k+=4
                        else:
                            for _ in range(itype): iv.append(int.from_bytes(ch[k:k+4],'little')); k+=4
                        print(f"   VS_FRAME_UNCOMPRESSED fmt={ch[3]} frame={ch[4]} {w}x{h} bmCap={ch[4]} minBW={minbr} maxBW={maxbr} maxFrameSize={mfs} defInterval={defi/1e7:.3f}s ({1e7/defi:.1f}fps) intervals={[round(x/1e7,3) for x in iv]}")
                    elif st==3:
                        print(f"   VS_STILL_IMAGE_FRAME: {ch.hex()}")
                    else:
                        print(f"   VS subtype {st}: {ch.hex()}")
                elif bt==0x25:
                    print(f"   CS_ENDPOINT addr={ch[2]:#04x} flags={ch[3]:#04x} bmControls={ch[4:].hex()}")
                else:
                    print(f"   desc {bt:#x}: {ch.hex()}")
                j+=bl
lib.libusb_free_config_descriptor(cp); lib.libusb_free_device_list(lst,1)
