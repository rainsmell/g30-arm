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

TOPO_SET = GUID("{720D4AC0-7533-11D0-A5D6-28DB04C10000}")
XU_SET = GUID("{3CB2C1D5-2C39-4B7D-A11C-E08A9E0D2AE8}")
KS_GET = 0x1

NODE_NAMES = {
    "941c7ac0-c559-11d0-8a2b-00a0c9255ac1": "DEV_SPECIFIC (Extension Unit!)",
    "65e8773d-8f56-11d0-a3b9-00a0c9223196": "VIDEO_CAMERA_TERMINAL",
    "dff229e2-553d-11d1-8ef0-00a0c9f4d10b": "VIDEO_INPUT_TERMINAL",
    "dff229e1-553d-11d1-8ef0-00a0c9f4d10b": "VIDEO_OUTPUT_TERMINAL",
    "dff229e3-553d-11d1-8ef0-00a0c9f4d10b": "VIDEO_SELECTOR",
    "dff229e4-553d-11d1-8ef0-00a0c9f4d10b": "VIDEO_PROCESSING",
}

def raw(ks, prop_struct, setg, pid, flags, node_id=None, dlen=512, data_in=None):
    if node_id is None:
        p = KSPROPERTY(setg, pid, flags)
        plen = sizeof(KSPROPERTY)
    else:
        p = KSP_NODE()
        p.Property = KSPROPERTY(setg, pid, flags)
        p.NodeId = node_id
        plen = sizeof(KSP_NODE)
    buf = (c_ubyte * dlen)()
    if data_in:
        ctypes.memmove(buf, data_in, len(data_in))
    ret = c_ulong(0)
    try:
        hr = ks.KsProperty(cast(byref(p), c_void_p), plen, cast(buf, c_void_p), dlen, byref(ret))
    except Exception as e:
        hr = getattr(e, "hresult", -1)
    return hr, bytes(buf), ret.value

def get_topology(ks):
    # pid 0 categories, 1 nodes, 2 connections
    result = {}
    for pid, tag in ((0, "categories"), (1, "nodes"), (2, "connections")):
        hr, data, ln = raw(ks, None, TOPO_SET, pid, KS_GET)
        if hr != 0:
            print(f"  topo {tag}: hr=0x{hr & 0xffffffff:08x}")
            result[tag] = []
            continue
        # 4-byte length prefix, then count
        payload = data[4:ln]
        cnt = int.from_bytes(payload[0:4], "little")
        items = []
        if pid in (0, 1):
            for i in range(cnt):
                off = 4 + i * 16
                items.append(str(uuid.UUID(bytes_le=payload[off:off + 16])))
        elif pid == 2:
            for i in range(cnt):
                off = 4 + i * 16
                a, b, c, d = (int.from_bytes(payload[off + 4 * k:off + 4 * k + 4], "little") for k in range(4))
                items.append((a, b, c, d))
        result[tag] = items
        print(f"  topo {tag}: count={cnt} -> {items}")
    return result

def main():
    sde = SystemDeviceEnum()
    filt, name = sde.get_filter_by_index(DeviceCategories.VideoInputDevice, 1)
    print("filter:", name)
    ks = filt.QueryInterface(IKsControl)
    topo = get_topology(ks)
    xus = [i for i, g in enumerate(topo["nodes"]) if g == "941c7ac0-c559-11d0-8a2b-00a0c9255ac1"]
    print("XU node ids:", xus)
    for nid in xus:
        hr, data, ln = raw(ks, None, XU_SET, 0, KS_GET, node_id=nid, dlen=256)
        print(f"\nnode {nid} XU_INFO hr=0x{hr & 0xffffffff:08x} ret={ln} hex={data[:40].hex()}")
        if hr == 0 and ln >= 17:
            print(f"   XU GUID = {uuid.UUID(bytes_le=data[0:16])}   numControls={data[16]}")
        # node name
        hr2, d2, l2 = raw(ks, None, TOPO_SET, 3, KS_GET, node_id=nid, dlen=256)
        print(f"   node name hr=0x{hr2 & 0xffffffff:08x} ret={l2} raw={d2[:64].hex()}")

if __name__ == "__main__":
    main()
