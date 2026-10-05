"""Мобы и предметы Майнкрафта (поток ent мода) -> плейсхолдеры в Ведьмаке (mcw_ents) и счётчик предметов на земле.

Только данные: ни одной ссылки на сущности Ведьмака. EntTable — последнее состояние каждой сущности мода по дельтам
ent{spawn, move, hp, fuse, item, remove}. EntDisplay решает, кого показать (мобы с шаблоном, в радиусе, ближние
первыми, не больше limit), раздаёт слоты 0..63 (у мода — тег MCW_E<id>) и выдаёт операции mcw_ents: spawn новым,
move сдвинувшимся больше move_eps или повернувшимся больше yaw_eps, kill ушедшим. Запись «что показано» меняется при
отправке, как у блоков (world.Display). Освобождённый слот встаёт в конец очереди, поэтому kill и spawn одного слота
в одном пакете не встречаются: отдаёт ли GetEntityByTag в том же кадре уничтоженную сущность — [проверить].
Координаты — только через spike/coords.py.
"""
from __future__ import annotations

import collections
import math
from dataclasses import dataclass, field
from typing import Optional

import spike_path  # noqa: F401
import coords

ITEM = "minecraft:item"


def short_type(t: str) -> str:
    return t.split(":", 1)[1] if t.startswith("minecraft:") else t


@dataclass
class Ent:
    id: int
    type: str
    p: tuple[float, float, float]          # ноги, координаты Майнкрафта
    yaw: float
    hp: Optional[float] = None
    fuse: int = 0
    item: Optional[str] = None
    count: int = 0


@dataclass
class EntEvents:
    """Что из сообщения ent интересно уведомлениям."""
    new: list[int] = field(default_factory=list)
    gone: list[int] = field(default_factory=list)
    fuse_on: list[int] = field(default_factory=list)
    hurt: list[int] = field(default_factory=list)


class EntTable:
    def __init__(self) -> None:
        self.ents: dict[int, Ent] = {}

    def reset(self) -> None:
        self.ents.clear()

    def apply(self, m: dict) -> EntEvents:
        ev = EntEvents()
        for s in m.get("spawn") or []:
            if isinstance(s, list) and len(s) >= 7:
                self.ents[s[0]] = Ent(s[0], str(s[1]), (s[2], s[3], s[4]), s[5] or 0.0, s[6])
                ev.new.append(s[0])
        for s in m.get("move") or []:
            e = self.ents.get(s[0]) if isinstance(s, list) and len(s) >= 5 else None
            if e is not None:
                e.p, e.yaw = (s[1], s[2], s[3]), s[4] or 0.0
        for s in m.get("hp") or []:
            e = self.ents.get(s[0]) if isinstance(s, list) and len(s) >= 2 else None
            if e is not None:
                if isinstance(s[1], (int, float)) and isinstance(e.hp, (int, float)) and s[1] < e.hp:
                    ev.hurt.append(e.id)
                e.hp = s[1]
        for s in m.get("fuse") or []:
            e = self.ents.get(s[0]) if isinstance(s, list) and len(s) >= 2 else None
            if e is not None:
                if s[1] and not e.fuse:
                    ev.fuse_on.append(e.id)
                e.fuse = int(bool(s[1]))
        for s in m.get("item") or []:
            e = self.ents.get(s[0]) if isinstance(s, list) and len(s) >= 3 else None
            if e is not None:
                e.item, e.count = s[1], s[2]
        for i in m.get("remove") or []:
            if self.ents.pop(i, None) is not None:
                ev.gone.append(i)
        return ev

    def items_near(self, center: tuple[float, float, float], radius: float) -> collections.Counter:
        """Предметы на земле в радиусе: {предмет: штук}."""
        out: collections.Counter = collections.Counter()
        r2 = radius * radius
        for e in self.ents.values():
            if e.type == ITEM and e.item and sum((e.p[a] - center[a]) ** 2 for a in range(3)) <= r2:
                out[e.item] += e.count
        return out


@dataclass
class Shown:
    slot: int
    t: int
    p: tuple[float, float, float]
    yaw: float
    type: str


