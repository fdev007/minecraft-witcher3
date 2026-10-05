"""Горячие клавиши этапа 1 без правок игры: опрос GetAsyncKeyState (ctypes), только когда на переднем плане окно
процесса witcher3.exe. В игру ничего не шлём и фокус не трогаем: только читаем состояние клавиш.

Срабатывание — по нажатию (фронт «отпущена -> нажата»), пока окно игры активно. Клавиша, зажатая в другом окне,
при переключении в игру не срабатывает: состояние запоминается и вне игры.
"""
from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
from typing import Callable, Iterable, Optional

log = logging.getLogger("bridge.keys")

# Имя в конфиге -> virtual-key code (winuser.h). Буквы и цифры — сами собой.
VK = {
    "MOUSE1": 0x01, "MOUSE2": 0x02, "MOUSE3": 0x04, "MOUSE4": 0x05, "MOUSE5": 0x06,
    "BACKSPACE": 0x08, "TAB": 0x09, "ENTER": 0x0D, "SHIFT": 0x10, "CTRL": 0x11, "ALT": 0x12, "PAUSE": 0x13,
    "CAPSLOCK": 0x14, "ESCAPE": 0x1B, "SPACE": 0x20, "PAGEUP": 0x21, "PAGEDOWN": 0x22, "END": 0x23, "HOME": 0x24,
    "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28, "INSERT": 0x2D, "DELETE": 0x2E,
    "MULTIPLY": 0x6A, "ADD": 0x6B, "SUBTRACT": 0x6D, "DECIMAL": 0x6E, "DIVIDE": 0x6F,
    "LSHIFT": 0xA0, "RSHIFT": 0xA1, "LCTRL": 0xA2, "RCTRL": 0xA3, "LALT": 0xA4, "RALT": 0xA5,
    "SEMICOLON": 0xBA, "EQUALS": 0xBB, "COMMA": 0xBC, "MINUS": 0xBD, "PERIOD": 0xBE, "SLASH": 0xBF, "GRAVE": 0xC0,
    "LBRACKET": 0xDB, "BACKSLASH": 0xDC, "RBRACKET": 0xDD, "APOSTROPHE": 0xDE,
    **{chr(c): c for c in range(ord("A"), ord("Z") + 1)},
    **{str(d): 0x30 + d for d in range(10)},
    **{f"F{n}": 0x6F + n for n in range(1, 25)},
    **{f"NUMPAD{n}": 0x60 + n for n in range(10)},
}

# Имя в конфиге -> имя клавиши Ведьмака (IK_*), как в input.settings: для проверки конфликтов
IK = {
    "MOUSE1": "IK_LeftMouse", "MOUSE2": "IK_RightMouse", "MOUSE3": "IK_MiddleMouse", "MOUSE4": "IK_Mouse4",
    "MOUSE5": "IK_Mouse5", "BACKSPACE": "IK_Backspace", "TAB": "IK_Tab", "ENTER": "IK_Enter", "SHIFT": "IK_Shift",
    "CTRL": "IK_Ctrl", "ALT": "IK_Alt", "PAUSE": "IK_Pause", "CAPSLOCK": "IK_CapsLock", "ESCAPE": "IK_Escape",
    "SPACE": "IK_Space", "PAGEUP": "IK_PageUp", "PAGEDOWN": "IK_PageDown", "END": "IK_End", "HOME": "IK_Home",
    "LEFT": "IK_Left", "UP": "IK_Up", "RIGHT": "IK_Right", "DOWN": "IK_Down", "INSERT": "IK_Insert",
    "DELETE": "IK_Delete", "MULTIPLY": "IK_NumStar", "ADD": "IK_NumPlus", "SUBTRACT": "IK_NumMinus",
    "DECIMAL": "IK_NumPeriod", "DIVIDE": "IK_NumSlash", "LSHIFT": "IK_LShift", "RSHIFT": "IK_RShift",
    "LCTRL": "IK_LControl", "RCTRL": "IK_RControl", "LALT": "IK_Alt", "RALT": "IK_Alt", "SEMICOLON": "IK_Semicolon",
    "EQUALS": "IK_Equals", "COMMA": "IK_Comma", "MINUS": "IK_Minus", "PERIOD": "IK_Period", "SLASH": "IK_Slash",
    "GRAVE": "IK_Tilde", "LBRACKET": "IK_LeftBracket", "BACKSLASH": "IK_Backslash", "RBRACKET": "IK_RightBracket",
    "APOSTROPHE": "IK_SingleQuote",
    **{chr(c): f"IK_{chr(c)}" for c in range(ord("A"), ord("Z") + 1)},
    **{str(d): f"IK_{d}" for d in range(10)},
    **{f"F{n}": f"IK_F{n}" for n in range(1, 25)},
    **{f"NUMPAD{n}": f"IK_NumPad{n}" for n in range(10)},
}

