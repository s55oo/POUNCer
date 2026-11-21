"""A Windows system-tray (notification area) icon, stdlib only (ctypes).

``TrayIcon(icon_path, tip, on_event).show()`` puts the icon in the tray on a
thread of its own (a hidden window and its own message loop - Tk is never
touched from there); ``hide()`` removes it again. ``on_event`` gets "show"
(left click / double click, or the menu's Show) or "exit" (the menu's Exit),
called on the tray thread: the caller hands it to its own UI thread (the
Autostarter polls a queue with ``root.after``). Off Windows, or if anything
fails, ``show()`` returns False and nothing else happens - the caller then
keeps the plain minimize (v0.7.242).
"""

from __future__ import annotations

import logging
import sys
import threading

log = logging.getLogger(__name__)

AVAILABLE = sys.platform == "win32"

if AVAILABLE:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _shell32 = ctypes.windll.shell32
    _kernel32 = ctypes.windll.kernel32

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                    ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                    ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                    ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
                    ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                    ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                    ("dwInfoFlags", wintypes.DWORD), ("guidItem", GUID),
                    ("hBalloonIcon", wintypes.HICON)]

    _user32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                       wintypes.LPARAM)
    _user32.DefWindowProcW.restype = LRESULT
    _user32.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                        wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                        wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID)
    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.RegisterClassW.argtypes = (ctypes.POINTER(WNDCLASSW),)
    _user32.RegisterClassW.restype = wintypes.ATOM
    _user32.UnregisterClassW.argtypes = (wintypes.LPCWSTR, wintypes.HINSTANCE)
    _user32.LoadImageW.argtypes = (wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                   ctypes.c_int, ctypes.c_int, wintypes.UINT)
    _user32.LoadImageW.restype = wintypes.HANDLE
    _user32.LoadIconW.argtypes = (wintypes.HINSTANCE, wintypes.LPVOID)
    _user32.LoadIconW.restype = wintypes.HICON
    _user32.DestroyIcon.argtypes = (wintypes.HICON,)
    _user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                    wintypes.UINT, wintypes.UINT)
    _user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
    _user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
    _user32.DispatchMessageW.restype = LRESULT
    _user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                     wintypes.LPARAM)
    _user32.DestroyWindow.argtypes = (wintypes.HWND,)
    _user32.CreatePopupMenu.restype = wintypes.HMENU
    _user32.AppendMenuW.argtypes = (wintypes.HMENU, wintypes.UINT, ctypes.c_size_t,
                                    wintypes.LPCWSTR)
    _user32.TrackPopupMenu.argtypes = (wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.LPVOID)
    _user32.DestroyMenu.argtypes = (wintypes.HMENU,)
    _user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    _user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
    _user32.RegisterWindowMessageW.argtypes = (wintypes.LPCWSTR,)
    _user32.RegisterWindowMessageW.restype = wintypes.UINT
    _shell32.Shell_NotifyIconW.argtypes = (wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW))
    _shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    _kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE

WM_DESTROY, WM_CLOSE, WM_NULL, WM_APP = 0x0002, 0x0010, 0x0000, 0x8000
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0202, 0x0203, 0x0205
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 1, 2, 4
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x10, 0x40
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100
MF_STRING, MF_SEPARATOR = 0x0000, 0x0800
IDI_APPLICATION = 32512
CALLBACK = WM_APP + 1
CMD_SHOW, CMD_EXIT = 1, 2


