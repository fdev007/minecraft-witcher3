"""Рельеф Ведьмака -> barrier в Майнкрафте. Мост спрашивает высоту опоры колонны у игры (mcw_ground), ближние
колонны первыми, кеширует её и шлёт моду ground{c:[x,z,y0,y1,...]}; чанки, от которых Геральт ушёл дальше
clear_dist_m, снимает ground_clear{box}. Только данные: колонна = блок Майнкрафта (x, z).

Верх колонны: опора на высоте h м -> верхний barrier = round(h) - 1 (через coords.cm_to_block от h + 0,5 м), так
поставленный на землю блок утоплен или висит не больше чем на 0,5 м. Колонна — depth блоков вниз от верха.
"""
from __future__ import annotations

import math
from typing import Optional

import spike_path  # noqa: F401
import coords

Col = tuple[int, int]
ChunkKey = tuple[int, int]


def col_center_cm(bx: int, bz: int, y_offset: int = coords.Y_OFFSET) -> tuple[int, int]:
    """Центр колонны в мировых см Ведьмака (x, y) — аргументы mcw_ground."""
    wx, wy, _ = coords.block_center_w(bx, 0, bz, y_offset=y_offset)
    return coords.to_cm(wx), coords.to_cm(wy)


def top_from_cm(bx: int, bz: int, z_cm: int, y_offset: int = coords.Y_OFFSET) -> int:
    """Высота опоры (см) -> y верхнего barrier колонны."""
    x_cm, y_cm = col_center_cm(bx, bz, y_offset)
    return coords.cm_to_block(x_cm, y_cm, z_cm + 50, y_offset=y_offset)[1] - 1


