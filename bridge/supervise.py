"""Сторож процессов: упал сервер Майнкрафта или скрытый клиент — мост перезапускает их их же скриптами
(mc\\run_server.ps1, mcclient\\run_client.ps1: сначала -Stop, потом запуск). Решение — по связи моста: связи нет дольше
down_after_s (или клиент на связи, но не в мире: сервер перезапускали, он висит на экране «связь потеряна») — рестарт,
не чаще min_interval_s. Скрипт ждёт «Done» / «жду мост» до нескольких минут, поэтому идёт в своём потоке: цикл моста
не ждёт. Только Windows (powershell); runner подменяется в тестах.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from typing import Callable, Optional, Sequence

log = logging.getLogger("bridge.supervise")
CREATE_NO_WINDOW = 0x08000000


def powershell(script: str, args: Sequence[str], timeout: float) -> tuple[int, str]:
    """powershell -File script args -> (код, хвост вывода). Окна не показывает."""
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    except subprocess.TimeoutExpired:
        return -1, f"не уложился в {timeout:.0f} с"
    except OSError as e:
        return -2, str(e)
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode, " | ".join(ln.strip() for ln in out.splitlines() if ln.strip())[-300:]


class Restarter:
    def __init__(self, name: str, script: str, down_after_s: float, min_interval_s: float, timeout_s: float,
                 runner: Optional[Callable[[str, Sequence[str], float], tuple[int, str]]] = None) -> None:
        self.name = name
        self.script = script
        self.down_after = down_after_s
        self.min_interval = min_interval_s
        self.timeout = timeout_s
        self.runner = runner or powershell
        self.down_since: Optional[float] = None
        self.last = -1e9
        self.busy = False
        self.restarts = 0
        self.last_result: Optional[str] = None

    def step(self, now: float, ok: bool, allowed: bool = True) -> bool:
        """ok — процесс в порядке (связь есть); allowed — можно ли сейчас (клиенту нужен живой сервер). -> начат рестарт."""
        if ok:
            self.down_since = None
            return False
        if self.down_since is None:
            self.down_since = now
            return False
        if (not allowed or self.busy or now - self.down_since < self.down_after
                or now - self.last < self.min_interval):
            return False
        self.last = now
        self.busy = True
        self.restarts += 1
        log.warning("%s: нет связи %.0f с — перезапускаю (%s)", self.name, now - self.down_since,
                    os.path.basename(self.script))
        threading.Thread(target=self._run, name="restart-" + self.name, daemon=True).start()
        return True

    def _run(self) -> None:
        try:
            code, out = self.runner(self.script, ["-Stop"], 60.0)
            log.info("%s: стоп -> %s %s", self.name, code, out)
            code, out = self.runner(self.script, [], self.timeout)
            self.last_result = f"{code} {out}"
            (log.info if code == 0 else log.warning)("%s: запуск -> %s %s", self.name, code, out)
        finally:
            self.busy = False
            self.down_since = None
