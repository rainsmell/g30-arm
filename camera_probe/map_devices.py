import comtypes.client as cc
from comtypes import GUID
from pygrabber.dshow_graph import SystemDeviceEnum, DeviceCategories, get_moniker_name
from comtypes.persist import IPropertyBag

sde = SystemDeviceEnum()
en = sde.system_device_enum.CreateClassEnumerator(GUID(DeviceCategories.VideoInputDevice), dwFlags=0)
i = 0
while True:
    try:
        moniker, count = en.Next(1)
    except ValueError:
        break
    if count == 0:
        break
    pb = moniker.BindToStorage(0, 0, IPropertyBag._iid_).QueryInterface(IPropertyBag)
    name = pb.Read("FriendlyName", pErrorLog=None)
    try:
        path = pb.Read("DevicePath", pErrorLog=None)
    except Exception:
        path = "<none>"
    print(f"[{i}] {name}\n     {path}")
    i += 1