class GroundPlanner:
    def __init__(self, radius_m: float = 24.0, depth: int = 3, clear_dist_m: float = 96.0, retry_none_s: float = 10.0,
                 y_offset: int = coords.Y_OFFSET) -> None:
        self.radius = radius_m
        self.depth = depth
        self.clear_dist = clear_dist_m
        self.retry_none = retry_none_s
        self.y_offset = y_offset
        r = int(math.ceil(radius_m))
        r2 = radius_m * radius_m
        # смещения колонн по кругу, ближние первыми: и порядок вопросов игре, и порядок отправки моду
        self.offsets: list[Col] = sorted(((dx, dz) for dx in range(-r, r + 1) for dz in range(-r, r + 1)
                                          if dx * dx + dz * dz <= r2), key=lambda o: (o[0] * o[0] + o[1] * o[1], o))
        self.reset()

    def reset(self) -> None:
        """Новый мир Ведьмака: всё забыть (высоты другого мира не годятся)."""
        self.top: dict[Col, int] = {}
        self.height: dict[Col, int] = {}          # непрерывная высота поверхности, y Майнкрафта в см (для клиента MC)
        self.none_until: dict[Col, float] = {}
        self.pending: set[Col] = set()
        self.mc_reset()
        self.client_reset()
        self._center: Optional[Col] = None
        self._cursor = 0

    def mc_reset(self) -> None:
        """Новое соединение с модом: что ему слали, неизвестно — известные высоты уйдут заново (ground идемпотентен:
        мод меняет только воздух, а уже стоящий barrier записывает в свои)."""
        self.sent: set[Col] = set()
        self.sent_by_chunk: dict[ChunkKey, set[Col]] = {}
        self.wait_chunk: dict[ChunkKey, set[Col]] = {}

    def client_reset(self) -> None:
        """Новое соединение с клиентом MC: высоты уйдут ему заново."""
        self.client_sent: set[Col] = set()

    def take_terrain(self, center: Col, max_cols: int) -> list[int]:
        """Известные высоты, ещё не отправленные клиенту MC (ближние первыми) -> [x, z, h_cm, ...] для terrain."""
        cx, cz = center
        c: list[int] = []
        for dx, dz in self.offsets:
            col = (cx + dx, cz + dz)
            h = self.height.get(col)
            if h is None or col in self.client_sent:
                continue
            c += [col[0], col[1], h]
            self.client_sent.add(col)
            if len(c) >= 3 * max_cols:
                break
        return c

    def top_at(self, x: int, z: int) -> Optional[int]:
        return self.top.get((x, z))

    # -- вопросы игре
    def want(self, center: Col, n: int, now: float) -> list[Col]:
        """До n колонн без известной высоты, ближние к center первыми; отмечаются как ждущие ответа."""
        if center != self._center:
            self._center, self._cursor = center, 0
        cx, cz = center
        offs = self.offsets
        while self._cursor < len(offs) and (cx + offs[self._cursor][0], cz + offs[self._cursor][1]) in self.top:
            self._cursor += 1
        out: list[Col] = []
        i = self._cursor
        while i < len(offs) and len(out) < n:
            col = (cx + offs[i][0], cz + offs[i][1])
            i += 1
            if col in self.top or col in self.pending:
                continue
            until = self.none_until.get(col)
            if until is not None and now < until:
                continue
            out.append(col)
            self.pending.add(col)
        return out

    def on_height(self, col: Col, ok: bool, z_cm: int, now: float) -> bool:
        """Ответ mcw_ground. True — верх колонны поменялся (открытость граней соседних блоков надо пересчитать)."""
        self.pending.discard(col)
        if not ok:
            self.none_until[col] = now + self.retry_none
            return False
        top = top_from_cm(col[0], col[1], z_cm, self.y_offset)
        h = z_cm + 100 * self.y_offset              # mc.y = w.z + y_offset
        if self.height.get(col) != h:
            self.height[col] = h
            self.client_sent.discard(col)
        self.none_until.pop(col, None)
        old = self.top.get(col)
        self.top[col] = top
        if old is not None and old != top:
            self._unsend(col)
        return old != top

    def on_fail(self, col: Col) -> None:
        """Вопрос потерялся (таймаут, обрыв): колонна снова неизвестна."""
        self.pending.discard(col)

    def on_blocked(self, col: Col, now: float, delay: float = 2.0) -> None:
        """Над колонной стоял плейсхолдер моба: высоте не верим (луч мог упереться в его ящик), спросить позже."""
        self.pending.discard(col)
        self.none_until[col] = now + delay

    def drop_pending(self) -> None:
        self.pending.clear()

    # -- мод
    def _unsend(self, col: Col) -> None:
        self.sent.discard(col)
        ch = (col[0] >> 4, col[1] >> 4)
        s = self.sent_by_chunk.get(ch)
        if s is not None:
            s.discard(col)
            if not s:
                del self.sent_by_chunk[ch]

    def take_ground(self, center: Col, max_cols: int) -> Optional[list[int]]:
        """Известные, ещё не отправленные колонны в радиусе (ближние первыми) -> c для ground или None."""
        cx, cz = center
        c: list[int] = []
        waiting = self.wait_chunk
        for dx, dz in self.offsets:
            col = (cx + dx, cz + dz)
            top = self.top.get(col)
            if top is None or col in self.sent:
                continue
            ch = (col[0] >> 4, col[1] >> 4)
            if col in waiting.get(ch, ()):
                continue
            c += [col[0], col[1], top - self.depth + 1, top]
            self.sent.add(col)
            self.sent_by_chunk.setdefault(ch, set()).add(col)
            if len(c) >= 4 * max_cols:
                break
        return c or None

    def on_ground_ok(self, m: dict) -> None:
        """ground_ok: колонны из skip лежат в незагруженных чанках — ждут chunk."""
        skip = m.get("skip") or []
        for i in range(0, len(skip) - 1, 2):
            col = (skip[i], skip[i + 1])
            self._unsend(col)
            self.wait_chunk.setdefault((col[0] >> 4, col[1] >> 4), set()).add(col)

    def on_chunk(self, chunk: ChunkKey) -> None:
        """Чанк загрузился у мода: его пропущенные колонны снова можно слать."""
        self.wait_chunk.pop(chunk, None)

    def take_clears(self, center_xz: tuple[float, float]) -> list[list[int]]:
        """Чанки с отправленным рельефом дальше clear_dist_m (по ближней точке чанка) -> box для ground_clear."""
        if self.clear_dist <= 0:
            return []
        px, pz = center_xz
        lim = self.clear_dist * self.clear_dist
        out = []
        for ch in list(self.sent_by_chunk):
            x0, z0 = ch[0] * 16, ch[1] * 16
            dx = max(x0 - px, 0.0, px - (x0 + 16))
            dz = max(z0 - pz, 0.0, pz - (z0 + 16))
            if dx * dx + dz * dz > lim:
                for col in self.sent_by_chunk.pop(ch):
                    self.sent.discard(col)
                self.wait_chunk.pop(ch, None)
                out.append([x0, z0, x0 + 15, z0 + 15])
        return out

    def trim(self, center_xz: tuple[float, float], keep_m: float) -> int:
        """Забыть высоты дальше keep_m (память на долгой прогулке). -> сколько забыто."""
        px, pz = center_xz
        lim = keep_m * keep_m
        far = [c for c in self.top if (c[0] + 0.5 - px) ** 2 + (c[1] + 0.5 - pz) ** 2 > lim and c not in self.sent]
        for c in far:
            del self.top[c]
            self.height.pop(c, None)
            self.client_sent.discard(c)
        for c in [c for c in self.none_until if (c[0] + 0.5 - px) ** 2 + (c[1] + 0.5 - pz) ** 2 > lim]:
            del self.none_until[c]
        if far:
            self._cursor = 0
        return len(far)
