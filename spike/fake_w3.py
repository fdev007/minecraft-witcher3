"""fake_w3 - фейковая игра: отладочный сервер Ведьмака с exec-функциями по КОНТРАКТУ modMCW/modMCWProbe.

Протокол как у rw3d mock-server (v0.8.0 @5a0cf7d): BIND, reload, RootPath, exec, pkgSync,
OpcodeBreakdownRequest, Config list. Сверху - игровой мир-заглушка, чтобы мост и бенч гонялись без Ведьмака:
Геральт, камера, плоская земля, сущности-блоки, лучи, проба @addField.

Модель кадра: все exec из всех соединений идут в одну очередь «игрового потока». Раз в frame_time он берёт
до max_per_frame команд (0 = все, что есть), исполняет их и шлёт ответы. engineMs в 'pong' - время начала
кадра, по нему бенч считает «сколько exec за кадр».

Контракт (каждая строка вывода через Log: '[MCW#seq] ...', последняя - '[MCW#seq] end'; числа целые:
см, сотые градуса, направления x10000, мс): см. CONTRACT ниже. Семантика функций повторяет
mod/modMCW/content/scripts/local/mcw_spike.ws и mod/modMCWProbe/.../mcw_probe.ws (ссылки ws:<строка> ниже).

Пределы exec - как замерено на 5.00c (w3net.EXEC_MAX_CMD / EXEC_MAX_REPLY): команду длиннее 2047 байт UTF-8 фейк
молча выбрасывает (не исполняет, не отвечает, соединение живо); строку Log() длиннее 1999 символов в ответе
заменяет на '......(truncated).......'; ответ длиннее 1999 байт не шлёт вовсе, хотя функция отработала (строки
ушли в scriptslog). legacy_limits=True - старое поведение: только пределы протокола, ответ режется до i16.
Разбор аргументов - тоже как на 5.00c (parse_exec): кавычка внутри строки и пустая строка "" - 'Warn: Failed to process
command', обратная косая - обычный символ; StrLen в mcw_echo считает байты UTF-8; округление см - MCW_Round (_rnd).

Режимы, которые моделируют неизвестное о 5.0 (всё [проверить] на спайке):
  utf8_only=False      все строки в ответах UTF-16 (до 5.0 так шли rootpath/modlist/уведомления; здесь и exec)
  per_line             пакет на каждую строку Log() вместо одного пакета с '\\n'
  trailing_status      после строк ещё пакет 'Spam: Command executed without errors'
  leading_status       пакет 'Spam: ...' ДО строк (rw3d PR #12: exec «streams its script log output»)
  log_in_reply=False   Log() не попадает в ответ: ответ 'Spam: ...', строки только в scriptslog
  unsolicited_ms       игра сама шлёт пакеты (сетевой лог? строка 'no-netlog' в exe); kind utility|exec
  drop_every K         каждый K-й exec исполняется, но ответа нет (загрузка, потеря)
  freeze_every K / freeze_ms   перед K-м exec игровой поток замирает (экран загрузки): ответы опаздывают
  stall_ms / stall_after       после N-го exec соединение перестаёт читать сокет на stall_ms
  require_bind=False   отвечать и без BIND (rw3d говорит, что BIND обязателен; на игре не проверено)
  probe=False          modMCWProbe не скомпилировался: mcwp_* -> 'Warn: Failed to process command'
  legacy=True          мод до расширений для моста: mcw_countblocks, mcw_boomat и mcw_ents -> 'Warn: Failed to process
                       command', у mcw_look нет строки normal (так мост проверяет откат на старые функции)
  reload_error         перекомпиляция падает с ошибкой в mcw_spike.ws
  legacy_limits        без пределов exec 5.00c (см. выше): команда и ответ до i16

Запуск отдельно:  python fake_w3.py --port 37101 [--per-line] [--frame-ms 16.7] ...
"""
from __future__ import annotations

import argparse
import collections
import math
import random
import re
import socket
import struct
import threading
import time
from typing import Callable, Deque, Dict, List, Optional, Sequence, Tuple

import w3net
from w3net import (I8, I32, U8, U16, U32, Item, EXEC_FAIL, EXEC_MAGIC1, EXEC_MAGIC2, CONFIG_MAGIC, EXEC_OK_SILENT,
                   EXEC_MAX_CMD, EXEC_MAX_REPLY, EXEC_TRUNCATED, MAX_STR, encode_packet, PacketStream)

# fn(args) -> None (тихий успех) | строки Log() (в ответ и в файл) | (строки Log(), строки LogChannel - только в файл)
ExecHandler = Callable[[List[str]], object]

_CALL = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\((.*)\))?\s*;?\s*$", re.S)

CRATE = r"gameplay\containers\_container_definitions\_mesh_entities\cont_crate.w2ent"
BARREL = r"gameplay\templates\oil_barrel.w2ent"
BOX250 = r"gameplay\explorations_reference_entiti\box_250cm.w2ent"
# шаблон -> число CMeshComponent сразу после CreateEntity (это и есть 'comps' в ответах ws); числа условные
TEMPLATES = {CRATE: 1, BARREL: 3, BOX250: 5}
ALIASES = {"barrel": BARREL, "denied_area": BOX250}
# MCW_TemplateByIndex / MCW_BlockPath: 0 cont_crate, 1 barrel, 2..12 - блоки modMCWBlocks (mcw\blocks\<имя>.w2ent)
MCW_BLOCKS = ("grass_block", "dirt", "stone", "cobblestone", "oak_log", "oak_planks", "sand", "crafting_table",
              "oak_leaves", "glass", "_unknown")
BATCH_TEMPLATES = {0: CRATE, 1: BARREL, **{2 + i: f"mcw\\blocks\\{b}.w2ent" for i, b in enumerate(MCW_BLOCKS)}}
PAUSE_REASONS = ("IngameMenu", "menus", "Popup", "FastMenu", "tutorial", "dialogOrCutscene", "")   # mcw_state, по порядку
ROUND_PROBE = (-2.5, -1.5, -0.5, 0.5, 1.5, 2.5, -1.6, -1.4, 1.4, 1.6)                               # mcw_roundtest
HALF = 50                                  # полуразмер куба, см
_INT_RE = re.compile(r"-?\d+")             # MCW_ParseInt (ws:95): пусто и мусор - не число


def _rnd(v: float) -> int:
    """MCW_Round мода: FloorF(v + 0.5), половина - вверх. Не round() Python (банковское) и не RoundF игры: на 5.00c
    он отбрасывает дробь к нулю (mcw_roundtest 2026-10-03)."""
    return math.floor(v + 0.5)


def _template(path: str) -> str:
    """MCW_Template: "-" (и "", который парсер exec всё равно не пропустит) - шаблон по умолчанию cont_crate."""
    return CRATE if path in ("", "-") else path


def _f32(x: float) -> float:
    return struct.unpack("f", struct.pack("f", x))[0]


def round_trip_bad() -> Tuple[int, int]:
    """mcw_roundtest 'trip': сколько целых см x из [-20000, 20000] не переживают (float)x / 100 * 100 в float32 у RoundF
    (на 5.00c - отбрасывание дроби) и у FloorF(v + 0.5). На игре замерено 2286 и 0 - фейк считает так же."""
    bad_r = bad_f = 0
    for i in range(-20000, 20001):
        f = _f32(_f32(_f32(float(i)) / 100.0) * 100.0)
        bad_r += math.trunc(f) != i
        bad_f += math.floor(_f32(f + 0.5)) != i
    return bad_r, bad_f