# Клавиши, которые игра или окружение держат сами, мимо input.settings
HARDCODED = {
    "F5": "быстрое сохранение (igmOptions.ws: permaLocked)",
    "F7": "RT в не-final сборке (igmOptions.ws)",
    "F8": "мод 13072 (первое лицо) дописывает IK_F8",
    "F9": "быстрая загрузка",
    "F12": "скриншот Steam",
    "GRAVE": "консоль (DBGConsoleOn)",
}

# Действия, слушатели которых ваниль регистрирует только в не-final сборке (game/player/playerInput.ws:173-182,
# обработчики ещё и проверяют IsFinalBuild, :3555+): в розничной игре их клавиши ничего не делают
INERT = {"Debug_SpeedUp", "Debug_Hit", "Debug_KillTarget", "Debug_KillAllEnemies", "Debug_KillAllTargetingPlayer",
         "PanelFakeHud", "Debug_TeleportToPin"}

ACTIONS = (("toggle", "break", "place", "slot_prev", "slot_next") + tuple(f"slot{i}" for i in range(1, 10))
           + ("craft_menu", "craft_prev", "craft_next", "craft_do", "summon", "fp", "inventory", "horse"))


def vk_of(name: str) -> int:
    n = name.strip().upper()
    if re.fullmatch(r"0X[0-9A-F]{1,2}", n):
        return int(n, 16)
    if n not in VK:
        raise ValueError(f"неизвестная клавиша {name!r}; есть: {', '.join(sorted(VK))}")
    return VK[n]


def parse_bindings(keys: dict) -> dict[str, tuple[int, ...]]:
    """{'break': ['MOUSE4', '8'], ...} -> {'break': (5, 56), ...}. Одна клавиша на два действия — ошибка."""
    out: dict[str, tuple[int, ...]] = {}
    owner: dict[int, str] = {}
    for action, names in keys.items():
        if action not in ACTIONS:
            raise ValueError(f"input.keys: неизвестное действие {action!r} (есть {', '.join(ACTIONS)})")
        if isinstance(names, str):
            names = [names]
        vks = tuple(vk_of(n) for n in names)
        for vk in vks:
            if vk in owner and owner[vk] != action:
                raise ValueError(f"input.keys: клавиша 0x{vk:02X} и у {owner[vk]!r}, и у {action!r}")
            owner[vk] = action
        out[action] = vks
    return out


class Hotkeys:
    """Один шаг опроса — step(): какие действия сработали. key_down(vk) и is_foreground() подменяются в тестах."""

    def __init__(self, bindings: dict[str, tuple[int, ...]], key_down: Callable[[int], bool],
                 is_foreground: Callable[[], bool]) -> None:
        self.bindings = bindings
        self.key_down = key_down
        self.is_foreground = is_foreground
        self.vks = sorted({vk for vks in bindings.values() for vk in vks})
        self._prev: dict[int, bool] = {}

    def step(self) -> list[str]:
        fg = bool(self.is_foreground())
        state = {vk: bool(self.key_down(vk)) for vk in self.vks}
        fired: list[str] = []
        if fg:
            # фронт действия, а не клавиши: вторая клавиша того же действия, пока первая зажата, дубля не даёт
            for action, vks in self.bindings.items():
                if any(state[vk] for vk in vks) and not any(self._prev.get(vk, False) for vk in vks):
                    fired.append(action)
        self._prev = state
        return fired

    def run(self, on_action: Callable[[str], None], stop: threading.Event, hz: float = 100.0) -> None:
        period = 1.0 / max(1.0, hz)
        while not stop.is_set():
            try:
                for a in self.step():
                    on_action(a)
            except Exception:
                log.exception("опрос клавиш упал")
            time.sleep(period)    # time.sleep на Windows точнее Event.wait (~2 мс против ~15 мс, docs/NOTES.md)


