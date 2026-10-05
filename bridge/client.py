"""Скрытый клиент Майнкрафта (..\\mcclient, мод mcwc) со стороны моста: связь, оверлей и режим «Стив».

Клиент играет Геральтом на сервере MCW и рисует только руку, предмет и HUD на ключевом цвете; оверлей
(..\\overlay\\mcw_overlay.exe) снимает его окно и кладёт поверх Ведьмака. Мост:
  * держит TCP с клиентом (127.0.0.1:client.port, JSON-строки, протокол — mcclient/README.md);
  * шлёт ему позу Геральта из каждого mcw_poll (pose): клиент стоит там же, где Геральт в Ведьмаке, и смотрит туда же,
    куда камера, — луч прицела MC совпадает с центром экрана Ведьмака;
  * в режиме «Стив» (вид от первого лица в Ведьмаке + клиент в мире) отдаёт клиенту кнопки мыши (client.pass), слоты
    и колесо, показывает оверлей, а Ведьмаку блокирует удары (mcwf_block — решает Bridge);
  * с client.drive в режиме «Стив» ходит сам клиент: WASD, прыжок, присед и бег (client.pass_drive) — ему, позы ему
    больше не шлём (follow off), а его глаз (eye, каждый кадр) Bridge отдаёт Ведьмаку: камера в глазу MC, Геральт —
    кукла в ногах (mcwf_mc); взгляд Ведьмака возвращается клиенту (look);
  * землю Ведьмака (непрерывные высоты колонн из mcw_ground) клиент получает сообщениями terrain и ходит по ней, а не
    по округлённому до блоков barrier-рельефу;
  * запускает оверлей, пока клиент на связи, и перезапускает, если тот вышел.

Только stdlib. Состояние — только в главном потоке моста; потоки здесь — чтение сокета и вывода оверлея.
"""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import threading
import time
from typing import Any, Optional

import config as config_mod
import hotkeys
from links import McLink

log = logging.getLogger("bridge.client")

GLFW_KEY_0 = 48          # GLFW_KEY_1..9 = 49..57, как ASCII


def parse_pass(table: dict) -> dict[str, tuple[int, str, int]]:
    """{'MOUSE1': 'btn:0', 'F': 'key:70'} -> {'MOUSE1': (vk, 'btn', 0), ...}: клавиша Ведьмака -> ввод клиента."""
    out: dict[str, tuple[int, str, int]] = {}
    for name, target in table.items():
        vk = hotkeys.vk_of(name)
        kind, _, code = str(target).partition(":")
        if kind not in ("btn", "key") or not code.lstrip("-").isdigit():
            raise ValueError(f"client.pass[{name!r}] = {target!r}: ждали 'btn:<0..7>' или 'key:<GLFW_KEY_*>'")
        c = int(code)
        if kind == "btn" and not 0 <= c <= 7 or kind == "key" and not 32 <= c <= 348:
            raise ValueError(f"client.pass[{name!r}] = {target!r}: код вне диапазона")
        out[name.strip().upper()] = (vk, kind, c)
    return out


