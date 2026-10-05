"""Зеркало мира Minecraft по чанкам, отбор блоков для показа в Ведьмаке, дифф «показано» <-> «должно быть»,
нарезка mcw_batch по байтам и цель для break/place по лучу mcw_look.

Только данные: ни одной ссылки на сущности Ведьмака. Что стоит в Ведьмаке, мост знает по своей записи
Display.shown (клетка -> шаблон и готовая команда clear с тем же origin, что у set), а не по ответам игры.
Координаты — только через spike/coords.py.
"""
from __future__ import annotations

import fnmatch
import heapq
import math
from typing import Callable, Optional

import spike_path  # noqa: F401
import coords

Pos3 = tuple[int, int, int]
ChunkKey = tuple[int, int]
NEIGHBORS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
BARRIER = "minecraft:barrier"


def base_name(state: str) -> str:
    """'minecraft:oak_log[axis=y]' -> 'minecraft:oak_log'."""
    i = state.find("[")
    return state if i < 0 else state[:i]


class BlockRules:
    """Что не рисуем совсем (hide), что не закрывает соседа (transparent) и каким шаблоном mcw_batch рисуем.
    Шаблоны — fnmatch по имени без свойств; в templates ключ — полное состояние или имя без свойств."""

    def __init__(self, hide=(), transparent=(), templates: Optional[dict] = None, default_t: int = 0) -> None:
        self.hide = tuple(hide)
        self.transparent = tuple(transparent)
        self.templates = dict(templates or {})
        self.default_t = default_t
        self._hidden: dict[str, bool] = {}
        self._opaque: dict[str, bool] = {}

    @staticmethod
    def _match(name: str, patterns) -> bool:
        return any(fnmatch.fnmatchcase(name, p) for p in patterns)

    def hidden(self, state: str) -> bool:
        v = self._hidden.get(state)
        if v is None:
            b = base_name(state)
            v = b == BARRIER or self._match(b, self.hide)
            self._hidden[state] = v
        return v

    def opaque(self, state: str) -> bool:
        v = self._opaque.get(state)
        if v is None:
            v = not self.hidden(state) and not self._match(base_name(state), self.transparent)
            self._opaque[state] = v
        return v

    def template(self, state: str) -> int:
        t = self.templates.get(state)
        if t is None:
            t = self.templates.get(base_name(state), self.default_t)
        return t


class Mirror:
    """Зеркало мода по чанкам: (cx, cz) -> {(x, y, z): состояние}. Строится из chunk + blocks + unchunk, как
    mc/fake_bridge.Mirror; вдобавок копит чанки, у которых могла поменяться открытость граней (changed)."""

    def __init__(self) -> None:
        self.by_chunk: dict[ChunkKey, dict[Pos3, str]] = {}
        self.snap_done = False
        self.changed: set[ChunkKey] = set()

    def reset(self) -> None:
        self.changed.update(self.by_chunk)
        self.by_chunk.clear()
        self.snap_done = False

    def get(self, x: int, y: int, z: int) -> Optional[str]:
        ch = self.by_chunk.get((x >> 4, z >> 4))
        return None if ch is None else ch.get((x, y, z))

    def size(self) -> int:
        return sum(len(ch) for ch in self.by_chunk.values())

    def take_changed(self) -> set[ChunkKey]:
        out, self.changed = self.changed, set()
        return out

    def _touch_chunk(self, cx: int, cz: int) -> None:
        self.changed.update(((cx, cz), (cx + 1, cz), (cx - 1, cz), (cx, cz + 1), (cx, cz - 1)))

    def _touch(self, x: int, z: int) -> None:
        cx, cz = x >> 4, z >> 4
        self.changed.add((cx, cz))
        lx, lz = x & 15, z & 15
        if lx == 0:
            self.changed.add((cx - 1, cz))
        elif lx == 15:
            self.changed.add((cx + 1, cz))
        if lz == 0:
            self.changed.add((cx, cz - 1))
        elif lz == 15:
            self.changed.add((cx, cz + 1))

    def apply(self, m: dict) -> bool:
        """Сообщение мода -> True, если поменялись блоки или готовность снапшота."""
        t = m.get("t")
        if t == "chunk":
            cx, cz = m["c"]
            pal, b = m["pal"], m["b"]
            self.by_chunk[(cx, cz)] = {(b[i], b[i + 1], b[i + 2]): pal[b[i + 3]] for i in range(0, len(b), 4)}
            self._touch_chunk(cx, cz)
            return True
        if t == "unchunk":
            c = (m["c"][0], m["c"][1])
            if self.by_chunk.pop(c, None) is not None:
                self._touch_chunk(*c)
            return True
        if t == "blocks":
            pal, s, c = m["pal"], m["set"], m["clear"]
            for i in range(0, len(s), 4):
                x, y, z = s[i], s[i + 1], s[i + 2]
                self.by_chunk.setdefault((x >> 4, z >> 4), {})[(x, y, z)] = pal[s[i + 3]]
                self._touch(x, z)
            for i in range(0, len(c), 3):
                x, y, z = c[i], c[i + 1], c[i + 2]
                ch = self.by_chunk.get((x >> 4, z >> 4))
                if ch is not None:
                    ch.pop((x, y, z), None)
                self._touch(x, z)
            return bool(s or c)
        if t == "snap_end":
            self.snap_done = True
            return True
        if t == "hello":
            self.reset()  # ответ мода на hello: дальше идёт новый снапшот, он заменяет всё
            return True
        return False


