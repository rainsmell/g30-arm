"""Enumerate UVC topology (+ Extension Units) via DirectShow IKs interfaces (explicit buffers)."""
import ctypes, uuid
from ctypes import POINTER, byref, sizeof, c_ulong, c_void_p, c_wchar_p, c_ubyte, cast
from comtypes import IUnknown, COMMETHOD, HRESULT, GUID
from pygrabber.dshow_graph import SystemDeviceEnum, DeviceCategories

class KSTOPOLOGY_CONNECTION(ctypes.Structure):
    _fields_ = [("FromNode", c_ulong), ("FromNodePin", c_ulong),
                ("ToNode", c_ulong), ("ToNodePin", c_ulong)]

class IKsTopologyInfo(IUnknown):
    _iid_ = GUID("{720D4AC0-7533-11D0-A5D6-28DB04C10000}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetNumCategories", (["in"], POINTER(c_ulong), "p")),
        COMMETHOD([], HRESULT, "GetCategory", (["in"], c_ulong, "i"), (["in"], POINTER(GUID), "p")),
        COMMETHOD([], HRESULT, "GetNumNodes", (["in"], POINTER(c_ulong), "p")),
        COMMETHOD([], HRESULT, "GetNodeType", (["in"], c_ulong, "i"), (["in"], POINTER(GUID), "p")),
        COMMETHOD([], HRESULT, "GetNodeName", (["in"], c_ulong, "i"), (["in"], c_void_p, "nm"),
                  (["in"], POINTER(c_ulong), "plen")),
        COMMETHOD([], HRESULT, "GetNumConnections", (["in"], c_ulong, "i"), (["in"], POINTER(c_ulong), "p")),
        COMMETHOD([], HRESULT, "GetConnectionInfo", (["in"], c_ulong, "i"), (["in"], c_ulong, "j"),
                  (["in"], POINTER(KSTOPOLOGY_CONNECTION), "p")),
        COMMETHOD([], HRESULT, "GetNodeInstance", (["in"], c_ulong, "i"),
                  (["in"], POINTER(c_void_p), "pp"), (["in"], c_void_p, "unk")),
        COMMETHOD([], HRESULT, "CreateNodeInstance", (["in"], c_ulong, "i"),
                  (["in"], POINTER(GUID), "iid"), (["in"], POINTER(c_void_p), "pp")),
    ]

class KSPROPERTY(ctypes.Structure):
    _fields_ = [("Set", GUID), ("Id", c_ulong), ("Flags", c_ulong)]

class KSP_NODE(ctypes.Structure):
    _fields_ = [("Property", KSPROPERTY), ("NodeId", c_ulong), ("Reserved", c_ulong)]

class IKsControl(IUnknown):
    _iid_ = GUID("{28F54685-06FD-11D2-B27A-00A0C9223196}")
    _methods_ = [
        COMMETHOD([], HRESULT, "KsProperty", (["in"], POINTER(KSPROPERTY), "Property"),
                  (["in"], c_ulong, "PropertyLength"), (["in"], c_void_p, "PropertyData"),
                  (["in"], c_ulong, "DataLength"), (["in"], POINTER(c_ulong), "BytesReturned")),
        COMMETHOD([], HRESULT, "KsMethod", (["in"], c_void_p, "Method"),
                  (["in"], c_ulong, "MethodLength"), (["in"], c_void_p, "MethodData"),
                  (["in"], c_ulong, "DataLength"), (["in"], POINTER(c_ulong), "BytesReturned")),
        COMMETHOD([], HRESULT, "KsEvent", (["in"], c_void_p, "Event"),
                  (["in"], c_ulong, "EventLength"), (["in"], c_void_p, "EventData"),
                  (["in"], c_ulong, "DataLength"), (["in"], POINTER(c_ulong), "BytesReturned")),
    ]

KSNODETYPE_DEV_SPECIFIC = "{941c7ac0-c559-11d0-8a2b-00a0c9255ac1}"
KS_EXTENSION_UNIT_SET = GUID("{3CB2C1D5-2C39-4B7D-A11C-E08A9E0D2AE8}")
KSPROPERTY_TYPE_GET = 0x1
KSPROPERTY_TYPE_SET = 0x2
KSPROPERTY_TYPE_DEFAULTVALUES = 0x00010000

