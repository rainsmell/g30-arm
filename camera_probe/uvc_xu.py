"""UVC Extension Unit control tool for the arm camera.

Works through DirectShow IKsControl (usbvideo.sys) on Windows 10/11.

Usage:
  python uvc_xu.py list                 # enumerate XU controls
  python uvc_xu.py get  <id>            # GET_CUR
  python uvc_xu.py set  <id> <hexbytes> # SET_CUR
  python uvc_xu.py def  <id>            # GET_DEF (default)
  python uvc_xu.py info                 # XU descriptor
"""
import ctypes, uuid, sys, time
from ctypes import POINTER, byref, sizeof, c_ulong, c_void_p, c_ubyte, cast
from comtypes import IUnknown, COMMETHOD, HRESULT, GUID
from pygrabber.dshow_graph import SystemDeviceEnum, DeviceCategories

XU_GUID = "{28F03370-6311-4A2E-BA2C-6890EB334016}"
XU_NODE_ID = 1
CAMERA_INDEX = 1

KS_GET = 0x1
KS_SET = 0x2
KS_BASICSUPPORT = 0x200
KS_TOPOLOGY = 0x10000000
KS_DEFAULTVALUES = 0x00010000

# KSPROPERTY_MEMBERSHEADER flags
KSPROPERTY_MEMBER_RANGES = 0x1
KSPROPERTY_MEMBER_STEPPEDRANGES = 0x2
KSPROPERTY_MEMBER_VALUES = 0x3
KSPROPERTY_MEMBER_FLAG_DEFAULT = 0x10000000
KSPROPERTY_MEMBER_FLAG_BASICSUPPORT_MULTICHANNEL = 0x20000000
KSPROPERTY_MEMBER_FLAG_BASICSUPPORT_UNIFORM = 0x40000000


class IKsControl(IUnknown):
    _iid_ = GUID("{28F54685-06FD-11D2-B27A-00A0C9223196}")
    _methods_ = [
        COMMETHOD([], HRESULT, "KsProperty", (["in"], c_void_p, "P"), (["in"], c_ulong, "PL"),
                  (["in"], c_void_p, "PD"), (["in"], c_ulong, "DL"), (["in"], POINTER(c_ulong), "BR")),
        COMMETHOD([], HRESULT, "KsMethod", (["in"], c_void_p, "M"), (["in"], c_ulong, "ML"),
                  (["in"], c_void_p, "MD"), (["in"], c_ulong, "DL"), (["in"], POINTER(c_ulong), "BR")),
        COMMETHOD([], HRESULT, "KsEvent", (["in"], c_void_p, "E"), (["in"], c_ulong, "EL"),
                  (["in"], c_void_p, "ED"), (["in"], c_ulong, "DL"), (["in"], POINTER(c_ulong), "BR")),
    ]


class KSPROPERTY(ctypes.Structure):
    _fields_ = [("Set", GUID), ("Id", c_ulong), ("Flags", c_ulong)]


class KSP_NODE(ctypes.Structure):
    _fields_ = [("Property", KSPROPERTY), ("NodeId", c_ulong), ("Reserved", c_ulong)]


def open_ks(index=CAMERA_INDEX):
    sde = SystemDeviceEnum()
    filt, name = sde.get_filter_by_index(DeviceCategories.VideoInputDevice, index)
    return filt.QueryInterface(IKsControl), name


def call(ks, ctrl_id, flags, dlen=1024, data_in=None):
    p = KSP_NODE()
    p.Property = KSPROPERTY(GUID(XU_GUID), ctrl_id, flags | KS_TOPOLOGY)
    p.NodeId = XU_NODE_ID
    buf = (c_ubyte * dlen)()
    if data_in:
        ctypes.memmove(buf, bytes(data_in), min(len(data_in), dlen))
    ret = c_ulong(0)
    try:
        hr = ks.KsProperty(cast(byref(p), c_void_p), sizeof(p), cast(buf, c_void_p), dlen, byref(ret))
    except Exception as e:
        hr = getattr(e, "hresult", -1)
    return hr, bytes(buf[:ret.value]), ret.value


def hexascii(b):
    h = b.hex()
    a = "".join(chr(c) if 32 <= c < 127 else "." for c in b)
    return f"{h}  |{a}|"