class TrayIcon:
    """One tray icon, shown and hidden as often as needed."""

    _count = 0

    def __init__(self, icon_path: str, tip: str, on_event,
                 menu=(("Show", "show"), ("Exit", "exit"))) -> None:
        self.icon_path = icon_path
        self.tip = (tip or "")[:127]
        self.on_event = on_event
        self.menu = tuple(menu)
        self._hwnd = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._ok = False
        self._lock = threading.Lock()

    # ---- public -------------------------------------------------------------
    def visible(self) -> bool:
        return self._hwnd is not None

    def show(self) -> bool:
        """Put the icon in the tray (no-op if it is there). False = no tray
        here (not Windows / it failed): keep the window's plain minimize."""
        if not AVAILABLE:
            return False
        with self._lock:
            if self._hwnd is not None:
                return True
            self._ready.clear()
            self._ok = False
            self._thread = threading.Thread(target=self._run, daemon=True, name="tray")
            self._thread.start()
        self._ready.wait(5.0)
        return self._ok

    def hide(self) -> None:
        """Remove the icon (no-op if it is not there)."""
        hwnd = self._hwnd
        if hwnd is not None and AVAILABLE:
            _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(3.0)

    # ---- tray thread --------------------------------------------------------
    def _emit(self, what: str) -> None:
        try:
            self.on_event(what)
        except Exception:  # noqa: BLE001 - never kill the tray thread
            log.exception("tray event %s", what)

    def _popup(self, hwnd) -> None:
        menu = _user32.CreatePopupMenu()
        if not menu:
            return
        try:
            for i, (label, _what) in enumerate(self.menu):
                if label == "-":
                    _user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                else:
                    _user32.AppendMenuW(menu, MF_STRING, i + 1, label)
            pt = wintypes.POINT()
            _user32.GetCursorPos(ctypes.byref(pt))
            _user32.SetForegroundWindow(hwnd)      # else the menu never closes
            cmd = _user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                                         pt.x, pt.y, 0, hwnd, None)
            _user32.PostMessageW(hwnd, WM_NULL, 0, 0)
        finally:
            _user32.DestroyMenu(menu)
        if cmd and 0 < cmd <= len(self.menu):
            self._emit(self.menu[cmd - 1][1])

    def _nid(self, hwnd, hicon) -> "NOTIFYICONDATAW":
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = CALLBACK
        nid.hIcon = hicon
        nid.szTip = self.tip
        return nid

    def _run(self) -> None:
        hinst = _kernel32.GetModuleHandleW(None)
        TrayIcon._count += 1
        cls_name = f"POUNCerTray{id(self)}_{TrayIcon._count}"
        hicon = None
        own_icon = False
        state = {"nid": None}
        taskbar_created = _user32.RegisterWindowMessageW("TaskbarCreated")

        def wndproc(hwnd, msg, wparam, lparam):
            try:
                if msg == CALLBACK:
                    ev = lparam & 0xFFFF
                    if ev in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                        self._emit("show")
                    elif ev == WM_RBUTTONUP:
                        self._popup(hwnd)
                    return 0
                if msg == taskbar_created and state["nid"] is not None:
                    _shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(state["nid"]))  # Explorer restarted
                    return 0
                if msg == WM_CLOSE:
                    if state["nid"] is not None:
                        _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(state["nid"]))
                        state["nid"] = None
                    _user32.DestroyWindow(hwnd)
                    return 0
                if msg == WM_DESTROY:
                    _user32.PostQuitMessage(0)
                    return 0
            except Exception:  # noqa: BLE001
                log.exception("tray window message %s", msg)
            return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        proc = WNDPROC(wndproc)                       # kept alive for the loop
        wc = WNDCLASSW()
        wc.lpfnWndProc = proc
        wc.hInstance = hinst
        wc.lpszClassName = cls_name
        try:
            if not _user32.RegisterClassW(ctypes.byref(wc)):
                raise OSError("RegisterClassW failed")
            hwnd = _user32.CreateWindowExW(0, cls_name, cls_name, 0, 0, 0, 0, 0,
                                           None, None, hinst, None)
            if not hwnd:
                raise OSError("CreateWindowExW failed")
            if self.icon_path:
                hicon = _user32.LoadImageW(None, self.icon_path, IMAGE_ICON, 0, 0,
                                           LR_LOADFROMFILE | LR_DEFAULTSIZE)
                own_icon = bool(hicon)
            if not hicon:
                hicon = _user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))
            nid = self._nid(hwnd, hicon)
            if not _shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
                _user32.DestroyWindow(hwnd)
                raise OSError("Shell_NotifyIconW failed")
            state["nid"] = nid
            self._hwnd = hwnd
            self._ok = True
        except Exception as exc:  # noqa: BLE001
            log.warning("tray icon not available: %s", exc)
            self._ready.set()
            _user32.UnregisterClassW(cls_name, hinst)
            return
        self._ready.set()
        msg = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))
        self._hwnd = None
        if own_icon:
            _user32.DestroyIcon(hicon)
        _user32.UnregisterClassW(cls_name, hinst)
        del proc
