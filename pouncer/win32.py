"""Windows helpers: window focus and keyboard input simulation (ctypes).

Used so POUNCer can type a callsign into the focused N1MM Entry Window
without ever asking N1MM to transmit a message. ``send_fkey`` is the one
exception - it presses F<n> on purpose so N1MM runs its function-key message
(which does transmit); it is only called from the opt-in TX buttons.
``send_enter`` presses Enter to log the QSO (no transmission).
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

try:
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    _WIN = True
except (AttributeError, OSError):
    _user32 = None
    _kernel32 = None
    _WIN = False

_SW_RESTORE = 9

_INPUT_KEYBOARD = 1
_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_VK_MENU = 0x12
_VK_W = 0x57
_VK_RETURN = 0x0D
_VK_PRIOR = 0x21  # Page Up   - N1MM: CW speed up
_VK_NEXT = 0x22   # Page Down - N1MM: CW speed down
_VK_F1 = 0x70


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),  # ULONG_PTR
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


_TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

# N1MM Logger+ runs as "N1MMLogger.net.exe" (no space). Names are compared
# with spaces removed, so "N1MM Logger.net.exe" would match too.
N1MM_PROCESS_PREFIX = "n1mmlogger"

if _WIN:
    _user32.SendInput.argtypes = (wintypes.UINT, ctypes.c_void_p, ctypes.c_int)
    _user32.SendInput.restype = wintypes.UINT
    _kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    _kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    _kernel32.Process32FirstW.argtypes = (ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32W))
    _kernel32.Process32FirstW.restype = wintypes.BOOL
    _kernel32.Process32NextW.argtypes = (ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32W))
    _kernel32.Process32NextW.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    _kernel32.CloseHandle.restype = wintypes.BOOL


def running_process_names() -> set[str] | None:
    """Lower-cased exe names of all running processes (None if unknown)."""
    if not _WIN:
        return None
    snap = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snap or snap == _INVALID_HANDLE_VALUE:
        return None
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        names: set[str] = set()
        ok = _kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            names.add(entry.szExeFile.lower())
            ok = _kernel32.Process32NextW(snap, ctypes.byref(entry))
        return names
    finally:
        _kernel32.CloseHandle(snap)


def is_n1mm_running() -> bool | None:
    """True/False if N1MM Logger+ is running; None when it can't be told."""
    names = running_process_names()
    if not names:  # scan failed or returned nothing: can't tell
        return None
    return any(n.replace(" ", "").startswith(N1MM_PROCESS_PREFIX) for n in names)


def force_foreground(hwnd: int) -> bool:
    """Bring the given window to the foreground, restoring it if minimized."""
    if not _WIN or not hwnd:
        return False
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, _SW_RESTORE)
    cur_thread = _kernel32.GetCurrentThreadId()
    fg = _user32.GetForegroundWindow()
    fg_thread = _user32.GetWindowThreadProcessId(fg, None)
    attached = False
    if cur_thread != fg_thread:
        attached = bool(_user32.AttachThreadInput(cur_thread, fg_thread, True))
    try:
        _user32.BringWindowToTop(hwnd)
        ok = bool(_user32.SetForegroundWindow(hwnd))
    finally:
        if attached:
            _user32.AttachThreadInput(cur_thread, fg_thread, False)
    return ok


def _key(vk: int = 0, scan: int = 0, flags: int = 0) -> "_INPUT":
    return _INPUT(
        type=_INPUT_KEYBOARD,
        u=_INPUTUNION(ki=_KEYBDINPUT(vk, scan, flags, 0, None)),
    )


def _send(inputs: list) -> None:
    if not _WIN or not inputs:
        return
    arr = (_INPUT * len(inputs))(*inputs)
    _user32.SendInput(len(inputs), ctypes.byref(arr), ctypes.sizeof(_INPUT))


def wipe_callsign() -> bool:
    """Send N1MM's wipe-callsign hotkey (Alt+W) to the focused window."""
    if not _WIN:
        return False
    _send([
        _key(vk=_VK_MENU),
        _key(vk=_VK_W),
        _key(vk=_VK_W, flags=_KEYEVENTF_KEYUP),
        _key(vk=_VK_MENU, flags=_KEYEVENTF_KEYUP),
    ])
    return True


def type_text(text: str) -> bool:
    """Type literal text into the focused control via unicode keystrokes."""
    if not _WIN:
        return False
    inputs: list = []
    for ch in text:
        code = ord(ch)
        inputs.append(_key(scan=code, flags=_KEYEVENTF_UNICODE))
        inputs.append(_key(scan=code, flags=_KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP))
    _send(inputs)
    return True


def send_fkey(n: int) -> bool:
    """Press function key F<n> (1-12) in the focused window.

    In N1MM this triggers the configured function-key message, which
    TRANSMITS (F4 is normally the {MYCALL} message).
    """
    if not _WIN or not 1 <= n <= 12:
        return False
    vk = _VK_F1 + (n - 1)
    _send([_key(vk=vk), _key(vk=vk, flags=_KEYEVENTF_KEYUP)])
    return True


def send_cw_speed(up: bool) -> bool:
    """Press PgUp (faster) / PgDn (slower) in the focused N1MM Entry Window.

    N1MM changes the CW speed by the step set in its Configurer (usually
    2 WPM). Does not transmit.
    """
    if not _WIN:
        return False
    vk = _VK_PRIOR if up else _VK_NEXT
    # extended key: PgUp/PgDn on the navigation block, not the numpad
    _send([_key(vk=vk, flags=_KEYEVENTF_EXTENDEDKEY),
           _key(vk=vk, flags=_KEYEVENTF_EXTENDEDKEY | _KEYEVENTF_KEYUP)])
    return True


def send_enter() -> bool:
    """Press Enter in the focused window (N1MM logs the QSO). Does not transmit."""
    if not _WIN:
        return False
    _send([_key(vk=_VK_RETURN), _key(vk=_VK_RETURN, flags=_KEYEVENTF_KEYUP)])
    return True
