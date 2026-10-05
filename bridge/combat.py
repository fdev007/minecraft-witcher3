"""Бой в режиме Стива (docs/DEVELOPMENT.md «Что дальше», п. 4; схема SkyCraft) и стрелы MC в Ведьмаке. Только данные и пересчёт.

NPC Ведьмака (mcw_npcs, mcw_combat.ws) -> прокси на сервере MC (npcs{l}): клиент MC целится и бьёт в них как в мобов,
урон считает Майнкрафт. Удар по прокси приходит от мода (npc_hit) -> mcw_npchit: доля максимального здоровья NPC.
Удар NPC по Геральту Ведьмак обнуляет и отдаёт строкой h в ответе mcw_npcs -> phurt{id, dmg} серверу: урон по игроку
MC от прокси этого NPC (щит, броня, отбрасывание — ванильные).

Шкала: удар MC по NPC — удар Геральта его мечом (урон, броня и сопротивления — по Ведьмаку), умноженный на урон MC /
ref_mc_dmg (6 — полный удар железным мечом): гусь падает с одного удара, бандит — как от Геральта. Меча у Геральта
нет — у каждого NPC npc_mc_hp здоровья MC (удар в 6 снимает 6/20 максимума). Удар NPC — доля максимума жизненной силы Геральта, x20 (здоровье игрока MC) x hurt_mult.
Координаты — только через spike/coords.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import spike_path  # noqa: F401
import coords

MC_PLAYER_HP = 20.0
ARROW_SLOTS = 8               # у мода MCWA_Slots()


@dataclass
class Npc:
    id: int
    p: tuple[float, float, float]      # ноги, координаты Майнкрафта
    yaw: float                         # курс MC
    w: float                           # ширина (2 радиуса капсулы), блоки
    h: float                           # рост капсулы, блоки
    flags: int                         # 1 враждебен, 2 человек, 4 чудовище, 8 животное
    v: tuple[float, float, float] = (0.0, 0.0, 0.0)   # скорость, блоки/с (NpcTracker)


def parse_npcs(body: list[str], y_offset: int = coords.Y_OFFSET
               ) -> tuple[list[Npc], list[tuple[int, float]], Optional[float]]:
    """Строки ответа mcw_npcs -> (NPC, удары по Геральту [(id, доля его максимума)], время снимка в с или None —
    старый мод без строки t). Битые строки пропускаются."""
    npcs: list[Npc] = []
    hurts: list[tuple[int, float]] = []
    t: Optional[float] = None
    for ln in body:
        parts = ln.split()
        if not parts:
            continue
        try:
            v = [int(x) for x in parts[1:]]
        except ValueError:
            continue
        if parts[0] == "n" and len(v) == 8:
            i, x, y, z, heading, rad, h, flags = v
            p = coords.w_to_mc_pos(x / 100.0, y / 100.0, z / 100.0, y_offset)
            npcs.append(Npc(i, p, coords.w_yaw_to_mc(heading), max(0.2, 2 * rad / 100.0), max(0.3, h / 100.0), flags))
        elif parts[0] == "h" and len(v) == 2 and v[1] > 0:
            hurts.append((v[0], v[1] / 10000.0))
        elif parts[0] == "t" and len(v) == 1:
            t = v[0] / 1000.0
    return npcs, hurts, t


class NpcTracker:
    """Скорость NPC по двум снимкам mcw_npcs (время — движка Ведьмака из строки t, не моста: RTT exec скачет).
    Сглаживание — половина на половину с прошлой; снимков дальше 0,5 с — скорость 0 (NPC только что появился или
    игра стояла). Нужна, чтобы прокси на сервере и у клиента шли туда, где NPC сейчас, а не где был при снимке."""

    MAX_SPEED = 25.0

    def __init__(self) -> None:
        self.last: dict[int, tuple[tuple[float, float, float], float, tuple[float, float, float]]] = {}

    def update(self, npcs: list[Npc], t: float) -> None:
        out = {}
        for n in npcs:
            prev = self.last.get(n.id)
            v = (0.0, 0.0, 0.0)
            if prev is not None and 0.0 < t - prev[1] <= 0.5:
                dt = t - prev[1]
                raw = tuple((n.p[a] - prev[0][a]) / dt for a in range(3))
                v = tuple(0.5 * raw[a] + 0.5 * prev[2][a] for a in range(3))
                sp = sum(c * c for c in v) ** 0.5
                if sp > self.MAX_SPEED:
                    v = tuple(c * self.MAX_SPEED / sp for c in v)
            elif prev is not None and t == prev[1]:
                v = prev[2]
            n.v = v  # type: ignore[assignment]
            out[n.id] = (n.p, t, n.v)
        self.last = out


def npcs_msg(npcs: list[Npc]) -> dict:
    """npcs{l:[[id, x, y, z, курс, ширина, рост, flags, vx, vy, vz], ...]} серверу (Npcs.update)."""
    return {"t": "npcs", "l": [[n.id, round(n.p[0], 3), round(n.p[1], 3), round(n.p[2], 3), round(n.yaw, 1), round(n.w, 2),
                                round(n.h, 2), n.flags, round(n.v[0], 3), round(n.v[1], 3), round(n.v[2], 3)] for n in npcs]}


def client_npcs_msg(npcs: list[Npc], ents: dict[int, int]) -> dict:
    """npcs{l:[[сущность MC, x, y, z, vx, vy, vz, ширина, рост], ...]} клиенту (NpcProxies): только те, чей прокси
    сервер уже назвал (npc_map)."""
    return {"t": "npcs", "l": [[ents[n.id], round(n.p[0], 3), round(n.p[1], 3), round(n.p[2], 3), round(n.v[0], 3), round(n.v[1], 3),
                                round(n.v[2], 3), round(n.w, 2), round(n.h, 2)] for n in npcs if n.id in ents]}


def path_cmds(pts: list, y_offset: int = coords.Y_OFFSET) -> Optional[str]:
    """arrow_path{p} мода (точки пути стрелы, координаты MC) -> аргумент mcw_path "x:y:z|..." (мировые см)."""
    out = []
    try:
        for p in pts:
            w = coords.mc_pos_to_w(float(p[0]), float(p[1]), float(p[2]), y_offset)
            out.append(":".join(str(coords.to_cm(c)) for c in w))
    except (TypeError, ValueError, IndexError):
        return None
    return "|".join(out) if len(out) >= 2 else None


def wall_point(tokens: Optional[list[str]], y_offset: int = coords.Y_OFFSET) -> tuple[int, Optional[list[float]]]:
    """Ответ mcw_path ("path <i> <x> <y> <z>" см | "path -1") -> (i, точка в координатах MC или None)."""
    if not tokens:
        return -1, None
    try:
        v = [int(x) for x in tokens]
    except ValueError:
        return -1, None
    if v[0] < 0 or len(v) < 4:
        return -1, None
    p = coords.w_to_mc_pos(v[1] / 100.0, v[2] / 100.0, v[3] / 100.0, y_offset)
    return v[0], [round(c, 3) for c in p]


def hurt_dmg(frac: float, mult: float) -> float:
    """Доля жизненной силы Геральта -> урон игроку MC (до брони и щита)."""
    return frac * MC_PLAYER_HP * mult


def hit_args(m: dict, npc_mc_hp: float, heavy_dmg: float, ref_dmg: float = 6.0
             ) -> Optional[tuple[int, int, int, int, int]]:
    """npc_hit{id, dmg, kind, crit} мода -> аргументы mcw_npchit (id, доля x10000, heavy, ranged, сила x1000); None —
    нечем бить. Сила — урон MC против ref_dmg (полный удар железным мечом): Ведьмак бьёт ударом Геральта его мечом,
    умноженным на неё; доля здоровья (dmg / npc_mc_hp) — только если меча у Геральта нет."""
    try:
        i, dmg = int(m["id"]), float(m["dmg"])
    except (KeyError, TypeError, ValueError):
        return None
    if not dmg > 0.0 or npc_mc_hp <= 0:
        return None
    frac = min(1.0, dmg / npc_mc_hp)
    heavy = 1 if m.get("crit") or dmg >= heavy_dmg else 0
    ranged = 1 if m.get("kind") == "arrow" else 0
    return i, max(1, round(frac * 10000)), heavy, ranged, max(1, round(dmg / max(0.1, ref_dmg) * 1000))


class ArrowSlots:
    """Стрелы клиента MC (arrows{a}) -> слоты пула стрел Ведьмака (mcwa_arrows). Стрела держит слот, пока она в
    списке клиента; пропала — "<слот>:off". Воткнувшаяся и не сдвинувшаяся стрела повторно не шлётся."""

    def __init__(self, slots: int = ARROW_SLOTS) -> None:
        self.slots = slots
        self.reset()

    def reset(self) -> None:
        self.by_id: dict[int, int] = {}
        self.sent: dict[int, str] = {}           # слот -> последняя команда

    def ops(self, arrows: list, y_offset: int = coords.Y_OFFSET) -> tuple[list[str], int]:
        """-> (команды mcwa_arrows, битых записей). Позиция и скорость — см и см/с, направление x1000, оси Ведьмака."""
        out: list[str] = []
        bad = 0
        seen: set[int] = set()
        for a in arrows:
            try:
                i, x, y, z, dx, dy, dz, vx, vy, vz, flags = a
                i, flags = int(i), int(flags)
                wx, wy, wz = coords.mc_pos_to_w(float(x), float(y), float(z), y_offset)
                # MC (x, y вверх, z на юг) -> Ведьмак (x, y на север, z вверх): (x, -z, y)
                d = (round(float(dx) * 1000), round(-float(dz) * 1000), round(float(dy) * 1000))
                v = (coords.to_cm(float(vx)), coords.to_cm(-float(vz)), coords.to_cm(float(vy)))
            except (TypeError, ValueError):
                bad += 1
                continue
            slot = self.by_id.get(i)
            if slot is None:
                free = sorted(set(range(self.slots)) - set(self.by_id.values()))
                if not free:
                    continue
                slot = self.by_id[i] = free[0]
            seen.add(i)
            fly = 0 if flags & 1 else 1
            cmd = ":".join(str(k) for k in (slot, coords.to_cm(wx), coords.to_cm(wy), coords.to_cm(wz), *d, *v, fly))
            if fly or self.sent.get(slot) != cmd:
                out.append(cmd)
                self.sent[slot] = cmd
        for i in [k for k in self.by_id if k not in seen]:
            slot = self.by_id.pop(i)
            self.sent.pop(slot, None)
            out.append(f"{slot}:off")
        return out, bad
