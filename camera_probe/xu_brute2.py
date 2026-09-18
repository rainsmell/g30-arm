import ctypes, uuid
from ctypes import POINTER, byref, sizeof, c_ulong, c_void_p, c_ubyte, cast
from comtypes import IUnknown, COMMETHOD, HRESULT, GUID
from pygrabber.dshow_graph import SystemDeviceEnum, DeviceCategories

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

KS_GET = 0x1
KS_SET = 0x2
KS_BASICSUPPORT = 0x200

SETS = {
    "KSPROPERTYSETID_EXTENSIONUNIT": "{3CB2C1D5-2C39-4B7D-A11C-E08A9E0D2AE8}",
    "XU_GUID_AS_SET": "{28F03370-6311-4A2E-BA2C-6890EB334016}",
}

def call(ks, setg, pid, flags, node_id, dlen=512):
    if node_id is None:
        p = KSPROPERTY(GUID(setg), pid, flags); plen = sizeof(KSPROPERTY)
    else:
        p = KSP_NODE(); p.Property = KSPROPERTY(GUID(setg), pid, flags); p.NodeId = node_id
        plen = sizeof(KSP_NODE)
    buf = (c_ubyte * dlen)()
    ret = c_ulong(0)
    try:
        hr = ks.KsProperty(cast(byref(p), c_void_p), plen, cast(buf, c_void_p), dlen, byref(ret))
    except Exception as e:
        hr = getattr(e, "hresult", -1)
    return hr, bytes(buf), ret.value

def main():
    sde = SystemDeviceEnum()
    filt, name = sde.get_filter_by_index(DeviceCategories.VideoInputDevice, 1)
    ks = filt.QueryInterface(IKsControl)
    print("filter:", name)
    hits = []
    for label, sg in SETS.items():
        for node_id in [None] + list(range(0, 12)):
            for pid in list(range(0, 12)) + [0xFFFF]:
                for flags in (KS_GET, KS_BASICSUPPORT):
                    hr, data, ln = call(ks, sg, pid, flags, node_id)
                    if hr == 0:
                        hits.append((label, node_id, pid, flags, ln, data[:32].hex()))
                        print(f"OK {label} node={node_id} pid={pid} flags={flags:#x} ret={ln} {data[:32].hex()}")
                    elif (hr & 0xffffffff) != 0x80070492 and (hr & 0xffffffff) != 0x80070057:
                        print(f"?  {label} node={node_id} pid={pid} flags={flags:#x} hr=0x{hr & 0xffffffff:08x}")
    if not hits:
        print("no hits for INFO-style queries")
    # selector-based control queries on plausible node ids
    print("\n-- control selector sweep --")
    for label, sg in SETS.items():
        for node_id in list(range(0, 8)):
            for sel in range(1, 33):
                for flags in (KS_GET | (sel << 16), KS_BASICSUPPORT | (sel << 16)):
                    hr, data, ln = call(ks, sg, 1, flags, node_id, dlen=64)
                    if hr == 0:
                        print(f"OK {label} node={node_id} sel={sel} flags={flags:#x} ret={ln} {data[:16].hex()}")

def fmt(hr):
    import ctypes
    buf = ctypes.create_unicode_buffer(512)
    ctypes.windll.kernel32.FormatMessageW(0x1000, None, hr, 0, buf, 512, None)
    return buf.value.strip()

print("0x80070492 =>", fmt(0x80070492))
main()