# имя -> типы аргументов: i int, f float, n name, s string
CONTRACT = {
    "mcw_ping": "i", "mcw_echo": "is", "mcw_logtest": "i", "mcw_poll": "i", "mcw_cube": "is",
    "mcw_alias": "inf", "mcw_stress": "iiis", "mcw_clearall": "i", "mcw_count": "i", "mcw_batch": "is",
    "mcw_look": "i", "mcw_ground": "iii", "mcw_grounds": "is", "mcw_boom": "i", "mcw_notify": "is",
    "mcw_countblocks": "i", "mcw_boomat": "iiii", "mcw_ents": "is",
    "mcw_state": "i", "mcw_resume": "i", "mcw_roundtest": "i", "mcw_quit": "i",
    "mcwp_state": "i", "mcwp_put": "is", "mcwp_get": "is", "mcwp_del": "is",
    # mcw_steve.ws: проба «Стив вместо Геральта» (docs/STEVE.md) и вид от первого лица
    "mcws_cam": "iiiiiii", "mcws_puppet": "ii", "mcws_ptp": "iiiii", "mcws_hide": "ii", "mcws_hit": "ii",
    "mcws_god": "ii", "mcws_scam": "iiiiiiis", "mcwf_on": "iiii", "mcwf_eye": "iiiiiii", "mcwf_state": "i",
    "mcwf_block": "ii", "mcwf_mc": "iiiiiiii", "mcwf_fov": "ii",
    "mcw_fadetest": "iii", "mcw_fadeshow": "ii", "mcwf_freeze": "ii", "mcwf_interact": "i",
    # mcw_collide.ws: коллизия Ведьмака для физики клиента MC
    "mcw_cols": "iis",
    # пробы mcw_collide.ws (лучи, навмеш, кости): фейк их не изображает — ответ как у мода без них
    "mcwx_ray": "iiiiiiii", "mcwx_scan": "iiiiii", "mcwx_nav": "iiiii", "mcwx_navline": "iiiiiiii", "mcwx_bones": "i",
    "mcwx_slice": "iiiiiiiiii",
    # mcw_models.ws: тело Стива на костях Геральта и лошадь MC (модели modMCWModels); проба матриц
    "mcwm_skin": "ii", "mcwm_horse": "iiiiiiiiii", "mcwx_mat": "iiii", "mcwx_skinpos": "i", "mcwx_skinvis": "ii", "mcwx_inside": "iiiiii", "mcwx_covered": "iiiiii", "mcwx_interact": "ii", "mcwx_push": "iiii",
}
# Добавлены в mcw_spike.ws для моста; legacy=True изображает мод без них
BRIDGE_EXT = {"mcw_countblocks", "mcw_boomat", "mcw_ents"}
ENT_SLOTS = 64                             # MCW_EntTag: теги слотов 'MCW_E0'..'MCW_E63'


def parse_exec(cmd: str):
    """'name(a, "b c", 3)' -> ('name', ['a', 'b c', '3']); голое 'name' -> ('name', []); аргументы не разобрались ->
    ('name', None): игра ответит 'Warn: Failed to process command'.
    Как парсер exec 5.00c (charset-замер 2026-10-03): обратная косая - обычный символ (не экранирует), кавычка
    внутри строки (любого вида), пустая строка "" и незакрытая кавычка ломают разбор всей команды; " " проходит.
    Пробелы вне кавычек не значимы, внутри - сохраняются; аргумент без кавычек разбирается нестрого."""
    m = _CALL.match(cmd)
    if not m:
        return None, []
    name, argstr = m.group(1), (m.group(2) or "")
    args, cur, quote, tok, closed = [], [], None, False, False
    for ch in argstr:
        if quote:
            if ch == quote:
                if not cur:
                    return name, None              # "" - пустая строка
                quote, closed = None, True
            elif ch in "\"'":
                return name, None                  # кавычка внутри строки
            else:
                cur.append(ch)
        elif ch == ",":
            args.append("".join(cur))
            cur, tok, closed = [], False, False
        elif ch.isspace():
            continue
        elif closed or (tok and ch in "\"'"):
            return name, None                      # что-то после закрытой кавычки или кавычка посреди слова
        elif ch in "\"'":
            quote, tok = ch, True
        else:
            cur.append(ch)
            tok = True
    if quote:
        return name, None                          # незакрытая кавычка
    if tok or args:
        args.append("".join(cur))
    return name, args


def _typed(sig: str, args: List[str]):
    """Аргументы по сигнатуре; None - не разобрались (игра ответит 'Failed to process command')."""
    if len(args) != len(sig):
        return None
    out = []
    try:
        for t, a in zip(sig, args):
            out.append(int(a) if t == "i" else float(a) if t == "f" else a)
    except ValueError:
        return None
    return out


class Ent:
    __slots__ = ("id", "template", "x", "y", "z", "tags", "scale", "on_fire", "yaw", "moves")

    def __init__(self, eid: int, template: str, x: int, y: int, z: int, tags: Sequence[str] = ()) -> None:
        self.id, self.template, self.x, self.y, self.z = eid, template, x, y, z
        self.tags = set(tags)
        self.scale = 1.0
        self.on_fire = False
        self.yaw = 0                           # курс, сотые градуса (mcw_ents)
        self.moves = 0                         # сколько раз TeleportWithRotation (mcw_ents move)