class Passthrough:
    """Клавиши и кнопки, которые уходят в клиент Майнкрафта как есть: и нажатие, и отпускание. step() -> [(имя, True|False)].

    Нажатие засчитывается только фронтом внутри окна игры (кнопка, зажатая в другом окне, — например, клик, которым
    вернулись в игру, — не нажимает). Окно игры ушло с переднего плана — всё нажатое отпускается."""

    def __init__(self, keys: dict[str, int], key_down: Callable[[int], bool], is_foreground: Callable[[], bool]) -> None:
        self.keys = keys
        self.key_down = key_down
        self.is_foreground = is_foreground
        self._raw: dict[str, bool] = {}
        self.held: set[str] = set()

    def step(self) -> list[tuple[str, bool]]:
        fg = bool(self.is_foreground())
        out: list[tuple[str, bool]] = []
        for name, vk in self.keys.items():
            raw = bool(self.key_down(vk))
            if name in self.held:
                if not raw or not fg:
                    self.held.discard(name)
                    out.append((name, False))
            elif raw and not self._raw.get(name, False) and fg:
                self.held.add(name)
                out.append((name, True))
            self._raw[name] = raw
        return out

    def run(self, on_edge: Callable[[str, bool], None], stop: threading.Event, hz: float = 100.0) -> None:
        period = 1.0 / max(1.0, hz)
        while not stop.is_set():
            try:
                for name, down in self.step():
                    on_edge(name, down)
            except Exception:
                log.exception("опрос сквозных клавиш упал")
            time.sleep(period)


# ---- Win32 -----------------------------------------------------------------------------------------------------
def win32_key_down() -> Callable[[int], bool]:
    import ctypes
    gaks = ctypes.windll.user32.GetAsyncKeyState
    gaks.argtypes = [ctypes.c_int]
    gaks.restype = ctypes.c_short
    return lambda vk: bool(gaks(vk) & 0x8000)


class Win32Foreground:
    """Окно на переднем плане принадлежит процессу с именем exe? Имя по pid кешируется на ttl секунд."""

    def __init__(self, exe: str = "witcher3.exe", ttl: float = 5.0) -> None:
        import ctypes
        from ctypes import wintypes
        self.exe = exe.lower()
        self.ttl = ttl
        self._cache: dict[int, tuple[float, str]] = {}
        self.ct = ctypes
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        u32.GetForegroundWindow.restype = wintypes.HWND
        u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u32.GetWindowThreadProcessId.restype = wintypes.DWORD
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                   ctypes.POINTER(wintypes.DWORD)]
        k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.u32, self.k32, self.wt = u32, k32, wintypes

    def _name(self, pid: int) -> str:
        now = time.monotonic()
        hit = self._cache.get(pid)
        if hit and now - hit[0] < self.ttl:
            return hit[1]
        name = ""
        h = self.k32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            try:
                buf = self.ct.create_unicode_buffer(1024)
                size = self.wt.DWORD(len(buf))
                if self.k32.QueryFullProcessImageNameW(h, 0, buf, self.ct.byref(size)):
                    name = os.path.basename(buf.value).lower()
            finally:
                self.k32.CloseHandle(h)
        if len(self._cache) > 64:
            self._cache.clear()
        self._cache[pid] = (now, name)
        return name

    def __call__(self) -> bool:
        hwnd = self.u32.GetForegroundWindow()
        if not hwnd:
            return False
        pid = self.wt.DWORD()
        self.u32.GetWindowThreadProcessId(hwnd, self.ct.byref(pid))
        return bool(pid.value) and self._name(pid.value) == self.exe


def available() -> bool:
    return sys.platform == "win32"


# ---- конфликты с привязками Ведьмака -------------------------------------------------------------------------------
def default_input_settings() -> str:
    return os.path.join(os.path.expanduser("~"), "Documents", "The Witcher 3", "input.settings")


def bound_keys(path: str) -> dict[str, set[str]]:
    """input.settings (только чтение) -> {IK_*: {действия}} по всем контекстам."""
    out: dict[str, set[str]] = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r"\s*(IK_\w+)=\(Action=(\w+)", line)
            if m and m.group(1) != "IK_None":
                out.setdefault(m.group(1), set()).add(m.group(2))
    return out


def conflicts(keys: dict, bound: dict[str, set[str]]) -> list[str]:
    """Клавиши конфига, занятые игрой: по input.settings (кроме INERT) и по HARDCODED. Пусто — конфликтов нет."""
    out = []
    for action, names in keys.items():
        for n in ([names] if isinstance(names, str) else names):
            u = n.strip().upper()
            ik = IK.get(u)
            live = sorted(bound.get(ik, set()) - INERT) if ik else []
            if live:
                out.append(f"{action}: {n} = {ik}, в игре: {', '.join(live)}")
            if u in HARDCODED:
                out.append(f"{action}: {n} — {HARDCODED[u]}")
    return out


def check_conflicts(keys: dict, path: Optional[str] = None) -> Optional[list[str]]:
    """None — файла нет (проверять нечем)."""
    p = path or default_input_settings()
    if not os.path.exists(p):
        return None
    return conflicts(keys, bound_keys(p))


def names_for(vks: Iterable[int]) -> list[str]:
    rev = {v: k for k, v in VK.items()}
    return [rev.get(vk, f"0x{vk:02X}") for vk in vks]
