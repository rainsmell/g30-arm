import ctypes, uuid
from ctypes import POINTER, byref, sizeof, c_ulong, c_void_p, c_ubyte, cast
from comtypes import IUnknown, COMMETHOD, HRESULT, GUID
from pygrabber.dshow_graph import SystemDeviceEnum, DeviceCategories

class IKsTopologyInfo(IUnknown):
    _iid_ = GUID("{720D4AC0-7533-11D0-A5D6-28DB04C10000}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetNumCategories", (["in"], POINTER(c_ulong), "p")),
        COMMETHOD([], HRESULT, "GetCategory", (["in"], c_ulong, "i"), (["in"], c_void_p, "p")),
        COMMETHOD([], HRESULT, "GetNumNodes", (["in"], POINTER(c_ulong), "p")),
        COMMETHOD([], HRESULT, "GetNodeType", (["in"], c_ulong, "i"), (["in"], c_void_p, "p")),
        COMMETHOD([], HRESULT, "GetNodeName", (["in"], c_ulong, "i"), (["in"], c_void_p, "nm"),
                  (["in"], POINTER(c_ulong), "plen")),
        COMMETHOD([], HRESULT, "GetNumConnections", (["in"], c_ulong, "i"), (["in"], POINTER(c_ulong), "p")),
        COMMETHOD([], HRESULT, "GetConnectionInfo", (["in"], c_ulong, "i"), (["in"], c_ulong, "j"),
                  (["in"], c_void_p, "p")),
        COMMETHOD([], HRESULT, "GetNodeInstance", (["in"], c_ulong, "i"),
                  (["in"], POINTER(c_void_p), "pp"), (["in"], c_void_p, "unk")),
        COMMETHOD([], HRESULT, "CreateNodeInstance", (["in"], c_ulong, "i"),
                  (["in"], c_void_p, "iid"), (["in"], POINTER(c_void_p), "pp")),
    ]

def dump(idx):
    sde = SystemDeviceEnum()
    filt, name = sde.get_filter_by_index(DeviceCategories.VideoInputDevice, idx)
    print(f"\n##### index {idx}: {name}")
    try:
        topo = filt.QueryInterface(IKsTopologyInfo)
    except Exception as e:
        print("  no IKsTopologyInfo:", e); return
    nc = c_ulong(0)
    try:
        topo.GetNumCategories(byref(nc))
    except Exception as e:
        print("  GetNumCategories err", e)
    print("  numCategories:", nc.value)
    for i in range(nc.value):
        b = (c_ubyte * 16)()
        try:
            topo.GetCategory(i, cast(b, c_void_p))
            print(f"    cat{i}: {uuid.UUID(bytes_le=bytes(b))}")
        except Exception as e:
            print(f"    cat{i}: err {e}")
    n = c_ulong(0)
    try:
        topo.GetNumNodes(byref(n))
    except Exception as e:
        print("  GetNumNodes err", e)
    print("  numNodes:", n.value)
    for nid in range(n.value):
        b = (c_ubyte * 16)()
        try:
            topo.GetNodeType(nid, cast(b, c_void_p))
            print(f"    node{nid} type: {uuid.UUID(bytes_le=bytes(b))}")
        except Exception as e:
            print(f"    node{nid} type err {e}")

for i in (0, 1):
    dump(i)