class FakeWorld:
    """Мир-заглушка. Координаты в см, Z вверх, курс в сотых градуса (как в контракте)."""

    def __init__(self, seed: int = 1) -> None:
        self.rng = random.Random(seed)
        self.player = [1234500, -678900, 1500]
        self.yaw = 9000
        self.pitch_deg = 15.0                 # камера смотрит чуть вниз
        self.area = "AN_Velen"
        self.world = r"levels\novigrad\novigrad.w2w"
        self.ents: Dict[int, Ent] = {}
        self.notifications: List[str] = []
        self.statics: Dict[Tuple[int, int], str] = {}   # mcw_cols: блок MC -> «d s0/.../s15» (дом, стена, дверь)
        self.skin, self.skin_parts = False, 20               # mcwm_skin: надето ли тело Стива, частей в моде
        self.horses: Dict[int, tuple] = {}                   # mcwm_horse: слот -> последняя поза
        self.has_player = True                # False = главное меню: thePlayer == NULL
        # mcw_state / mcw_resume: окно в фокусе, пауза, меню (движок открывает его при потере фокуса)
        self.active, self.paused, self.apause, self.stopped, self.menu = True, False, False, False, False
        self.pause_reasons: List[str] = []    # из PAUSE_REASONS
        self.game_time_s = 12 * 3600          # GameTimeToSeconds(GetGameTime())
        self._eid = 0
        self.spawn_us = 20.0                  # цена спавна одной сущности, мкс (модель)
        self.respawn_player()

    # -- игрок и камера
    def respawn_player(self) -> None:
        """Как загрузка сейва: новый CR4Player, поля @addField пустые, OnSpawned выдаёт новый токен."""
        self.token = self.rng.randrange(1, 1 << 30)
        self.slots: List[str] = []
        self.index: Dict[str, str] = {}       # mcw_probe.ws кладёт в map сам thePlayer

    def fwd(self) -> Tuple[float, float]:
        h = math.radians(self.yaw / 100.0)
        return -math.sin(h), math.cos(h)          # VecFromHeading: курс 0 -> +Y

    def ahead(self, dist_cm: float) -> Tuple[int, int, int]:
        fx, fy = self.fwd()
        x, y, z = self.player
        return _rnd(x + fx * dist_cm), _rnd(y + fy * dist_cm), self.ground(x, y)

    def ground(self, x: float, y: float) -> int:
        return self.player[2]                     # плоская земля на уровне ног

    def cam(self) -> Tuple[Tuple[int, int, int], Tuple[int, int, int]]:
        fx, fy = self.fwd()
        x, y, z = self.player
        p = math.radians(self.pitch_deg)
        pos = (_rnd(x - fx * 300), _rnd(y - fy * 300), z + 180)
        d = (_rnd(fx * math.cos(p) * 10000), _rnd(fy * math.cos(p) * 10000), _rnd(-math.sin(p) * 10000))
        return pos, d

    # -- сущности
    def spawn(self, template: str, x: int, y: int, z: int, tags: Sequence[str] = ("MCW_BLOCK",)) -> Ent:
        self._eid += 1
        e = Ent(self._eid, template, x, y, z, tags)
        self.ents[e.id] = e
        return e

    def destroy(self, eid: int) -> bool:
        return self.ents.pop(eid, None) is not None

    def count(self) -> int:
        return sum(1 for e in self.ents.values() if "MCW_BLOCK" in e.tags)

    def clearall(self) -> int:
        ids = [e.id for e in self.ents.values() if "MCW_BLOCK" in e.tags]
        for i in ids:
            del self.ents[i]
        return len(ids)

    def ray(self, max_cm: float = 1000.0):
        hit = self.ray_hit(max_cm)
        return None if hit is None else hit[:4]

    def ray_hit(self, max_cm: float = 1000.0):
        """Луч камеры -> (x, y, z, isBlock, (nx, ny, nz)) в см и x10000 или None. Нормаль куба — его грань, через
        которую луч вошёл (ось наибольшего смещения точки от центра), у земли — (0, 0, 10000)."""
        (cx, cy, cz), (dx, dy, dz) = self.cam()
        n = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
        dx, dy, dz = dx / n, dy / n, dz / n
        best, is_block, hit_ent = None, 0, None
        for e in self.ents.values():
            t = _slab((cx, cy, cz), (dx, dy, dz), (e.x - HALF, e.y - HALF, e.z - HALF), (e.x + HALF, e.y + HALF, e.z + HALF))
            if t is not None and t <= max_cm and (best is None or t < best):
                # isBlock — HasTag('MCW_BLOCK') у сущности компонента (ws mcw_look): моб моста (MCW_ENT) — не блок
                best, is_block, hit_ent = t, int("MCW_BLOCK" in e.tags), e
        if dz < 0:
            t = (self.ground(cx, cy) - cz) / dz
            if 0 <= t <= max_cm and (best is None or t < best):
                best, is_block, hit_ent = t, 0, None
        if best is None:
            return None
        p = (cx + dx * best, cy + dy * best, cz + dz * best)
        normal = (0, 0, 10000)
        if hit_ent is not None:
            off = (p[0] - hit_ent.x, p[1] - hit_ent.y, p[2] - hit_ent.z)
            a = max(range(3), key=lambda k: abs(off[k]))
            normal = tuple((10000 if off[a] >= 0 else -10000) if k == a else 0 for k in range(3))
        return _rnd(p[0]), _rnd(p[1]), _rnd(p[2]), is_block, normal

    def spawn_cost(self, n: int) -> None:
        if n > 0 and self.spawn_us > 0:
            time.sleep(n * self.spawn_us / 1e6)


def _slab(o, d, lo, hi) -> Optional[float]:
    tmin, tmax = 0.0, math.inf
    for k in range(3):
        if abs(d[k]) < 1e-12:
            if o[k] < lo[k] or o[k] > hi[k]:
                return None
            continue
        t1, t2 = (lo[k] - o[k]) / d[k], (hi[k] - o[k]) / d[k]
        if t1 > t2:
            t1, t2 = t2, t1
        tmin, tmax = max(tmin, t1), min(tmax, t2)
        if tmin > tmax:
            return None
    return tmin


class _Conn:
    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.lock = threading.Lock()
        self.bound: set = set()
        self.alive = True
        self.execs_in = 0
        self.execs_done = 0
        self.stalled = False


