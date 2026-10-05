"""bridge — мост этапа 1: Fabric-мод MCW (TCP 127.0.0.1:25600, JSON-строки) <-> The Witcher 3 (отладочный протокол,
exec-функции modMCW на 127.0.0.1:37001).

    python bridge.py                          # bridge.toml рядом; Ведьмак 37001, мод 25600
    python bridge.py --w3-port 37101          # против фейковой игры: python ../spike/fake_w3.py --port 37101
    python bridge.py --check-keys             # клавиши конфига против input.settings Ведьмака (только чтение) и выход

Что делает (подробно — docs/DEVELOPMENT.md):
  * опрашивает mcw_poll(seq) конвейером (poll.hz, poll.depth; игра отвечает медленнее — частота падает сама) и шлёт
    моду pos; смена world/area, пустой мир после загрузки сейва (mcw_count = 0 при непустой записи) или новое
    соединение -> mcw_clearall и полная пересинхронизация;
  * держит зеркало блоков по чанкам и показывает в Ведьмаке открытые блоки в радиусе, ближние первыми, пакетами
    mcw_batch не длиннее batch.bytes; помнит, что и где поставил (clear с тем же origin, что set);
  * рельеф Ведьмака -> barrier: mcw_ground по колоннам вокруг Геральта -> ground, далеко ушёл -> ground_clear;
  * горячие клавиши (только при активном окне witcher3.exe): режим строительства, ломать/ставить по mcw_look, слоты,
    список крафта (recipes) и крафт (craft), призыв крипера для теста (spawn);
  * мобы из потока ent -> плейсхолдеры mcw_ents (слоты 0..63, wipe после сбоев), предметы на земле — счётчиком;
  * explosion -> mcw_boom; ack-отказ, hp, inv, death, шипение крипера, итог крафта -> mcw_notify;
  * скрытый клиент Майнкрафта (client.py): поза Геральта ему, в режиме Стива — мышь и слоты ему, оверлей поверх
    Ведьмака, удары Геральта заблокированы (mcwf_block).

Только stdlib, Python 3.12. Ни одной ссылки на сущности Ведьмака: только данные.
"""
from __future__ import annotations

import argparse
import collections
import logging
import logging.handlers
import math
import os
import queue
import re
import statistics
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import spike_path  # noqa: F401
import coords
import w3net

import config as config_mod
import hotkeys
from client import GLFW_KEY_0, ClientSide
import combat
from craft import CraftMenu
from ground import GroundPlanner, col_center_cm
from solid import SolidPlanner
from supervise import Restarter
from links import McLink, W3Link
import rawmouse
from mobs import EntDisplay, EntTable, pack_ents, short_type
from world import BlockRules, Display, Mirror, Selector, look_target, pack

log = logging.getLogger("bridge")

# Действия горячих клавиш, которые в режиме Стива делает сам Майнкрафт (ЛКМ, ПКМ, инвентарь): мост их пропускает
STEVE_MC_ACTIONS = {"break", "place", "craft_menu", "craft_prev", "craft_next", "craft_do"}
GLFW_KEY_E = 69

WHY = {
    "occupied": "occupied", "too_far": "too far", "too_high": "too high", "too_low": "too low",
    "out_of_world": "outside the world", "not_allowed": "not allowed", "no_item": "empty hand",
    "used_block": "block used it (sneak to place)", "rejected": "rejected", "bad_args": "bad target",
    "bad_slot": "bad slot", "unloaded": "chunk loading, retry", "air": "nothing there", "unbreakable": "unbreakable",
    "cancelled": "cancelled", "changed": "block changed",
    "missing": "missing ingredients", "no_table": "needs a crafting table in reach", "inv_full": "inventory full",
    "bad_recipe": "unknown recipe", "impossible": "special recipe", "no_ground": "no ground there",
    "bad_type": "unknown mob",
}
# Алфавит текста mcw_notify. Замер 5.00c (bench_w3 charset, 2026-10-03): кавычка " или ' внутри строкового аргумента
# exec ломает разбор всей команды, '\' доходит как есть (экранирования нет), кириллица проходит (StrLen считает байты
# UTF-8). Прочее (<, >, &, обратная косая, управляющие) в уведомлении не нужно и в GUI не проверено — тоже '?'.
_SAFE = re.compile(r"[^A-Za-z0-9А-Яа-яЁё .,:;!?+\-_/()%#=]")


def safe_text(s: str, limit: int = 120) -> str:
    """Текст для mcw_notify: символы вне _SAFE (в том числе кавычки) -> '?', не длиннее limit байт UTF-8 (кириллица —
    2 байта на букву; буква пополам не режется)."""
    out = _SAFE.sub("?", s)
    raw = out.encode("utf-8")
    return raw[:limit].decode("utf-8", errors="ignore") if len(raw) > limit else out


def short_item(item: str) -> str:
    return item.split(":", 1)[1] if item.startswith("minecraft:") else item


def world_name(depot: Optional[str]) -> str:
    """'levels\\novigrad\\novigrad.w2w' -> 'novigrad'."""
    if not depot:
        return ""
    return os.path.splitext(re.split(r"[\\/]", depot)[-1])[0]


@dataclass
class Req:
    kind: str
    ctx: dict
    t0: float
    epoch: int


@dataclass
class Pose:
    pos: coords.Pos
    cam: Optional[coords.Cam]
    t: float

    def mc(self, y_offset: int) -> tuple[float, float, float, float]:
        return self.pos.mc(y_offset)

    def look_angles(self) -> tuple[float, float]:
        """(yaw, pitch) Майнкрафта по взгляду камеры; без камеры — курс Геральта и 0."""
        if self.cam is not None:
            try:
                return self.cam.mc_angles()
            except ValueError:
                pass
        return coords.w_yaw_to_mc(self.pos.yaw), 0.0


@dataclass
class Backoff:
    lo: float
    hi: float
    cur: float = field(init=False)

    def __post_init__(self) -> None:
        self.cur = self.lo

    def fail(self) -> float:
        d = self.cur
        self.cur = min(self.hi, self.cur * 2)
        return d

    def ok(self) -> None:
        self.cur = self.lo