class Selector:
    """Какие блоки показывать: не воздух (его в зеркале нет), не barrier и не hide, хотя бы одна открытая грань,
    в радиусе от Геральта, не больше limit, ближние первыми. Грань закрыта непрозрачным блоком зеркала или
    рельефом Ведьмака (barrier моста: y <= верх колонны, ground_top). Список открытых блоков кешируется на чанк."""

    def __init__(self, mirror: Mirror, rules: BlockRules, ground_top: Callable[[int, int], Optional[int]]) -> None:
        self.mirror = mirror
        self.rules = rules
        self.ground_top = ground_top
        self.cache: dict[ChunkKey, list[tuple[int, int, int, int]]] = {}

    def invalidate(self, chunks) -> None:
        for c in chunks:
            self.cache.pop(c, None)

    def clear(self) -> None:
        self.cache.clear()

    def covered(self, x: int, y: int, z: int) -> bool:
        s = self.mirror.get(x, y, z)
        if s is not None:
            return self.rules.opaque(s)
        top = self.ground_top(x, z)
        return top is not None and y <= top

    def exposed(self, chunk: ChunkKey) -> list[tuple[int, int, int, int]]:
        got = self.cache.get(chunk)
        if got is None:
            got = []
            for (x, y, z), s in (self.mirror.by_chunk.get(chunk) or {}).items():
                if self.rules.hidden(s):
                    continue
                if all(self.covered(x + dx, y + dy, z + dz) for dx, dy, dz in NEIGHBORS):
                    continue
                got.append((x, y, z, self.rules.template(s)))
            self.cache[chunk] = got
        return got

    def select(self, center: tuple[float, float, float], radius: float, limit: int) -> list[tuple[float, Pos3, int]]:
        """-> [(d2, (x, y, z), t)] по возрастанию расстояния от center (позиция Майнкрафта) до центра блока."""
        px, py, pz = center
        r2 = radius * radius
        out: list[tuple[float, Pos3, int]] = []
        for cx in range(math.floor(px - radius) >> 4, (math.floor(px + radius) >> 4) + 1):
            ddx = max(cx * 16 - px, 0.0, px - (cx * 16 + 16))
            for cz in range(math.floor(pz - radius) >> 4, (math.floor(pz + radius) >> 4) + 1):
                if (cx, cz) not in self.mirror.by_chunk:
                    continue
                ddz = max(cz * 16 - pz, 0.0, pz - (cz * 16 + 16))
                if ddx * ddx + ddz * ddz > r2:
                    continue
                for x, y, z, t in self.exposed((cx, cz)):
                    d2 = (x + 0.5 - px) ** 2 + (y + 0.5 - py) ** 2 + (z + 0.5 - pz) ** 2
                    if d2 <= r2:
                        out.append((d2, (x, y, z), t))
        if len(out) > limit:
            return heapq.nsmallest(limit, out)
        out.sort()
        return out