class FakeWitcher:
    def __init__(self, host: str = "127.0.0.1", port: int = 0, *, utf8_only: bool = True,
                 require_bind: bool = True, frame_time: float = 0.0, max_per_frame: int = 0,
                 per_line: bool = False, trailing_status: bool = False, leading_status: bool = False,
                 log_in_reply: bool = True,
                 chunk_size: Optional[int] = None, chunk_delay: float = 0.0, coalesce: bool = False,
                 scriptslog_path: Optional[str] = None, drop_every: int = 0, freeze_every: int = 0,
                 freeze_ms: float = 0.0, stall_ms: float = 0.0, stall_after: int = 1,
                 unsolicited_ms: float = 0.0, unsolicited_kind: str = "utility",
                 drop_after_execs: Optional[int] = None, probe: bool = True, reload_error: bool = False,
                 spawn_us: float = 20.0, verbose: bool = False, legacy: bool = False,
                 legacy_limits: bool = False) -> None:
        self.host = host
        self.utf8_only = utf8_only
        self.require_bind = require_bind
        self.frame_time = frame_time
        self.max_per_frame = max_per_frame
        self.per_line = per_line
        self.trailing_status = trailing_status
        self.leading_status = leading_status
        self.log_in_reply = log_in_reply
        self.chunk_size = chunk_size
        self.chunk_delay = chunk_delay
        self.coalesce = coalesce
        self.scriptslog_path = scriptslog_path
        self.drop_every = drop_every
        self.freeze_every = freeze_every
        self.freeze_ms = freeze_ms
        self.stall_ms = stall_ms
        self.stall_after = stall_after
        self.unsolicited_ms = unsolicited_ms
        self.unsolicited_kind = unsolicited_kind
        self.drop_after_execs = drop_after_execs
        self.probe = probe
        self.legacy = legacy
        self.legacy_limits = legacy_limits
        self.max_cmd = MAX_STR if legacy_limits else EXEC_MAX_CMD        # байт UTF-8: длиннее - выбросить молча
        self.max_reply = MAX_STR if legacy_limits else EXEC_MAX_REPLY    # строка (символы) и ответ (байт UTF-8)
        self.reload_error = reload_error
        self.verbose = verbose
        self.world = FakeWorld()
        self.world.spawn_us = spawn_us
        # ванильные exec. logstats() (temp.ws:6184) пишет сотни строк LogChannel('Stats') - в фейке они идут только
        # в scriptslog; попадает ли LogChannel в ответ на игре, неизвестно (тогда ответ длиннее 1999 байт потерялся бы)
        self.handlers: Dict[str, ExecHandler] = {
            "logstats": lambda a: ([], [f"[Stats] stat line {i}" for i in range(300)]),
            "additem": lambda a: None, "spawn": lambda a: None, "spawnt": lambda a: None,
            "spawnbarrel": lambda a: None,
        }
        self.config_vars = [("Visuals", "GammaValue", "1", 2), ("Visuals", "AllowClothSimulationOnGpu", "false", 1),
                            ("Visuals", "HdrGamma1", "1.1", 3), ("Scripts", "LogDebugScripts", "true", 1)]
        self.execs_seen: List[str] = []
        self.bound_seen: List[str] = []
        self.frames: List[Tuple[int, int]] = []          # (engineMs, execs в кадре)
        self.n_exec = 0
        self.dropped = 0
        self.too_long_cmds: List[str] = []               # команды длиннее max_cmd: не исполнены, ответа нет
        self.replies_lost = 0                            # ответы длиннее max_reply: исполнены, ответ не ушёл
        self.lines_truncated = 0                         # строки Log() длиннее max_reply -> EXEC_TRUNCATED
        self.quit_requested = False                      # был mcw_quit (игра бы вышла)
        # mcw_steve.ws: вид от первого лица (взгляд — градусы ×100) и камера/кукла пробы
        self.fp = {"on": False, "src": 0, "yaw": 0, "pitch": 0, "ticks": 0, "eye": (0, 0, 0), "block": 0, "eyeh": 0,
                   "fov": 6000, "mc_calls": 0, "freeze": False, "susp": False, "interact": False}
        self.steve = {"god": False, "hits": 0, "hidden": False, "puppet": False, "scam": False}
        self._trip: Optional[Tuple[int, int]] = None     # mcw_roundtest 'trip', считается один раз
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if stall_ms:
            self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8192)   # окно мало: отправитель упрётся
        self._listener.bind((host, port))
        self._listener.listen(8)
        self._listener.settimeout(0.2)
        self.port = self._listener.getsockname()[1]
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self._conns: List[_Conn] = []
        self._log_lock = threading.Lock()
        self._jobs: Deque[Tuple[_Conn, str]] = collections.deque()
        self._jobs_cv = threading.Condition()
        self.t_start = time.perf_counter()

    # -- жизненный цикл
    def start(self) -> "FakeWitcher":
        for target, name in ((self._accept_loop, "fake-w3-accept"), (self._game_loop, "fake-w3-game")):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)
        return self

    def stop(self) -> None:
        self._stop.set()
        with self._jobs_cv:
            self._jobs_cv.notify_all()
        for c in list(self._conns):
            self._close_conn(c)
        self._listener.close()
        for t in self._threads:
            t.join(timeout=2)

    def __enter__(self) -> "FakeWitcher":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    def add_exec(self, name: str, fn: ExecHandler) -> None:
        """Своя «ванильная» функция: fn(args) -> строки Log() (как есть, без маркеров), None (тихий успех)
        или (строки Log(), строки LogChannel только для scriptslog)."""
        self.handlers[name] = fn

    # -- сеть
    def _s(self, text: str) -> Item:
        return U8(text) if self.utf8_only else U16(text)

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                sock, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            c = _Conn(sock)
            self._conns.append(c)
            for target in [self._serve] + ([self._unsolicited_loop] if self.unsolicited_ms else []):
                t = threading.Thread(target=target, args=(c,), name="fake-w3-conn", daemon=True)
                t.start()
                self._threads.append(t)

    def _close_conn(self, c: _Conn) -> None:
        c.alive = False
        try:
            c.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            c.sock.close()
        except OSError:
            pass

    def _send(self, c: _Conn, data: bytes) -> None:
        if not data or not c.alive:
            return
        try:
            with c.lock:
                if self.chunk_size:
                    for i in range(0, len(data), self.chunk_size):
                        c.sock.sendall(data[i:i + self.chunk_size])
                        if self.chunk_delay:
                            time.sleep(self.chunk_delay)
                else:
                    c.sock.sendall(data)
        except OSError:
            c.alive = False

    def _ok(self, c: _Conn, ns: str) -> bool:
        return (not self.require_bind) or ns in c.bound

    def _serve(self, c: _Conn) -> None:
        stream = PacketStream()
        c.sock.settimeout(0.2)
        try:
            while not self._stop.is_set() and c.alive:
                try:
                    data = c.sock.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    break
                for items in stream.feed(data):
                    self._handle(c, items)
                    if self.stall_ms and c.execs_in == self.stall_after and not c.stalled:
                        c.stalled = True
                        time.sleep(self.stall_ms / 1000.0)        # кадр/загрузка: сокет не читается
        except OSError:
            pass
        finally:
            c.alive = False
            try:
                c.sock.close()
            except OSError:
                pass

    def _unsolicited_loop(self, c: _Conn) -> None:
        k = 0
        while not self._stop.is_set() and c.alive:
            time.sleep(self.unsolicited_ms / 1000.0)
            k += 1
            text = f"netlog tick {k}"
            if self.unsolicited_kind == "exec":
                pkt = encode_packet([I32(EXEC_MAGIC1), I32(EXEC_MAGIC2), self._s(text)])
            else:
                pkt = encode_packet([U8(w3net.NS_UTILITY), U8("log"), self._s(text)])
            self._send(c, pkt)

    def _handle(self, c: _Conn, items: List[Item]) -> None:
        t = [i.text for i in items]
        if self.verbose:
            print("fake_w3 <-", [i.value for i in items])
        if len(items) == 2 and t[0] == "BIND" and t[1]:
            c.bound.add(t[1])
            self.bound_seen.append(t[1])
            return
        if (len(items) >= 4 and t[0] == w3net.NS_REMOTE and items[1] == I32(EXEC_MAGIC1)
                and items[2] == I32(EXEC_MAGIC2) and t[3] is not None):
            c.execs_in += 1
            self.execs_seen.append(t[3])
            if len(t[3].encode("utf-8")) > self.max_cmd:
                self.too_long_cmds.append(t[3])      # 5.00c: длиннее 2047 байт - ни исполнения, ни ответа
                return
            with self._jobs_cv:
                self._jobs.append((c, t[3]))
                self._jobs_cv.notify()
            return
        for pkt in self._other(c, items, t):
            self._send(c, pkt)

    def _other(self, c: _Conn, items: List[Item], t: List[Optional[str]]) -> List[bytes]:
        ns, sub = (t[0] if t else None), (t[1] if len(t) > 1 else None)
        if ns == w3net.NS_SCRIPTS and sub == "reload":
            if not self._ok(c, w3net.NS_SCRIPT_COMPILER):
                return []
            sc = U8(w3net.NS_SCRIPT_COMPILER)
            out = [encode_packet([sc, U8("started"), I8(0), I8(1)]),
                   encode_packet([sc, U8("log"), self._s("Compiling mcw_spike.ws")])]
            if self.reload_error:
                out += [encode_packet([sc, U8("error"), U32(42), self._s(r"local\mcw_spike.ws"),
                                       self._s("Unexpected token 'map'")]),
                        encode_packet([sc, U8("finished"), I8(1)])]
            else:
                out += [encode_packet([sc, U8("log"), self._s("Compiling mcw_probe.ws")]),
                        encode_packet([sc, U8("warn"), U32(12), self._s(r"local\mcw_probe.ws"),
                                       self._s("Variable declared, but unused")]),
                        encode_packet([sc, U8("finished"), I8(0)])]
            return out
        if ns == w3net.NS_SCRIPT_COMPILER and sub == "RootPath":
            if not self._ok(c, ns):
                return []
            return [encode_packet([U8(ns), U8("RootPathConfirm"),
                                   self._s(r"C:\Program Files (x86)\Steam\steamapps\common\The Witcher 3\content\content0\scripts")])]
        if ns == w3net.NS_SCRIPTS and sub == "pkgSync":
            if not self._ok(c, ns):
                return []
            pk = [("content0", r"C:\W3\content\content0\scripts"), ("modMCW", r"C:\W3\mods\modMCW\content\scripts")]
            payload = [U8(ns), U8("pkgSyncListing"), I32(len(pk))]
            for name, path in pk:
                payload += [self._s(name), self._s(path)]
            return [encode_packet(payload)]
        if ns == w3net.NS_SCRIPT_DEBUGGER and sub == "OpcodeBreakdownRequest":
            if not self._ok(c, ns):
                return []
            return [encode_packet([U8(ns), U8("OpcodeBreakdownResponse"), I32(1), self._s(""), I32(1),
                                   I32(0), self._s("00000152F45B8361 (   1): Return\n00000152F45B8362 (   2): Nop")])]
        if ns == w3net.NS_CONFIG and len(items) >= 3 and items[1] == I32(CONFIG_MAGIC):
            if t[2] == "list":
                if not self._ok(c, ns):
                    return []
                sec_f = t[3] if len(t) > 3 else ""
                name_f = t[4] if len(t) > 4 else ""
                payload: List[Item] = [I32(CONFIG_MAGIC), U8("vars")]
                for sec, name, val, dtype in self.config_vars:
                    if (not sec_f or sec_f == sec) and (not name_f or name_f.lower() in name.lower()):
                        payload += [I8(dtype), I8(0), U8(name), U8(sec), U8(val)]
                payload.append(I8(0))
                return [encode_packet(payload)]
            if t[2] == "set" and len(items) >= 6:
                self.config_vars = [(s, n, (t[5] if (s, n) == (t[3], t[4]) else v), d)
                                    for s, n, v, d in self.config_vars]
        return []

    # -- игровой поток
    def _game_loop(self) -> None:
        next_frame = time.perf_counter()
        while not self._stop.is_set():
            if self.frame_time:
                now = time.perf_counter()
                if now < next_frame:
                    time.sleep(next_frame - now)
                frame_start = time.perf_counter()
                next_frame = max(next_frame + self.frame_time, frame_start)
                with self._jobs_cv:
                    batch = self._take()
            else:
                with self._jobs_cv:
                    while not self._jobs and not self._stop.is_set():
                        self._jobs_cv.wait(0.1)
                    batch = self._take()
                frame_start = time.perf_counter()
            if not batch:
                continue
            engine_ms = int((frame_start - self.t_start) * 1000)
            self.frames.append((engine_ms, len(batch)))
            out: Dict[_Conn, List[bytes]] = collections.defaultdict(list)
            for c, cmd in batch:
                for pkt in self._exec(c, cmd, engine_ms):
                    if self.coalesce:
                        out[c].append(pkt)
                    else:
                        self._send(c, pkt)
                c.execs_done += 1
                if self.drop_after_execs is not None and c.execs_done >= self.drop_after_execs:
                    for pkt in out.pop(c, []):
                        self._send(c, pkt)
                    self._close_conn(c)
            for c, pkts in out.items():
                self._send(c, b"".join(pkts))

    def _take(self) -> List[Tuple[_Conn, str]]:
        n = len(self._jobs) if self.max_per_frame <= 0 else min(self.max_per_frame, len(self._jobs))
        return [self._jobs.popleft() for _ in range(n)]

    def _exec(self, c: _Conn, cmd: str, engine_ms: int) -> List[bytes]:
        self.n_exec += 1
        k = self.n_exec
        if self.freeze_every and k % self.freeze_every == 0:
            time.sleep(self.freeze_ms / 1000.0)
        name, args = parse_exec(cmd)
        name = name or ""
        status: Optional[str] = None
        log_lines: List[str] = []              # то, что пишет Log() (в ответ и в файл)
        file_lines: List[str] = []             # всё, что попадает в scriptslog, в порядке вызовов
        if args is None:                       # кавычка внутри строки, "" - не разобралась вся команда
            status = EXEC_FAIL
        elif (name in CONTRACT and (self.probe or not name.startswith("mcwp_"))
                and not (self.legacy and name in BRIDGE_EXT)):
            typed = _typed(CONTRACT[name], args)
            if typed is None:
                status = EXEC_FAIL
            else:
                seq = typed[0]
                try:
                    body, chan = getattr(self, "_x_" + name)(engine_ms, *typed[1:])
                except Exception:
                    status = EXEC_FAIL
                else:
                    # порядок как в mcw_logtest: Log(...), LogChannel('MCW', ...), Log(end)
                    end = f"[MCW#{seq}] end"
                    log_lines = [f"[MCW#{seq}] {b}" for b in body] + [end]
                    file_lines = log_lines[:-1] + [f"[MCW] [MCW#{seq}] {x}" for x in chan] + [end]
        elif name in self.handlers:
            try:
                res = self.handlers[name](args)
            except Exception:
                status = EXEC_FAIL
            else:
                chan: List[str] = []
                if isinstance(res, tuple):
                    res, chan = res
                log_lines = list(res or [])
                file_lines = log_lines + list(chan)
        else:
            status = EXEC_FAIL
        self._log_to_file(file_lines)
        if self.drop_every and k % self.drop_every == 0:
            self.dropped += 1
            return []
        if not self._ok(c, w3net.NS_REMOTE):
            return []
        streamed = status is None and log_lines and self.log_in_reply
        reply_lines = [self._reply_line(ln) for ln in log_lines] if streamed else []
        if status is not None:
            texts = [status]
        elif not streamed:
            texts = [EXEC_OK_SILENT]
        elif self.per_line:
            texts = reply_lines
        else:
            texts = ["\n".join(reply_lines)]
        if streamed and self.leading_status:
            texts.insert(0, EXEC_OK_SILENT)
        if streamed and self.trailing_status:
            texts.append(EXEC_OK_SILENT)
        out = []
        for tx in texts:
            if self.legacy_limits:
                tx = _fit(tx)
            elif len(tx.encode("utf-8")) > self.max_reply:
                self.replies_lost += 1                 # 5.00c: такой ответ теряется целиком и молча
                continue
            out.append(encode_packet([I32(EXEC_MAGIC1), I32(EXEC_MAGIC2), self._s(tx)]))
        return out

    def _reply_line(self, line: str) -> str:
        """5.00c: строка Log() длиннее ~1999 символов приходит в ответе как EXEC_TRUNCATED (в scriptslog - целиком)."""
        if self.legacy_limits or len(line) <= self.max_reply:
            return line
        self.lines_truncated += 1
        return EXEC_TRUNCATED

    def _log_to_file(self, lines: Sequence[str]) -> None:
        if not self.scriptslog_path or not lines:
            return
        with self._log_lock, open(self.scriptslog_path, "a", encoding="utf-8", newline="") as f:
            for ln in lines:
                f.write(ln + "\r\n")

    # -- КОНТРАКТ modMCW: каждая функция -> (строки Log без маркера, строки LogChannel)
    def _x_mcw_ping(self, ms):
        return [f"pong {ms}"], []

    def _x_mcw_echo(self, ms, s):
        return [f"echo {len(s.encode('utf-8'))} {s}"], []               # StrLen на 5.00c считает байты UTF-8

    def _x_mcw_logtest(self, ms):
        return ["via Log"], ["via LogChannel"]

    def _x_mcw_poll(self, ms):
        w = self.world
        (cx, cy, cz), (dx, dy, dz) = w.cam()
        x, y, z = w.player
        out = [f"pos {x} {y} {z} {w.yaw}"] if w.has_player else []
        return out + [f"cam {cx} {cy} {cz} {dx} {dy} {dz}", f"area {w.area}", f"world {w.world}"], []

    @staticmethod
    def _known(template: Optional[str]) -> bool:
        """LoadResource вернёт шаблон: известный путь/алиас или любой *.w2ent (фейк не знает депо)."""
        return template is not None and (template in TEMPLATES or template.endswith(".w2ent"))

    def _spawn_one(self, template: Optional[str], dist: float, dz: int, test: bool = True):
        """Спавн перед Геральтом на опоре + dz см. Без thePlayer (главное меню) - ничего (ws:317, 346, 637)."""
        w = self.world
        if not w.has_player or not self._known(template):
            return None, 0
        x, y, gz = w.ahead(dist)
        w.spawn_cost(1)
        e = w.spawn(template, x, y, gz + dz, ("MCW_BLOCK", "MCW_TEST") if test else ("MCW_BLOCK",))  # type: ignore[arg-type]
        return e, TEMPLATES.get(template, 1)                                                    # type: ignore[arg-type]

    def _x_mcw_cube(self, ms, path):
        e, comps = self._spawn_one(_template(path), 200, HALF)       # ws:319: опора + 0.5 м
        return [f"spawned {1 if e else 0} comps {comps}"], []

    def _x_mcw_alias(self, ms, alias, scale):
        e, comps = self._spawn_one(ALIASES.get(alias), 200, 0)          # ws:348: на опоре, без +0.5 м
        scaled = 0
        if e and scale > 0:                                             # ws:354-363: масштаб каждому CMeshComponent
            e.scale = scale
            scaled = comps
        return [f"spawned {1 if e else 0} comps {comps} scaled {scaled}"], []

    def _x_mcw_stress(self, ms, n, spacing, path):
        w = self.world
        template = _template(path)
        t0 = time.perf_counter()
        spawned = 0
        if w.has_player and n > 0 and self._known(template):          # ws:385
            sp = spacing if spacing > 0 else 100                        # ws:388-390
            side = max(1, math.ceil(math.sqrt(n)))
            fx, fy = w.fwd()
            rx, ry = fy, -fx
            px, py, pz = w.player
            w.spawn_cost(n)
            for i in range(n):
                row, col = divmod(i, side)
                d, s = 300 + row * sp, (col - side / 2) * sp
                w.spawn(template, _rnd(px + fx * d + rx * s), _rnd(py + fy * d + ry * s), w.ground(px, py) + HALF)
                spawned += 1
        return [f"stress {spawned} {int((time.perf_counter() - t0) * 1000)}"], []

    def _x_mcw_clearall(self, ms):
        return [f"cleared {self.world.clearall()}"], []

    def _x_mcw_count(self, ms):
        return [f"count {self.world.count()}"], []

    def _x_mcw_countblocks(self, ms):
        """ws mcw_countblocks: MCW_BLOCK минус MCW_TEST (оба списка целиком, как GetEntitiesByTag)."""
        tests = sum(1 for e in self.world.ents.values() if "MCW_TEST" in e.tags)
        return [f"countblocks {self.world.count() - tests} {tests}"], []

    def _x_mcw_batch(self, ms, cmds):
        """ws:427-513. Разбор всего пакета, затем ВСЕ clear, затем ВСЕ set. clear ищет среди всех MCW_BLOCK по ключу
        позиции 'x:y:z' (целые см) и уничтожает каждое совпадение; ключ без совпадений - ошибка, повтор того же
        ключа тоже (FindFirst отдаёт первый). set занятость не проверяет. Пустые элементы пропускаются как есть."""
        w = self.world
        t0 = time.perf_counter()
        n_set = n_clear = n_err = 0
        sets: List[Tuple[int, int, int, int]] = []
        clr_keys: List[str] = []
        for op in cmds.split("|"):
            if op == "":
                continue
            f = op.split(":")
            nums = f[1:]
            if f[0] == "set" and len(f) == 5 and all(_INT_RE.fullmatch(v) for v in nums):
                x, y, z, t = (int(v) for v in nums)
                if t in BATCH_TEMPLATES:
                    sets.append((x, y, z, t))
                    continue
            elif f[0] == "clear" and len(f) == 4 and all(_INT_RE.fullmatch(v) for v in nums):
                clr_keys.append(":".join(str(int(v)) for v in nums))
                continue
            n_err += 1
        if clr_keys:
            hits = [0] * len(clr_keys)
            for e in [e for e in w.ents.values() if "MCW_BLOCK" in e.tags]:
                key = f"{e.x}:{e.y}:{e.z}"
                if key in clr_keys:
                    w.destroy(e.id)
                    hits[clr_keys.index(key)] += 1
                    n_clear += 1
            n_err += hits.count(0)
        for x, y, z, t in sets:
            w.spawn(BATCH_TEMPLATES[t], x, y, z)
            n_set += 1
        w.spawn_cost(n_set)
        return [f"batch {n_set} {n_clear} {n_err} {int((time.perf_counter() - t0) * 1000)}"], []

    def _x_mcw_look(self, ms):
        hit = self.world.ray_hit()
        if hit is None:
            return ["look 0 0 0 0 0"], []                               # без normal, как ws при hit 0
        x, y, z, is_block, (nx, ny, nz) = hit
        out = [f"look 1 {x} {y} {z} {is_block}"]
        if not self.legacy:
            out.append(f"normal {nx} {ny} {nz}")
        return out, []

    def _x_mcw_ground(self, ms, x, y):
        return [f"ground 1 {self.world.ground(x, y)}"], []

    def _x_mcw_grounds(self, ms, cmds):
        """ws mcw_grounds: пакет mcw_ground «x:y|...» (до 64), ответ по 16 на строку «gs ok:z ...», затем «gms»."""
        vals = []
        for part in cmds.split("|")[:64]:
            f = part.split(":")
            try:
                vals.append(f"1:{self.world.ground(int(f[0]), int(f[1]))}" if len(f) == 2 else "0:0")
            except ValueError:
                vals.append("0:0")
        return ["gs " + " ".join(vals[k:k + 16]) for k in range(0, len(vals), 16)] + ["gms 0"], []

    def _x_mcwm_skin(self, ms, on):
        """ws mcwm_skin: идемпотентно; без шаблонов modMCWModels (world.skin_parts = 0) — «skin 0 0»."""
        w = self.world
        if not w.has_player:
            return ["skin -1 0"], []
        w.skin = bool(on) and w.skin_parts > 0
        return [f"skin {1 if w.skin else 0} {w.skin_parts if w.skin else 0}"], []

    def _x_mcwm_horse(self, ms, slot, x, y, z, yaw, head, walk, speed, flags):
        """ws mcwm_horse: слоты 0..3; поза последнего вызова — world.horses[slot]."""
        if not self.world.has_player or not 0 <= slot <= 3:
            return [f"horse {slot} 0"], []
        self.world.horses[slot] = (x, y, z, yaw, head, walk, speed, flags)
        return [f"horse {slot} 1"], []

    def _x_mcw_cols(self, ms, zc, cmds):
        """ws mcw_cols: на блок — «t bx bz h» (земля, см от zc) или заготовка из world.statics[(bx, bz)] — строка
        подколонн «d s0/.../s15» (см от zc), как у игры. Набрано больше ~1300 байт — дальше «stop k»."""
        out, total = [], 40
        for k, part in enumerate(cmds.split("|")):
            f = part.split(":")
            if len(f) != 2:
                continue
            if total > 1300:
                out.append(f"stop {k}")
                break
            bx, bz = int(f[0]), int(f[1])
            h = self.world.ground(bx * 100 + 50, 50 - bz * 100) - zc
            st = self.world.statics.get((bx, bz))
            line = f"t {bx} {bz} {h}" if st is None else f"c {bx} {bz} {h} {st}"
            total += len(line) + 18
            out.append(line)
        out.append("cms 0")
        return out, []

    def _x_mcw_boom(self, ms):
        e, _ = self._spawn_one(BARREL, 400, 0)                         # ws:637-647: на опоре, SetOnFire
        if e:
            e.on_fire = True
        return [f"boom {1 if e else 0}"], []

    def _x_mcw_boomat(self, ms, x, y, z):
        """ws mcw_boomat: бочка в точке, опора лучом от +100 до -500 см (MCW_SnapDown; в фейке опора — плоская земля
        под Геральтом), нет опоры — в самой точке. Без thePlayer — boom 0."""
        w = self.world
        if not w.has_player:
            return ["boom 0"], []
        g = w.ground(x, y)
        e = w.spawn(BARREL, x, y, g if z - 500 <= g <= z + 100 else z, ("MCW_BLOCK", "MCW_TEST"))
        w.spawn_cost(1)
        e.on_fire = True
        return ["boom 1"], []

    def _x_mcw_notify(self, ms, s):
        self.world.notifications.append(s)
        return ["notified"], []

    def _x_mcw_state(self, ms):
        """ws mcw_state: флаги игры (MCW_B: 1/0) и причины паузы из списка PAUSE_REASONS по порядку."""
        w = self.world
        b = lambda v: "1" if v else "0"                                 # noqa: E731
        reasons = "".join(f" '{r}'" for r in PAUSE_REASONS if r in w.pause_reasons)
        return [f"state active {b(w.active)} paused {b(w.paused)} apause {b(w.apause)} stopped {b(w.stopped)} "
                f"menu {b(w.menu)} gtime {int(w.game_time_s)} ems {ms}", "reasons" + reasons], []

    def _x_mcw_resume(self, ms):
        """ws mcw_resume: открыто меню - CloseMenu (closed 1); в фейке это снимает и паузу меню."""
        w = self.world
        closed = int(w.menu)
        if w.menu:
            w.menu = False
            w.pause_reasons = [r for r in w.pause_reasons if r not in ("IngameMenu", "menus")]
            w.paused = bool(w.pause_reasons)
        return [f"resume {closed} paused {int(w.paused)}"], []

    def _x_mcw_roundtest(self, ms):
        """ws mcw_roundtest: RoundF 5.00c отбрасывает дробь к нулю; 'trip' - как в игре (2286 0)."""
        if self._trip is None:
            self._trip = round_trip_bad()
        return ["round " + " ".join(str(math.trunc(v)) for v in ROUND_PROBE), "trip %d %d" % self._trip], []

    def _x_mcw_fadetest(self, ms, t, hidden):
        return ["fadetest 1"], []

    def _x_mcw_fadeshow(self, ms, show):
        return ["fadeshow 0"], []

    def _x_mcw_quit(self, ms):
        """ws mcw_quit: ответ, затем theGame.ExitGame(). Фейк только отмечает quit_requested (соединение живо)."""
        self.quit_requested = True
        return ["quit"], []

    # -- mcw_steve.ws (docs/STEVE.md). Логику камеры фейк не моделирует: только состояние и формат ответов.
    def _x_mcwf_on(self, ms, on, src, sens):
        """ws mcwf_on: включить вид от первого лица (взгляд берётся у камеры) или выключить."""
        f = self.fp
        if on == 1 and not f["on"]:
            f.update(on=True, src=src, yaw=self.world.yaw, pitch=0, ticks=0)
        elif on == 1:
            f["src"] = src                    # уже включён: как ws, источник глаза меняется сразу
        else:
            f["on"] = False
        return [f"fp {on} {f['src']} {f['yaw']} {f['pitch']}"], []

    def _x_mcwf_eye(self, ms, x, y, z, vx, vy, vz):
        """ws mcwf_eye: позиция глаза от моста (см); ответ — взгляд Ведьмака."""
        f = self.fp
        f["eye"] = (x, y, z)
        return [f"look {f['yaw']} {f['pitch']} {f['ticks']}"], []

    def _x_mcwf_block(self, ms, on):
        """ws mcwf_block: 0 — свои блокировки сняты, 1 — бой Геральта заблокирован (мышь у клиента MC), 2 — бой и
        движение (ходит клиент MC)."""
        self.fp["block"] = on
        return [f"fpblock {on}"], []

    def _x_mcwf_freeze(self, ms, on):
        """ws mcwf_freeze: взгляд от мыши не двигается (открыт экран Майнкрафта)."""
        self.fp["freeze"] = on == 1
        return [f"fpfreeze {on}"], []

    def _x_mcwf_mc(self, ms, x, y, z, vx, vy, vz, eyeh):
        """ws mcwf_mc: глаз и скорость от клиента MC; кукла — Геральт в ногах (глаз − eyeh), если он не дальше 10 м."""
        f = self.fp
        f["eye"] = (x, y, z)
        f["eyeh"] = eyeh
        f["mc_calls"] += 1
        far = 0
        if f["on"] and f["src"] == 1:
            feet = [x, y, z - eyeh]
            if math.dist(feet, self.world.player[:3]) > 1000:
                far = 1
            else:
                self.world.player[:3] = feet
        return [f"look {f['yaw']} {f['pitch']} {f['ticks']} {far}"], []

    def _x_mcwf_fov(self, ms, fov):
        if fov > 0:
            self.fp["fov"] = fov
        return [f"fov {self.fp['fov']} {self.fp['fov']}"], []

    def _x_mcwf_state(self, ms):
        f = self.fp
        return [f"fpstate {int(f['on'])} {f['src']} {f['yaw']} {f['pitch']} {f['ticks']} 0 0 {int(f['susp'])}"], []

    def _x_mcwf_interact(self, ms):
        """ws mcwf_interact: есть ли подсказка взаимодействия (тест ставит fp["interact"])."""
        return [f"interact {int(self.fp['interact'])}"], []

    def _x_mcws_cam(self, ms, mode, x, y, z, pitch, yaw):
        return [f"cam {mode} {x} {y} {z} {pitch} {yaw}", "dir %d %d %d" % self.world.cam()[0]], []

    def _x_mcws_puppet(self, ms, on):
        self.steve["puppet"] = on == 1
        return [f"puppet {on} 1"], []

    def _x_mcws_ptp(self, ms, x, y, z, yaw):
        """ws mcws_ptp: TeleportWithRotation применяется к следующему кадру, поэтому ответ — позиция ДО телепорта."""
        w = self.world
        old = w.player
        w.player = [x, y, z]
        w.yaw = yaw
        return ["ptp %d %d %d" % tuple(old)], []

    def _x_mcws_hide(self, ms, hide):
        self.steve["hidden"] = hide != 0
        return [f"hide {hide}"], []

    def _x_mcws_hit(self, ms, dmg):
        return ["hit 0 0 0"], []

    def _x_mcws_god(self, ms, on):
        st = self.steve
        if on in (0, 1):
            st["god"] = on == 1
            if on == 1:
                st["hits"] = 0
        return [f"god {on} {st['hits']} 0"], []

    def _x_mcws_scam(self, ms, op, x, y, z, pitch, yaw, path):
        st = self.steve
        ok = int(op == 1 or st["scam"])
        st["scam"] = op != 0 and (op == 1 or st["scam"])
        return [f"scam {op} {ok} {x} {y} {z}", "dir %d %d %d" % self.world.cam()[0]], []

    def _x_mcw_ents(self, ms, ops):
        """ws mcw_ents: операции ПО ПОРЯДКУ. spawn:id:x:y:z:yaw:t — сначала уничтожить всё с тегом слота MCW_E<id>,
        потом сущность с тегами MCW_ENT и MCW_E<id> (без MCW_BLOCK); move:id:x:y:z:yaw — GetEntityByTag +
        TeleportWithRotation, слот пуст — ошибка; kill:id — всё с тегом слота, пусто — ошибка; wipe — все MCW_ENT
        (в killed). id вне 0..63, не то число полей, не -?[0-9]+, t вне 0..12 — ошибка. Пустые куски пропускаются."""
        w = self.world
        t0 = time.perf_counter()
        n_spawn = n_move = n_kill = n_err = 0

        def tagged(tag: str) -> List[Ent]:
            return [e for e in w.ents.values() if tag in e.tags]

        def kill(tag: str) -> int:
            got = tagged(tag)
            for e in got:
                w.destroy(e.id)
            return len(got)

        for op in ops.split("|"):
            if op == "":
                continue
            f = op.split(":")
            nums = f[1:]
            good = False
            if f == ["wipe"]:
                n_kill += kill("MCW_ENT")
                good = True
            elif all(_INT_RE.fullmatch(v) for v in nums) and nums and 0 <= int(nums[0]) < ENT_SLOTS:
                slot = f"MCW_E{int(nums[0])}"
                v = [int(x) for x in nums]
                if f[0] == "kill" and len(f) == 2:
                    k = kill(slot)
                    n_kill += k
                    good = k > 0
                elif f[0] == "move" and len(f) == 6:
                    got = tagged(slot)
                    if got:
                        e = got[0]                                          # GetEntityByTag: одна из
                        e.x, e.y, e.z, e.yaw = v[1], v[2], v[3], v[4]
                        e.moves += 1
                        n_move += 1
                        good = True
                elif f[0] == "spawn" and len(f) == 7 and v[5] in BATCH_TEMPLATES:
                    kill(slot)
                    e = w.spawn(BATCH_TEMPLATES[v[5]], v[1], v[2], v[3], ("MCW_ENT", slot))
                    e.yaw = v[4]
                    w.spawn_cost(1)
                    n_spawn += 1
                    good = True
            if not good:
                n_err += 1
        return [f"ents {n_spawn} {n_move} {n_kill} {n_err} {int((time.perf_counter() - t0) * 1000)}"], []

    def mobs(self) -> Dict[int, Ent]:
        """Сущности моста по слоту (для тестов): {id: Ent}."""
        out: Dict[int, Ent] = {}
        for e in self.world.ents.values():
            if "MCW_ENT" in e.tags:
                for t in e.tags:
                    if t.startswith("MCW_E") and t[5:].isdigit():
                        out[int(t[5:])] = e
        return out

    # -- КОНТРАКТ modMCWProbe: поля @addField(CR4Player), токен из @wrapMethod OnSpawned
    def _x_mcwp_state(self, ms):
        w = self.world
        if not w.has_player:
            return ["state -1 -1 -1"], []
        return [f"state {w.token} {len(w.slots)} {len(w.index)}"], []

    def _x_mcwp_put(self, ms, key):
        w = self.world
        if not w.has_player:
            return [f"put {key} -1"], []
        w.index[key] = "thePlayer"
        w.slots.append("thePlayer")
        return [f"put {key} {len(w.index)}"], []

    def _x_mcwp_get(self, ms, key):
        w = self.world
        return [f"get {key} {1 if w.has_player and key in w.index else 0}"], []

    def _x_mcwp_del(self, ms, key):
        w = self.world
        ok = w.has_player and w.index.pop(key, None) is not None
        return [f"del {key} {1 if ok else 0}"], []