class Bridge:
    def __init__(self, cfg, *, key_down=None, foreground=None, raw=None) -> None:
        self.cfg = cfg
        self.q: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.y_off = cfg.coords.y_offset
        tpls = coords.by_batch_t(coords.load_templates())
        for t in [cfg.display.default_template, *cfg.display.templates.values()]:
            if t not in tpls:
                raise ValueError(f"шаблон mcw_batch {t} не задан в templates.json (есть {sorted(tpls)})")
        for t in [cfg.ents.default_template, *cfg.ents.templates.values()]:
            if t >= 0 and t not in tpls:          # -1 — моба не показывать
                raise ValueError(f"шаблон моба {t} не задан в templates.json (есть {sorted(tpls)})")
        self.rules = BlockRules(cfg.display.hide, cfg.display.transparent, cfg.display.templates,
                                cfg.display.default_template)
        self.mirror = Mirror()
        g = cfg.ground
        self.ground = GroundPlanner(g.radius_m, g.depth, g.clear_dist_m, g.retry_none_s, self.y_off)
        self.selector = Selector(self.mirror, self.rules, self.ground.top_at)
        s = cfg.solid
        self.solid = SolidPlanner(s.radius_m, s.rescan_dz_m, s.door_radius_m, s.door_every_s, y_offset=self.y_off)
        self.display = Display(tpls, self.y_off)
        self.mc = McLink(cfg.mc.host, cfg.mc.port, self.q, connect_timeout=cfg.mc.connect_timeout_s)
        self.w3 = W3Link(cfg.w3.host, cfg.w3.port, self.q, timeout=cfg.poll.timeout_s,
                         connect_timeout=cfg.w3.connect_timeout_s, scriptslog=cfg.w3.scriptslog)
        self.mc_backoff = Backoff(cfg.mc.reconnect_min_s, cfg.mc.reconnect_max_s)
        self.w3_backoff = Backoff(cfg.w3.reconnect_min_s, cfg.w3.reconnect_max_s)
        self.mc_next = 0.0
        self.w3_next = 0.0
        # Ведьмак
        self.w3_state = "down"                 # down | clearing | synced
        self.inflight: dict[int, Req] = {}
        self.epoch = 0                         # растёт с каждым mcw_clearall: ответы до него про старое состояние
        self.clearall_seq: Optional[int] = None
        self.clearall_retry_at = 0.0
        self.resync_reason: Optional[str] = None
        self.resync_force = False
        self.last_resync = -1e9
        self.resync_interval = cfg.resync.min_interval_s
        self.booms = 0                         # бочки mcw_boom тоже с тегом MCW_BLOCK: mcw_count их видит
        # расширения мода для моста (mcw_countblocks, mcw_boomat): None — не пробовали, False — мод старый
        # ('Warn: Failed to process command'), откат на mcw_count / mcw_boom. Сбрасывается с каждым соединением
        self.caps: dict[str, Optional[bool]] = {"countblocks": None, "boomat": None}
        # mcw_ents (этап 5) — отдельно от caps: None — не пробовали, False — мод без неё (мобов не показываем)
        self.ents_cap: Optional[bool] = None
        self.pose: Optional[Pose] = None
        self.in_world = False
        self.world: Optional[str] = None
        self.area: Optional[str] = None
        self.next_poll = 0.0
        self.last_poll_ok = -1e9
        self.next_count = 0.0
        self.ground_tokens = 0.0
        self.ground_tick = time.perf_counter()
        self.solid_tokens = 0.0
        self.solid_tick = time.perf_counter()
        self.solid_cap: Optional[bool] = None  # мод с mcw_cols: None — не пробовали, False — старый (дома сплошные)
        # модели MC (mcw_models.ws): лошади по слотам 0..3 (id сущности MC -> слот), тело Стива на Геральте
        self.horse_slots: dict[int, int] = {}
        self.horse_cap: Optional[bool] = None
        self.skin_state: Optional[int] = None  # 1 надето, 0 нет шаблонов (мод не загружен), None — не спрашивали
        self.skin_cap: Optional[bool] = None
        self.next_skin = 0.0
        self.skin_asked = 0.0
        # пауза Ведьмака (mcw_state) -> заморозка сервера MC (tick freeze); None — не знаем / не слали
        self.w3_paused: Optional[bool] = None
        self.next_w3pause = 0.0
        self.mc_frozen: Optional[bool] = None
        # сторож процессов: упал сервер MC или клиент — перезапуск их скриптами (supervise.py)
        sv = cfg.supervise
        self.sup_server = (Restarter("сервер MC", config_mod.resolve(sv.server_script), sv.server_down_s, sv.min_interval_s,
                                     sv.timeout_s) if sv.enabled and sv.server else None)
        self.sup_client = (Restarter("клиент MC", config_mod.resolve(sv.client_script), sv.client_down_s,
                                     sv.min_interval_s, sv.timeout_s)
                           if sv.enabled and sv.client and cfg.client.enabled else None)
        self.solid_ms: collections.deque = collections.deque(maxlen=200)
        self.next_solid_trim = 0.0
        # Майнкрафт
        self.mc_hello: Optional[dict] = None
        self.t_hello = 0.0
        self.pos_seq = 0
        self.last_pos_key: Optional[tuple] = None
        self.last_pos_t = -1e9
        self.next_ground_send = 0.0
        self.next_ground_clear = 0.0
        self.next_trim = 0.0
        # показ
        self.desired: list = []
        self.desired_dirty = True
        self.ops_dirty = True
        self.pending_ops = 0
        self.sel_center: Optional[tuple] = None
        self.sel_t = -1e9
        # ввод и действия
        self.build_mode = False
        self.fp_on = False                  # вид от первого лица (mcwf_on), docs/STEVE.md
        self.fp_auto_done = False           # fp.start_on уже отработал для этого подключения
        self.slot = 0
        self.inv_slots: dict[int, tuple[str, int]] = {}
        self.inv_counts: Optional[collections.Counter] = None
        self.act_id = 0
        self.acts: dict[int, dict] = {}
        # уведомления
        self.notes: collections.deque = collections.deque()
        self.next_note = 0.0
        self.inv_delta: collections.Counter = collections.Counter()
        self.inv_flush_at: Optional[float] = None
        self.last_hp: Optional[float] = None
        self.last_boom = -1e9
        # мобы (поток ent мода -> mcw_ents) и крафт
        e = cfg.ents
        self.ents = EntTable()
        self.entd = EntDisplay(tpls, e.templates, e.default_template, self.y_off, move_eps=e.move_eps_m,
                               yaw_eps=e.yaw_eps_deg)
        self.next_ents = 0.0
        self.t_snap_end = -1e9
        self.items_near = 0
        self.craft = CraftMenu()
        self.next_recipes = 0.0
        # скрытый клиент MC (client.py) и блокировка ударов Геральта, пока мышь у клиента: None — не знаем (новое
        # соединение с Ведьмаком), False/True — что стоит в игре; block_cap False — мод без mcwf_block
        self.client: Optional[ClientSide] = ClientSide(cfg, self.q) if cfg.client.enabled else None
        self.w3_block: Optional[int] = None    # уровень mcwf_block в игре: 0 свой снят, 1 бой, 2 бой и движение
        self.block_cap: Optional[bool] = None
        self.fp_src: Optional[int] = None      # откуда глаз камеры от первого лица: 0 Геральт, 1 клиент MC (mcwf_mc)
        self.w3_susp = False                   # идёт сцена Ведьмака (диалог, катсцена, лошадь): вид от первого лица уступил
        self.w3_freeze: Optional[bool] = None  # взгляд Ведьмака стоит (открыт экран MC), mcwf_freeze
        self.next_fpwatch = 0.0
        self.cam_fov: Optional[float] = None   # FOV камеры от первого лица (mcwf_fov), None — не спрашивали
        self.next_terrain = 0.0
        self.far_logged = False
        # земля под игроком MC, где мост её знал последний раз ((колонна), см) — для колонн, о которых ещё не знает
        self.last_ground: Optional[tuple[tuple[int, int], float]] = None
        # бой (combat.py, mcw_combat.ws): NPC Ведьмака -> прокси на сервере MC, удары туда и обратно; стрелы MC
        self.npcs_cap: Optional[bool] = None    # мод с mcw_npcs: None — не пробовали, False — старый (боя нет)
        self.next_npcs = 0.0
        self.npcs_sent = False                  # серверу ушёл непустой список прокси (выйти из режима — очистить)
        self.npc_count = 0
        self.npc_tracker = combat.NpcTracker()
        self.npc_map: dict[int, int] = {}       # номер NPC -> сущность прокси на сервере (npc_map мода)
        self.client_npcs_sent = False           # клиенту ушёл непустой npcs (выйти из режима — очистить)
        self.npc_hits: list[tuple[float, tuple, dict]] = []   # удары MC, ждущие замаха: (когда, аргументы, npc_hit)
        self.w3_guard: Optional[bool] = None    # кукла в защите (щит MC, mcwf_guard); None — не знаем
        self.guard_cap: Optional[bool] = None
        self.path_cap: Optional[bool] = None    # мод с mcw_path (стены Ведьмака для стрел MC)
        self.grounds_cap: Optional[bool] = None  # мод с mcw_grounds (рельеф пачками); False — по одной mcw_ground
        self.next_grounds = 0.0
        self.arrows = combat.ArrowSlots()
        self.arrows_cap: Optional[bool] = None  # мод с mcwa_arrows
        self.arrows_ready = False               # пул стрел заведён (mcwa_arrows "init") на этом подключении
        # метрики
        self.stats: collections.Counter = collections.Counter()
        self.poll_rtt: collections.deque = collections.deque(maxlen=600)
        self.batch_ms: collections.deque = collections.deque(maxlen=200)
        self.next_metrics = time.perf_counter() + cfg.log.metrics_every_s
        self._metrics_prev: tuple[float, int] = (time.perf_counter(), 0)
        self.snapshot: dict[str, Any] = {}
        self.next_snapshot = 0.0
        # клавиши
        self.hotkeys: Optional[hotkeys.Hotkeys] = None
        self.passthrough: Optional[hotkeys.Passthrough] = None
        self.foreground = foreground
        # мышь мимо игры (курсор экранов MC, колесо): тестам — своя подмена, вживую — Raw Input, если есть клиент
        self.raw = raw
        if cfg.input.enabled:
            bindings = hotkeys.parse_bindings(cfg.input.bind)
            if key_down is None or foreground is None:
                if not hotkeys.available():
                    log.warning("клавиши: не Windows, ввод выключен")
                    bindings = None
                else:
                    key_down = key_down or hotkeys.win32_key_down()
                    foreground = foreground or hotkeys.Win32Foreground(cfg.input.process)
                    self.foreground = foreground
                    if self.raw is None and self.client is not None and rawmouse.RawMouse.available():
                        self.raw = rawmouse.RawMouse()
            if bindings is not None:
                self.hotkeys = hotkeys.Hotkeys(bindings, key_down, foreground)
                passes = {**self.client.passes, **self.client.passes_drive} if self.client is not None else {}
                if passes:
                    # 8, 9, 0 — и строительство (без клиента), и слоты MC; в режиме Стива строительство молчит
                    own = {vk for a, vks in bindings.items() if a not in STEVE_MC_ACTIONS for vk in vks}
                    for name, (vk, _, _) in passes.items():
                        if vk in own:
                            log.warning("клавиша %s и в input.bind, и в client.pass: сработает и там, и там", name)
                    self.passthrough = hotkeys.Passthrough({n: p[0] for n, p in passes.items()}, key_down, foreground)

    # ------------------------------------------------------------------------------------------------ цикл
    def run(self) -> None:
        log.info("мост: Ведьмак %s:%d, мод %s:%d, конфиг %s", self.cfg.w3.host, self.cfg.w3.port, self.cfg.mc.host,
                 self.cfg.mc.port, self.cfg.get("_file") or "по умолчанию")
        if self.hotkeys is not None:
            threading.Thread(target=self.hotkeys.run, args=(lambda a: self.q.put(("key", a)), self.stop_event,
                                                            self.cfg.input.poll_hz), name="hotkeys", daemon=True).start()
        if self.raw is not None and hasattr(self.raw, "start"):
            self.raw.start()
        if self.passthrough is not None:
            threading.Thread(target=self.passthrough.run, args=(lambda n, d: self.q.put(("pass", n, d)),
                                                                self.stop_event, self.cfg.input.poll_hz),
                             name="passthrough", daemon=True).start()
        try:
            while not self.stop_event.is_set():
                self._pump(0.005)
                self.step(time.perf_counter())
        finally:
            self.mc.close()
            self.w3.close()
            if self.client is not None:
                self.client.close()
            log.info("мост остановлен")

    def stop(self) -> None:
        self.stop_event.set()

    def _pump(self, timeout: float) -> None:
        try:
            ev = self.q.get(timeout=timeout)
        except queue.Empty:
            return
        self.handle(ev)
        for _ in range(2000):
            try:
                ev = self.q.get_nowait()
            except queue.Empty:
                return
            self.handle(ev)

    def handle(self, ev: tuple) -> None:
        kind = ev[0]
        try:
            if kind == "mc":
                if ev[1] == self.mc.gen and self.mc.up:
                    self.on_mc(ev[2])
            elif kind == "w3_res":
                if ev[1] == self.w3.gen:
                    self.on_w3_result(ev[2], ev[3])
            elif kind == "key":
                self.on_key(ev[1])
            elif kind == "mc_sock":
                if self.mc.attach(ev[1], ev[2]):
                    self.on_mc_up()
            elif kind == "mc_fail":
                if self.mc.failed(ev[1]):
                    d = self.mc_backoff.fail()
                    self.mc_next = time.perf_counter() + d
                    log.info("мод не отвечает (%s), повтор через %.1f с", ev[2], d)
            elif kind == "mc_down":
                if ev[1] == self.mc.gen and self.mc.up:
                    self.on_mc_down(ev[2])
            elif kind == "w3_up":
                if self.w3.attach(ev[1], ev[2]):
                    self.on_w3_up()
            elif kind == "w3_fail":
                if self.w3.failed(ev[1]):
                    d = self.w3_backoff.fail()
                    self.w3_next = time.perf_counter() + d
                    log.info("Ведьмак не отвечает (%s), повтор через %.1f с", ev[2], d)
            elif kind == "w3_down":
                if ev[1] == self.w3.gen:
                    self.w3_lost(ev[2])
            elif self.client is None:
                pass
            elif kind == "cl":
                self.client.on_msg(ev[1], ev[2])
            elif kind == "pass":
                self.client.edge(ev[1], ev[2])
            elif kind == "cl_sock":
                self.client.on_sock(ev[1], ev[2])
            elif kind == "cl_fail":
                self.client.on_fail(ev[1], ev[2])
            elif kind == "cl_down":
                self.client.on_down(ev[1], ev[2])
        except Exception:
            log.exception("событие %s упало", kind)

    def step(self, now: float) -> None:
        if not self.mc.up and not self.mc.connecting and now >= self.mc_next:
            self.mc.start_connect()
        if not self.w3.up and not self.w3.connecting and now >= self.w3_next:
            self.w3.start_connect()
        if self.w3.up:
            self._step_poll(now)
            self._step_clearall(now)
            self._step_display(now)
            self._step_ents(now)
            self._step_count(now)
            self._step_ground_queries(now)
            self._step_notify(now)
            self._step_fp_start()
            self._step_skin(now)
            self._step_w3pause(now)
        self._step_mc_freeze()
        self._step_supervise(now)
        if self.mc.up:
            self._step_mc_ground(now)
            self._step_craft(now)
        if self.client is not None:
            self._step_block(self.client.step(now, self.steve_wanted()))
            if self.w3.up:
                self._step_drive(now)
                self._step_fpwatch(now)
                self._step_freeze()
            if self.w3.up:
                self._step_solid(now)
                self._step_horses()
                self._step_npcs(now)
                self._step_npc_hits(now)
                self._step_guard()
                self._step_arrows()
            self._step_terrain(now)
            self._step_mouse()
        if self.acts and len(self.acts) > 64:
            for k in [k for k, a in self.acts.items() if now - a["t"] > 60]:
                del self.acts[k]
        if now >= self.next_snapshot:
            self.next_snapshot = now + 0.25
            self.snapshot = self.metrics(now)
        if now >= self.next_metrics:
            self.next_metrics = now + self.cfg.log.metrics_every_s
            self._log_metrics(now)

    # ------------------------------------------------------------------------------------------------ Ведьмак
    def _n(self, kind: str) -> int:
        return sum(1 for r in self.inflight.values() if r.kind == kind)

    def _call(self, kind: str, func: str, args: tuple, timeout: float, ctx: dict) -> Optional[int]:
        try:
            seq = self.w3.call(func, *args, timeout=timeout)
        except ConnectionError as e:
            self.w3_lost(e)
            return None
        except ValueError as e:       # fmt_arg / CommandTooLong: такую команду игра не разберёт — не шлём, мост живёт
            log.warning("%s не отправлен: %s", func, e)
            self.stats["refused_" + kind] += 1
            return None
        self.inflight[seq] = Req(kind, ctx, time.perf_counter(), self.epoch)
        self.stats["sent_" + kind] += 1
        return seq

    def on_w3_up(self) -> None:
        log.info("Ведьмак подключён (%s:%d): mcw_clearall и пересинхронизация", self.cfg.w3.host, self.cfg.w3.port)
        self.inflight.clear()
        self.display.reset()
        self.entd.reset(wipe=True)
        self.booms = 0
        self.w3_state = "clearing"
        self.clearall_seq = None
        self.clearall_retry_at = 0.0
        self.resync_reason = None
        self.resync_force = False
        self.ground.drop_pending()
        self.caps = {k: None for k in self.caps}
        self.ents_cap = None
        self.fp_on = False                  # новое подключение: вид — по fp.start_on или по mcwf_state (_step_fp_start)
        self.fp_auto_done = False
        self.w3_block = None
        self.block_cap = None
        self.fp_src = None
        self.cam_fov = None
        self.w3_susp = False
        self.w3_freeze = None
        self.solid_cap = None
        self.solid.pending.clear()
        self.horse_slots = {}
        self.horse_cap = None
        self.skin_state = None
        self.skin_cap = None
        self.next_skin = 0.0
        self.skin_asked = 0.0
        self.w3_paused = None
        self.npcs_cap = None
        self.npc_hits = []
        self.w3_guard = None
        self.guard_cap = None
        self.path_cap = None
        self.grounds_cap = None
        self.arrows_cap = None
        self.arrows_ready = False
        self.arrows.reset()
        self.stats["w3_connects"] += 1

    def w3_lost(self, err) -> None:
        if not self.w3.up:
            return
        log.warning("Ведьмак: соединение потеряно (%s)", err)
        self.w3.close()
        self.inflight.clear()
        self.clearall_seq = None
        self.w3_state = "down"
        self.in_world = False
        self.ground.drop_pending()
        self.solid.pending.clear()
        d = self.w3_backoff.fail()
        self.w3_next = time.perf_counter() + d
        self.stats["w3_lost"] += 1

    def request_resync(self, reason: str, force: bool = False) -> None:
        if self.resync_reason is None or force:
            self.resync_reason = reason
        self.resync_force |= force

    def on_w3_result(self, seq: int, fut) -> None:
        req = self.inflight.pop(seq, None)
        if req is None:
            return
        res, err = None, None
        try:
            res = fut.result()
        except BaseException as e:  # noqa: BLE001 — ReplyLost, TimeoutError, ConnectionError
            err = e
        if isinstance(err, ConnectionError):
            self.w3_lost(err)
            return
        if err is not None:
            self.stats[f"{req.kind}_{type(err).__name__}"] += 1
        getattr(self, "_res_" + req.kind)(req, seq, res, err)

    # -- poll
    def _step_poll(self, now: float) -> None:
        c = self.cfg.poll
        if now < self.next_poll:
            return
        if self._n("poll") >= c.depth or len(self.inflight) >= self.cfg.w3.max_inflight:
            return
        # по расписанию, а не «через 1/hz после прошлого»: шаг цикла (~15 мс на Windows) не съедает частоту; конвейер
        # полон (игра отвечает медленнее) — опрос ждёт ответа, частота = depth / RTT
        interval = 1.0 / c.hz
        self.next_poll = max(self.next_poll + interval, now - interval)
        self._call("poll", "mcw_poll", (), c.timeout_s, {})

    def _res_poll(self, req: Req, seq: int, res, err) -> None:
        if err is not None or res is None or not res.ok:
            self.stats["poll_fail"] += 1
            return
        now = time.perf_counter()
        self.poll_rtt.append(res.rtt)
        self.stats["poll_ok"] += 1
        p = coords.parse_poll(res.text, seq)
        gap = now - self.last_poll_ok
        self.last_poll_ok = now
        if p["pos"] is None:
            if self.in_world:
                log.info("Ведьмак: Геральта нет (меню или загрузка)")
            self.in_world = False
            return
        was = self.in_world
        self.in_world = True
        self.pose = Pose(p["pos"], p["cam"], now)
        if (p["world"], p["area"]) != (self.world, self.area):
            if self.world is not None or self.area is not None:
                self.on_world_change(p["world"], p["area"])
            else:
                log.info("Ведьмак: мир %s, область %s", p["world"], p["area"])
            self.world, self.area = p["world"], p["area"]
        if not was or gap > 1.0:
            self.next_count = 0.0     # вернулись в мир или игра молчала: сразу сверить число блоков
        self._send_pos(now)
        self._send_client_pose()

    def on_world_change(self, world: Optional[str], area: Optional[str]) -> None:
        log.warning("Ведьмак: мир сменился %s/%s -> %s/%s: рельеф заново, mcw_clearall", self.world, self.area,
                    world, area)
        self.ground.reset()
        self.solid.reset()
        self.selector.clear()
        self.desired_dirty = True
        if self.client is not None:
            self.client.terrain_reset = True        # высоты прежнего мира клиенту не годятся
        if self.mc.up:
            self.mc.send({"t": "ground_clear"})
        self.request_resync("смена мира", force=True)
        self.stats["world_changes"] += 1

    def _send_pos(self, now: float) -> None:
        if not self.mc.up or self.pose is None:
            return
        x, y, z, yaw = self.pose.mc(self.y_off)
        pitch = self.pose.look_angles()[1]
        key = (round(x, 2), round(y, 2), round(z, 2), round(yaw, 1), round(pitch, 1))
        if key == self.last_pos_key and now - self.last_pos_t < self.cfg.poll.pos_keepalive_s:
            return
        self.pos_seq += 1
        if self.mc.send({"t": "pos", "seq": self.pos_seq, "p": [round(x, 3), round(y, 3), round(z, 3)],
                         "yaw": round(yaw, 2), "pitch": round(pitch, 2)}):
            self.last_pos_key = key
            self.last_pos_t = now

    # -- пересинхронизация
    def _send_client_pose(self) -> None:
        """Клиент MC стоит там же, где Геральт, и смотрит по камере Ведьмака: луч его прицела — центр экрана."""
        if self.client is None or not self.client.up or self.pose is None:
            return
        x, y, z, _ = self.pose.mc(self.y_off)
        yaw, pitch = self.pose.look_angles()
        self.client.pose(x, y, z, yaw, pitch)

    def _step_clearall(self, now: float) -> None:
        if self.w3_state == "clearing":
            if self.clearall_seq is None and now >= self.clearall_retry_at:
                self._send_clearall()
            return
        if self.resync_reason is None or self.w3_state != "synced":
            return
        if not self.resync_force:
            if now - self.last_resync < self.resync_interval:
                return
            # ресинки подряд — частота падает вдвое (до max_interval_s): не долбить игру, если что-то не ставится
            r = self.cfg.resync
            near = now - self.last_resync < max(30.0, 2 * self.resync_interval)
            self.resync_interval = min(r.max_interval_s, self.resync_interval * 2) if near else r.min_interval_s
        log.warning("ресинк: %s", self.resync_reason)
        self.stats["resyncs"] += 1
        self.last_resync = now
        self.resync_reason = None
        self.resync_force = False
        self.w3_state = "clearing"
        self._send_clearall()

    def _send_clearall(self) -> None:
        self.epoch += 1
        self.clearall_seq = self._call("clearall", "mcw_clearall", (), self.cfg.resync.clearall_timeout_s, {})

    def _res_clearall(self, req: Req, seq: int, res, err) -> None:
        if seq != self.clearall_seq:
            return
        self.clearall_seq = None
        if err is not None or res is None or not res.ok:
            log.warning("mcw_clearall без ответа (%s), повтор через 1 с", err or "нет маркера")
            self.clearall_retry_at = time.perf_counter() + 1.0
            return
        n = (res.ints("cleared") or [0])[0]
        log.info("mcw_clearall: убрано %d, ставлю заново", n)
        self.display.reset()
        self.entd.reset(wipe=True)              # мобов mcw_clearall не трогает (MCW_ENT): wipe и заново
        self.booms = 0
        self.w3_state = "synced"
        self.w3_backoff.ok()
        self.ops_dirty = True
        self.desired_dirty = True
        self.next_count = time.perf_counter() + self.cfg.resync.count_every_s

    # -- показ
    def display_ready(self) -> bool:
        return (self.w3_state == "synced" and self.in_world and self.mc.up and self.mirror.snap_done
                and self.pose is not None)

    def _reselect(self, now: float) -> None:
        changed = self.mirror.take_changed()
        if changed:
            self.selector.invalidate(changed)
            self.desired_dirty = True
        d = self.cfg.display
        center = self.pose.mc(self.y_off)[:3]
        moved = self.sel_center is None or math.dist(center, self.sel_center) >= d.reselect_move_m
        if (self.desired_dirty or moved or now - self.sel_t > 1.0) and now - self.sel_t >= d.reselect_interval_s:
            self.desired = self.selector.select(center, d.radius_m, d.max_entities)
            self.sel_center = center
            self.sel_t = now
            self.desired_dirty = False
            self.ops_dirty = True

    def _step_display(self, now: float) -> None:
        if not self.display_ready():
            return
        self._reselect(now)
        if not self.ops_dirty:
            return
        b = self.cfg.batch
        nb = self._n("batch")
        if nb >= b.inflight or len(self.inflight) >= self.cfg.w3.max_inflight:
            return
        ops = self.display.ops(self.desired)
        self.pending_ops = len(ops)
        if not ops:
            self.ops_dirty = False
            return
        strs = [o[0] for o in ops]
        i = 0
        while i < len(ops) and nb < b.inflight and len(self.inflight) < self.cfg.w3.max_inflight:
            try:
                seq = self.w3.next_seq()
                n = pack(strs[i:], seq, b.bytes)
                cmd = coords.batch_call(seq, strs[i:i + n])
                self.w3.exec(seq, cmd, b.timeout_s)
            except ConnectionError as e:
                self.w3_lost(e)
                return
            part = ops[i:i + n]
            sets = sum(1 for o in part if o[2] is not None)
            self.inflight[seq] = Req("batch", {"sets": sets, "clears": n - sets, "bytes": len(cmd)}, now, self.epoch)
            self.display.commit(part)
            self.stats["sent_batch"] += 1
            self.stats["ops_set"] += sets
            self.stats["ops_clear"] += n - sets
            i += n
            nb += 1
        self.pending_ops = len(ops) - i

    def _res_batch(self, req: Req, seq: int, res, err) -> None:
        if req.epoch != self.epoch:
            return                    # отправлен до mcw_clearall: его след и так стёрт
        self.ops_dirty = True
        r = res.ints("batch") if res is not None and res.ok else None
        if err is not None or r is None or len(r) != 4:
            self.stats["batch_lost"] += 1
            self.request_resync(f"пакет mcw_batch без ответа ({err or 'нет маркера'}): что стоит в игре, неизвестно",
                                force=True)
            return
        self.batch_ms.append(r[3])
        if r[2]:
            # ошибки пакета (clear без попадания, set без сущности): расхождение покажет mcw_count — сверить сразу
            self.stats["batch_errors"] += r[2]
            log.info("mcw_batch: set %d clear %d, ошибок %d", r[0], r[1], r[2])
            self.next_count = 0.0

    # -- мобы
    def _step_ents(self, now: float) -> None:
        """Плейсхолдеры мобов: не чаще ents.hz, один пакет mcw_ents в полёте. Снапшот мода только что пришёл —
        ждём его первый ent (иначе пустая таблица сняла бы всех, кого потом поставим заново)."""
        c = self.cfg.ents
        if not c.enabled or self.ents_cap is False or now < self.next_ents or not self.display_ready():
            return
        if now - self.t_snap_end < 0.25 or self._n("ents") or len(self.inflight) >= self.cfg.w3.max_inflight - 1:
            return
        ops = self.entd.ops(self.ents, self.pose.mc(self.y_off)[:3], c.radius_m, c.max)
        if not ops:
            return
        self.next_ents = now + 1.0 / c.hz
        strs = [o[0] for o in ops]
        try:
            seq = self.w3.next_seq()
            n = pack_ents(strs, seq, self.cfg.batch.bytes)
            cmd = coords.ents_call(seq, strs[:n])
            self.w3.exec(seq, cmd, c.timeout_s)
        except ConnectionError as e:
            self.w3_lost(e)
            return
        part = ops[:n]
        kinds = collections.Counter(k for _, _, k in part)
        self.inflight[seq] = Req("ents", {"kinds": kinds, "bytes": len(cmd)}, now, self.epoch)
        self.entd.commit(part, self.ents)
        self.stats["sent_ents"] += 1
        for k, v in kinds.items():
            self.stats["ents_" + k] += v

    def _res_ents(self, req: Req, seq: int, res, err) -> None:
        if err is None and res is not None and res.failed:
            if self.ents_cap is not False:
                log.info("мод без mcw_ents: мобы в Ведьмаке не показываются")
            self.ents_cap = False
            self.entd.reset(wipe=False)
            return
        if req.epoch != self.epoch:
            return                    # до mcw_clearall: после него и так wipe
        r = res.ints("ents") if res is not None and res.ok else None
        if err is not None or r is None or len(r) != 5:
            self.stats["ents_lost"] += 1
            log.info("mcw_ents без ответа (%s): wipe и мобы заново", err or "нет маркера")
            self.entd.reset(wipe=True)
            return
        self.ents_cap = True
        if r[3]:
            # move/kill пустого слота и прочее: игра и мост разошлись (загрузка сейва?) — wipe и всех заново
            self.stats["ents_errors"] += r[3]
            log.info("mcw_ents: spawn %d move %d kill %d, ошибок %d: wipe и мобы заново", r[0], r[1], r[2], r[3])
            self.entd.reset(wipe=True)

    def _ents_notify(self, ev) -> None:
        c = self.cfg.ents
        if c.fuse_notify:
            for i in ev.fuse_on:
                e = self.ents.ents.get(i)
                if e is not None:
                    self.notify(f"MCW: {short_type(e.type)} is hissing!", key="fuse")
        if c.items_notify and (ev.new or ev.gone) and self.pose is not None:
            near = self.ents.items_near(self.pose.mc(self.y_off)[:3], c.items_radius_m)
            total = sum(near.values())
            if total > self.items_near:
                parts = [f"{n} {short_item(k)}" for k, n in near.most_common(3)]
                self.notify("MCW: on the ground: " + ", ".join(parts) + (" ..." if len(near) > 3 else ""), key="items")
            self.items_near = total

    def _step_count(self, now: float) -> None:
        if self.w3_state != "synced" or not self.in_world or now < self.next_count:
            return
        if self._n("count") or len(self.inflight) >= self.cfg.w3.max_inflight:
            return
        self.next_count = now + self.cfg.resync.count_every_s
        n = len(self.display.shown)
        if self.caps["countblocks"] is not False:
            # блоки без проб MCW_TEST: бочки mcw_boom/boomat не мешают, сверка точная
            self._call("count", "mcw_countblocks", (), self.cfg.poll.timeout_s, {"min": n, "max": n, "word": "countblocks"})
        else:
            self._call("count", "mcw_count", (), self.cfg.poll.timeout_s, {"min": n, "max": n + self.booms, "word": "count"})

    def _res_count(self, req: Req, seq: int, res, err) -> None:
        if err is None and res is not None and res.failed and req.ctx["word"] == "countblocks":
            log.info("мод без mcw_countblocks: сверка по mcw_count с допуском на бочки")
            self.caps["countblocks"] = False
            self.next_count = 0.0
            return
        if req.epoch != self.epoch or self.w3_state != "synced" or err is not None or res is None or not res.ok:
            return
        r = res.ints(req.ctx["word"])
        if not r:
            return
        if req.ctx["word"] == "countblocks":
            self.caps["countblocks"] = True
        n, lo, hi = r[0], req.ctx["min"], req.ctx["max"]
        self.stats["counts"] += 1
        if lo <= n <= hi:
            return
        if n == 0:
            self.request_resync(f"в Ведьмаке 0 блоков, а поставлено {lo} (загрузка сейва?)")
        else:
            self.request_resync(f"mcw_{req.ctx['word']} {n}, ждали {lo}..{hi}")

    # -- рельеф
    def _step_ground_queries(self, now: float) -> None:
        g = self.cfg.ground
        dt, self.ground_tick = now - self.ground_tick, now
        self.ground_tokens = min(max(1.0, g.rate_hz * 0.25), self.ground_tokens + dt * g.rate_hz)
        if not g.enabled or not self.in_world or self.pose is None or self.w3_state == "down":
            return
        # деградация: игра отвечает на опрос медленно — рельеф (самое неважное) ждёт, опрос и пакеты идут
        slow = g.pause_rtt_ms / 1000.0
        if (self.poll_rtt and self.poll_rtt[-1] > slow) or any(
                r.kind == "poll" and now - r.t0 > slow for r in self.inflight.values()):
            self.stats["ground_paused"] += 1
            return
        if self.grounds_cap is not False:
            self._step_grounds(now)
            return
        free = min(g.inflight - self._n("ground"), self.cfg.w3.max_inflight - 1 - len(self.inflight),
                   int(self.ground_tokens))
        if free <= 0:
            return
        bx, _, bz = self.pose.pos.block(self.y_off)
        for col in self.ground.want((bx, bz), free, now):
            x, y = col_center_cm(col[0], col[1], self.y_off)
            # игра исполняет exec по порядку: плейсхолдеры стоят там, где их поставили уже отправленные mcw_ents
            ctx = {"col": col, "world": self.world, "mob": self.entd.near_column(*col)}
            if self._call("ground", "mcw_ground", (x, y), g.timeout_s, ctx) is None:
                self.ground.on_fail(col)
                return
            self.ground_tokens -= 1

    def _ground_center(self, now: float) -> tuple[int, int]:
        """Откуда спрашивать рельеф: ноги, а на ходу — точка впереди по скорости клиента MC (лошадь на полном скаку
        иначе выбегала за известную землю)."""
        g, c = self.cfg.ground, self.client
        if c is not None and c.driving and c.eye_fresh(now):
            try:
                x, _, z = (float(v) for v in c.eye["p"])
                vx, _, vz = (float(v) for v in c.eye["v"])
                ahead = min(1.0, g.lookahead_max_m / max(1e-6, math.hypot(vx, vz))) * g.lookahead_s
                return math.floor(x + vx * ahead), math.floor(z + vz * ahead)
            except (KeyError, TypeError, ValueError):
                pass
        bx, _, bz = self.pose.pos.block(self.y_off)
        return bx, bz

    def _step_grounds(self, now: float) -> None:
        """Рельеф пачками (mcw_grounds): batch_cols колонн за вызов, не чаще batch_hz, ближние к точке впереди первыми."""
        g = self.cfg.ground
        if (now < self.next_grounds or self._n("grounds") >= g.batch_inflight
                or len(self.inflight) >= self.cfg.w3.max_inflight - 1):
            return
        cols = self.ground.want(self._ground_center(now), g.batch_cols, now)
        if not cols:
            return
        xy = [col_center_cm(c[0], c[1], self.y_off) for c in cols]
        ctx = {"cols": cols, "world": self.world, "mob": [self.entd.near_column(*c) for c in cols]}
        if self._call("grounds", "mcw_grounds", ("|".join(f"{x}:{y}" for x, y in xy),), g.timeout_s, ctx) is None:
            for c in cols:
                self.ground.on_fail(c)
            return
        self.next_grounds = now + 1.0 / g.batch_hz

    def _res_grounds(self, req: Req, seq: int, res, err) -> None:
        cols = req.ctx["cols"]
        if res is not None and res.failed:
            if self.grounds_cap is None:
                log.info("мод без mcw_grounds: рельеф по одной колонне (mcw_ground)")
            self.grounds_cap = False
        vals: list[tuple[int, int]] = []
        if err is None and res is not None and res.ok and req.ctx["world"] == self.world:
            for ln in res.body:
                parts = ln.split()
                if parts and parts[0] == "gs":
                    for e in parts[1:]:
                        try:
                            ok, z = e.split(":")
                            vals.append((int(ok), int(z)))
                        except ValueError:
                            vals.append((0, 0))
        if len(vals) != len(cols):
            for c in cols:
                self.ground.on_fail(c)
            return
        self.grounds_cap = True
        for col, (ok, z), mob in zip(cols, vals, req.ctx["mob"]):
            self._apply_ground(col, ok, z, mob)

    def _res_ground(self, req: Req, seq: int, res, err) -> None:
        col = req.ctx["col"]
        if req.ctx["world"] != self.world:
            return
        r = res.ints("ground") if res is not None and res.ok else None
        if err is not None or r is None or len(r) != 2:
            self.ground.on_fail(col)
            return
        self._apply_ground(col, r[0], r[1], req.ctx.get("mob"))

    def _apply_ground(self, col: tuple[int, int], ok: int, z: int, mob) -> None:
        if mob:
            # луч мог упереться в ящик моба (у него нет MCW_BLOCK, мод его не пропускает): спросить позже
            self.ground.on_blocked(col, time.perf_counter())
            self.stats["ground_mob_blocked"] += 1
            return
        if self.ground.on_height(col, bool(ok), z, time.perf_counter()):
            x, z = col
            self.selector.invalidate({((x + dx) >> 4, (z + dz) >> 4) for dx, dz in ((0, 0), (1, 0), (-1, 0), (0, 1),
                                                                                       (0, -1))})
            self.desired_dirty = True

    def _step_mc_ground(self, now: float) -> None:
        g = self.cfg.ground
        if not g.enabled or self.pose is None or not self.mirror.snap_done or now < self.next_ground_send:
            return
        self.next_ground_send = now + g.send_interval_s
        bx, _, bz = self.pose.pos.block(self.y_off)
        c = self.ground.take_ground((bx, bz), g.max_columns)
        if c and self.mc.send({"t": "ground", "c": c}):
            self.stats["ground_cols_sent"] += len(c) // 4
        if now >= self.next_ground_clear:
            self.next_ground_clear = now + 2.0
            mx, _, mz, _ = self.pose.mc(self.y_off)
            for box in self.ground.take_clears((mx, mz)):
                self.mc.send({"t": "ground_clear", "box": box})
                self.stats["ground_clears"] += 1
            if now >= self.next_trim:
                self.next_trim = now + 10.0
                self.ground.trim((mx, mz), 2 * max(g.clear_dist_m, g.radius_m))

    # -- действия
    def on_key(self, action: str) -> None:
        self.stats["key_" + action] += 1
        if action == "fp":
            self.set_fp(not self.fp_on)
            return
        if action == "inventory":
            self.inventory_key()
            return
        if action == "horse":
            if self.client is not None and self.client.active and self.mc.up:
                self.mc.send({"t": "horse"})         # лошадь MC; Плотву в режиме Стива блокирует mcwf_block
            return
        if self.client is not None and self.client.active:
            # режим Стива: слоты — у клиента, как цифры и колесо в Майнкрафте (сервер узнает от клиента сам)
            if action.startswith("slot") and action[4:].isdigit():
                self.client.tap_key(GLFW_KEY_0 + int(action[4:]))
                return
            if action in ("slot_prev", "slot_next"):
                self.client.scroll(1.0 if action == "slot_prev" else -1.0)
                return
            if action in STEVE_MC_ACTIONS:
                return                               # ломать, ставить и крафтить — сам Майнкрафт (ЛКМ, ПКМ, инвентарь)
        if action == "toggle":
            self.build_mode = not self.build_mode
            log.info("режим строительства: %s", "вкл" if self.build_mode else "выкл")
            self.notify("MCW: build mode " + ("ON" if self.build_mode else "OFF"), key="mode")
            return
        if not self.build_mode:
            return
        if action in ("slot_prev", "slot_next"):
            self.select_slot((self.slot + (1 if action == "slot_next" else -1)) % 9)
        elif action.startswith("slot"):
            self.select_slot(int(action[4:]) - 1)
        elif action in ("break", "place"):
            self.start_act(action)
        elif action == "craft_menu":
            self.toggle_craft()
        elif action in ("craft_prev", "craft_next"):
            if not self.craft.open:
                self.toggle_craft()
            elif self.craft.items:
                self.craft.move(1 if action == "craft_next" else -1)
                self.notify(self.craft.text(), key="craft")
        elif action == "craft_do":
            self.craft_selected()
        elif action == "summon":
            self.summon()

    def select_slot(self, k: int) -> None:
        """Слот хотбара — у сервера: без мода выбор не делаем (иначе следующий inv молча вернул бы старый sel).
        Номер мост ставит сразу, подтверждение — inv.sel того же тика."""
        if not self.mc.up:
            self.notify("MCW: Minecraft is offline", key="offline")
            return
        self.slot = k
        self.act_id += 1
        self.acts[self.act_id] = {"a": "select", "t": time.perf_counter()}
        self.mc.send({"t": "act", "id": self.act_id, "a": "select", "slot": k})
        item = self.inv_slots.get(k)
        self.notify(f"MCW: slot {k + 1} - " + (f"{short_item(item[0])} x{item[1]}" if item else "empty"), key="slot")

    # -- крафт: список (recipes) и крафт (craft) решает мод, здесь курсор и текст для mcw_notify
    def toggle_craft(self) -> None:
        if self.craft.open:
            self.craft.open = False
            self.notify("MCW: craft list closed", key="craft")
            return
        if not self.mc.up:
            self.notify("MCW: Minecraft is offline", key="offline")
            return
        self.craft.open = True
        self.request_recipes(notify=True)

    def request_recipes(self, notify: bool) -> None:
        if not self.mc.up:
            return
        self.act_id += 1
        self.craft.req_id = self.act_id
        self.craft.notify_on_reply |= notify
        self.craft.stale = False
        self.next_recipes = time.perf_counter() + 0.5
        self.mc.send({"t": "recipes", "id": self.act_id})
        self.stats["sent_recipes"] += 1

    def _step_craft(self, now: float) -> None:
        """Инвентарь поменялся при открытом списке — перезапросить, не чаще раза в 0,5 с."""
        c = self.craft
        if c.open and c.stale and c.req_id is None and now >= self.next_recipes:
            self.request_recipes(notify=False)

    def on_recipes(self, m: dict) -> None:
        c = self.craft
        if m.get("id") != c.req_id:
            return                                   # ответ на старый запрос: следом придёт новый
        c.req_id = None
        c.set_list(m)
        if c.notify_on_reply and c.open:
            self.notify(c.text(), key="craft")
        c.notify_on_reply = False

    def craft_selected(self) -> None:
        r = self.craft.current() if self.craft.open else None
        if r is None:
            self.notify(self.craft.text() if self.craft.open else "MCW: open the craft list first", key="craft")
            return
        if not self.mc.up:
            self.notify("MCW: Minecraft is offline", key="offline")
            return
        self.act_id += 1
        self.acts[self.act_id] = {"a": "craft", "recipe": r.id, "item": r.item, "t": time.perf_counter()}
        self.mc.send({"t": "craft", "id": self.act_id, "recipe": r.id, "n": 1})
        self.stats["act_craft"] += 1

    def on_craft_ack(self, a: dict, m: dict) -> None:
        """Итог крафта — своим родом уведомления: листание списка (род craft) его не вытеснит."""
        if m.get("ok"):
            self.stats["craft_ok"] += 1
            self.notify(f"MCW: crafted {m.get('made')} {short_item(str(m.get('item') or a['item']))}", key="crafted")
        else:
            self.stats["craft_fail"] += 1
            why = str(m.get("why", "?"))
            self.notify(f"MCW: cannot craft {short_item(a['item'])}: {WHY.get(why, why)}", key="crafted")
        if self.craft.open:
            self.request_recipes(notify=False)

    # -- вид от первого лица: камера и взгляд — в Ведьмаке (mcw_steve.ws), docs/STEVE.md
    def set_fp(self, on: bool) -> None:
        if self.w3_state != "synced" or not self.in_world:
            self.notify("MCW: first person needs the game world", key="fp")
            return
        self._call("fp", "mcwf_on", (1 if on else 0, 0, self.cfg.fp.sens), self.cfg.poll.timeout_s, {"on": on})

    def _step_fp_start(self) -> None:
        """Раз на подключение: fp.start_on — включить; иначе спросить игру (mcwf_state): мост мог перезапуститься, когда
        вид от первого лица в игре уже включён, и F4 тогда сначала «включал» его ещё раз."""
        if self.fp_auto_done or self.w3_state != "synced" or not self.in_world:
            return
        self.fp_auto_done = True
        if self.cfg.fp.start_on:
            self.set_fp(True)
        else:
            self._call("fpstate", "mcwf_state", (), self.cfg.poll.timeout_s, {})

    def _res_fpstate(self, req, seq: int, res, err) -> None:
        v = res.ints("fpstate") if res is not None and res.ok else None
        if v and v[0] == 1 and not self.fp_on:
            self.fp_on = True
            self.fp_src = v[1]
            log.info("вид от первого лица уже включён в игре (взгляд %.1f / %.1f)", v[2] / 100, v[3] / 100)

    def _res_fp(self, req, seq: int, res, err) -> None:
        v = res.ints("fp") if res is not None and res.ok else None
        if not v or v[0] < 0:
            log.warning("mcwf_on не сработал (%s): нужен mcw_steve.ws в modMCW", err or (res.text if res else None))
            self.notify("MCW: first person unavailable (mcw_steve.ws)", key="fp")
            return
        self.fp_on = v[0] == 1
        self.fp_src = v[1] if self.fp_on else None
        self.cam_fov = None                     # камера от первого лица создаётся заново
        log.info("вид от первого лица: %s (взгляд %.1f / %.1f)", "вкл" if self.fp_on else "выкл",
                 v[2] / 100, v[3] / 100)
        self.notify("MCW: first person " + ("ON" if self.fp_on else "OFF"), key="fp")

    # -- режим Стива: мышь у клиента MC, удары Геральта в Ведьмаке заблокированы (mcw_steve.ws: mcwf_block)
    def steve_wanted(self) -> bool:
        return self.fp_on and self.in_world and self.w3_state == "synced" and not self.w3_susp

    def _step_fpwatch(self, now: float) -> None:
        """Вид от первого лица включён — 4 раза в секунду mcwf_state: не идёт ли сцена (диалог, катсцена, лошадь). На
        время сцены режим Стива гаснет: бой и движение снова у Геральта, клиент идёт за ним, оверлея нет."""
        if not self.fp_on or now < self.next_fpwatch or self._n("fpwatch") or self.w3_state != "synced":
            return
        self.next_fpwatch = now + 0.25
        self._call("fpwatch", "mcwf_state", (), self.cfg.poll.timeout_s, {})

    def _res_fpwatch(self, req, seq: int, res, err) -> None:
        v = res.ints("fpstate") if res is not None and res.ok else None
        if not v or v[0] < 0:
            return
        if v[0] == 0:
            if self.fp_on:
                log.info("вид от первого лица в игре выключен (перезапуск скриптов?)")
            self.fp_on = False
            self.w3_susp = False
            return
        susp = len(v) > 7 and v[7] == 1
        if susp != self.w3_susp:
            log.info("Ведьмак: %s", "сцена (диалог, катсцена, лошадь) - режим Стива ждёт" if susp else "сцена кончилась")
            self.w3_susp = susp

    def _step_freeze(self) -> None:
        """Открыт экран MC — взгляд Ведьмака стоит (мышь ведёт курсор MC), закрыт — снова крутится."""
        want = self.client.screen_open
        if self.w3_freeze == want or self._n("freeze") or self.w3_state != "synced" or not self.fp_on:
            return
        self._call("freeze", "mcwf_freeze", (1 if want else 0,), self.cfg.poll.timeout_s, {"on": want})

    def _res_freeze(self, req, seq: int, res, err) -> None:
        v = res.ints("fpfreeze") if res is not None and res.ok else None
        if v and v[0] >= 0:
            self.w3_freeze = v[0] == 1

    def _step_mouse(self) -> None:
        """Сдвиги и колесо мыши (Raw Input) — клиенту: курсор экрана MC, колесо — слоты. Только в окне Ведьмака."""
        if self.raw is None:
            return
        dx, dy, wheel = self.raw.take()
        c = self.client
        if not c.active or (self.foreground is not None and not self.foreground()):
            return
        if dx or dy:
            c.cursor(dx, dy)
        if wheel:
            c.scroll(wheel)

    def inventory_key(self) -> None:
        """E в режиме Стива. Экран MC открыт — закрыть. Иначе спросить Ведьмака: есть подсказка взаимодействия (дверь,
        NPC, сундук) — E уже его, нет — открыть инвентарь MC. Вне режима Стива E — только Ведьмака."""
        c = self.client
        if c is None or not c.active:
            return
        if c.screen_open:
            c.tap_key(GLFW_KEY_E)
            return
        if not self._n("interact"):
            self._call("interact", "mcwf_interact", (), self.cfg.poll.timeout_s, {})

    def _res_interact(self, req, seq: int, res, err) -> None:
        v = res.ints("interact") if res is not None and res.ok else None
        if v is None:
            if res is not None and res.failed:
                v = [0]                              # старый мод без mcwf_interact: E — инвентарю
            else:
                return
        if v[0] == 1:
            self.stats["e_witcher"] += 1
            return
        self.stats["e_inventory"] += 1
        if self.client is not None:
            self.client.tap_key(GLFW_KEY_E)

    def _step_block(self, active: bool) -> None:
        """Уровень блокировки Геральта: 0 — режим Стива выключен, 1 — мышь у клиента (бой), 2 — ходит клиент (бой и
        движение)."""
        if not self.cfg.client.block_witcher or self.block_cap is False:
            return
        level = 0 if not active else (2 if self.client is not None and self.client.driving else 1)
        if self.w3_state != "synced" or not self.in_world or self.w3_block == level or self._n("block"):
            return
        if not self.fp_auto_done or self._n("fpstate"):
            return                               # сначала узнаем, включён ли уже вид от первого лица (_step_fp_start)
        self._call("block", "mcwf_block", (level,), self.cfg.poll.timeout_s, {"on": level})

    def _res_block(self, req, seq: int, res, err) -> None:
        if err is None and res is not None and res.failed:
            log.warning("мод без mcwf_block (старый mcw_steve.ws): удары Геральта не блокируются")
            self.block_cap = False
            return
        v = res.ints("fpblock") if res is not None and res.ok else None
        if not v or v[0] < 0:
            return                               # нет ответа или Геральта: следующий шаг спросит снова
        self.block_cap = True
        self.w3_block = v[0]
        log.info("Геральт: %s", ("бой и движение снова его", "бой заблокирован (мышь у Майнкрафта)",
                                 "бой и движение заблокированы (ходит Майнкрафт, Геральт - кукла)")[min(2, max(0, v[0]))])

    # -- ходит клиент MC (client.drive): глаз и кукла Ведьмака — по клиенту, взгляд — клиенту, FOV — как у камеры
    def _step_drive(self, now: float) -> None:
        c = self.client
        if not self.fp_on or self.w3_state != "synced" or not self.in_world:
            return
        sens, tmo = self.cfg.fp.sens, self.cfg.poll.timeout_s
        want_src = 1 if c.driving and c.eye_fresh(now) else 0
        if self.fp_src != want_src and not self._n("fpsrc") and not self._n("fp"):
            self._call("fpsrc", "mcwf_on", (1, want_src, sens), tmo, {"src": want_src})
            return
        if want_src == 1 and c.eye_new and not self._n("mc"):
            e = c.eye
            try:
                x, y, z = (float(v) for v in e["p"])
                vx, vy, vz = (float(v) for v in e["v"])
                h = float(e["h"])
            except (KeyError, TypeError, ValueError):
                self.stats["bad_eye"] += 1
                c.eye_new = False
                return
            col = (math.floor(x), math.floor(z))
            hg = self._ground_near(col)
            if hg is not None and y - h < hg / 100.0 - 2.0:
                # игрок MC провалился сквозь землю Ведьмака: камеру и куклу за ним не ведём, иначе Геральт уйдёт под мир,
                # мост отдаст эту точку серверу, и тот будет возвращать туда игрока (клиент сам вытолкнет его наверх)
                self.stats["eye_below_ground"] += 1
                c.eye_new = False
                return
            wx, wy, wz = coords.mc_pos_to_w(x, y, z, y_offset=self.y_off)
            # скорость MC (x, y вверх, z на юг) -> Ведьмак (x, y на север, z вверх): (vx, -vz, vy)
            args = tuple(coords.to_cm(v) for v in (wx, wy, wz, vx, -vz, vy, h))
            c.eye_new = False
            self._call("mc", "mcwf_mc", args, tmo, {"t": now})
        if c.active and self.cam_fov is None and not self._n("fov"):
            self._call("fov", "mcwf_fov", (round(self.cfg.fp.fov * 100),), tmo, {})
        elif c.active and self.cam_fov is not None:
            c.set_fov(self._client_vfov())         # клиент переподключился или окно сменило размер — шлётся только разница

    def _ground_near(self, col: tuple[int, int]) -> Optional[float]:
        """Земля Ведьмака под колонной (см): своя, ближайшая известная в 2 колоннах или последняя известная не дальше 8
        колонн. Колонны, рельеф которых ещё не пришёл (лошадь MC на полном скаку), тоже под страховкой: 2026-10-04
        лошадь ушла в пустоту там, где земли мост не знал, камера и кукла ушли за ней под мир."""
        def at(c: tuple[int, int]) -> Optional[float]:
            h = self.solid.ground_at(c)               # земля без крыш (в доме крыша над головой — не земля)
            return h if h is not None else self.ground.height.get(c)

        hg = at(col)
        if hg is None:
            near = [(abs(dx) + abs(dz), v) for dx in range(-2, 3) for dz in range(-2, 3)
                    if (v := at((col[0] + dx, col[1] + dz))) is not None]
            hg = min(near)[1] if near else None
        if hg is not None:
            self.last_ground = (col, hg)
        elif self.last_ground is not None and max(abs(col[0] - self.last_ground[0][0]),
                                                  abs(col[1] - self.last_ground[0][1])) <= 8:
            hg = self.last_ground[1]
        return hg

    def _step_terrain(self, now: float) -> None:
        """Земля Ведьмака клиенту MC (его физика, Terrain): известные высоты колонн, ближние к Геральту первыми."""
        c = self.client
        if not c.up or self.pose is None or now < self.next_terrain:
            return
        if c.terrain_reset:
            if c.send({"t": "terrain", "reset": True}) and c.send({"t": "solid", "reset": True}):
                c.terrain_reset = False
                self.ground.client_reset()
                self.solid.client_reset()
        x, _, z, _ = self.pose.mc(self.y_off)
        cols = self.ground.take_terrain((math.floor(x), math.floor(z)), 512)
        if cols and c.send({"t": "terrain", "c": cols}):
            self.stats["terrain_cols"] += len(cols) // 3
        at = self._solid_pos(now)
        if at is not None:
            sc = self.solid.take_client(at[0], self.cfg.solid.max_columns)
            if sc and c.send({"t": "solid", "c": sc}):
                self.stats["solid_sent"] += len(sc)
        self.next_terrain = now + 0.05

    # -- коллизия Ведьмака для физики клиента MC (solid.py, mcw_cols в mcw_collide.ws): дома, стены, полы, двери
    def _solid_pos(self, now: float) -> Optional[tuple[tuple[float, float, float], tuple[float, float]]]:
        """Ноги игрока MC и его скорость по x, z: от клиента, если он ходит сам, иначе — поза Геральта."""
        c = self.client
        if c is not None and c.driving and c.eye_fresh(now):
            e = c.eye
            try:
                x, y, z = (float(v) for v in e["p"])
                vx, _, vz = (float(v) for v in e["v"])
                return (x, y - float(e["h"]), z), (vx, vz)
            except (KeyError, TypeError, ValueError):
                pass
        if self.pose is None:
            return None
        x, y, z, _ = self.pose.mc(self.y_off)
        return (x, y, z), (0.0, 0.0)

    def _step_solid(self, now: float) -> None:
        """Колонны вокруг игрока MC — у игры (mcw_cols), пока включён вид от первого лица: только тогда клиент ходит
        сам. Темп — токенами rate_hz, не больше inflight вопросов сразу."""
        s, c = self.cfg.solid, self.client
        dt, self.solid_tick = now - self.solid_tick, now
        self.solid_tokens = min(max(1.0, s.rate_hz * 0.25), self.solid_tokens + dt * s.rate_hz)
        if (not s.enabled or c is None or not c.up or not self.fp_on or not self.in_world or self.w3_state != "synced"
                or self.solid_cap is False):
            return
        if self._n("cols") >= s.inflight or self.solid_tokens < 1.0 or len(self.inflight) >= self.cfg.w3.max_inflight - 1:
            return
        at = self._solid_pos(now)
        if at is None:
            return
        pos, vel = at
        if now >= self.next_solid_trim:
            self.next_solid_trim = now + 5.0
            self.solid.trim(pos, s.keep_m)
        cols = self.solid.want(pos, vel, s.per_call, now)
        if not cols:
            return
        z0 = round((pos[1] - self.y_off) * 100)              # z Ведьмака ног, см
        ctx = {"cols": cols, "z0": z0, "world": self.world}
        if self._call("cols", "mcw_cols", (z0, "|".join(f"{x}:{z}" for x, z in cols)), s.timeout_s, ctx) is None:
            self.solid.on_fail(cols)
            return
        self.solid_tokens -= 1.0

    # -- пауза Ведьмака -> сервер MC стоит (tick freeze): мобы не бьют, пока игра в меню или без фокуса
    def _step_w3pause(self, now: float) -> None:
        if self.w3_state != "synced" or now < self.next_w3pause or self._n("w3pause"):
            return
        self.next_w3pause = now + 0.5
        self._call("w3pause", "mcw_state", (), self.cfg.poll.timeout_s, {})

    def _res_w3pause(self, req, seq: int, res, err) -> None:
        t = res.tokens("state") if res is not None and res.ok else None   # "active 1 paused 0 apause 0 ... menu 0 ..."
        if not t:
            return
        kv = dict(zip(t[0::2], t[1::2]))
        self.w3_paused = kv.get("paused") == "1" or kv.get("menu") == "1"

    def _step_mc_freeze(self) -> None:
        """Заморозить сервер MC, пока Ведьмак на паузе, в меню, в сцене (режим Стива ждёт), не в мире или не на связи;
        разморозить, когда снова играем. Шлётся только смена (и после переподключения к серверу)."""
        if not self.cfg.mc.freeze_on_pause or not self.mc.up:
            return
        if self.w3_state == "synced" and self.w3_paused is None:
            return                                   # ждём первого mcw_state
        want = self.w3_state != "synced" or not self.in_world or bool(self.w3_paused) or self.w3_susp
        if want == self.mc_frozen:
            return
        if self.mc.send({"t": "cmd", "c": "tick freeze" if want else "tick unfreeze"}):
            self.mc_frozen = want
            self.stats["mc_freeze" if want else "mc_unfreeze"] += 1
            log.info("сервер MC: %s", "заморожен (Ведьмак на паузе, в меню или в сцене)" if want else "идёт")

    def _step_supervise(self, now: float) -> None:
        if self.sup_server is not None:
            self.sup_server.step(now, self.mc.up)
        if self.sup_client is not None:
            c = self.client
            self.sup_client.step(now, c.up and c.in_world, allowed=self.mc.up)

    # -- модели MC (mcw_models.ws): лошади по позе от клиента, тело Стива на Геральте
    def _step_horses(self) -> None:
        """Лошади рядом с игроком MC (horses от клиента, раз в кадр) -> mcwm_horse по слотам 0..3; лошадь пропала —
        слот прячется (flags 0). Пока ответы прошлого кадра в полёте, кадр пропускается."""
        c = self.client
        if not c.horses_new or self.w3_state != "synced" or not self.in_world or self.horse_cap is False:
            return
        if self._n("horse") >= 4:
            self.stats["horse_skip"] += 1
            return
        c.horses_new = False
        tmo, seen = self.cfg.poll.timeout_s, set()
        for h in c.horses:
            try:
                hid, x, y, z, yaw, pitch, walk, speed, flags = h[:9]
                hid, flags = int(hid), int(flags)
                x, y, z = float(x), float(y), float(z)
                yaw_w = round(((180.0 - float(yaw)) % 360.0) * 100)       # курс W3 = 180 - yaw MC, как у взгляда
                if flags & 4 and len(h) >= 12:
                    # под всадником: ноги от глаза игрока (MC -> W3: (dx, -dz, dy)) — Ведьмак ставит лошадь от своей
                    # камеры каждый кадр (flags 8, mcw_models.ws), без дрожи на скаку
                    dx, dy, dz = (float(v) for v in h[9:12])
                    pos = (coords.to_cm(dx), coords.to_cm(-dz), coords.to_cm(dy))
                    flags |= 8
                else:
                    if not flags & 4:
                        y = self._horse_ground(x, y, z)
                    wx, wy, wz = coords.mc_pos_to_w(x, y, z, y_offset=self.y_off)
                    pos = (coords.to_cm(wx), coords.to_cm(wy), coords.to_cm(wz))
                args = (*pos, yaw_w, round(float(pitch) * 100), round(float(walk) * 1000), round(float(speed) * 1000),
                        flags)
            except (TypeError, ValueError):
                self.stats["bad_horse"] += 1
                continue
            slot = self.horse_slots.get(hid)
            if slot is None:
                free = sorted(set(range(4)) - set(self.horse_slots.values()))
                if not free:
                    continue
                slot = self.horse_slots[hid] = free[0]
            seen.add(hid)
            self._call("horse", "mcwm_horse", (slot,) + args, tmo, {"slot": slot})
        for hid in [k for k in self.horse_slots if k not in seen]:
            slot = self.horse_slots.pop(hid)
            self._call("horse", "mcwm_horse", (slot, 0, 0, 0, 0, 0, 0, 0, 0), tmo, {"slot": slot})

    def _horse_ground(self, x: float, y: float, z: float) -> float:
        """Лошадь без всадника на сервере стоит на barrier (высота округлена до блока): до 0,5 м висит или утопает.
        Ведьмаку — на настоящей земле колонны, если та в пределах 0,6 м (иначе лошадь стоит на блоке MC или упала)."""
        col = (math.floor(x), math.floor(z))
        hg = self.solid.ground_at(col)
        if hg is None:
            hg = self.ground.height.get(col)
        if hg is not None and abs(y - hg / 100.0) <= 0.6:
            return hg / 100.0
        return y

    def _res_horse(self, req, seq: int, res, err) -> None:
        if res is not None and res.failed and self.horse_cap is None:
            log.warning("мод без mcwm_horse (старый mcw_models.ws): лошадей MC в Ведьмаке не видно")
            self.horse_cap = False
        elif res is not None and res.ok:
            self.horse_cap = True

    def _step_skin(self, now: float) -> None:
        """Тело Стива на Геральте (mcwm_skin(1), идемпотентно): после загрузки сейва Геральт новый — надеть снова.
        Надето — сверка раз в 5 с; шаблонов нет (modMCWModels не загружен) — раз в 30 с."""
        if (not self.cfg.skin.enabled or self.w3_state != "synced" or not self.in_world or self.skin_cap is False
                or now < self.next_skin or self._n("skin")):
            return
        self.next_skin = now + 2.0                   # ответа не будет — переспросить скоро; ответ переставит срок
        self.skin_asked = now
        self._call("skin", "mcwm_skin", (1,), self.cfg.poll.timeout_s, {})

    def _res_skin(self, req, seq: int, res, err) -> None:
        if res is not None and res.failed:
            if self.skin_cap is None:
                log.warning("мод без mcwm_skin (старый mcw_models.ws): тела Стива на Геральте нет")
            self.skin_cap = False
            return
        v = res.ints("skin") if res is not None and res.ok else None
        if not v or v[0] < 0:
            return
        self.skin_cap = True
        self.next_skin = self.skin_asked + (5.0 if v[0] == 1 else 30.0)
        if v[0] != self.skin_state:
            if v[0] == 1:
                log.info("тело Стива на Геральте: %d частей", v[1])
            else:
                log.warning("тело Стива не надето: шаблонов modMCWModels нет (мод не установлен или игра не "
                            "перезапущена после установки)")
        self.skin_state = v[0]

    def _res_cols(self, req, seq: int, res, err) -> None:
        cols = req.ctx["cols"]
        if req.ctx["world"] != self.world:
            self.solid.on_fail(cols)
            return
        if res is not None and res.failed:
            if self.solid_cap is None:
                log.warning("мод без mcw_cols (старый mcw_collide.ws): для клиента MC дома остаются сплошными")
            self.solid_cap = False
            self.solid.on_fail(cols)
            return
        if err is not None or res is None or not res.ok:
            self.solid.on_fail(cols)
            return
        if self.solid_cap is None:
            log.info("коллизия Ведьмака для клиента MC: mcw_cols есть (дома, стены, двери)")
        self.solid_cap = True
        self.stats["solid_cols"] += self.solid.on_reply(cols, req.ctx["z0"], res.body, time.perf_counter())
        ms = res.ints("cms")
        if ms:
            self.solid_ms.append(ms[0])

    def _client_vfov(self) -> float:
        """FOV камеры Ведьмака -> вертикальный FOV мира клиента (client.fov_axis: какой FOV у камеры Ведьмака)."""
        win = self.client.window()
        if self.cfg.client.fov_axis == "h" and win:
            return math.degrees(2 * math.atan(math.tan(math.radians(self.cam_fov) / 2) * win[1] / win[0]))
        return self.cam_fov

    def _res_fpsrc(self, req, seq: int, res, err) -> None:
        v = res.ints("fp") if res is not None and res.ok else None
        if v and v[0] == 1:
            self.fp_src = v[1]
            log.info("камера от первого лица: глаз %s", "клиента MC (Геральт - кукла)" if v[1] == 1 else "Геральта")

    def _res_mc(self, req, seq: int, res, err) -> None:
        v = res.ints("look") if res is not None and res.ok else None
        if not v or len(v) < 4:
            self.stats["mc_fail"] += 1
            return
        self.stats["mc_ok"] += 1
        # взгляд Ведьмака -> клиенту: курс MC = 180 - курс W3, наклон MC (+ вниз) = -наклон W3 (+ вверх)
        self.client.look(coords.w_yaw_to_mc(v[0] / 100.0), -v[1] / 100.0)
        if v[3] and not self.far_logged:
            log.info("Геральт дальше 10 м от игрока MC (его перенёс Ведьмак): куклу не тянем, сервер перенесёт игрока")
        self.far_logged = bool(v[3])

    def _res_fov(self, req, seq: int, res, err) -> None:
        v = res.ints("fov") if res is not None and res.ok else None
        if not v or v[0] <= 0:
            return
        self.cam_fov = v[0] / 100.0
        vfov = self._client_vfov()
        log.info("FOV камеры Ведьмака %.2f (рендер %.2f) -> клиенту %.2f по вертикали", self.cam_fov, v[1] / 100.0, vfov)
        self.client.set_fov(vfov)

    # -- бой (combat.py, mcw_combat.ws): NPC вокруг -> прокси на сервере MC; удары NPC по Геральту -> phurt
    def _step_npcs(self, now: float) -> None:
        cb, c = self.cfg.combat, self.client
        if not cb.enabled or self.npcs_cap is False or self.w3_state != "synced" or not self.mc.up:
            return
        if not (self.fp_on and c.driving and self.in_world):
            if self.npcs_sent and self.mc.send({"t": "npcs", "l": []}):
                self.npcs_sent = False                # вышли из режима Стива: прокси не нужны
                self.npc_count = 0
            if self.client_npcs_sent and c.send({"t": "npcs", "l": []}):
                self.client_npcs_sent = False
            return
        if now < self.next_npcs or self._n("npcs"):
            return
        self.next_npcs = now + 1.0 / cb.hz
        self._call("npcs", "mcw_npcs", (round(cb.radius_m), cb.max), cb.timeout_s, {})

    def _res_npcs(self, req, seq: int, res, err) -> None:
        if res is not None and res.failed:
            if self.npcs_cap is None:
                log.warning("мод без mcw_npcs (нет mcw_combat.ws): боя с NPC Ведьмака нет")
            self.npcs_cap = False
            return
        if err is not None or res is None or not res.ok:
            return
        if self.npcs_cap is None:
            log.info("бой: mcw_npcs есть, NPC Ведьмака -> прокси на сервере MC")
        self.npcs_cap = True
        npcs, hurts, t = combat.parse_npcs(res.body, self.y_off)
        self.npc_tracker.update(npcs, t if t is not None else time.perf_counter())
        if not self.mc.up:
            return
        if (npcs or self.npcs_sent) and self.mc.send(combat.npcs_msg(npcs)):
            self.npcs_sent = bool(npcs)
        # и клиенту напрямую: он ставит прокси сам перед каждым выбором цели (NpcProxies), без задержки сервера
        c = self.client
        if c is not None and c.up and (npcs or self.client_npcs_sent):
            msg = combat.client_npcs_msg(npcs, self.npc_map)
            if c.send(msg):
                self.client_npcs_sent = bool(msg["l"])
        if len(npcs) != self.npc_count:
            log.debug("бой: NPC рядом %d", len(npcs))
        self.npc_count = len(npcs)
        for i, frac in hurts:
            dmg = combat.hurt_dmg(frac, self.cfg.combat.hurt_mult)
            if dmg >= self.cfg.combat.min_hurt and self.mc.send({"t": "phurt", "id": i, "dmg": round(dmg, 3)}):
                self.stats["npc_hurts"] += 1
                log.info("бой: NPC %d ударил Геральта: %.1f%% жизненной силы -> %.2f урона MC", i, frac * 100, dmg)

    def on_npc_hit(self, m: dict) -> None:
        """npc_hit мода: Геральт MC попал по прокси NPC -> удар по NPC в Ведьмаке. Ближний — как удар Геральта: сразу
        предупреждение (mcw_npcwarn: NPC может поднять парирование или отскочить), сам удар — через impact_delay_s."""
        cb = self.cfg.combat
        args = combat.hit_args(m, cb.npc_mc_hp, cb.heavy_dmg, cb.ref_mc_dmg)
        if args is None or self.w3_state != "synced" or not self.in_world:
            self.stats["npc_hit_lost"] += 1
            return
        if args[3] or cb.impact_delay_s <= 0:
            self._call("npchit", "mcw_npchit", args, cb.timeout_s, {"m": m})
            return
        self._call("npcwarn", "mcw_npcwarn", (args[0], args[2]), cb.timeout_s, {})
        self.npc_hits.append((time.perf_counter() + cb.impact_delay_s, args, m))

    def _step_npc_hits(self, now: float) -> None:
        if not self.npc_hits or self.w3_state != "synced":
            return
        due = [h for h in self.npc_hits if h[0] <= now]
        if not due:
            return
        self.npc_hits = [h for h in self.npc_hits if h[0] > now]
        for _, args, m in due:
            self._call("npchit", "mcw_npchit", args, self.cfg.combat.timeout_s, {"m": m})

    def _res_npcwarn(self, req, seq: int, res, err) -> None:
        if res is not None and res.failed:
            self.stats["npcwarn_missing"] += 1        # старый мод: удар всё равно дойдёт, только без парирования

    def _res_npchit(self, req, seq: int, res, err) -> None:
        v = res.tokens("npchit") if res is not None and res.ok else None
        m = req.ctx["m"]
        if not v or v[0] != "1":
            self.stats["npc_hit_miss"] += 1
            log.info("бой: удар MC по NPC %s (%s) мимо: NPC уже нет", m.get("id"), m.get("kind"))
            return
        try:
            before, after = int(v[1]) / 100.0, int(v[2]) / 100.0
        except (IndexError, ValueError):
            return
        how = "parry" if v[3:4] == ["1"] else "dodge" if v[4:5] == ["1"] else None
        if how is not None:
            self.stats["npc_" + how] += 1
            if self.mc.up:
                self.mc.send({"t": "npc_block", "id": m.get("id"), "how": how})
            log.info("бой: NPC %s %s удар MC (%s)", m.get("id"), "парировал" if how == "parry" else "увернулся от",
                     m.get("kind"))
            return
        self.stats["npc_hit"] += 1
        log.info("бой: %s MC по NPC %s: %.2f%s -> здоровье %.0f -> %.0f", m.get("kind"), m.get("id"), float(m.get("dmg", 0)),
                 " крит" if m.get("crit") else "", before, after)

    # -- щит MC -> кукла Ведьмака в защите (mcwf_guard): удары NPC спереди парирует Ведьмак, урон ловит щит MC
    def _step_guard(self) -> None:
        c = self.client
        want = bool(self.fp_on and c.driving and self.in_world and c.state and c.state.get("block"))
        if (self.guard_cap is False or self.w3_state != "synced" or want == self.w3_guard or self._n("guard")
                or (not want and self.w3_guard is None and not self.fp_on)):
            return
        self._call("guard", "mcwf_guard", (int(want),), self.cfg.poll.timeout_s, {"on": want})

    def _res_guard(self, req, seq: int, res, err) -> None:
        if res is not None and res.failed:
            if self.guard_cap is None:
                log.warning("мод без mcwf_guard: щит MC не парирует удары NPC в Ведьмаке")
            self.guard_cap = False
            return
        v = res.ints("fpguard") if res is not None and res.ok else None
        if v and v[0] >= 0:
            self.guard_cap = True
            self.w3_guard = bool(v[0])

    # -- стены Ведьмака для стрел MC: путь стрелы (arrow_path мода) -> первое попадание луча (mcw_path) -> arrow_wall
    def on_arrow_path(self, m: dict) -> None:
        i = m.get("id")
        cmds = combat.path_cmds(m.get("p") or [], self.y_off)
        if (cmds is None or not self.cfg.arrows.walls or self.path_cap is False or self.w3_state != "synced"
                or not self.in_world or self._call("path", "mcw_path", (cmds,), self.cfg.poll.timeout_s, {"id": i}) is None):
            self.mc.send({"t": "arrow_wall", "id": i, "i": -1})   # стрела летит как в чистом MC

    def _res_path(self, req, seq: int, res, err) -> None:
        i = req.ctx["id"]
        if res is not None and res.failed:
            if self.path_cap is None:
                log.warning("мод без mcw_path: стрелы MC пролетают дома Ведьмака")
            self.path_cap = False
        elif res is not None and res.ok:
            self.path_cap = True
        k, p = combat.wall_point(res.tokens("path") if res is not None and res.ok else None, self.y_off)
        if self.mc.up:
            self.mc.send({"t": "arrow_wall", "id": i, "i": k, **({"p": p} if p is not None else {})})
        if k >= 0:
            self.stats["arrow_walls"] += 1
            log.debug("стрела %s воткнётся в стену Ведьмака на отрезке %d: %s", i, k, p)

    # -- стрелы клиента MC -> пул стрел Ведьмака (mcwa_arrows); пул заводится заранее: новая сущность растворяется
    def _step_arrows(self) -> None:
        c = self.client
        if (not self.cfg.arrows.enabled or self.arrows_cap is False or self.w3_state != "synced" or not self.in_world
                or self._n("arrows") >= self.cfg.arrows.inflight):
            return
        if not self.arrows_ready:
            if not self._n("arrows"):
                self._call("arrows", "mcwa_arrows", ("init",), self.cfg.poll.timeout_s, {"init": True})
            return
        if not c.arrows_new:
            return
        c.arrows_new = False
        cmds, bad = self.arrows.ops(c.arrows, self.y_off)
        self.stats["bad_arrow"] += bad
        if cmds:
            self._call("arrows", "mcwa_arrows", ("|".join(cmds),), self.cfg.poll.timeout_s, {"init": False})

    def _res_arrows(self, req, seq: int, res, err) -> None:
        if res is not None and res.failed:
            if self.arrows_cap is None:
                log.warning("мод без mcwa_arrows (нет mcw_combat.ws): стрел MC в Ведьмаке не видно")
            self.arrows_cap = False
            return
        v = res.ints("arrows") if res is not None and res.ok else None
        if v is None:
            if req.ctx["init"]:
                return                                # ответа нет: init повторится следующим шагом
            self.arrows.reset()                       # что показано, неизвестно: следующие кадры пришлют всё заново
            return
        self.arrows_cap = True
        if req.ctx["init"]:
            self.arrows_ready = v[0] >= 0
            if self.arrows_ready:
                log.info("стрелы MC: пул стрел Ведьмака заведён")
        elif len(v) > 1 and v[1]:
            self.stats["arrow_errors"] += v[1]

    # -- тест: призвать моба (ents.summon_type) перед Геральтом
    def summon(self) -> None:
        if not self.mc.up:
            self.notify("MCW: Minecraft is offline", key="offline")
            return
        c = self.cfg.ents
        self.act_id += 1
        self.acts[self.act_id] = {"a": "spawn", "type": c.summon_type, "t": time.perf_counter()}
        self.mc.send({"t": "spawn", "id": self.act_id, "type": c.summon_type, "d": c.summon_dist_m})

    def on_spawn_ack(self, a: dict, m: dict) -> None:
        if m.get("ok"):
            log.info("призван %s, сущность %s в %s", a["type"], m.get("ent"), m.get("p"))
            self.notify(f"MCW: {short_type(a['type'])} summoned", key="summon")
        else:
            why = str(m.get("why", "?"))
            self.notify(f"MCW: cannot summon {short_type(a['type'])}: {WHY.get(why, why)}", key="summon")

    def start_act(self, action: str) -> None:
        if not self.w3.up or self.pose is None or not self.in_world:
            log.info("%s: Ведьмак не в мире", action)
            return
        if not self.mc.up:
            self.notify("MCW: Minecraft is offline", key="offline")
            return
        if self._n("look") >= 2:
            return
        self._call("look", "mcw_look", (), self.cfg.act.look_timeout_s,
                   {"action": action, "pose": self.pose, "slot": self.slot, "t": time.perf_counter()})

    def _res_look(self, req: Req, seq: int, res, err) -> None:
        action, pose = req.ctx["action"], req.ctx["pose"]
        r = res.ints("look") if res is not None and res.ok else None
        if err is not None or r is None or len(r) != 5:
            self.notify(f"MCW: {action}: no reply from the game", key="act")
            return
        if not r[0]:
            self.notify(f"MCW: {action}: no target in reach", key="act")
            return
        if not r[4]:
            # луч упёрся в ящик моба (не MCW_BLOCK): это не рельеф и не стена — ни ставить к нему, ни ломать
            mob = self.entd.hit(coords.w_to_mc_pos(r[1] / 100.0, r[2] / 100.0, r[3] / 100.0, y_offset=self.y_off))
            if mob is not None:
                self.notify(f"MCW: {action}: {short_type(mob.type)} in the way", key="act")
                return
        cam_dir = pose.cam.dir if pose.cam is not None else (0.0, 1.0, 0.0)
        normal = None
        nr = res.ints("normal")                  # новый мод; у старого строки нет — грань выводим сами
        if nr and len(nr) == 3:
            v = tuple(c / 10000.0 for c in nr)
            if 0.5 <= math.sqrt(sum(c * c for c in v)) <= 1.5:
                normal = v
            else:
                self.stats["normal_bad"] += 1
        self.stats["normal_game" if normal else "normal_derived"] += 1
        cell, face, frac, own = look_target((r[1], r[2], r[3]), bool(r[4]), cam_dir, self.display.shown, self.y_off,
                                            eps=self.cfg.act.eps_m, ground_top=self.ground.top_at, normal_w=normal)
        if action == "break" and not own:
            self.notify("MCW: nothing to break there", key="act")
            return
        if not self.mc.up:
            return
        x, y, z, _ = pose.mc(self.y_off)
        yaw, pitch = pose.look_angles()
        self.act_id += 1
        msg = {"t": "act", "id": self.act_id, "a": action, "b": list(cell), "face": face, "slot": req.ctx["slot"],
               "p": [round(x, 3), round(y, 3), round(z, 3)], "yaw": round(yaw, 2), "pitch": round(pitch, 2)}
        if action == "place":
            msg["hit"] = list(frac)
        if self.mc.send(msg):
            self.acts[self.act_id] = {"a": action, "b": cell, "t": time.perf_counter(), "t_key": req.ctx["t"]}
            self.stats["act_" + action] += 1
            log.info("act %s %s грань %s (своё: %s, нормаль: %s, кнопка -> act %.0f мс)", action, cell, face, own,
                     "из игры" if normal else "выведена", (time.perf_counter() - req.ctx["t"]) * 1000)

    def on_ack(self, m: dict) -> None:
        a = self.acts.pop(m.get("id"), None)
        if a is None:
            return
        if a["a"] == "craft":
            self.on_craft_ack(a, m)
            return
        if a["a"] == "spawn":
            self.on_spawn_ack(a, m)
            return
        if m.get("ok"):
            self.stats["ack_ok"] += 1
            return
        self.stats["ack_fail"] += 1
        why = str(m.get("why", "?"))
        log.info("ack %s %s: отказ %s", a["a"], a.get("b"), why)
        if a["a"] != "select":
            self.notify(f"MCW: cannot {a['a']}: {WHY.get(why, why)}", key="act")

    # -- уведомления
    def notify(self, text: str, key: Optional[str] = None) -> None:
        """В очередь mcw_notify. key — одно сообщение такого рода в очереди: новое заменяет старое."""
        if not self.cfg.notify.enabled:
            return
        text = safe_text(text)
        if not text.strip():
            return                    # пустую строку "" парсер exec не разберёт, а пробел показывать незачем
        if key is not None:
            for i, (k, _) in enumerate(self.notes):
                if k == key:
                    del self.notes[i]
                    break
        self.notes.append((key, text))
        while len(self.notes) > self.cfg.notify.queue:
            self.notes.popleft()

    def _step_notify(self, now: float) -> None:
        if self.inv_flush_at is not None and now >= self.inv_flush_at:
            parts = [f"{d:+d} {short_item(k)}" for k, d in self.inv_delta.items() if d]
            self.inv_delta.clear()
            self.inv_flush_at = None
            if parts:
                self.notify("MCW: " + ", ".join(parts[:4]) + (" ..." if len(parts) > 4 else ""), key="inv")
        if not self.notes or now < self.next_note or not self.in_world:
            return
        if self._n("notify") or len(self.inflight) >= self.cfg.w3.max_inflight - 1:
            return
        _, text = self.notes.popleft()
        self.next_note = now + self.cfg.notify.min_interval_s
        self._call("notify", "mcw_notify", (text,), self.cfg.poll.timeout_s, {"text": text})

    def _res_notify(self, req: Req, seq: int, res, err) -> None:
        if err is not None or res is None or not res.ok:
            log.info("mcw_notify %r без ответа (%s)", req.ctx["text"], err or "нет маркера")

    def _res_boom(self, req: Req, seq: int, res, err) -> None:
        if err is None and res is not None and res.failed and req.ctx["func"] == "boomat":
            log.info("мод без mcw_boomat: взрывы — mcw_boom перед Геральтом")
            self.caps["boomat"] = False
            self._boom(req.ctx["p"])
            return
        if req.ctx["func"] == "boomat" and res is not None and res.ok:
            self.caps["boomat"] = True
        r = res.ints("boom") if res is not None and res.ok else None
        if r and r[0] == 1 and req.epoch == self.epoch:
            self.booms += 1

    # ------------------------------------------------------------------------------------------------ Майнкрафт
    def on_mc_up(self) -> None:
        log.info("мод подключён (%s:%d): hello, жду снапшот", self.cfg.mc.host, self.cfg.mc.port)
        self.mirror.reset()
        self.selector.clear()
        self.ground.mc_reset()
        self.desired_dirty = True
        self.acts.clear()
        self.ents.reset()                        # мод после hello пришлёт всех заново как spawn
        self.items_near = 0
        self.craft.req_id = None
        self.inv_counts = None
        self.last_hp = None
        self.last_pos_key = None
        self.mc_frozen = None                    # новый сервер: заморозку — заново по состоянию Ведьмака
        self.npc_map = {}                        # прокси на новом сервере — заново (npc_map)
        self.npcs_sent = False
        self.stats["mc_connects"] += 1
        self.mc.send({"t": "hello", "v": 1, "world": world_name(self.world),
                      "want": {"radius": self.cfg.mc.radius_chunks}})
        self.t_hello = time.perf_counter()

    def on_mc_down(self, err) -> None:
        log.warning("мод: соединение потеряно (%s); показ в Ведьмаке заморожен до нового снапшота", err)
        self.mc.close()
        self.mirror.snap_done = False
        self.acts.clear()
        d = self.mc_backoff.fail()
        self.mc_next = time.perf_counter() + d
        self.stats["mc_lost"] += 1

    def on_mc(self, m: dict) -> None:
        t = m.get("t")
        if t in ("chunk", "blocks", "unchunk", "snap_end", "hello"):
            self.mirror.apply(m)
            self.desired_dirty = True
            if t == "chunk":
                self.ground.on_chunk((m["c"][0], m["c"][1]))
            elif t == "blocks":
                self.stats["mc_blocks"] += 1
            elif t == "hello":
                self.mc_hello = m
                self.mc_backoff.ok()
                log.info("мод: hello, Minecraft %s, мод %s, радиус %s, Геральт %s", m.get("mc"), m.get("mod"),
                         m.get("radius"), (m.get("player") or {}).get("p"))
            elif t == "snap_end":
                self.t_snap_end = time.perf_counter()
                for ch in list(self.ground.wait_chunk):
                    if ch in self.mirror.by_chunk:
                        self.ground.on_chunk(ch)
                log.info("мод: снапшот %s чанков, %s блоков за %.0f мс", m.get("chunks"), m.get("blocks"),
                         (time.perf_counter() - self.t_hello) * 1000)
        elif t == "ack":
            self.on_ack(m)
        elif t == "ent":
            self.stats["mc_ent"] += 1
            self._ents_notify(self.ents.apply(m))
        elif t == "recipes":
            self.on_recipes(m)
        elif t == "npc_hit":
            self.on_npc_hit(m)
        elif t == "npc_map":
            try:
                self.npc_map = {int(a): int(b) for a, b in m.get("m") or []}
            except (TypeError, ValueError):
                self.stats["bad_npc_map"] += 1
        elif t == "arrow_path":
            self.on_arrow_path(m)
        elif t == "horse":
            if m.get("ok"):
                log.info("лошадь Геральта %s (id %s) у %s", "призвана" if m.get("new") else "подошла", m.get("id"), m.get("p"))
                self.notify("MCW: horse" + (" summoned" if m.get("new") else " is here"), key="horse")
            else:
                log.warning("мод: лошадь не появилась: %s", m)
        elif t == "ground_ok":
            self.ground.on_ground_ok(m)
            if m.get("unloaded"):
                log.debug("ground: %s колонн в незагруженных чанках", m.get("unloaded"))
        elif t == "inv":
            self.on_inv(m)
        elif t == "hp":
            self.on_hp(m)
        elif t == "death":
            self.stats["deaths"] += 1
            log.info("мод: смерть: %s", m.get("msg"))
            self.notify(f"MCW: died - {m.get('msg', '')}", key="death")
        elif t == "respawn":
            self.last_pos_key = None              # Геральт в Майнкрафте на точке возрождения: вернуть сразу
            self.notify("MCW: respawned", key="death")
        elif t == "explosion":
            self.on_explosion(m)
        elif t == "geralt":
            # клиент MC вошёл под именем Геральта или вышел (марионетка вернулась): pos — сразу, inv и hp придут сами
            self.stats["geralt_" + ("client" if m.get("client") else "puppet")] += 1
            log.info("мод: Геральт теперь %s (%s)", "игрок клиента MC" if m.get("client") else "марионетка", m.get("p"))
            self.last_pos_key = None
        elif t == "pteleport":
            log.info("мод: сервер сам сдвинул Геральта в %s (%s); Ведьмак вернёт его следующим pos", m.get("p"),
                     m.get("dim"))
            self.last_pos_key = None
        elif t == "error":
            log.warning("мод: error %s: %s", m.get("why"), m.get("text"))
        elif t == "dig":
            log.debug("dig %s стадия %s", m.get("b"), m.get("stage"))

    def on_inv(self, m: dict) -> None:
        slots = {}
        for s in m.get("s") or []:
            if isinstance(s, list) and len(s) == 3:
                slots[s[0]] = (s[1], s[2])
        self.inv_slots = slots
        if isinstance(m.get("sel"), int):
            self.slot = m["sel"]
        counts: collections.Counter = collections.Counter()
        for item, n in slots.values():
            counts[item] += n
        if self.inv_counts is not None and self.cfg.notify.inv:
            for k in set(counts) | set(self.inv_counts):
                d = counts[k] - self.inv_counts[k]
                if d:
                    self.inv_delta[k] += d
            if any(self.inv_delta.values()) and self.inv_flush_at is None:
                self.inv_flush_at = time.perf_counter() + self.cfg.notify.inv_window_s
        if self.craft.open and counts != self.inv_counts:
            self.craft.stale = True              # подобрал, скрафтил, потратил: список рецептов устарел
        self.inv_counts = counts

    def on_hp(self, m: dict) -> None:
        hp = m.get("hp")
        if not isinstance(hp, (int, float)):
            return
        if self.cfg.notify.hp and self.last_hp is not None and hp < self.last_hp - 0.01:
            self.notify(f"MCW: HP {hp:g}/20, food {m.get('food')}", key="hp")
        self.last_hp = hp

    def on_explosion(self, m: dict) -> None:
        b = self.cfg.boom
        p = m.get("p")
        if not b.enabled or self.w3_state != "synced" or not self.in_world or self.pose is None or not p:
            return
        now = time.perf_counter()
        x, y, z, _ = self.pose.mc(self.y_off)
        if math.dist((x, y, z), p) > b.radius_m or now - self.last_boom < b.min_interval_s:
            return
        self.last_boom = now
        self._boom(p)

    def _boom(self, p) -> None:
        """Бочка в точке взрыва (mcw_boomat); старый мод — mcw_boom, бочка в 4 м перед Геральтом."""
        if self.caps["boomat"] is not False:
            w = coords.mc_pos_to_w(p[0], p[1], p[2], y_offset=self.y_off)
            self._call("boom", "mcw_boomat", tuple(coords.to_cm(v) for v in w), self.cfg.poll.timeout_s,
                       {"p": p, "func": "boomat"})
        else:
            self._call("boom", "mcw_boom", (), self.cfg.poll.timeout_s, {"p": p, "func": "boom"})

    # ------------------------------------------------------------------------------------------------ метрики
    def metrics(self, now: Optional[float] = None) -> dict[str, Any]:
        now = time.perf_counter() if now is None else now
        rtt = sorted(self.poll_rtt)
        return {
            "w3": self.w3_state, "mc": "up" if self.mc.up else "down", "in_world": self.in_world,
            "snap_done": self.mirror.snap_done, "world": self.world, "area": self.area,
            "poll_rtt_p50_ms": round(statistics.median(rtt) * 1000, 1) if rtt else None,
            "poll_rtt_p95_ms": round(rtt[min(len(rtt) - 1, int(len(rtt) * 0.95))] * 1000, 1) if rtt else None,
            "inflight": len(self.inflight), "batch_inflight": self._n("batch"), "pending_ops": self.pending_ops,
            "shown": len(self.display.shown), "desired": len(self.desired), "mirror": self.mirror.size(),
            "chunks": len(self.mirror.by_chunk), "ground_known": len(self.ground.top),
            "ground_sent": len(self.ground.sent), "ground_pending": len(self.ground.pending), "booms": self.booms,
            "build_mode": self.build_mode, "fp": self.fp_on, "slot": self.slot, "epoch": self.epoch, "stats": dict(self.stats),
            "ents": len(self.ents.ents), "ents_shown": len(self.entd.shown), "ents_inflight": self._n("ents"),
            "craft_open": self.craft.open, "craft_items": len(self.craft.items),
            "client": self.client.metrics() if self.client is not None else None, "w3_block": self.w3_block,
            "solid_known": len(self.solid.cols), "solid_pending": len(self.solid.pending), "mc_frozen": self.mc_frozen,
            "restarts": {"server": self.sup_server.restarts if self.sup_server else None,
                         "client": self.sup_client.restarts if self.sup_client else None},
            "solid_ms_p50": statistics.median(self.solid_ms) if self.solid_ms else None,
            "npcs": self.npc_count, "arrows": len(self.arrows.by_id),
        }

    def _log_metrics(self, now: float) -> None:
        m = self.metrics(now)
        t0, n0 = self._metrics_prev
        n = self.stats["poll_ok"]
        hz = (n - n0) / max(1e-6, now - t0)
        self._metrics_prev = (now, n)
        degraded = self.w3.up and hz < 0.5 * self.cfg.poll.hz
        log.info("метрики: w3=%s mc=%s мир=%s | poll %.1f Гц%s, RTT p50 %s p95 %s мс | в полёте %d (batch %d), "
                 "очередь batch %d оп | показано %d / должно %d | зеркало %d бл., %d чанков | рельеф %d известно, "
                 "%d у мода, %d ждут | мобы %d, показано %d | NPC %d, стрел %d | ресинков %d | клиент %s", m["w3"], m["mc"],
                 m["in_world"], hz, " (деградация)" if degraded else "", m["poll_rtt_p50_ms"], m["poll_rtt_p95_ms"],
                 m["inflight"], m["batch_inflight"], m["pending_ops"], m["shown"], m["desired"], m["mirror"],
                 m["chunks"], m["ground_known"], m["ground_sent"], m["ground_pending"], m["ents"], m["ents_shown"],
                 m["npcs"], m["arrows"], self.stats["resyncs"], self._client_line(m["client"]))

    @staticmethod
    def _client_line(c: Optional[dict]) -> str:
        if c is None:
            return "выкл"
        if not c["up"]:
            return "нет"
        ov = "нет" if c["overlay"] is None else ("работает" if c["overlay"] else "не запущен")
        return (f"{'в мире' if c['world'] else 'вне мира'}, {c['fps']} FPS, Стив {'вкл' if c['active'] else 'выкл'}, "
                f"оверлей {ov}")


