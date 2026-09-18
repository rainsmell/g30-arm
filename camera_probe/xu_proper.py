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
    _fields_=[("Set",GUID),("Id",c_ulong),("Flags",c_ulong)]
class KSP_NODE(ctypes.Structure):
    _fields_=[("Property",KSPROPERTY),("NodeId",c_ulong),("Reserved",c_ulong)]

KS_GET=0x1; KS_SET=0x2; KS_BASICSUPPORT=0x200
KS_TOPOLOGY=0x10000000
KS_DEFAULTVALUES=0x00010000

XU_GUID_LE = "{28F03370-6311-4A2E-BA2C-6890EB334016}"
XU_GUID_BE = "{7033F028-1163-2E4A-BA2C-6890EB334016}"

def call(ks, setg, pid, flags, node_id, dlen=256, data_in=None):
    p = KSP_NODE(); p.Property = KSPROPERTY(GUID(setg), pid, flags); p.NodeId = node_id
    buf = (c_ubyte*dlen)()
    if data_in:
        ctypes.memmove(buf, data_in, min(len(data_in), dlen))
    ret = c_ulong(0)
    try:
        hr = ks.KsProperty(cast(byref(p), c_void_p), sizeof(p), cast(buf, c_void_p), dlen, byref(ret))
    except Exception as e:
        hr = getattr(e, "hresult", -1)
    return hr, bytes(buf), ret.value

def main():
    sde = SystemDeviceEnum()
    filt, name = sde.get_filter_by_index(DeviceCategories.VideoInputDevice, 1)
    ks = filt.QueryInterface(IKsControl)
    print("filter:", name)
    for label, sg in (("XU_GUID_LE", XU_GUID_LE), ("XU_GUID_BE", XU_GUID_BE)):
        print(f"\n########## set = {label} {sg}")
        for node_id in range(0, 8):
            # INFO
            for flags in (KS_GET|KS_TOPOLOGY, KS_GET):
                hr, data, ln = call(ks, sg, 0, flags, node_id, 256)
                if hr == 0:
                    print(f"  node={node_id} INFO flags={flags:#x} ret={ln} {data[:40].hex()}")
                    if ln >= 17:
                        print(f"     -> descriptor GUID={uuid.UUID(bytes_le=data[0:16])} numControls={data[16]}")
            # controls
            for pid in range(1, 32):
                for flags in (KS_GET|KS_TOPOLOGY, KS_BASICSUPPORT|KS_TOPOLOGY, KS_GET|KS_TOPOLOGY|KS_DEFAULTVALUES):
                    hr, data, ln = call(ks, sg, pid, flags, node_id, 64)
                    if hr == 0:
                        print(f"  node={node_id} ctrl id={pid} flags={flags:#x} ret={ln} {data[:24].hex()}")

if __name__ == "__main__":
    main()
