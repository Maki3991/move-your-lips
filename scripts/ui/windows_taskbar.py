"""Windows shell properties for the Qt Quick main window."""

from __future__ import annotations

import ctypes
import os
import uuid
from pathlib import Path


APP_ID = "MoveYourLips.Desktop"


def set_taskbar_icon(hwnd: int, icon_path: Path) -> None:
    """Give a Python-hosted window its own taskbar group icon."""
    if os.name != "nt":
        return

    class GUID(ctypes.Structure):
        _fields_ = [
            ("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16),
            ("data3", ctypes.c_uint16), ("data4", ctypes.c_ubyte * 8),
        ]

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", ctypes.c_uint32)]

    class PROPVARIANT(ctypes.Structure):
        _fields_ = [
            ("vt", ctypes.c_uint16), ("reserved", ctypes.c_uint16 * 3),
            ("value", ctypes.c_void_p), ("padding", ctypes.c_byte * 8),
        ]

    def guid(value: str) -> GUID:
        return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    get_store = shell32.SHGetPropertyStoreForWindow
    get_store.argtypes = [ctypes.c_void_p, ctypes.POINTER(GUID),
                          ctypes.POINTER(ctypes.c_void_p)]
    get_store.restype = ctypes.c_long
    store = ctypes.c_void_p()
    iid = guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")  # IPropertyStore
    result = get_store(hwnd, ctypes.byref(iid), ctypes.byref(store))
    if result < 0:
        raise OSError(f"SHGetPropertyStoreForWindow failed: 0x{result & 0xffffffff:08x}")

    vtable = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    release = ctypes.WINFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p)(vtable[2])
    set_value = ctypes.WINFUNCTYPE(
        ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(PROPERTYKEY),
        ctypes.POINTER(PROPVARIANT),
    )(vtable[6])
    fmtid = guid("9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3")

    def set_string(pid: int, value: str) -> None:
        key = PROPERTYKEY(fmtid, pid)
        buffer = ctypes.create_unicode_buffer(value)
        prop = PROPVARIANT()
        prop.vt = 31  # VT_LPWSTR
        prop.value = ctypes.cast(buffer, ctypes.c_void_p).value
        hr = set_value(store, ctypes.byref(key), ctypes.byref(prop))
        if hr < 0:
            raise OSError(f"IPropertyStore::SetValue({pid}) failed: 0x{hr & 0xffffffff:08x}")

    try:
        # Setting the window ID last tells Explorer to refresh the group icon.
        set_string(3, f"{icon_path.resolve()},0")  # RelaunchIconResource
        set_string(5, APP_ID)  # AppUserModel.ID
    finally:
        release(store)