class Display:
    """Что мост поставил в Ведьмаке: клетка -> (t, clear-команда того же origin). Запись оптимистичная: меняется при
    отправке пакета (игра исполняет exec по порядку). Потерянный или упавший пакет — не догадки, а полная
    пересинхронизация (mcw_clearall и всё заново)."""

    def __init__(self, templates_by_t: dict, y_offset: int = coords.Y_OFFSET) -> None:
        self.tpl = templates_by_t
        self.y_offset = y_offset
        self.shown: dict[Pos3, tuple[int, str]] = {}

    def reset(self) -> None:
        self.shown.clear()

    def ops(self, desired: list[tuple[float, Pos3, int]]) -> list[tuple[str, Pos3, Optional[int]]]:
        """Дифф -> [(op, клетка, t или None для clear)]: сначала все clear (освобождают бюджет и место под замену),
        потом set ближние первыми. Замена = clear + set: set в занятую клетку дал бы в Ведьмаке дубль сущности."""
        want = {p: t for _, p, t in desired}
        out: list[tuple[str, Pos3, Optional[int]]] = []
        for p, (t, clr) in self.shown.items():
            if want.get(p) != t:
                out.append((clr, p, None))
        for _, p, t in desired:
            cur = self.shown.get(p)
            if cur is None or cur[0] != t:
                out.append((coords.set_op(*p, self.tpl[t], y_offset=self.y_offset), p, t))
        return out

    def commit(self, ops: list[tuple[str, Pos3, Optional[int]]]) -> None:
        """Пакет ушёл в игру: обновить запись. clear той же клетки в ops стоит раньше set."""
        for _, p, t in ops:
            if t is None:
                self.shown.pop(p, None)
            else:
                self.shown[p] = (t, coords.clear_op(*p, self.tpl[t], y_offset=self.y_offset))


def pack(ops: list[str], seq: int, limit: int) -> int:
    """Сколько первых ops влезает в одну команду coords.batch_call(seq, ...) не длиннее limit байт utf8.
    Длину считаем приращениями (op + '|'), итог совпадает с coords.utf8_len(coords.batch_call(seq, ops[:n]))."""
    if not 0 < limit <= coords.MAX_CMD:
        raise ValueError(f"limit {limit} вне (0, {coords.MAX_CMD}]")
    if not ops:
        return 0                  # пустой mcw_batch не шлётся вовсе: "" игра не разбирает
    size = coords.utf8_len(coords.batch_call(seq, ops[:1]))
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


# ---- цель для break/place ---------------------------------------------------------------------------------
FACE = {(0, 1): "east", (0, -1): "west", (1, 1): "up", (1, -1): "down", (2, 1): "south", (2, -1): "north"}


def mc_dir(cam_dir_w: tuple[float, float, float]) -> tuple[float, float, float]:
    """Направление Ведьмака -> единичный вектор Майнкрафта (те же оси, что coords.w_dir_to_mc_angles)."""
    dx, dy, dz = cam_dir_w
    v = (dx, dz, -dy)
    n = math.sqrt(sum(a * a for a in v))
    return (0.0, 0.0, 0.0) if n == 0 else (v[0] / n, v[1] / n, v[2] / n)


