"""Мышь мимо игры: Raw Input (RIDEV_INPUTSINK) на своём окне сообщений — сдвиги и колесо приходят, даже когда на
переднем плане Ведьмак. Нужна, пока открыт экран Майнкрафта (инвентарь): курсор MC ведёт мост, а камера Ведьмака
стоит (mcwf_freeze); и для колеса — слоты хотбара (GetAsyncKeyState колесо не видит).

Сдвиги копятся под замком, мост забирает их раз в шаг цикла (take). Свой процесс Raw Input Ведьмака не трогает:
регистрация устройств — на процесс. Только Windows; ctypes, без зависимостей.
"""
from __future__ import annotations

import ctypes
import logging
import sys
import threading
from ctypes import wintypes

log = logging.getLogger("bridge.rawmouse")

WM_INPUT = 0x00FF
WM_CLOSE = 0x0010
RIDEV_INPUTSINK = 0x00000100
RID_INPUT = 0x10000003
RIM_TYPEMOUSE = 0
MOUSE_MOVE_ABSOLUTE = 0x01
RI_MOUSE_WHEEL = 0x0400
HWND_MESSAGE = wintypes.HWND(-3)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT), ("dwFlags", wintypes.DWORD),
                ("hwndTarget", wintypes.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD), ("hDevice", wintypes.HANDLE),
                ("wParam", wintypes.WPARAM)]


class RAWMOUSE(ctypes.Structure):
    # usFlags; union { ULONG ulButtons; struct { USHORT usButtonFlags; USHORT usButtonData; } } — union выровнен на 4
    _fields_ = [("usFlags", wintypes.USHORT), ("_pad", wintypes.USHORT), ("usButtonFlags", wintypes.USHORT),
                ("usButtonData", wintypes.USHORT), ("ulRawButtons", wintypes.ULONG), ("lLastX", wintypes.LONG),
                ("lLastY", wintypes.LONG), ("ulExtraInformation", wintypes.ULONG)]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("mouse", RAWMOUSE)]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


class RawMouse:
    """Поток с окном сообщений; take() -> (dx, dy, wheel) с прошлого вызова (wheel в «щелчках», 120 = 1)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._dx = 0
        self._dy = 0
        self._wheel = 0
        self._hwnd = None
        self._proc = None                    # держим ссылку: иначе колбэк соберёт GC
        self.ok = False

    @staticmethod
    def available() -> bool:
        return sys.platform == "win32"

    def start(self) -> None:
        threading.Thread(target=self._run, name="rawmouse", daemon=True).start()

    def take(self) -> tuple[int, int, float]:
        with self._lock:
            out = (self._dx, self._dy, self._wheel / 120.0)
            self._dx = self._dy = self._wheel = 0
        return out

    def stop(self) -> None:
        if self._hwnd:
            ctypes.windll.user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)

    def _run(self) -> None:
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        u32.DefWindowProcW.restype = LRESULT
        u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p, ctypes.POINTER(wintypes.UINT),
                                        wintypes.UINT]
        u32.CreateWindowExW.restype = wintypes.HWND
        u32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                        wintypes.HINSTANCE, wintypes.LPVOID]
        buf = RAWINPUT()

        def proc(hwnd, msg, wparam, lparam):
            if msg == WM_INPUT:
                size = wintypes.UINT(ctypes.sizeof(buf))
                if u32.GetRawInputData(lparam, RID_INPUT, ctypes.byref(buf), ctypes.byref(size),
                                       ctypes.sizeof(RAWINPUTHEADER)) > 0 and buf.header.dwType == RIM_TYPEMOUSE:
                    m = buf.mouse
                    with self._lock:
                        if not m.usFlags & MOUSE_MOVE_ABSOLUTE:
                            self._dx += m.lLastX
                            self._dy += m.lLastY
                        if m.usButtonFlags & RI_MOUSE_WHEEL:
                            self._wheel += ctypes.c_short(m.usButtonData).value
            elif msg == WM_CLOSE:
                u32.DestroyWindow(hwnd)
                u32.PostQuitMessage(0)
                return 0
            return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._proc = WNDPROC(proc)
        hinst = k32.GetModuleHandleW(None)
        wc = WNDCLASSEXW(ctypes.sizeof(WNDCLASSEXW), 0, self._proc, 0, 0, hinst, None, None, None, None,
                         "McwRawMouse", None)
        u32.RegisterClassExW(ctypes.byref(wc))
        self._hwnd = u32.CreateWindowExW(0, "McwRawMouse", "mcw raw mouse", 0, 0, 0, 0, 0, HWND_MESSAGE, None, hinst,
                                         None)
        if not self._hwnd:
            log.warning("Raw Input: окно не создалось (%d) — курсор в экранах MC и колесо не работают",
                        k32.GetLastError())
            return
        dev = RAWINPUTDEVICE(1, 2, RIDEV_INPUTSINK, self._hwnd)   # Generic Desktop / Mouse
        if not u32.RegisterRawInputDevices(ctypes.byref(dev), 1, ctypes.sizeof(dev)):
            log.warning("Raw Input: RegisterRawInputDevices не удался (%d)", k32.GetLastError())
            return
        self.ok = True
        log.info("Raw Input: мышь читается мимо игры (курсор экранов MC, колесо)")
        msg = wintypes.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))


if __name__ == "__main__":
    import time
    logging.basicConfig(level=logging.INFO)
    r = RawMouse()
    r.start()
    for _ in range(50):
        time.sleep(0.1)
        dx, dy, w = r.take()
        if dx or dy or w:
            print(dx, dy, w)
