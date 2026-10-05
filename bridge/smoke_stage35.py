"""Сквозной прогон этапов 3 и 5: настоящий Fabric-сервер из mc\\ + фейковая игра spike/fake_w3.py + bridge.py, клавиши —
подменой GetAsyncKeyState, камера — полями фейка (в настоящие окна ничего не шлём).

    python smoke_stage35.py --start --stop      # бэкап mc\\run\\world, сервер, прогон, stop, мир из бэкапа

Ведьмак — только фейк на 127.0.0.1:37102 (--w3-port, не ниже 37101: на 37000/37001 настоящая игра). Сервер — только
mc\\run\\ и только 127.0.0.1. Итог — таблица в консоль и logs\\smoke35_result.json, журнал моста —
logs\\smoke35_bridge.log.

Сценарий (этап 3): дуб из 4 брёвен в луче камеры -> F2 и 8 трижды (наклон камеры 15°, 5°, −5°) -> брёвна сломаны, дроп
лежит на barrier-земле (поток ent, счётчик в mcw_notify) -> Геральт подходит -> подобрал (inv) -> F3, PageDown, 0:
доски x3, кирка без верстака (no_table), верстак, палки -> NumPad-слот и MOUSE5: верстак на земле -> кирка у верстака.
Этап 5: F6 -> крипер в 6 м перед Геральтом -> плейсхолдер в фейке (mcw_ents) идёт к Геральту -> шипит -> Геральт
отступает на 2 м -> взрыв: explosion -> mcw_boomat, земля рядом снесена (blocks), hp упал (или death) -> плейсхолдер снят.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

import spike_path  # noqa: F401
import coords
import fake_w3
import w3net

import bridge as bridge_mod
import config
import hotkeys
from smoke_bridge import LOGS, MC, RUN, fb, start_server, wait_pid, wait_until

LOG = LOGS / "smoke35_bridge.log"
OUT = LOGS / "smoke35_result.json"
# Ведьмак (−300,5; −339,5; 70) м -> MC (−300,5; 70; 340,5): своя область, в стороне от smoke_bridge.py
G = (-300.5, 70.0, 340.5)
TREE = [(-304, y, 340) for y in (70, 71, 72, 73)]        # в луче камеры: 3,5 м на запад от Геральта
ARENA = (-300.5, 70.0, 360.5)                            # крипер: отдельная площадка в 20 м
VICTIMS = [(x, 70, 358) for x in (-303, -302, -301, -300, -299)] + [(x, 70, 363) for x in (-303, -302, -301, -300, -299)]
AREA = "-316 60 324 -284 80 376"
CRATE = coords.by_batch_t(coords.load_templates())[0]


def w_cm(p):
    """Позиция MC (ноги Геральта) -> [x, y, z] см Ведьмака для fake_w3.world.player."""
    w = coords.mc_pos_to_w(*p, y_offset=0)
    return [coords.to_cm(w[0]), coords.to_cm(w[1]), coords.to_cm(w[2])]


def origin(cell):
    return tuple(int(v) for v in coords.set_op(*cell, CRATE).split(":")[1:4])


class Smoke:
    def __init__(self, w3_port: int) -> None:
        self.w3_port = w3_port
        self.rows: list[tuple[str, bool, str]] = []
        self.metrics: dict = {}
        self.down: set[int] = set()
        self.fw = None
        self.br = None
        self.th = None
        self.dbg = None

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.rows.append((name, bool(ok), detail))
        print(f"[{'ok' if ok else 'FAIL'}] {name}: {detail}", flush=True)
        return ok

    # -- стенд
    def cmd(self, c: str) -> str:
        for _ in range(10):
            out = self.dbg.cmd(c, timeout=10)
            if "not loaded" not in out:
                return out
            time.sleep(0.5)
        return out

    def block(self, cell, name: str) -> bool:
        return "passed" in self.cmd(f"execute if block {cell[0]} {cell[1]} {cell[2]} minecraft:{name}").lower()

    def press(self, name: str) -> float:
        t = time.perf_counter()
        self.down.add(hotkeys.VK[name])
        time.sleep(0.05)
        self.down.discard(hotkeys.VK[name])
        time.sleep(0.04)
        return t

    def has(self, cell) -> bool:
        o = origin(cell)
        return any((e.x, e.y, e.z) == o and "MCW_BLOCK" in e.tags for e in list(self.fw.world.ents.values()))

    def move_geralt(self, p, settle: float = 0.6) -> None:
        self.fw.world.player = w_cm(p)
        time.sleep(settle)                                  # опрос 15 Гц -> pos моду

    def notes(self) -> list[str]:
        return list(self.fw.world.notifications)

    def inv(self) -> dict:
        return dict(self.br.inv_counts or {})

    # -- шаги
    def setup(self) -> None:
        self.dbg = fb.connect(wait=10.0)
        self.dbg.send({"t": "pos", "seq": 1, "p": list(G), "yaw": 90.0})
        time.sleep(1.5)
        for c in ("time set day", "gamerule minecraft:advance_time false", "gamemode survival Geralt", "clear Geralt",
                  "effect clear Geralt", f"fill {AREA} minecraft:air", "kill @e[type=minecraft:item]",
                  "kill @e[type=minecraft:creeper]", "fill -304 70 340 -304 73 340 minecraft:oak_log"):
            self.cmd(c)
        self.dbg.send({"t": "ground_clear", "box": [-316, 324, -284, 376]})
        self.dbg.wait(lambda m: m.get("t") == "ground_ok", 3)
        ok = all(self.block(c, "oak_log") for c in TREE) and self.block((-305, 70, 340), "air")
        self.check("подготовка области", ok, f"дуб {TREE[0]}..{TREE[-1]}, область {AREA} очищена, инвентарь Геральта пуст")

    def start(self) -> None:
        self.fw = fake_w3.FakeWitcher("127.0.0.1", self.w3_port, frame_time=1 / 60)
        self.fw.world.player = w_cm(G)
        self.fw.start()
        cfg = config.load(None, {"w3": {"port": self.w3_port}, "log": {"file": str(LOG), "metrics_every_s": 5.0},
                                 "supervise": {"enabled": False}})      # сервер и клиент смоука — не сторожа
        self.br = bridge_mod.Bridge(cfg, key_down=lambda vk: vk in self.down, foreground=lambda: True)
        self.th = threading.Thread(target=self.br.run, name="bridge", daemon=True)
        self.th.start()
        ok = wait_until(lambda: all(self.has(c) for c in TREE) and self.br.snapshot.get("pending_ops") == 0, 30)
        top = wait_until(lambda: self.br.ground.top_at(-305, 340) == 69 and self.br.ground.top_at(-301, 340) == 69, 20)
        self.check("мост: снапшот, дуб в фейке, рельеф", ok is not None and top is not None,
                   f"4 бревна в фейке через {None if ok is None else round(ok, 2)} с; рельеф у дуба и под Геральтом известен "
                   f"через {None if top is None else round(top, 2)} с")

    def chop(self) -> None:
        self.press("F2")
        wait_until(lambda: self.br.build_mode, 2)
        took = []
        for cell, pitch in zip(TREE[:3], (15.0, 5.0, -5.0)):
            self.fw.world.pitch_deg = pitch
            time.sleep(0.4)                                 # камера доходит до моста опросом
            t = self.press("8")
            dt = wait_until(lambda: not self.has(cell), 10)
            took.append(None if dt is None else round((time.perf_counter() - t) * 1000))
        self.fw.world.pitch_deg = 15.0
        air = all(self.block(c, "air") for c in TREE[:3])
        self.metrics["chop_ms"] = took
        drops = wait_until(lambda: sum(e.count for e in self.br.ents.ents.values() if e.item == "minecraft:oak_log") >= 3, 5)
        items = [(e.id, e.item, e.count, [round(v, 2) for v in e.p]) for e in self.br.ents.ents.values() if e.type == "minecraft:item"]
        self.metrics["drops"] = items
        note = wait_until(lambda: any("on the ground" in n and "oak_log" in n for n in self.notes()), 3)
        self.check("8 x3 -> брёвна сломаны в MC и в фейке, дроп на земле в потоке ent",
                   all(took) and air and drops is not None and note is not None,
                   f"от нажатия до исчезновения в фейке {took} мс (рукой 60 тиков на бревно); в MC воздух {air}; "
                   f"предметы в ent: {items}; уведомление про дроп: {note is not None}")

    def pickup(self) -> None:
        t0 = time.perf_counter()
        self.move_geralt((-303.0, 70.0, 340.5), settle=0.0)
        dt = wait_until(lambda: self.inv().get("minecraft:oak_log", 0) >= 3, 8)
        gone = wait_until(lambda: not any(e.type == "minecraft:item" for e in self.br.ents.ents.values()), 3)
        self.metrics["pickup_s"] = None if dt is None else round(dt, 2)
        self.check("Геральт подошёл -> подобрал брёвна (inv), предметы ушли из ent", dt is not None and gone is not None,
                   f"от сдвига в фейке до inv с 3 oak_log {self.metrics['pickup_s']} с; инвентарь {self.inv()}; "
                   f"remove в ent: {gone is not None}; прошло {round(time.perf_counter() - t0, 2)} с")

    # крафт клавишами
    def craft_ready(self) -> bool:
        c = self.br.craft
        return c.open and c.req_id is None and not c.stale and bool(c.items)

    def select_recipe(self, rid: str) -> bool:
        if not wait_until(self.craft_ready, 5):
            return False
        c = self.br.craft
        ids = [r.id for r in c.items]
        if rid not in ids:
            return False
        for _ in range(len(ids) + 1):
            if c.current() is not None and c.current().id == rid:
                return True
            i0 = c.i
            self.press("PAGEDOWN")
            wait_until(lambda: c.i != i0, 1)
        return c.current() is not None and c.current().id == rid

    def craft(self, rid: str, times: int = 1) -> list:
        acks = []
        for _ in range(times):
            if not self.select_recipe(rid):
                acks.append(None)
                continue
            n0 = self.br.stats["craft_ok"] + self.br.stats["craft_fail"]
            seen = len(self.fw.world.notifications)
            t = self.press("0")
            dt = wait_until(lambda: self.br.stats["craft_ok"] + self.br.stats["craft_fail"] > n0, 5)
            acks.append(None if dt is None else round((time.perf_counter() - t) * 1000))
            # как человек: дождаться, пока итог покажется в игре (mcw_notify не чаще notify.min_interval_s)
            item = rid.split(":", 1)[1]
            wait_until(lambda: any(item in n and ("crafted" in n or "cannot craft" in n)
                                   for n in self.fw.world.notifications[seen:]), 4)
            wait_until(self.craft_ready, 3)
        return acks

    def crafting(self) -> None:
        t = self.press("F3")
        listed = wait_until(lambda: self.craft_ready() and any("MCW craft" in n for n in self.notes()), 5)
        self.metrics["recipes_ms"] = None if listed is None else round((time.perf_counter() - t) * 1000)
        ids = [r.id for r in self.br.craft.items]
        self.check("F3 -> список крафта из инвентаря (recipes) в mcw_notify", listed is not None and "minecraft:oak_planks" in ids,
                   f"{self.metrics['recipes_ms']} мс от нажатия; рецептов {len(ids)}: {ids[:6]}; "
                   f"уведомление {[n for n in self.notes() if 'MCW craft' in n][-1:]}")
        lat = self.craft("minecraft:oak_planks", 3)
        planks = self.inv().get("minecraft:oak_planks", 0)
        self.metrics["craft_planks_ms"] = lat
        self.check("PageDown + 0 x3 -> доски 2x2 (12 шт.)", all(lat) and planks == 12 and not self.inv().get("minecraft:oak_log"),
                   f"от нажатия до ack {lat} мс; доски {planks}, бревна {self.inv().get('minecraft:oak_log', 0)}")
        lat3 = self.craft("minecraft:stick", 1)
        listed = wait_until(lambda: self.craft_ready() and any(r.id == "minecraft:wooden_pickaxe" and r.grid == 3
                                                             for r in self.br.craft.items), 3)
        fail0 = self.br.stats["craft_fail"]
        lat2 = self.craft("minecraft:wooden_pickaxe", 1)
        no_table = self.br.stats["craft_fail"] == fail0 + 1 and any("needs a crafting table" in n for n in self.notes())
        self.check("палки; кирка в списке как 3x3 (needs table) -> без верстака ack no_table, в notify",
                   all(lat3 + lat2) and listed is not None and no_table,
                   f"палки {lat3} мс, кирка {lat2} мс; уведомления {[n for n in self.notes() if 'needs table' in n or 'cannot craft' in n][-2:]}")
        lat3 = self.craft("minecraft:crafting_table", 1)
        inv = self.inv()
        self.check("верстак 2x2", all(lat3) and inv.get("minecraft:crafting_table") == 1
                   and inv.get("minecraft:stick") == 4 and inv.get("minecraft:oak_planks") == 6,
                   f"{lat3} мс; инвентарь {inv}")

    def place_table(self) -> None:
        slot = next((k for k, (item, _) in self.br.inv_slots.items() if item == "minecraft:crafting_table"), None)
        ok_slot = slot is not None and slot < 9
        if ok_slot:
            self.press(f"NUMPAD{slot + 1}")
            wait_until(lambda: self.br.slot == slot, 2)
        self.fw.world.pitch_deg = 15.0
        time.sleep(0.4)
        before = {k for k, v in self.br.mirror.by_chunk.get((-307 >> 4, 340 >> 4), {}).items() if "crafting_table" in v}
        t = self.press("MOUSE5")
        cell = None

        def placed():
            nonlocal cell
            for ch in list(self.br.mirror.by_chunk.values()):
                for k, v in list(ch.items()):
                    if v.startswith("minecraft:crafting_table") and k not in before:
                        cell = k
                        return self.has(k)
            return False

        dt = wait_until(placed, 6)
        in_mc = cell is not None and self.block(cell, "crafting_table")
        self.metrics["table_cell"] = cell
        self.check("NumPad-слот + MOUSE5 -> верстак на земле в MC и в фейке", ok_slot and dt is not None and in_mc,
                   f"слот хотбара {slot}, выбран сервером: {self.br.slot == slot}; верстак в {cell} через "
                   f"{None if dt is None else round((time.perf_counter() - t) * 1000)} мс; в MC: {in_mc}")
        lat = self.craft("minecraft:wooden_pickaxe", 1)
        inv = self.inv()
        table_flag = self.br.craft.table
        self.check("кирка у верстака (3x3) -> wooden_pickaxe", all(lat) and inv.get("minecraft:wooden_pickaxe") == 1
                   and inv.get("minecraft:stick") == 2 and inv.get("minecraft:oak_planks") == 3,
                   f"{lat} мс от нажатия до ack; верстак в recipes.table {table_flag}; инвентарь {inv}")

    def creeper(self) -> None:
        self.press("F3")                                          # закрыть список
        for c in VICTIMS:
            self.cmd(f"setblock {c[0]} {c[1]} {c[2]} minecraft:dirt")
        self.move_geralt(ARENA)
        ground = wait_until(lambda: all(self.br.ground.top_at(x, 360) == 69 for x in range(-308, -299))
                            and all(self.has(c) for c in VICTIMS), 30)
        barrier = wait_until(lambda: self.block((-307, 69, 360), "barrier") and self.block((-303, 69, 360), "barrier"), 10, 0.2)
        barrier = barrier is not None
        self.check("площадка: рельеф (barrier) на пути крипера, земля рядом в фейке", ground is not None and barrier,
                   f"{None if ground is None else round(ground, 2)} с; barrier под точкой призыва {barrier}")
        hp0 = self.br.last_hp
        deaths0 = self.br.stats.get("deaths", 0)
        t0 = self.press("F6")
        mob = wait_until(lambda: len(self.fw.mobs()) == 1, 5)
        t_mob = time.perf_counter()
        cre = [e for e in self.br.ents.ents.values() if e.type == "minecraft:creeper"]
        slot, ent = next(iter(self.fw.mobs().items())) if self.fw.mobs() else (None, None)
        start = (ent.x, ent.y, ent.z) if ent else None
        self.check("F6 -> spawn -> крипер в ent -> плейсхолдер MCW_ENT в фейке", mob is not None and len(cre) == 1,
                   f"{None if mob is None else round((t_mob - t0) * 1000)} мс от нажатия; крипер в MC "
                   f"{[round(v, 2) for v in cre[0].p] if cre else None}; слот {slot}, origin {start} см, теги "
                   f"{sorted(ent.tags) if ent else None}")
        track = []
        fuse = None
        end = time.perf_counter() + 25
        while time.perf_counter() < end:
            if ent is not None:
                track.append((round(time.perf_counter() - t_mob, 2), ent.x, ent.y, ent.z))
            if any(e.fuse for e in self.br.ents.ents.values() if e.type == "minecraft:creeper"):
                fuse = time.perf_counter()
                break
            time.sleep(0.1)
        moved = math.dist(track[0][1:], track[-1][1:]) / 100.0 if len(track) > 1 else 0.0
        cre = [e for e in self.br.ents.ents.values() if e.type == "minecraft:creeper"]
        gap = math.dist(cre[0].p, ARENA) if cre else None
        self.metrics["creeper"] = {"approach_s": None if fuse is None else round(fuse - t_mob, 2), "moved_m": round(moved, 2),
                                   "moves": ent.moves if ent else 0, "gap_at_fuse_m": None if gap is None else round(gap, 2)}
        hiss = wait_until(lambda: any("hissing" in n for n in self.notes()), 3)
        self.check("плейсхолдер идёт к Геральту (mcw_ents move), крипер шипит (fuse -> notify)",
                   fuse is not None and moved > 1.5 and hiss is not None,
                   f"шипит через {self.metrics['creeper']['approach_s']} с, плейсхолдер прошёл {round(moved, 2)} м за "
                   f"{ent.moves if ent else 0} move, до Геральта {self.metrics['creeper']['gap_at_fuse_m']} м; "
                   f"уведомление {hiss is not None}")
        # Геральт отступает на 4 м на восток: шипящий крипер идёт следом (ваниль), но взрыв дальше — урон меньше
        self.move_geralt((ARENA[0] + 4.0, ARENA[1], ARENA[2]), settle=0.0)
        booms0 = sum(1 for c in self.fw.execs_seen if c.startswith("mcw_boomat("))
        boom = wait_until(lambda: sum(1 for c in self.fw.execs_seen if c.startswith("mcw_boomat(")) > booms0, 12)
        t_boom = time.perf_counter()
        gone = wait_until(lambda: not self.fw.mobs(), 3)
        cleared = [c for c in VICTIMS if not self.block(c, "dirt")]
        fake_gone = wait_until(lambda: all(not self.has(c) for c in cleared), 5)
        hurt = wait_until(lambda: (self.br.last_hp is not None and self.br.last_hp < 20.0)
                          or self.br.stats.get("deaths", 0) > deaths0, 3)
        barrel = [(e.x, e.y, e.z) for e in list(self.fw.world.ents.values()) if e.on_fire]
        died = self.br.stats.get("deaths", 0) - deaths0
        at = coords.w_to_mc_pos(*(v / 100.0 for v in barrel[-1]), y_offset=0) if barrel else None
        self.metrics["explosion"] = {"fuse_to_boomat_s": None if boom is None or fuse is None else round(t_boom - fuse, 2),
                                     "cleared": len(cleared), "victims": len(VICTIMS), "hp_before": hp0,
                                     "hp_after": self.br.last_hp, "deaths": died, "barrel": barrel[-1:],
                                     "boom_to_geralt_m": None if at is None else round(math.dist(at[::2], (ARENA[0] + 4.0, ARENA[2])), 2)}
        self.check("взрыв -> mcw_boomat, земля снесена в MC и в фейке, hp Геральта упал, плейсхолдер снят",
                   boom is not None and gone is not None and len(cleared) >= 2 and fake_gone is not None and hurt is not None,
                   f"от шипения до mcw_boomat {self.metrics['explosion']['fuse_to_boomat_s']} с (фитиль 30 тиков); бочка "
                   f"{barrel[-1:]}; снесено {len(cleared)} из {len(VICTIMS)} блоков земли, в фейке тоже: "
                   f"{fake_gone is not None}; hp {hp0} -> {self.br.last_hp}, смертей {died}; взрыв в "
                   f"{self.metrics['explosion']['boom_to_geralt_m']} м от Геральта; MCW_ENT в фейке: {len(self.fw.mobs())}")

    def summary(self) -> None:
        st = dict(self.br.stats)                         # не snapshot: тот обновляется раз в 0,25 с
        self.metrics["bridge"] = {k: st.get(k) for k in ("sent_ents", "ents_spawn", "ents_move", "ents_kill", "ents_wipe",
                                                         "ents_errors", "ents_lost", "mc_ent", "sent_recipes", "act_craft",
                                                         "craft_ok", "craft_fail", "ground_mob_blocked", "resyncs")}
        ents = [c for c in self.fw.execs_seen if c.startswith("mcw_ents(")]
        self.metrics["mcw_ents_max_bytes"] = max((coords.utf8_len(c) for c in ents), default=0)
        self.check("канал: mcw_ents без ошибок, лишних ресинков нет",
                   bool(ents) and not st.get("ents_errors") and not st.get("ents_lost") and not st.get("resyncs"),
                   f"{self.metrics['bridge']}; пакетов mcw_ents {len(ents)}, самый длинный {self.metrics['mcw_ents_max_bytes']} Б")

    def cleanup(self) -> None:
        if self.br is not None:
            self.br.stop()
            self.th.join(5)
        if self.fw is not None:
            self.fw.stop()
        if self.dbg is None:
            return
        try:
            for c in ("kill @e[type=minecraft:creeper]", "kill @e[type=minecraft:item]", f"fill {AREA} minecraft:air",
                      "clear Geralt"):
                self.cmd(c)
            self.dbg.send({"t": "ground_clear", "box": [-340, 300, -260, 400]})
            got = self.dbg.wait(lambda m: m.get("t") == "ground_ok", 3)
            self.metrics["cleanup_ground"] = got[1] if got else None
            self.dbg.send({"t": "pos", "seq": 2, "p": [0.5, 70.0, 0.5], "yaw": 0.0})
            time.sleep(0.3)
        except (OSError, TimeoutError) as e:
            print("уборка:", e)

    def stop_server(self, pid) -> bool:
        how = "уже не работал"
        try:
            self.dbg.send({"t": "cmd", "c": "stop"})
            how = "stop через мод"
        except (OSError, AttributeError):
            pass
        ok = wait_pid(pid, 40) if pid else True
        if pid and not ok:
            import subprocess
            subprocess.run(["powershell", "-NoProfile", "-File", str(MC / "run_server.ps1"), "-Stop"], check=False)
            how = "Stop-Process по PID"
            ok = wait_pid(pid, 15)
        (RUN / "server.pid").unlink(missing_ok=True)
        self.metrics["server_stopped"] = how
        print(f"сервер pid {pid} погашен: {how}")
        return ok


def backup_world() -> Path:
    dst = Path(tempfile.gettempdir()) / "claude" / "mcw_smoke35_world_backup"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(RUN / "world", dst)
    return dst


def restore_world(src: Path) -> str:
    shutil.rmtree(RUN / "world")
    shutil.copytree(src, RUN / "world")
    n = sum(1 for _ in (RUN / "world").rglob("*") if _.is_file())
    shutil.rmtree(src)
    return f"run\\world восстановлен из бэкапа ({n} файлов)"


def main() -> int:
    w3net.utf8_stdio()
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", action="store_true", help="бэкап run\\world и подъём сервера через mc\\run_server.ps1")
    ap.add_argument("--stop", action="store_true", help="в конце погасить сервер (с --start — и вернуть мир из бэкапа)")
    ap.add_argument("--w3-port", type=int, default=37102)
    ap.add_argument("--backup-dir", default=os.environ.get("MCW_SMOKE_BACKUP", ""),
                    help="куда копировать run\\world (по умолчанию %%TEMP%%\\claude\\mcw_smoke35_world_backup)")
    a = ap.parse_args()
    if a.w3_port < 37101:
        raise SystemExit("--w3-port не ниже 37101: 37000/37001 — настоящая игра")
    LOGS.mkdir(exist_ok=True)
    LOG.unlink(missing_ok=True)
    bridge_mod.setup_logging(str(LOG), "INFO", console=False)
    s = Smoke(a.w3_port)
    pid, backup = None, None
    t_all = time.perf_counter()
    try:
        if a.start:
            if (RUN / "server.pid").exists():
                raise SystemExit("run\\server.pid есть: сервер уже запущен, --start не нужен")
            if a.stop:
                if a.backup_dir:
                    backup = Path(a.backup_dir)
                    if backup.exists():
                        shutil.rmtree(backup)
                    shutil.copytree(RUN / "world", backup)
                else:
                    backup = backup_world()
                s.metrics["world_backup"] = str(backup)
            info = start_server()
            pid = info["pid"]
            s.metrics["server"] = info
            s.check("сервер до Done", True, f"pid {pid}, {info['wall_s']} с")
        elif (RUN / "server.pid").exists():
            pid = int((RUN / "server.pid").read_text().strip())
        s.setup()
        s.start()
        s.chop()
        s.pickup()
        s.crafting()
        s.place_table()
        s.creeper()
        s.summary()
    except Exception as e:  # noqa: BLE001 — итог пишем при любом падении
        import traceback
        traceback.print_exc()
        s.check("прогон без исключений", False, repr(e))
    finally:
        s.cleanup()
        if a.stop:
            stopped = s.stop_server(pid)
            if backup is not None:
                if stopped:
                    s.metrics["world_restore"] = restore_world(backup)
                else:
                    s.metrics["world_restore"] = f"сервер не погашен, мир НЕ восстановлен; бэкап в {backup}"
    s.metrics["wall_s"] = round(time.perf_counter() - t_all, 1)
    s.metrics["notifications"] = s.fw.world.notifications[-40:] if s.fw else []
    errors = [ln for ln in LOG.read_text(encoding="utf-8").splitlines() if " ERROR " in ln or "Traceback" in ln] \
        if LOG.exists() else []
    s.check("журнал моста без ERROR", not errors, f"{len(errors)} строк" + (f": {errors[0][:120]}" if errors else ""))
    n_ok = sum(1 for _, ok, _ in s.rows if ok)
    print(f"\nитог {n_ok}/{len(s.rows)}, {s.metrics['wall_s']} с; {s.metrics.get('world_restore', '')}")
    out = {"rows": [{"check": n, "ok": ok, "detail": d} for n, ok, d in s.rows], "metrics": s.metrics,
           "when": time.strftime("%Y-%m-%d %H:%M:%S")}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return 0 if n_ok == len(s.rows) else 1


if __name__ == "__main__":
    if os.name != "nt":
        raise SystemExit("только Windows (mc\\run_server.ps1)")
    sys.exit(main())
