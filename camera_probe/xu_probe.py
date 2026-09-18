import ctypes, uuid
from ctypes import POINTER, byref, sizeof, c_ulong, c_void_p, c_ubyte, cast
from comtypes import IUnknown, COMMETHOD, HRESULT, GUID
from pygrabber.dshow_graph import SystemDeviceEnum, DeviceCategories

class IKsControl(IUnknown):
    _iid_ = GUID("{28F54685-06FD-11D2-B27A-00A0C9223196}")
    _methods_ = [
        COMMETHOD([], HRESULT, "KsProperty", (["in"], c_void_p, "Property"),
                  (["in"], c_ulong, "PropertyLength"), (["in"], c_void_p, "PropertyData"),
                  (["in"], c_ulong, "DataLength"), (["in"], POINTER(c_ulong), "BytesReturned")),
        COMMETHOD([], HRESULT, "KsMethod", (["in"], c_void_p, "Method"),
                  (["in"], c_ulong, "MethodLength"), (["in"], c_void_p, "MethodData"),
                  (["in"], c_ulong, "DataLength"), (["in"], POINTER(c_ulong), "BytesReturned")),
        COMMETHOD([], HRESULT, "KsEvent", (["in"], c_void_p, "Event"),
                  (["in"], c_ulong, "EventLength"), (["in"], c_void_p, "EventData"),
                  (["in"], c_ulong, "DataLength"), (["in"], POINTER(c_ulong), "BytesReturned")),
    ]

class KSPROPERTY(ctypes.Structure):
    _fields_ = [("Set", GUID), ("Id", c_ulong), ("Flags", c_ulong)]

class KSP_NODE(ctypes.Structure):
    _fields_ = [("Property", KSPROPERTY), ("NodeId", c_ulong), ("Reserved", c_ulong)]

XU_SET = GUID("{3CB2C1D5-2C39-4B7D-A11C-E08A9E0D2AE8}")
KS_GET = 0x1

def call(ks, node_id, prop_id, selector, flags, dlen=256, data_in=None):
    p = KSP_NODE()
    p.Property.Set = XU_SET
    p.Property.Id = prop_id
    p.Property.Flags = flags | (selector << 16)
    p.NodeId = node_id
    buf = (c_ubyte * dlen)()
    if data_in:
        ctypes.memmove(buf, data_in, len(data_in))
    ret = c_ulong(0)
    try:
        hr = ks.KsProperty(cast(byref(p), c_void_p), sizeof(p), cast(buf, c_void_p), dlen, byref(ret))
    except Exception as e:
        hr = getattr(e, "hresult", -1)
    return hr, bytes(buf), ret.value

def main():
    sde = SystemDeviceEnum()
    filt, name = sde.get_filter_by_index(DeviceCategories.VideoInputDevice, 1)
    print("filter:", name)
    ks = filt.QueryInterface(IKsControl)
    for nid in range(6):
        hr, data, ln = call(ks, nid, 0, 0, KS_GET)
        print(f"node {nid}: XU_INFO id=0 hr=0x{hr & 0xffffffff:08x} ret={ln} hex={data[:48].hex()}")
        if hr == 0 and ln >= 17:
            print(f"        GUID={uuid.UUID(bytes_le=data[0:16])} numControls={data[16]} nrInPins={data[17] if ln>17 else '?'} rest={data[17:40].hex()}")
    # also try id=node_id variant
    print("--- variant Property.Id=node_id ---")
    for nid in range(6):
        hr, data, ln = call(ks, nid, nid, 0, KS_GET)
        print(f"node {nid}: id={nid} hr=0x{hr & 0xffffffff:08x} ret={ln} hex={data[:32].hex()}")

if __name__ == "__main__":
    main()