def _fit(text: str) -> str:
    """Только legacy_limits: ответ длиннее i16 фейк обрезает (маркер end при этом теряется)."""
    raw = text.encode("utf-8")
    if len(raw) <= MAX_STR:
        return text
    return raw[:MAX_STR].decode("utf-8", errors="ignore")


def main(argv: Optional[Sequence[str]] = None) -> None:
    w3net.utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=w3net.GAME_PORT)
    ap.add_argument("--utf16", action="store_true", help="все строки ответов в UTF-16 (как до 5.0)")
    ap.add_argument("--frame-ms", type=float, default=16.7, help="длительность кадра игрового потока")
    ap.add_argument("--max-per-frame", type=int, default=0, help="exec за кадр (0 = все из очереди)")
    ap.add_argument("--per-line", action="store_true", help="пакет на каждую строку Log()")
    ap.add_argument("--trailing-status", action="store_true", help="после строк ещё пакет 'Spam: ...'")
    ap.add_argument("--leading-status", action="store_true", help="пакет 'Spam: ...' до строк Log()")
    ap.add_argument("--log-not-in-reply", action="store_true", help="Log() только в scriptslog, ответ 'Spam: ...'")
    ap.add_argument("--unsolicited", type=float, default=0.0, metavar="MS", help="слать пакеты без запроса раз в MS")
    ap.add_argument("--unsolicited-kind", choices=["utility", "exec"], default="utility")
    ap.add_argument("--drop-every", type=int, default=0, metavar="K", help="терять ответ каждого K-го exec")
    ap.add_argument("--freeze-every", type=int, default=0, metavar="K", help="замирать перед каждым K-м exec")
    ap.add_argument("--freeze-ms", type=float, default=0.0)
    ap.add_argument("--stall", type=float, default=0.0, metavar="MS", help="перестать читать сокет на MS")
    ap.add_argument("--stall-after", type=int, default=1, metavar="N", help="... после N-го exec")
    ap.add_argument("--no-bind-required", action="store_true", help="отвечать и без BIND")
    ap.add_argument("--no-probe", action="store_true", help="modMCWProbe не скомпилирован")
    ap.add_argument("--legacy", action="store_true", help="мод без mcw_countblocks/mcw_boomat и строки normal")
    ap.add_argument("--reload-error", action="store_true", help="перекомпиляция с ошибкой")
    ap.add_argument("--legacy-limits", action="store_true",
                    help="без пределов exec 5.00c (команда 2047, ответ 1999 байт): только i16 протокола")
    ap.add_argument("--spawn-us", type=float, default=20.0, help="цена спавна сущности, мкс")
    ap.add_argument("--chunk", type=int, default=None, help="слать ответы кусками по N байт")
    ap.add_argument("--coalesce", action="store_true", help="ответы кадра одним send()")
    ap.add_argument("--scriptslog", default=None, help="дописывать строки Log/LogChannel в этот файл")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    srv = FakeWitcher(a.host, a.port, utf8_only=not a.utf16, frame_time=a.frame_ms / 1000.0,
                      max_per_frame=a.max_per_frame, per_line=a.per_line, trailing_status=a.trailing_status,
                      leading_status=a.leading_status,
                      log_in_reply=not a.log_not_in_reply, unsolicited_ms=a.unsolicited,
                      unsolicited_kind=a.unsolicited_kind, drop_every=a.drop_every, freeze_every=a.freeze_every,
                      freeze_ms=a.freeze_ms, stall_ms=a.stall, stall_after=a.stall_after,
                      require_bind=not a.no_bind_required, probe=not a.no_probe, reload_error=a.reload_error,
                      legacy=a.legacy, legacy_limits=a.legacy_limits,
                      spawn_us=a.spawn_us, chunk_size=a.chunk, coalesce=a.coalesce, scriptslog_path=a.scriptslog,
                      verbose=a.verbose).start()
    print(f"фейковый Ведьмак на {a.host}:{srv.port} (Ctrl-C - стоп)", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.stop()


if __name__ == "__main__":
    main()