class EntDisplay:
    """Что мост поставил в Ведьмаке: id сущности мода -> Shown. ops() — дифф, commit() — пакет ушёл в игру."""

    def __init__(self, templates_by_t: dict, type_t: Optional[dict] = None, default_t: int = 0,
                 y_offset: int = coords.Y_OFFSET, slots: int = coords.ENT_SLOTS, move_eps: float = 0.05,
                 yaw_eps: float = 5.0) -> None:
        self.tpl = templates_by_t
        self.type_t = dict(type_t or {})
        self.default_t = default_t
        self.y_offset = y_offset
        self.slots = slots
        self.move_eps = move_eps
        self.yaw_eps = yaw_eps
        self.reset()

    def reset(self, wipe: bool = True) -> None:
        """Новое соединение с игрой, mcw_clearall или сбой пакета: что стоит в игре, неизвестно. wipe — первым
        пакетом снести все MCW_ENT (spawn и так идемпотентен по слоту, а wipe убирает и слоты, о которых мост забыл)."""
        self.shown: dict[int, Shown] = {}
        self.free: collections.deque[int] = collections.deque(range(self.slots))
        self.need_wipe = wipe

    def template(self, typ: str) -> int:
        """Индекс шаблона mcw_batch для типа моба; -1 — не показывать (предметы — только счётчиком)."""
        t = self.type_t.get(typ)
        if t is None:
            t = -1 if typ == ITEM else self.default_t
        return t

    def wanted(self, table: EntTable, center: tuple[float, float, float], radius: float, limit: int) -> list[Ent]:
        r2 = radius * radius
        out = []
        for e in table.ents.values():
            if self.template(e.type) < 0 or self.template(e.type) not in self.tpl:
                continue
            d2 = sum((e.p[a] - center[a]) ** 2 for a in range(3))
            if d2 <= r2:
                out.append((d2, e.id, e))
        out.sort(key=lambda x: (x[0], x[1]))
        return [e for _, _, e in out[:min(limit, self.slots)]]

    def ops(self, table: EntTable, center: tuple[float, float, float], radius: float,
            limit: int) -> list[tuple[str, int, str]]:
        """-> [(op, id сущности мода, вид)], вид: wipe | kill | spawn | move. Порядок: wipe, kill, потом spawn и move
        ближних первыми. Слоты spawn берутся из начала очереди, kill отдаёт свои в конец."""
        out: list[tuple[str, int, str]] = []
        if self.need_wipe:
            out.append(("wipe", -1, "wipe"))
        want = self.wanted(table, center, radius, limit)
        keep = {e.id for e in want}
        for i, s in self.shown.items():
            if i not in keep:
                out.append((coords.ent_kill_op(s.slot), i, "kill"))
        free = iter(self.free)
        for e in want:
            t = self.template(e.type)
            s = self.shown.get(e.id)
            if s is not None and s.t != t:
                s = None                     # другой шаблон: spawn в тот же слот (spawn сам сносит старое)
                slot = self.shown[e.id].slot
            elif s is None:
                slot = next(free, None)
                if slot is None:
                    break
            else:
                slot = s.slot
            if s is None:
                out.append((coords.ent_spawn_op(slot, *e.p, e.yaw, self.tpl[t], self.y_offset), e.id, "spawn"))
            elif math.dist(e.p, s.p) > self.move_eps or abs((e.yaw - s.yaw + 180.0) % 360.0 - 180.0) > self.yaw_eps:
                out.append((coords.ent_move_op(slot, *e.p, e.yaw, self.tpl[t], self.y_offset), e.id, "move"))
        return out

    def commit(self, ops: list[tuple[str, int, str]], table: EntTable) -> None:
        for op, i, kind in ops:
            if kind == "wipe":
                self.need_wipe = False
            elif kind == "kill":
                s = self.shown.pop(i, None)
                if s is not None:
                    self.free.append(s.slot)
            else:
                e = table.ents.get(i)
                slot = int(op.split(":")[1])
                if e is None:
                    continue
                if kind == "spawn":
                    if slot in self.free:
                        self.free.remove(slot)
                    self.shown[i] = Shown(slot, self.template(e.type), e.p, e.yaw, e.type)
                else:
                    s = self.shown[i]
                    s.p, s.yaw = e.p, e.yaw

    # -- кто занимает место в мире Ведьмака (ящик ~1,03 м с низом на ногах моба)
    def near_column(self, x: int, z: int, reach: float = 1.05) -> bool:
        """Плейсхолдер над колонной рельефа (центр колонны (x+0.5, z+0.5)) — mcw_ground мог попасть в его ящик."""
        return any(abs(s.p[0] - (x + 0.5)) < reach and abs(s.p[2] - (z + 0.5)) < reach for s in self.shown.values())

    def hit(self, p: tuple[float, float, float], pad: float = 0.15) -> Optional[Shown]:
        """Точка луча mcw_look (координаты Майнкрафта) в ящике плейсхолдера или рядом с ним."""
        for s in self.shown.values():
            if (abs(p[0] - s.p[0]) <= 0.52 + pad and abs(p[2] - s.p[2]) <= 0.52 + pad
                    and s.p[1] - pad <= p[1] <= s.p[1] + 1.03 + pad):
                return s
        return None


def pack_ents(ops: list[str], seq: int, limit: int) -> int:
    """Сколько первых ops влезает в одну команду coords.ents_call(seq, ...) не длиннее limit байт utf8."""
    if not 0 < limit <= coords.MAX_CMD:
        raise ValueError(f"limit {limit} вне (0, {coords.MAX_CMD}]")
    if not ops:
        return 0                  # пустой mcw_ents не шлётся вовсе: "" игра не разбирает
    size = coords.utf8_len(coords.ents_call(seq, ops[:1]))
    if size > limit:
        raise ValueError(f"одна команда {ops[0]!r} не влезает в {limit} байт")
    n = 1
    for op in ops[1:]:
        add = coords.utf8_len(op) + 1
        if size + add > limit:
            break
        size += add
        n += 1
    return n
