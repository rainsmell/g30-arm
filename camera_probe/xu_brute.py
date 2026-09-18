import ctypes, uuid, itertools
from ctypes import POINTER, byref, sizeof, c_ulong, c_void_p, c_ubyte, cast
from comtypes import IUnknown, COMMETHOD, HRESULT, GUID
from pygrabber.dshow_graph import SystemDeviceEnum, DeviceCategories

class IKsControl(IUnknown):
    _iid_ = GUID("{28F54685-06FD-11D2-B27A-00A0C9223196}")
    _methods_ = [
        COMMETHOD([], HRESULT, "KsProperty", (["in"], c_void_p, "Property"),
                  (["in"], c_ulong, "PropertyLength"), (["in"], c_void_p, "PropertyData"),
                  (["in"], c_ulong, "DataLength"), (["in"], POINTER(c_ulong), "BytesReturned")),
        COMMETHOD([], HRESULT, "KsMethod", (["in"], c_void_p, "Property"),
                  (["in"], c_ulong, "PropertyLength"), (["in"], c_void_p, "PropertyData"),
                  (["in"], c_ulong, "DataLength"), (["in"], POINTER(c_ulong), "BytesReturned")),
        COMMETHOD([], HRESULT, "KsEvent", (["in"], c_void_p, "Event"),
                  (["in"], c_ulong, "EventLength"), (["in"], c_void_p, "EventData"),
                  (["in"], c_ulong, "DataLength"), (["in"], POINTER(c_ulong), "BytesReturned")),
    ]

class KSPROPERTY(ctypes.Structure):
    _fields_ = [("Set", GUID), ("Id", c_ulong), ("Flags", c_ulong)]

class KSP_NODE(ctypes.Structure):
    _fields_ = [("Property", KSPROPERTY), ("NodeId", c_ulong), ("Reserved", c_ulong)]

KS_GET = 0x1
KS_BASICSUPPORT = 0x200

CANDIDATE_SETS = {
    "KSPROPERTYSETID_EXTENSIONUNIT": "{3CB2C1D5-2C39-4B7D-A11C-E08A9E0D2AE8}",
    "PROPSETID_VIDCAP_EXTENSIONUNIT": "{A6FEA1A0-30AC-11D0-A18C-00A0C9118956}",
}

def call(ks, setg, pid, flags, node_id=None, dlen=256):
    if node_id is None:
        p = KSPROPERTY(GUID(setg), pid, flags); plen = sizeof(KSPROPERTY)
    else:
        p = KSP_NODE(); p.Property = KSPROPERTY(GUID(setg), pid, flags); p.NodeId = node_id; p.Reserved = 0
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
    for label, setg in CANDIDATE_SETS.items():
        print(f"\n### {label} {setg}")
        for node_id in (None, 0, 1, 2, 3, 0xFFFFFFFF):
            for pid in (0, 1, 0xFFFF):
                for flags in (KS_GET, KS_BASICSUPPORT, KS_GET | 0x10000, KS_GET | (1 << 16), KS_GET | (2 << 16)):
                    hr, data, ln = call(ks, setg, pid, flags, node_id=node_id)
                    if hr == 0:
                        print(f"  OK node={node_id} pid={pid} flags=0x{flags:x} ret={ln} {data[:32].hex()}")
        # also probe node instances
    # node instances via CreateNodeInstance
    print("\n### CreateNodeInstance per node")
    from comtypes import IUnknown as IU
    IID_IKsControl = GUID("{28F54685-06FD-11D2-B27A-00A0C9223196}")
    topo = filt.QueryInterface(__import__("uvc_topo").IKsTopologyInfo) if False else None

if __name__ == "__main__":
    main()
