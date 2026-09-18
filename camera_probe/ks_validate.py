import ctypes, uuid
from ctypes import POINTER, byref, sizeof, c_ulong, c_long, c_void_p, c_ubyte, cast
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

def ksprop(ks, setguid, prop_id, flags, dlen=64):
    p = KSPROPERTY(setguid, prop_id, flags)
    buf = (c_ubyte * dlen)()
    ret = c_ulong(0)
    try:
        hr = ks.KsProperty(cast(byref(p), c_void_p), sizeof(p), cast(buf, c_void_p), dlen, byref(ret))
    except Exception as e:
        hr = getattr(e, "hresult", -1)
    return hr, bytes(buf), ret.value

def main():
    sde = SystemDeviceEnum()
    for idx in (0, 1):
        filt, name = sde.get_filter_by_index(DeviceCategories.VideoInputDevice, idx)
        print(f"\n=== idx {idx} {name}")
        ks = filt.QueryInterface(IKsControl)
        # PROPSETID_VIDCAP_VIDEOPROCAMP brightness (id 0) and PROPSETID_VIDCAP_CAMERACONTROL (id 0=pan)
        for label, setguid in [
            ("VIDEOPROCAMP", "{C6E13360-30AC-11D0-A18C-00A0C9118956}"),
            ("CAMERACONTROL", "{C6E13370-30AC-11D0-A18C-00A0C9118956}"),
            ("XU", "{3CB2C1D5-2C39-4B7D-A11C-E08A9E0D2AE8}"),
            ("TOPOLOGY", "{720D4AC0-7533-11D0-A5D6-28DB04C10000}"),
        ]:
            g = GUID(setguid)
            for pid in (0, 1, 2):
                for fl in (0x1, 0x2):
                    hr, data, ln = ksprop(ks, g, pid, fl)
                    if hr == 0:
                        print(f"  {label} pid={pid} flags={fl:#x}: OK ret={ln} {data[:24].hex()}")

if __name__ == "__main__":
    main()