GUID_NAMES = {
    KSNODETYPE_DEV_SPECIFIC: "DEV_SPECIFIC(XU)",
    "{17cca71b-ecd7-11d0-b908-00a0c9223196}": "VIDEO_STREAMING",
    "{65e8773d-8f56-11d0-a3b9-00a0c9223196}": "VIDEO_CAMERA_TERMINAL",
    "{dff229e2-553d-11d1-8ef0-00a0c9f4d10b}": "VIDEO_INPUT_TERMINAL",
    "{dff229e1-553d-11d1-8ef0-00a0c9f4d10b}": "VIDEO_OUTPUT_TERMINAL",
    "{dff229e3-553d-11d1-8ef0-00a0c9f4d10b}": "VIDEO_SELECTOR",
    "{dff229e4-553d-11d1-8ef0-00a0c9f4d10b}": "VIDEO_PROCESSING",
}


def get_filter(index=1):
    sde = SystemDeviceEnum()
    return sde.get_filter_by_index(DeviceCategories.VideoInputDevice, index)


def ks_call(ks, node_id, prop_set, prop_id, selector, flags, data_len, data_in=None):
    prop = KSP_NODE()
    prop.Property.Set = prop_set
    prop.Property.Id = prop_id
    prop.Property.Flags = flags | (selector << 16)
    prop.NodeId = node_id
    prop.Reserved = 0
    buf = (c_ubyte * max(data_len, 1))()
    if data_in is not None:
        ctypes.memmove(buf, data_in, min(len(data_in), len(buf)))
    ret = c_ulong(0)
    hr = ks.KsProperty(byref(prop), sizeof(prop), cast(buf, c_void_p), data_len, byref(ret))
    return hr, bytes(buf[:ret.value]), ret.value


def main():
    filt, name = get_filter(1)
    print("filter:", name)
    topo = filt.QueryInterface(IKsTopologyInfo)
    n = c_ulong(0)
    topo.GetNumNodes(byref(n))
    print("num nodes:", n.value)
    xu_nodes = []
    for nid in range(n.value):
        g = GUID()
        hr = topo.GetNodeType(nid, byref(g))
        gs = str(g).lower()
        label = GUID_NAMES.get(gs, gs)
        buf = ctypes.create_unicode_buffer(512)
        ln = c_ulong(512)
        try:
            hr2 = topo.GetNodeName(nid, cast(buf, c_void_p), byref(ln))
            nm = buf.value
        except Exception as e:
            nm = f"<err {e}>"
        print(f"  node {nid}: type={gs} ({label}) name={nm!r}")
        if gs == KSNODETYPE_DEV_SPECIFIC:
            xu_nodes.append(nid)
    print("XU nodes:", xu_nodes)

    for nid in xu_nodes:
        # node instance -> IKsControl
        ks = None
        pp = c_void_p(0)
        hr = topo.GetNodeInstance(nid, byref(pp), None)
        print(f"\n== node {nid}: GetNodeInstance hr=0x{hr & 0xffffffff:08x} ptr={pp.value} ==")
        srcs = []
        if pp.value:
            unk = ctypes.POINTER(IUnknown)(pp)
            try:
                ks = unk.QueryInterface(IKsControl); srcs.append("node-instance")
            except Exception as e:
                print("  node-instance QI failed:", e)
        # filter IKsControl
        try:
            ksf = filt.QueryInterface(IKsControl); srcs.append("filter")
        except Exception as e:
            ksf = None
        for tag, k in (("node-instance", ks), ("filter", ksf)):
            if k is None:
                continue
            hr, data, ln = ks_call(k, nid, KS_EXTENSION_UNIT_SET, 0, 0, KSPROPERTY_TYPE_GET, 256)
            print(f"  [{tag}] XU_INFO hr=0x{hr & 0xffffffff:08x} len={ln} {data.hex()}")
            if ln >= 17:
                print(f"  [{tag}]   XU GUID   = {uuid.UUID(bytes_le=data[0:16])}")
                print(f"  [{tag}]   numCtl={data[16]}")

if __name__ == "__main__":
    main()