# ---------------------------------------------------------------------------------------------------------------- CLI
def own_keys(cfg) -> dict:
    """Клавиши моста для сверки с input.settings: E (inventory) и X (horse) делит с Ведьмаком намеренно — E:
    взаимодействие, если есть подсказка, иначе инвентарь MC (Bridge.inventory_key); X: в режиме Стива лошадь MC (Плотву
    тогда блокирует mcwf_block), вне его — Плотва."""
    return {a: k for a, k in cfg.input.bind.items() if a not in ("inventory", "horse")}


def setup_logging(path: str, level: str = "INFO", console: bool = True) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
    if path:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(path, maxBytes=5 << 20, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.setLevel(getattr(logging, level.upper(), logging.INFO))
        root.addHandler(fh)
    if console:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        sh.setLevel(getattr(logging, level.upper(), logging.INFO))
        root.addHandler(sh)


def main(argv: Optional[list[str]] = None) -> int:
    w3net.utf8_stdio()
    ap = argparse.ArgumentParser(description="мост Minecraft (Fabric MCW) <-> The Witcher 3 (exec modMCW)")
    ap.add_argument("--config", default=None, help="TOML (по умолчанию bridge.toml рядом)")
    ap.add_argument("--w3-host")
    ap.add_argument("--w3-port", type=int, help="порт отладочного сервера Ведьмака (фейк: 37101)")
    ap.add_argument("--mc-host")
    ap.add_argument("--mc-port", type=int)
    ap.add_argument("--log", help="файл журнала (по умолчанию log.file из конфига)")
    ap.add_argument("--metrics", type=float, help="метрики в журнал раз в N с")
    ap.add_argument("--no-input", action="store_true", help="без горячих клавиш")
    ap.add_argument("--check-keys", action="store_true", help="сверить клавиши с input.settings и выйти")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    over: dict = {}
    for sec, key, val in (("w3", "host", a.w3_host), ("w3", "port", a.w3_port), ("mc", "host", a.mc_host),
                          ("mc", "port", a.mc_port), ("log", "file", a.log), ("log", "metrics_every_s", a.metrics)):
        if val is not None:
            over.setdefault(sec, {})[key] = val
    if a.no_input:
        over.setdefault("input", {})["enabled"] = False
    if a.verbose:
        over.setdefault("log", {})["level"] = "DEBUG"
    cfg = config_mod.load(a.config, over)
    if a.check_keys:
        hotkeys.parse_bindings(cfg.input.bind)
        got = hotkeys.check_conflicts(own_keys(cfg), cfg.input.settings or None)
        if got is None:
            print("input.settings не найден: сверять не с чем")
            return 2
        print("\n".join(got) if got else "конфликтов с input.settings и жёсткими клавишами нет")
        return 1 if got else 0
    setup_logging(config_mod.resolve(cfg.log.file) if cfg.log.file else "", cfg.log.level)
    if cfg.input.enabled:
        got = hotkeys.check_conflicts(own_keys(cfg), cfg.input.settings or None)
        for c in got or []:
            log.warning("клавиша занята игрой: %s", c)
    br = Bridge(cfg)
    try:
        br.run()
    except KeyboardInterrupt:
        br.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