def decode_basic(data):
    """Parse KSPROPERTY_DESCRIPTION + members list -> (access, [(min,max,step,defaults)])."""
    if len(data) < 8:
        return None, []
    access = int.from_bytes(data[0:4], "little")
    dsize = int.from_bytes(data[4:8], "little")
    out = []
    off = 8
    while off + 12 <= min(len(data), dsize):
        mflags = int.from_bytes(data[off:off + 4], "little")
        msize = int.from_bytes(data[off + 4:off + 8], "little")
        mcount = int.from_bytes(data[off + 8:off + 12], "little")
        off += 12
        base = mflags & 0xFF
        for i in range(mcount):
            if base == KSPROPERTY_MEMBER_RANGES:
                # KSPROPERTY_BOUNDS_LONG: min, max (4+4)
                if off + 8 > len(data):
                    break
                mn = int.from_bytes(data[off:off + 4], "little", signed=True)
                mx = int.from_bytes(data[off + 4:off + 8], "little", signed=True)
                off += 8
                out.append((mn, mx, None, mflags))
            elif base == KSPROPERTY_MEMBER_STEPPEDRANGES:
                if off + 12 > len(data):
                    break
                mn = int.from_bytes(data[off:off + 4], "little", signed=True)
                mx = int.from_bytes(data[off + 4:off + 8], "little", signed=True)
                st = int.from_bytes(data[off + 8:off + 12], "little", signed=True)
                off += 12
                out.append((mn, mx, st, mflags))
            elif base == KSPROPERTY_MEMBER_VALUES:
                if off + 4 > len(data):
                    break
                v = int.from_bytes(data[off:off + 4], "little", signed=True)
                off += 4
                out.append((v, v, None, mflags))
            else:
                break
    return access, out


def cmd_info(ks):
    hr, data, ln = call(ks, 0, KS_GET, 256)
    print(f"XU_INFO hr=0x{hr & 0xffffffff:08x} len={ln}")
    if ln >= 17:
        print("  GUID       :", uuid.UUID(bytes_le=data[0:16]))
        print("  numControls:", data[16])
        print("  nrInPins   :", data[17])
        nr = data[17]
        print("  sourceIDs  :", list(data[18:18 + nr]))
        cs = data[18 + nr]
        print("  ctrlSize   :", cs, " bmControls:", data[19 + nr:19 + nr + cs].hex())
    print("  raw:", data.hex())


def cmd_list(ks):
    print(f"{'id':>3} {'GET_CUR':>28}  {'DEFAULT':>20}  range")
    print("-" * 100)
    for i in range(1, 32):
        hr, d, ln = call(ks, i, KS_GET, 256)
        cur = hexascii(d[:16]) if hr == 0 else f"err 0x{hr & 0xffffffff:08x}"
        hrd, dd, lnd = call(ks, i, KS_GET | KS_DEFAULTVALUES, 256)
        dflt = dd[:16].hex() if hrd == 0 else "-"
        hrb, db, lnb = call(ks, i, KS_BASICSUPPORT, 256)
        acc, members = decode_basic(db) if hrb == 0 else (None, [])
        rng = ", ".join(f"[{a}..{b}{'/step'+str(s) if s else ''}]" for a, b, s, _ in members) if members else ""
        accs = f" access=0x{acc:x}" if acc is not None else ""
        if hr == 0 or hrb == 0 or hrd == 0:
            print(f"{i:>3} {cur:>28}  {dflt:>20} {rng}{accs}")


def cmd_get(ks, cid):
    hr, d, ln = call(ks, cid, KS_GET, 256)
    print(f"GET id={cid} hr=0x{hr & 0xffffffff:08x} len={ln} {hexascii(d)}")
    hr, d, ln = call(ks, cid, KS_GET | KS_DEFAULTVALUES, 256)
    print(f"GET_DEF id={cid} hr=0x{hr & 0xffffffff:08x} len={ln} {hexascii(d)}")
    hr, d, ln = call(ks, cid, KS_BASICSUPPORT, 256)
    acc, members = decode_basic(d) if hr == 0 else (None, [])
    print(f"BASICSUPPORT hr=0x{hr & 0xffffffff:08x} access=0x{acc:x}" if acc is not None else "BASICSUPPORT: n/a")
    for a, b, s, f in members:
        print(f"   member flags=0x{f:x} min={a} max={b} step={s}")


def cmd_set(ks, cid, hexstr):
    payload = bytes.fromhex(hexstr)
    hr, d, ln = call(ks, cid, KS_SET, len(payload) if payload else 4, payload)
    print(f"SET id={cid} {hexstr} -> hr=0x{hr & 0xffffffff:08x}")
    hr, d, ln = call(ks, cid, KS_GET, 256)
    print(f"  readback: hr=0x{hr & 0xffffffff:08x} {hexascii(d)}")


def main():
    ks, name = open_ks()
    print("device:", name)
    if len(sys.argv) < 2:
        cmd_list(ks); return
    c = sys.argv[1]
    if c == "list":
        cmd_list(ks)
    elif c == "info":
        cmd_info(ks)
    elif c == "get":
        cmd_get(ks, int(sys.argv[2]))
    elif c == "def":
        cmd_get(ks, int(sys.argv[2]))
    elif c == "set":
        cmd_set(ks, int(sys.argv[2]), sys.argv[3])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
