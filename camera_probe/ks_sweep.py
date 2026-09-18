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

KS_GET=0x1; KS_BASICSUPPORT=0x200
SETS={
 "CAMERACONTROL":"{C6E13370-30AC-11D0-A18C-00A0C9118956}",
 "VIDEOPROCAMP":"{C6E13360-30AC-11D0-A18C-00A0C9118956}",
 "EXTENSIONUNIT":"{3CB2C1D5-2C39-4B7D-A11C-E08A9E0D2AE8}",
 "SELECTOR":"{1ABDAECA-68B6-4F83-9371-B413907C7B9F}",
}
def call(ks,setg,pid,flags,node_id,dlen=64):
    if node_id is None:
        p=KSPROPERTY(GUID(setg),pid,flags); pl=sizeof(KSPROPERTY)
    else:
        p=KSP_NODE(); p.Property=KSPROPERTY(GUID(setg),pid,flags); p.NodeId=node_id; pl=sizeof(KSP_NODE)
    buf=(c_ubyte*dlen)(); ret=c_ulong(0)
    try: hr=ks.KsProperty(cast(byref(p),c_void_p),pl,cast(buf,c_void_p),dlen,byref(ret))
    except Exception as e: hr=getattr(e,"hresult",-1)
    return hr,bytes(buf),ret.value

for idx in (0,1):
    sde=SystemDeviceEnum(); filt,name=sde.get_filter_by_index(DeviceCategories.VideoInputDevice,idx)
    ks=filt.QueryInterface(IKsControl)
    print(f"\n===== idx{idx} {name} =====")
    for label,sg in SETS.items():
        got=[]
        for node_id in [None]+list(range(0,7)):
            for pid in range(0,12):
                for flags in (KS_GET,KS_BASICSUPPORT):
                    hr,data,ln=call(ks,sg,pid,flags,node_id)
                    if hr==0:
                        got.append((node_id,pid,hex(flags),ln,data[:16].hex()))
        if got:
            for g in got: print(f"  {label}: OK node={g[0]} pid={g[1]} flags={g[2]} ret={g[3]} {g[4]}")
        else:
            print(f"  {label}: no supported property (all SET_NOT_FOUND/other)")