class Overlay:
    """mcw_overlay.exe как дочерний процесс: stdin 'mode 1/0', 'quit'; stdout — в журнал. Вышел — перезапуск."""

    RESTART_S = 5.0

    def __init__(self, exe: str, args: list[str]) -> None:
        self.exe = exe
        self.args = args
        self.proc: Optional[subprocess.Popen] = None
        self.mode = False
        self.next_start = 0.0
        self.last_line = ""
        self.starts = 0

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def ensure(self, now: float) -> None:
        if self.running or now < self.next_start:
            return
        if self.proc is not None:
            log.warning("оверлей вышел (код %s, последнее: %s), перезапуск", self.proc.returncode, self.last_line)
            self.proc = None
        self.next_start = now + self.RESTART_S
        try:
            self.proc = subprocess.Popen([self.exe, *self.args, "--mode", "1" if self.mode else "0"],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         text=True, encoding="utf-8", errors="replace", bufsize=1,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            log.warning("оверлей не запустился (%s): %s", self.exe, e)
            self.proc = None
            return
        self.starts += 1
        log.info("оверлей запущен: pid %d", self.proc.pid)
        threading.Thread(target=self._read, args=(self.proc,), name="overlay-out", daemon=True).start()

    def _read(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:            # type: ignore[union-attr]
            line = line.strip()
            if not line:
                continue
            self.last_line = line
            (log.debug if line.startswith("captured") else log.info)("оверлей: %s", line)

    def set_mode(self, on: bool) -> None:
        self.mode = on
        self._write("mode 1" if on else "mode 0")

    def _write(self, line: str) -> None:
        if not self.running:
            return
        try:
            self.proc.stdin.write(line + "\n")          # type: ignore[union-attr]
            self.proc.stdin.flush()                     # type: ignore[union-attr]
        except OSError:
            pass

    def stop(self) -> None:
        if self.proc is None:
            return
        self._write("quit")
        try:
            self.proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc = None
        self.next_start = 0.0


class ClientSide:
    def __init__(self, cfg, q: queue.Queue) -> None:
        c = cfg.client
        self.cfg = c
        self.link = McLink(c.host, c.port, q, connect_timeout=c.connect_timeout_s, tag="cl")
        self.delay = c.reconnect_min_s
        self.next = 0.0
        self.hello: Optional[dict] = None
        self.state: Optional[dict] = None
        self.active = False
        self.passes = parse_pass(c["pass"])
        self.drive = c.drive
        self.passes_drive = parse_pass(c["pass_drive"])
        both = set(self.passes) & set(self.passes_drive)
        if both:
            raise ValueError(f"client.pass и client.pass_drive: одна клавиша в обоих — {sorted(both)}")
        self.follow: Optional[bool] = None       # что сказали клиенту (follow); None — ещё ничего
        self.fov_sent: Optional[float] = None
        self.eye: Optional[dict] = None          # последний eye клиента
        self.eye_t = -1e9
        self.eye_new = False
        self.horses: list = []                   # лошади рядом с игроком MC (horses), поза на кадр
        self.horses_new = False
        self.arrows: list = []                   # стрелы рядом с игроком MC (arrows), на кадр
        self.arrows_new = False
        self.terrain_reset = False               # послать клиенту terrain{reset} (сменился мир Ведьмака)
        self.stats: dict[str, int] = {}
        self.overlay: Optional[Overlay] = None
        if c.overlay:
            exe = config_mod.resolve(c.overlay)
            if os.path.exists(exe):
                self.overlay = Overlay(exe, ["--src", c.window, "--host", c.host_window, "--key", "magenta"])
            else:
                log.warning("оверлей не найден: %s (overlay\\build.bat); клиент без картинки поверх Ведьмака", exe)

    # ------------------------------------------------------------------------------------------------ состояние
    @property
    def up(self) -> bool:
        return self.link.up and self.hello is not None

    @property
    def in_world(self) -> bool:
        return self.up and bool(self.state and self.state.get("world"))

    @property
    def driving(self) -> bool:
        """Режим «Стив», и ходит сам клиент (client.drive)."""
        return self.active and self.drive

    def eye_fresh(self, now: float) -> bool:
        return self.eye is not None and now - self.eye_t < 0.5

    def _count(self, k: str) -> None:
        self.stats[k] = self.stats.get(k, 0) + 1

    # ------------------------------------------------------------------------------------------------ сеть
    def step(self, now: float, want_active: bool) -> bool:
        """Раз в шаг цикла моста. Возвращает, активен ли режим «Стив» после шага."""
        if not self.link.up and not self.link.connecting and now >= self.next:
            self.link.start_connect()
        if self.overlay is not None:
            if self.up:
                self.overlay.ensure(now)
            elif self.overlay.running:
                self.overlay.stop()
        self.set_active(want_active and self.in_world)
        want_follow = not self.driving
        if self.up and self.follow != want_follow:
            if self.send({"t": "follow", "on": want_follow}):
                self.follow = want_follow
                log.info("клиент MC: %s", "идёт за Ведьмаком" if want_follow else "ходит сам (физика MC), Геральт - кукла")
        return self.active

    def on_sock(self, gen: int, sock) -> None:
        if self.link.attach(gen, sock):
            log.info("клиент MC: соединение есть, жду hello")

    def on_fail(self, gen: int, err) -> None:
        if self.link.failed(gen):
            self.next = time.perf_counter() + self.delay
            self.delay = min(self.cfg.reconnect_max_s, self.delay * 2)
            log.debug("клиент MC не отвечает (%s), повтор через %.1f с", err, self.delay)

    def on_down(self, gen: int, err) -> None:
        if gen != self.link.gen or not self.link.up:
            return
        log.warning("клиент MC: соединение потеряно (%s)", err)
        self.link.close()
        self.hello = None
        self.state = None
        self.eye = None
        self.horses, self.horses_new = [], True  # клиента нет — лошадей в Ведьмаке спрятать
        self.arrows, self.arrows_new = [], True  # и стрелы
        self.set_active(False)
        self.next = time.perf_counter() + self.delay
        self._count("lost")

    def on_msg(self, gen: int, m: dict[str, Any]) -> None:
        if gen != self.link.gen or not self.link.up:
            return
        t = m.get("t")
        if t == "eye":
            self.eye = m
            self.eye_t = time.perf_counter()
            self.eye_new = True
        elif t == "horses":
            self.horses = m.get("h") or []
            self.horses_new = True
        elif t == "arrows":
            self.arrows = m.get("a") or []
            self.arrows_new = True
        elif t == "state":
            was = bool(self.state and self.state.get("world"))
            self.state = m
            if bool(m.get("world")) != was:
                log.info("клиент MC: %s (окно %s, %s FPS)", "в мире" if m.get("world") else "вне мира", m.get("win"),
                         m.get("fps"))
        elif t == "hello":
            self.hello = m
            self.follow = None                   # клиент мог перезапуститься: follow, FOV и земля — заново
            self.fov_sent = None
            self.terrain_reset = True
            self.delay = self.cfg.reconnect_min_s
            self._count("hello")
            log.info("клиент MC: hello, Minecraft %s, мод %s, игрок %s, окно %r %s, в мире: %s", m.get("mc"),
                     m.get("mod"), m.get("name"), m.get("title"), m.get("win"), m.get("world"))
        elif t == "error":
            log.warning("клиент MC: error %s: %s", m.get("why"), m.get("text"))

    def send(self, msg: dict[str, Any]) -> bool:
        return self.up and self.link.send(msg)

    # ------------------------------------------------------------------------------------------------ режим «Стив»
    def set_active(self, on: bool) -> None:
        if on == self.active:
            return
        self.active = on
        log.info("режим Стива: %s", "вкл (мышь и слоты - клиенту, оверлей виден)" if on else "выкл")
        if not on:
            self.send({"t": "release"})
        if self.overlay is not None:
            self.overlay.set_mode(on)

    def pose(self, x: float, y: float, z: float, yaw: float, pitch: float) -> None:
        """Клиент идёт за Ведьмаком (не driving): ноги и взгляд Геральта."""
        if self.up and not self.driving:
            self.send({"t": "pose", "p": [round(x, 3), round(y, 3), round(z, 3)], "yaw": round(yaw, 2),
                       "pitch": round(pitch, 2)})
            self._count("pose")

    def edge(self, name: str, down: bool) -> None:
        """Сквозная клавиша (client.pass, client.pass_drive) нажата/отпущена в окне Ведьмака. Нажатие — только в режиме
        «Стив» (pass_drive — только когда ходит клиент); отпускание уходит всегда: клиент сам отпустит только то, что
        нажимал (Control.key/button)."""
        p = self.passes.get(name)
        if p is None:
            p = self.passes_drive.get(name)
            if p is None or down and not self.driving:
                return
        elif down and not self.active:
            return
        _, kind, code = p
        self.send({"t": kind, "k" if kind == "key" else "b": code, "a": 1 if down else 0})
        self._count("pass_" + kind)

    def tap_key(self, glfw_key: int) -> None:
        if self.active:
            self.send({"t": "key", "k": glfw_key, "a": 1})
            self.send({"t": "key", "k": glfw_key, "a": 0})

    def scroll(self, d: float) -> None:
        if self.active:
            self.send({"t": "scroll", "d": d})

    def look(self, yaw: float, pitch: float) -> None:
        if self.up:
            self.send({"t": "look", "yaw": round(yaw, 2), "pitch": round(pitch, 2)})
            self._count("look")

    def set_fov(self, v: float) -> None:
        """Вертикальный FOV мира клиента, как у камеры Ведьмака: рамка блока ложится на блок Ведьмака."""
        v = round(v, 2)
        if self.up and v != self.fov_sent and self.send({"t": "fov", "v": v}):
            self.fov_sent = v
            log.info("клиент MC: FOV мира %.2f (как у камеры Ведьмака)", v)

    @property
    def screen_open(self) -> bool:
        """Режим «Стив» и у клиента открыт экран (инвентарь, верстак, сундук): мышь ведёт его курсор."""
        return self.active and bool((self.state or {}).get("screen"))

    def cursor(self, dx: float, dy: float) -> None:
        if self.screen_open and (dx or dy):
            self.send({"t": "cursor", "dx": round(dx * self.cfg.cursor_scale, 2), "dy": round(dy * self.cfg.cursor_scale, 2)})
            self._count("cursor")

    def window(self) -> Optional[tuple[int, int]]:
        w = (self.state or {}).get("win") or (self.hello or {}).get("win")
        return (int(w[0]), int(w[1])) if isinstance(w, list) and len(w) == 2 and w[1] else None

    def close(self) -> None:
        if self.overlay is not None:
            self.overlay.stop()
        self.link.close()

    def metrics(self) -> dict[str, Any]:
        s = self.state or {}
        return {"up": self.up, "world": self.in_world, "active": self.active, "driving": self.driving, "fps": s.get("fps"),
                "screen": s.get("screen"), "overlay": None if self.overlay is None else self.overlay.running,
                "stats": dict(self.stats)}
