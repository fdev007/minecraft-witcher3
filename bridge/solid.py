"""Коллизия Ведьмака для физики клиента MC: дома, стены, полы, заборы, деревья, двери (mcw_cols, mcw_collide.ws).

Колонна = блок Майнкрафта (x, z). Игра отвечает на блок одной строкой:
  "t bx bz h"                  статики в окне высот нет: только земля (h — см от z0, «n» — земли нет)
  "c bx bz h d s0/.../s15"     статика есть: 16 подколонн 25 см (sK, K = i + 4j: i по x MC, j по z MC), в каждой —
                               твёрдые промежутки «низ,верх;...» в см от z0 сверху вниз, низ «-» — до дна окна (земля);
                               «=» — как у предыдущей подколонны; d 1 — в колонне дверь (её коллизия меняется)
z0 — ноги игрока в момент вопроса (Ведьмак z, см). Высоты MC = z Ведьмака + y_offset блоков.

Клиенту уходит solid{c:[[x, z, h, d, subs], ...], reset?}: h — y MC земли в см или null, subs — null (только земля) или
16 списков [низ, верх, низ, верх, ...] в см MC (низ null — до земли). Только данные, без сети.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

Col = tuple[int, int]
Span = tuple[Optional[int], int]


@dataclass
class SolidCol:
    z0: int                                     # ноги игрока при скане (см Ведьмака): окно высот этого ответа
    h: Optional[int]                            # земля (только Terrain), y MC в см
    subs: Optional[list[list[Span]]]            # None — статики нет; иначе 16 подколонн, промежутки сверху вниз
    door: bool
    t: float                                    # когда отсканирована


def parse_spans(s: str, z0: int, base: int) -> list[Span]:
    """«низ,верх;...» (см от z0) -> [(низ|None, верх), ...] в см MC (base = 100 * y_offset)."""
    out: list[Span] = []
    if not s:
        return out
    for part in s.split(";"):
        lo, hi = part.split(",")
        out.append((None if lo == "-" else int(lo) + z0 + base, int(hi) + z0 + base))
    return out


def parse_line(line: str, z0: int, base: int, now: float) -> Optional[tuple[Col, SolidCol]]:
    """Строка ответа mcw_cols о блоке -> (колонна, данные); не о блоке или битая — None (ValueError ловит вызывающий)."""
    f = line.split(" ")
    if f[0] == "t" and len(f) == 4:
        h = None if f[3] == "n" else int(f[3]) + z0 + base
        return (int(f[1]), int(f[2])), SolidCol(z0, h, None, False, now)
    if f[0] == "c" and len(f) == 6:
        h = None if f[3] == "n" else int(f[3]) + z0 + base
        raw = f[5].split("/")
        if len(raw) != 16:
            raise ValueError(f"подколонн {len(raw)}, нужно 16")
        subs: list[list[Span]] = []
        prev: list[Span] = []
        for s in raw:
            cur = prev if s == "=" else parse_spans(s, z0, base)
            subs.append(cur)
            prev = cur
        return (int(f[1]), int(f[2])), SolidCol(z0, h, subs, f[4] == "1", now)
    return None


class SolidPlanner:
    def __init__(self, radius_m: float = 8.0, rescan_dz_m: float = 4.0, door_radius_m: float = 5.0,
                 door_every_s: float = 0.5, lead_s: float = 0.4, lead_max_m: float = 3.0, y_offset: int = 0) -> None:
        self.radius = radius_m
        self.rescan_dz = round(rescan_dz_m * 100)
        self.door_r2 = door_radius_m * door_radius_m
        self.door_every = door_every_s
        self.lead_s = lead_s
        self.lead_max = lead_max_m
        self.base = 100 * y_offset
        r = int(math.ceil(radius_m)) + 1
        self.offsets: list[Col] = sorted(((dx, dz) for dx in range(-r, r + 1) for dz in range(-r, r + 1)),
                                         key=lambda o: (o[0] * o[0] + o[1] * o[1], o))
        self.reset()

    def reset(self) -> None:
        """Новый мир Ведьмака: всё забыть."""
        self.cols: dict[Col, SolidCol] = {}
        self.pending: set[Col] = set()
        self.client_reset()

    def client_reset(self) -> None:
        self.client_sent: set[Col] = set()

    # -- вопросы игре
    def _stale(self, col: Col, c: SolidCol, feet_cm: int, now: float, px: float, pz: float) -> bool:
        if abs(c.z0 - feet_cm) > self.rescan_dz:
            return True                          # игрок ушёл по высоте: окно ответа его уже не покрывает
        if c.door and now - c.t >= self.door_every:
            dx, dz = col[0] + 0.5 - px, col[1] + 0.5 - pz
            return dx * dx + dz * dz <= self.door_r2
        return False

    def want(self, pos: tuple[float, float, float], vel: tuple[float, float], n: int, now: float) -> list[Col]:
        """До n колонн без ответа или устаревших, ближние к точке впереди игрока (pos + vel * lead_s) первыми."""
        px, py, pz = pos
        lx, lz = vel[0] * self.lead_s, vel[1] * self.lead_s
        d = math.hypot(lx, lz)
        if d > self.lead_max:
            lx, lz = lx * self.lead_max / d, lz * self.lead_max / d
        cx, cz = math.floor(px + lx), math.floor(pz + lz)
        feet = round(py * 100) - self.base
        r2 = self.radius * self.radius
        out: list[Col] = []
        for dx, dz in self.offsets:
            col = (cx + dx, cz + dz)
            ex, ez = col[0] + 0.5 - px, col[1] + 0.5 - pz          # радиус — от самого игрока
            if ex * ex + ez * ez > r2 or col in self.pending:
                continue
            c = self.cols.get(col)
            if c is not None and not self._stale(col, c, feet, now, px, pz):
                continue
            out.append(col)
            self.pending.add(col)
            if len(out) >= n:
                break
        return out

    def on_fail(self, cols: list[Col]) -> None:
        for col in cols:
            self.pending.discard(col)

    def on_reply(self, asked: list[Col], z0: int, lines: list[str], now: float) -> int:
        """Ответ mcw_cols. -> сколько колонн записано; не отвеченные (stop, битые) снова ждут вопроса."""
        got = 0
        for line in lines:
            try:
                r = parse_line(line, z0, self.base, now)
            except (ValueError, IndexError):
                continue
            if r is None:
                continue
            col, c = r
            old = self.cols.get(col)
            self.cols[col] = c
            self.pending.discard(col)
            if old is None or old.h != c.h or old.subs != c.subs:
                self.client_sent.discard(col)
            got += 1
        for col in asked:
            self.pending.discard(col)
        return got

    # -- клиенту
    def take_client(self, pos: tuple[float, float, float], max_cols: int) -> list[list]:
        """Новые и изменившиеся колонны (ближние первыми) -> c для solid."""
        px, _, pz = pos
        cx, cz = math.floor(px), math.floor(pz)
        out: list[list] = []
        for dx, dz in self.offsets:
            col = (cx + dx, cz + dz)
            c = self.cols.get(col)
            if c is None or col in self.client_sent:
                continue
            subs = None if c.subs is None else [[v for lo, hi in sub for v in (lo, hi)] for sub in c.subs]
            out.append([col[0], col[1], c.h, 1 if c.door else 0, subs])
            self.client_sent.add(col)
            if len(out) >= max_cols:
                break
        return out

    # -- сам мост
    def ground_at(self, col: Col) -> Optional[int]:
        """Самая нижняя поверхность колонны (y MC, см): земля или верх промежутка «до земли». None — не знаем."""
        c = self.cols.get(col)
        if c is None:
            return None
        if c.subs is None:
            return c.h
        tops = [sub[-1][1] for sub in c.subs if sub and sub[-1][0] is None]
        return min(tops) if tops else c.h

    def trim(self, pos: tuple[float, float, float], keep_m: float) -> int:
        """Забыть колонны дальше keep_m (клиенту — не нужно: у него своё окно). -> сколько забыто."""
        px, _, pz = pos
        lim = keep_m * keep_m
        far = [c for c in self.cols if (c[0] + 0.5 - px) ** 2 + (c[1] + 0.5 - pz) ** 2 > lim]
        for c in far:
            del self.cols[c]
            self.client_sent.discard(c)
        return len(far)