def look_target(hit_cm: tuple[int, int, int], is_block: bool, cam_dir_w: tuple[float, float, float],
                shown, y_offset: int = coords.Y_OFFSET, eps: float = 0.05, tol: float = 0.6,
                ground_top: Optional[Callable[[int, int], Optional[int]]] = None,
                normal_w: Optional[tuple[float, float, float]] = None):
    """Ответ mcw_look ('look 1 x y z isBlock', см) -> (клетка, грань, точка в клетке 0..1, попали ли в свой блок).

    normal_w — строка 'normal' нового мода (оси Ведьмака, /10000). С ней грань — ось нормали, клетка своего блока —
    на eps внутрь поверхности, клетка перед рельефом — на eps наружу. Нормаль, смотрящая от камеры (скалярное
    произведение с лучом > 0.2), считается недостоверной. Без неё (старый мод) грань выводим:

    Свой блок (isBlock=1): среди показанных клеток рядом с точкой — та, чей центр ближе всего по Чебышёву (ящик
    ~1.03 м, pivot сдвинут на 3.6 см, поэтому не floor точки); грань — ось наибольшего смещения точки от центра.
    Рельеф или чужое (isBlock=0): если верх barrier колонны перед поверхностью известен и точка у него (±1 м) — клик
    по этому barrier гранью up: Майнкрафт поставит блок на свою землю. Иначе (стена, рельеф не спрошен) — клетка
    перед поверхностью (шаг eps назад по лучу), для place это воздух, Майнкрафт заменит его блоком; грань — к камере."""
    hp = coords.w_to_mc_pos(hit_cm[0] / 100.0, hit_cm[1] / 100.0, hit_cm[2] / 100.0, y_offset=y_offset)
    d = mc_dir(cam_dir_w)
    n = mc_dir(normal_w) if normal_w is not None else None
    if n is not None and (n == (0.0, 0.0, 0.0) or sum(n[a] * d[a] for a in range(3)) > 0.2):
        n = None
    n_face = None
    if n is not None:
        n_axis = max(range(3), key=lambda a: abs(n[a]))
        n_face = FACE[(n_axis, 1 if n[n_axis] >= 0 else -1)]
    if is_block and n is not None:
        inside = tuple(math.floor(hp[a] - n[a] * eps) for a in range(3))
        if inside in shown:      # ящик выступает за клетку на 1.5 см, eps 5 см — точка уже в своей клетке
            frac = tuple(round(min(1.0, max(0.0, hp[a] - inside[a])), 4) for a in range(3))
            return inside, n_face, frac, True
    if is_block:
        base = [math.floor(v) for v in hp]
        best = None
        for i in (-1, 0, 1):
            for j in (-1, 0, 1):
                for k in (-1, 0, 1):
                    c = (base[0] + i, base[1] + j, base[2] + k)
                    if c not in shown:
                        continue
                    off = tuple(hp[a] - (c[a] + 0.5) for a in range(3))
                    # сначала клетки, в которые луч в этой точке входит (точка на ближней к камере стороне): ящики
                    # соседей перекрываются на 1.5 см, и точка на общей грани формально ближе к центру дальнего
                    exiting = sum(off[a] * d[a] for a in range(3)) > 0.05
                    key = (exiting, max(abs(v) for v in off),
                           sum((hp[a] + d[a] * eps - c[a] - 0.5) ** 2 for a in range(3)))
                    if best is None or key < best[0]:
                        best = (key, c, off)
        if best is not None and best[0][1] <= tol:
            _, cell, off = best
        else:
            cell = tuple(math.floor(hp[a] + d[a] * eps) for a in range(3))
            off = tuple(hp[a] - (cell[a] + 0.5) for a in range(3))
        axis = max(range(3), key=lambda a: abs(off[a]))
        face = n_face or FACE[(axis, 1 if off[axis] >= 0 else -1)]
    else:
        cell = tuple(math.floor(hp[a] + n[a] * eps) if n is not None else math.floor(hp[a] - d[a] * eps)
                     for a in range(3))
        top = ground_top(cell[0], cell[2]) if ground_top is not None else None
        if top is not None and abs(hp[1] - (top + 1)) <= 1.0 and (n is None or n[1] > 0.7):
            cell = (cell[0], top, cell[2])
            frac = (round(min(1.0, max(0.0, hp[0] - cell[0])), 4), 1.0, round(min(1.0, max(0.0, hp[2] - cell[2])), 4))
            return cell, "up", frac, False
        if n_face is not None:
            face = n_face
        else:
            axis = max(range(3), key=lambda a: abs(d[a]))
            face = FACE[(axis, -1 if d[axis] >= 0 else 1)]
    frac = tuple(round(min(1.0, max(0.0, hp[a] - cell[a])), 4) for a in range(3))
    return cell, face, frac, bool(is_block)
