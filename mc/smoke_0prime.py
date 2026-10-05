"""Этап 0': мини-спайк Майнкрафта без Ведьмака — сервер + мод MCW + fake_bridge.

    python smoke_0prime.py --start --stop          # поднять сервер (run_server.ps1), прогнать всё, погасить
    python smoke_0prime.py                         # сервер уже запущен
    python smoke_0prime.py --idle-only --start --stop --pause 60
                                                   # только простой 70 с при ванильной паузе пустого сервера:
                                                   # если Геральт не держит PlayerList, тики встанут

Проверки: hello+снапшот; pos; place/break через act -> blocks set/clear (+dig, ack, отказы); песок над пустотой
падает -> поток blocks; TNT, подожжённый огнивом -> explosion + пачка clear; вода из ведра -> поток blocks;
верстак (used_block / sneak); отладочное соединение не вытесняет мост; snap с меньшим радиусом -> unchunk;
NaN и высота; переподключение -> снапшот совпадает с зеркалом; 70 с без сообщений -> тики идут, песок падает;
смерть x2 с текстом; Нижний мир и обратно -> pteleport с dim, снапшот нового уровня, kill убивает; рестарт
сервера -> ground_clear находит свой рельеф (только с --start). Замеры: время до Done, act->blocks, RTT ping.
Итог: таблица в stdout (markdown) и run/logs/smoke_result.json. Мир только в mc/run, область x -2..32, z -4..4,
плюс 0,200,0 в Нижнем мире.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

import fake_bridge as fb

HERE = Path(__file__).parent
RUN = HERE / "run"
MARK = "minecraft:name_tag"  # перед каждой остановкой у Геральта в инвентаре: проверка playerdata на следующем старте
G = (0.5, 70.0, 0.5)  # где стоит Геральт; блоки опыта — в 1.5-3 м от него
VIEW_DISTANCE = 6     # server.properties.mcw: радиус снапшота мод режет до него


class Smoke:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []
        self.metrics: dict[str, object] = {}
        self.link: fb.Link | None = None
        self.act_id = 100

    # --- общее ---
    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.rows.append((name, bool(ok), detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)
        return ok

    @property
    def l(self) -> fb.Link:  # noqa: E743 — короткое имя: self.l.cmd(...) в каждом шаге
        assert self.link is not None
        return self.link

    def quiet(self, secs: float = 0.6, limit: float = 8.0) -> list[dict]:
        return self.l.drain(secs, limit)

    def pos(self, x: float, y: float, z: float, yaw: float = 0.0) -> None:
        self.l.send({"t": "pos", "seq": 1, "p": [x, y, z], "yaw": yaw, "pitch": 0.0})

    def act(self, a: str, b=None, face="up", slot=None, timeout=10.0, **extra):
        """act -> (ack, все сообщения до ack, время отправки, время ack)."""
        self.act_id += 1
        msg = {"t": "act", "id": self.act_id, "a": a, **extra}
        if b is not None:
            msg["b"] = list(b)
            msg["face"] = face
        if slot is not None:
            msg["slot"] = slot
        seen: list[tuple[float, dict]] = []
        t0 = self.l.send(msg)
        end = t0 + timeout
        while time.perf_counter() < end:
            got = self.l.recv(end - time.perf_counter())
            if got is None:
                break
            seen.append(got)
            if got[1].get("t") == "ack" and got[1].get("id") == self.act_id:
                return got[1], seen, t0, got[0]
        return None, seen, t0, None

    @staticmethod
    def first_blocks(seen, pred) -> tuple[float, dict] | None:
        for t, m in seen:
            if m.get("t") == "blocks" and pred(m):
                return t, m
        return None

    def wait_blocks(self, pred, timeout: float, acc: list | None = None) -> tuple[float, dict] | None:
        return self.l.wait(lambda m: m.get("t") == "blocks" and pred(m), timeout,
                           on_other=(acc.append if acc is not None else None))

    # --- шаги ---
    def connect_hello(self, wait: float, had_playerdata: bool = False) -> None:
        t = time.perf_counter()
        self.link = fb.connect(wait=wait)
        h, e = self.l.hello(radius=4)
        ms = (time.perf_counter() - t) * 1000
        inv = self.l.wait(lambda m: m.get("t") == "inv", 3)
        if had_playerdata:
            items = inv[1]["s"] if inv else []
            self.check("Геральт после рестарта: инвентарь из playerdata", bool(items),  # не пуст: stop_server оставляет MARK
                       json.dumps(items))
        ok = h.get("v") == 1 and h.get("mc") == "1.21.11" and h["player"]["name"] == "Geralt" and e["chunks"] > 0
        self.metrics["snapshot"] = {"chunks": e["chunks"], "blocks": e["blocks"], "ms": round(ms, 1)}
        self.check("hello + снапшот", ok, f"mc {h.get('mc')}, dim {h.get('dim')}, chunks {e['chunks']}, "
                   f"blocks {e['blocks']}, y {h['y']}, {ms:.0f} мс")

    def setup_area(self) -> None:
        for c in ("time set day", "gamemode survival Geralt", "clear Geralt", "kill @e[type=minecraft:item]",
                  "kill @e[type=minecraft:tnt]", "fill -2 55 -4 14 75 4 minecraft:air", "fill 15 55 -4 32 75 4 minecraft:air"):
            out = self.l.cmd(c)
            print(f"   cmd {c!r}: {out[:100]}")
        # свой рельеф переживает рестарт: убрать остатки прошлых прогонов в рабочей области
        self.l.send({"t": "ground_clear", "box": [-2, -4, 32, 4]})
        self.l.wait(lambda m: m.get("t") == "ground_ok", 3)
        self.pos(*G)
        self.quiet(1.0)
        left = [k for k in self.l.mirror.blocks if -2 <= k[0] <= 14 and 55 <= k[1] <= 75 and -4 <= k[2] <= 4]
        self.check("подготовка области (fill air)", not left, f"в зеркале осталось {len(left)}")

    def check_pos(self) -> None:
        self.pos(3.5, 71.0, -1.5, 90.0)
        a = self.l.cmd("data get entity Geralt Pos")
        self.pos(*G)
        b = self.l.cmd("data get entity Geralt Pos")
        ok = "3.5d, 71.0d, -1.5d" in a and "0.5d, 70.0d, 0.5d" in b
        self.check("pos двигает Геральта", ok, f"{a.split(':')[-1].strip()} -> {b.split(':')[-1].strip()}")

    def give_kit(self) -> None:
        for item, n in (("minecraft:stone", 16), ("minecraft:diamond_pickaxe", 1), ("minecraft:sand", 16),
                        ("minecraft:flint_and_steel", 1)):
            self.l.send({"t": "give", "item": item, "n": n})
        want = {0: "minecraft:stone", 1: "minecraft:diamond_pickaxe", 2: "minecraft:sand", 3: "minecraft:flint_and_steel"}
        got = self.l.wait(lambda m: m.get("t") == "inv" and {s[0]: s[1] for s in m["s"]} == want, 5)
        self.check("give -> inv", got is not None, json.dumps(got[1]["s"] if got else None))

    def place_break(self) -> None:
        out = self.l.cmd("setblock 2 70 0 minecraft:stone")
        got = self.wait_blocks(lambda m: (2, 70, 0, "minecraft:stone") in fb.blocks_set(m), 3)
        self.check("cmd setblock -> blocks set", got is not None, out)

        lat_place, lat_ack = [], []
        ok_all = True
        for y in (71, 72, 73, 74):
            ack, seen, t0, ta = self.act("place", (2, y - 1, 0), "up", slot=0)
            hit = self.first_blocks(seen, lambda m: (2, y, 0, "minecraft:stone") in fb.blocks_set(m))
            ok_all &= bool(ack and ack["ok"] and hit)
            if hit:
                lat_place.append((hit[0] - t0) * 1000)
            if ta:
                lat_ack.append((ta - t0) * 1000)
        self.metrics["place_ms"] = [round(v, 1) for v in lat_place]
        self.check("act place -> blocks set + ack ok (x4)", ok_all and len(lat_place) == 4,
                   f"act->blocks {fmt(lat_place)} мс; act->ack {fmt(lat_ack)} мс")

        ack, seen, *_ = self.act("place", (2, 70, 0), "up", slot=0)
        self.check("place в занятое -> ack occupied", bool(ack) and not ack["ok"] and ack.get("why") == "occupied", str(ack))
        ack, seen, *_ = self.act("place", (12, 70, 0), "up", slot=0)
        self.check("place далеко -> ack too_far", bool(ack) and not ack["ok"] and ack.get("why") == "too_far", str(ack))

        lat_break, digs_all = [], []
        ok_all = True
        for y in (74, 73, 72, 71):
            ack, seen, t0, ta = self.act("break", (2, y, 0), "up", slot=1)
            hit = self.first_blocks(seen, lambda m: (2, y, 0) in fb.blocks_clear(m))
            digs = [m["stage"] for _, m in seen if m.get("t") == "dig"]
            digs_all.append(digs)
            ok_all &= bool(ack and ack["ok"] and hit)
            if hit:
                lat_break.append((hit[0] - t0) * 1000)
        self.metrics["break_ms"] = [round(v, 1) for v in lat_break]
        self.metrics["dig_stages"] = digs_all[0] if digs_all else []
        self.check("act break (алмазная кирка, выживание) -> dig + blocks clear + ack ok (x4)", ok_all,
                   f"act->blocks {fmt(lat_break)} мс; стадии dig {digs_all[0] if digs_all else []}")
        items = self.l.cmd("execute if entity @e[type=minecraft:item,x=2,y=70,z=0,distance=..8]")
        self.check("дроп по правилам (камень киркой -> предмет)", "passed" in items.lower(), items)

        # камнем в руке камень копается 150 тиков: начать, дождаться стадии, отменить
        self.act_id += 1
        long_id = self.act_id
        self.l.send({"t": "act", "id": long_id, "a": "break", "b": [2, 70, 0], "face": "up", "slot": 0})
        dig = self.l.wait(lambda m: m.get("t") == "dig" and m.get("id") == long_id, 3)
        cancel, seen2, *_ = self.act("cancel")
        dig_acks = [m for _, m in seen2 if m.get("t") == "ack" and m.get("id") == long_id and m.get("why") == "cancelled"]
        self.check("break долгий -> cancel", bool(dig and cancel and cancel["ok"] and dig_acks) and self.l.mirror.get(2, 70, 0) == "minecraft:stone",
                   f"dig {dig[1] if dig else None}; cancel {cancel}; ack копания {dig_acks[:1]}")

    def ground(self) -> None:
        """Рельеф Ведьмака как barrier: в blocks его нет (мосту не новость), в мире есть; ground_clear убирает свои."""
        self.quiet(0.3)
        before = dict(self.l.mirror.blocks)
        cols = [v for x in range(-1, 2) for z in range(-3, -1) for v in (x, z, 60, 62)]  # 3x2 колонны по 3 блока
        self.l.send({"t": "ground", "c": cols})
        ok1 = self.l.wait(lambda m: m.get("t") == "ground_ok", 3)
        test = self.l.cmd("execute if block 0 61 -2 minecraft:barrier")
        self.quiet(0.4)
        same = self.l.mirror.blocks == before
        self.l.send({"t": "ground_clear", "box": [-1, -3, 1, -2]})
        ok2 = self.l.wait(lambda m: m.get("t") == "ground_ok", 3)
        gone = self.l.cmd("execute if block 0 61 -2 minecraft:air")
        self.check("ground -> barrier в мире, не в blocks; ground_clear", bool(ok1 and ok2 and ok1[1].get("placed") == 18
                   and ok2[1].get("removed") == 18 and "passed" in test.lower() and "passed" in gone.lower() and same),
                   f"{ok1[1] if ok1 else None}; {test}; зеркало без изменений: {same}; {ok2[1] if ok2 else None}")

    def sand(self, catcher_y: int = 60, label: str = "песок над пустотой падает -> поток blocks") -> None:
        self.l.cmd(f"setblock 3 {catcher_y} 0 minecraft:stone")
        self.l.cmd("setblock 2 70 0 minecraft:stone")
        self.quiet(0.4)
        acc: list[dict] = []
        ack, seen, t0, _ = self.act("place", (2, 70, 0), "east", slot=2)
        acc += [m for _, m in seen]
        placed = any((3, 70, 0, "minecraft:sand") in fb.blocks_set(m) for m in acc if m.get("t") == "blocks")
        gone = self.wait_blocks(lambda m: (3, 70, 0) in fb.blocks_clear(m), 3, acc) if placed else None
        landed = None
        if gone:
            landed = self.wait_blocks(lambda m: any(s[0] == 3 and s[2] == 0 and s[1] < 70 and s[3] == "minecraft:sand"
                                                    for s in fb.blocks_set(m)), 5, acc)
        land_y = None
        if landed:
            land_y = [s[1] for s in fb.blocks_set(landed[1]) if s[0] == 3 and s[2] == 0][0]
        stream = ["set y70"] + (["clear y70"] if gone else []) + ([f"set y{land_y}"] if landed else [])
        dt = (landed[0] - t0) * 1000 if landed else None
        self.check(label, bool(ack and ack["ok"] and placed and gone and landed),
                   f"{' -> '.join(stream)}; от act до приземления {dt:.0f} мс" if dt else f"{' -> '.join(stream)}; ack {ack}")

    def tnt(self) -> None:
        # TNT в каменной чаше: основание 5x3x5 и кольцо вокруг, верх TNT открыт для огнива
        self.l.cmd("fill 6 66 -2 10 68 2 minecraft:stone")
        self.l.cmd("fill 7 69 -1 9 69 1 minecraft:stone")
        self.l.cmd("setblock 8 69 0 minecraft:tnt")
        self.quiet(0.5)
        n_platform = sum(1 for k in self.l.mirror.blocks if 6 <= k[0] <= 10 and 66 <= k[1] <= 69 and -2 <= k[2] <= 2)
        self.pos(5.5, 69.0, 0.5, -90.0)
        ack, seen, t0, _ = self.act("place", (8, 69, 0), "up", slot=3)
        primed = self.first_blocks(seen, lambda m: (8, 69, 0) in fb.blocks_clear(m))
        self.pos(-20.5, 70.0, 0.5)  # из радиуса взрыва
        acc: list[dict] = []
        ex = self.l.wait(lambda m: m.get("t") == "explosion", 8, on_other=acc.append)
        clears: list[tuple[int, int, int]] = []
        burst = None
        if ex:
            end = time.perf_counter() + 1.5
            while time.perf_counter() < end:
                got = self.l.recv(end - time.perf_counter())
                if got is None:
                    break
                if got[1].get("t") == "blocks":
                    c = fb.blocks_clear(got[1])
                    clears += c
                    if burst is None and len(c) >= 5:
                        burst = (got[1]["tick"], len(c))
        fuse = (ex[0] - t0) * 1000 if ex else None
        self.metrics["tnt"] = {"platform": n_platform, "cleared": len(clears), "burst": burst, "fuse_ms": round(fuse) if fuse else None}
        self.check("огниво по TNT -> ack ok, TNT снят (clear)", bool(ack and ack["ok"] and primed), str(ack))
        self.check("взрыв -> explosion + пачка clear", bool(ex and burst and len(clears) >= 10),
                   f"explosion {ex[1] if ex else None}; clear всего {len(clears)} из {n_platform} блоков чаши, "
                   f"одним blocks (tick, n) = {burst}; запал {fuse/1000:.2f} с" if fuse else "нет explosion")
        self.pos(*G)
        self.quiet(1.0)

    def far_move(self) -> None:
        """Уход за 200 блоков: старые чанки -> unchunk, новые догружаются (CHUNK_LOAD) -> chunk; и обратно."""
        self.quiet(0.5)
        res = []
        for x, label in ((200.5, "туда"), (G[0], "обратно")):
            acc: list[dict] = []
            t0 = time.perf_counter()
            self.pos(x, G[1], G[2])
            end = t0 + 15
            while time.perf_counter() < end:
                got = self.l.recv(0.2)
                if got:
                    acc.append(got[1])
                if len(self.l.mirror.chunks) >= 81 and all(abs((cx * 16 + 8) - x) <= 16 * 4 + 8 for cx, _ in self.l.mirror.chunks) and time.perf_counter() - t0 > 1:
                    break
            n_chunk = sum(1 for m in acc if m.get("t") == "chunk")
            n_un = sum(1 for m in acc if m.get("t") == "unchunk")
            res.append((label, n_chunk, n_un, len(self.l.mirror.chunks), round(time.perf_counter() - t0, 2)))
        ok = all(c >= 81 and u >= 81 and tot == 81 for _, c, u, tot, _ in res)
        self.check("дальний pos (200 блоков) -> unchunk + chunk по догрузке", ok,
                   "; ".join(f"{lb}: chunk {c}, unchunk {u}, отслеживается {tot}, {dt} с" for lb, c, u, tot, dt in res))

    def death(self) -> None:
        """kill дважды: оба раза death + respawn. Второй раз проверяет, что после респауна Геральт не застрял
        неуязвимым (waitingForRespawn у listener'а без клиента)."""
        deaths, respawns = [], []
        for _ in range(2):
            acc: list[dict] = []
            self.l.send({"t": "cmd", "c": "kill Geralt"})
            r = self.l.wait(lambda m: m.get("t") == "respawn", 5, on_other=acc.append)
            deaths += [m["msg"] for m in acc if m.get("t") == "death"]
            if r:
                respawns.append(r[1]["p"])
            self.quiet(0.5)
        p = self.ping(2)
        where = self.l.cmd("data get entity Geralt Pos")
        self.l.send({"t": "give", "item": "minecraft:stone", "n": 4})
        self.l.cmd("setblock 2 70 0 minecraft:stone")
        self.quiet(0.4)
        ack, seen, *_ = self.act("place", (2, 70, 0), "up", slot=0)
        self.check("смерть x2 -> death (текст ванили) + respawn нового GeraltPlayer, снова уязвим",
                   bool(len(deaths) == 2 and len(respawns) == 2 and p.get("players") == 1
                        and all(d == "Geralt was killed" for d in deaths)
                        and "0.5d, 70.0d, 0.5d" in where and ack and ack["ok"]),
                   f"death {deaths}; respawn {respawns}; players {p.get('players')}; "
                   f"{where.split(':')[-1].strip()}; place после респауна ok={ack and ack['ok']}")

    # --- шаги по ревью: отладочное соединение, радиус, пределы, вода, верстак, измерения, рестарт ---
    def debug_peer(self) -> None:
        """Второе соединение без hello (как fake_bridge.py cmd) не вытесняет мост: ответы — ему, события — мосту."""
        self.quiet(0.3)
        b = fb.connect(wait=5)
        try:
            acc: list[dict] = []
            out = b.cmd("setblock 5 70 3 minecraft:stone")
            got = self.wait_blocks(lambda m: (5, 70, 3, "minecraft:stone") in fb.blocks_set(m), 3, acc)
            b.send({"t": "act", "id": 7, "a": "select", "slot": 0})
            ack_b = b.wait(lambda m: m.get("t") == "ack" and m.get("id") == 7, 3)
            b.send({"t": "ping", "ts": 77})
            pong_b = b.wait(lambda m: m.get("t") == "pong" and m.get("ts") == 77, 3)
            b.send({"t": "snap", "r": 1})
            err_b = b.wait(lambda m: m.get("t") == "error", 3)
            acc += self.quiet(0.5)
            leaked = sorted({m["t"] for m in acc if m.get("t") in ("cmd_out", "ack", "pong", "error", "snap_end")})
            b_events = [m["t"] for m in b.drain(0.3) if m.get("t") in ("blocks", "chunk", "inv", "hp")]
        finally:
            b.close()
        time.sleep(0.4)
        pong_l = self.ping(2)
        self.l.cmd("setblock 5 70 3 minecraft:air")
        self.quiet(0.4)
        ok = (got is not None and ack_b is not None and pong_b and pong_b[1].get("bridge") is False
              and err_b and err_b[1].get("why") == "no_hello" and not leaked and not b_events
              and not self.l.closed and pong_l.get("bridge") is True)
        self.check("отладочное соединение без hello не вытесняет мост", bool(ok),
                   f"cmd_out у B: {out!r}; blocks у моста: {got is not None}; ack/pong у B: {ack_b is not None}/"
                   f"bridge={pong_b[1].get('bridge') if pong_b else None}; snap от B: {err_b[1].get('why') if err_b else None}; "
                   f"ответов B у моста: {leaked}; событий у B: {b_events}; мост жив: {not self.l.closed}")

    def snap_radius(self) -> None:
        """hello r=16 -> radius = view-distance; snap r=2 -> unchunk внешних, зеркало = свежему hello r=2."""
        h, e = self.l.hello(radius=16)
        self.quiet(1.0)
        before = len(self.l.mirror.chunks)
        acc: list[dict] = []
        self.l.send({"t": "snap", "r": 2})
        end = self.l.wait(lambda m: m.get("t") == "snap_end", 5, on_other=acc.append)
        unchunks = sum(1 for m in acc if m.get("t") == "unchunk")
        self.quiet(0.5)
        self.l.cmd("setblock 56 70 0 minecraft:stone")  # чанк x=3: в радиусе 6, вне радиуса 2
        self.quiet(0.5)
        stale = (3, 0) in self.l.mirror.chunks or self.l.mirror.get(56, 70, 0) is not None
        old = self.l.mirror_copy()
        old_link = self.l
        fresh = fb.connect(wait=5)
        fresh.hello(radius=2)
        fresh.drain(0.5)
        d = old.diff(fresh.mirror_copy())
        time.sleep(0.3)
        evicted = old_link.closed
        old_link.close()
        self.link = fresh
        self.l.cmd("setblock 56 70 0 minecraft:air")
        self.l.send({"t": "snap", "r": 4})
        self.l.wait(lambda m: m.get("t") == "snap_end", 5)
        self.quiet(0.5)
        self.check("hello r=16 -> radius = view-distance", h.get("radius") == VIEW_DISTANCE,
                   f"hello.radius {h.get('radius')}, snap_end.chunks {e['chunks']}, отслеживалось {before}")
        self.check("snap r=2 после большого -> unchunk лишних, зеркало = свежему hello r=2",
                   bool(end and not d and not stale and len(old.chunks) == 25 and unchunks == before - 25 and evicted),
                   f"unchunk {unchunks} (из {before}), чанков у моста {len(old.chunks)}, setblock вне радиуса в зеркале: "
                   f"{stale}; расхождений со свежим {len(d)} {d[-2:]}; новый hello вытеснил старый мост: {evicted}")

    def limits(self) -> None:
        """NaN/Infinity в pos и act -> отказ, Геральт на месте; блок выше/ниже мира -> too_high/too_low."""
        self.quiet(0.3)
        self.l.sock.sendall(b'{"t":"pos","p":[NaN,70,0.5]}\n')
        err = self.l.wait(lambda m: m.get("t") == "error", 3)
        where = self.l.cmd("data get entity Geralt Pos")
        ack_inf, *_ = self.act("select", slot=0, p=[float("inf"), 70.0, 0.5])
        ack_hi, *_ = self.act("break", (0, 400, 0), "up", slot=1)
        ack_lo, *_ = self.act("break", (0, -100, 0), "up", slot=1)
        ok = (err and err[1].get("why") == "bad_args" and "0.5d, 70.0d, 0.5d" in where
              and ack_inf and ack_inf.get("why") == "bad_args" and ack_hi and ack_hi.get("why") == "too_high"
              and ack_lo and ack_lo.get("why") == "too_low")
        self.check("NaN/Infinity -> bad_args, Геральт на месте; y 400/-100 -> too_high/too_low", bool(ok),
                   f"pos NaN: {err[1] if err else None}; Pos {where.split(':')[-1].strip()}; act p=Infinity: "
                   f"{ack_inf and ack_inf.get('why')}; y400: {ack_hi and ack_hi.get('why')}; y-100: {ack_lo and ack_lo.get('why')}")

    def water(self) -> None:
        """Ведро воды через act place (useItemOn -> PASS -> useItem по лучу взгляда) -> поток blocks;
        act use пустым ведром забирает источник."""
        self.l.cmd("fill 20 66 -2 24 68 2 minecraft:stone")
        self.l.cmd("fill 21 67 -1 23 68 1 minecraft:air")
        self.l.send({"t": "give", "item": "minecraft:water_bucket", "n": 1})
        inv = self.l.wait(lambda m: m.get("t") == "inv" and any(s[1] == "minecraft:water_bucket" for s in m["s"]), 3)
        slot = next(s[0] for s in inv[1]["s"] if s[1] == "minecraft:water_bucket") if inv else 8
        self.quiet(0.4)
        eye = [22.5, 69.0, 0.5]  # над чашей, взгляд вниз на дно (22,66,0)
        ack, seen, _, _ = self.act("place", (22, 66, 0), "up", slot=slot, p=eye, yaw=0.0, pitch=90.0)
        acc = [m for _, m in seen]
        acc += self.quiet(1.0, 4.0)
        msgs = [m for m in acc if m.get("t") == "blocks" and any(n.startswith("minecraft:water") for n in m["pal"])]
        cells = {k: v for k, v in self.l.mirror.blocks.items() if v.startswith("minecraft:water")}
        bucket = any(m.get("t") == "inv" and any(s[0] == slot and s[1] == "minecraft:bucket" for s in m["s"]) for m in acc)
        ack2, seen2, *_ = self.act("use", slot=slot, p=eye, yaw=0.0, pitch=90.0)
        picked = any(m.get("t") == "blocks" and (22, 67, 0) in fb.blocks_clear(m) for _, m in seen2)
        self.quiet(1.5, 6.0)
        self.l.cmd("fill 20 66 -2 24 68 2 minecraft:air")
        self.pos(*G)
        self.quiet(0.5)
        self.check("ведро воды: act place -> поток blocks, act use забирает",
                   bool(ack and ack["ok"] and len(msgs) >= 2 and len(cells) >= 5 and bucket and ack2 and ack2["ok"] and picked),
                   f"ack {ack}; blocks с водой: {len(msgs)} сообщений, клеток воды {len(cells)} "
                   f"{sorted(set(cells.values()))[:4]}; в слоте {slot} пустое ведро: {bucket}; use {ack2}, источник снят: {picked}")

    def crafting(self) -> None:
        """place по верстаку: без sneak клик съедает верстак (used_block, ничего не ставится), со sneak — ставится."""
        self.l.cmd("setblock 3 70 0 minecraft:crafting_table")
        self.l.send({"t": "give", "item": "minecraft:stone", "n": 2})
        self.quiet(0.4)
        ack1, *_ = self.act("place", (3, 70, 0), "up", slot=0)
        self.quiet(0.3)
        none = self.l.mirror.get(3, 71, 0)
        ack2, seen, *_ = self.act("place", (3, 70, 0), "up", slot=0, sneak=True)
        put = self.first_blocks(seen, lambda m: (3, 71, 0, "minecraft:stone") in fb.blocks_set(m))
        self.l.cmd("fill 3 70 0 3 71 0 minecraft:air")
        self.quiet(0.4)
        self.check("place по верстаку -> used_block; со sneak -> блок на верстаке",
                   bool(ack1 and not ack1["ok"] and ack1.get("why") == "used_block" and none is None
                        and ack2 and ack2["ok"] and put),
                   f"без sneak {ack1}, над верстаком {none}; со sneak {ack2}, blocks set: {put is not None}")

    def _tp(self, c: str) -> tuple[dict | None, int, dict | None]:
        """cmd с телепортом -> (pteleport, сколько unchunk до snap_end, snap_end)."""
        acc: list[dict] = []
        self.l.send({"t": "cmd", "c": c})
        pt = self.l.wait(lambda m: m.get("t") == "pteleport", 5, on_other=acc.append)
        se = self.l.wait(lambda m: m.get("t") == "snap_end", 5, on_other=acc.append) if pt else None
        return (pt[1] if pt else None), sum(1 for m in acc if m.get("t") == "unchunk"), (se[1] if se else None)

    def _kill(self) -> tuple[list[str], dict | None, list[dict]]:
        acc: list[dict] = []
        self.l.send({"t": "cmd", "c": "kill Geralt"})
        r = self.l.wait(lambda m: m.get("t") == "respawn", 5, on_other=acc.append)
        if r:
            acc += self.quiet(0.6, 5.0)
        return [m["msg"] for m in acc if m.get("t") == "death"], (r[1] if r else None), acc

    def dimension(self) -> None:
        """Нижний мир и обратно: pteleport с dim, unchunk старых, снапшот нового; после этого kill убивает.
        Смерть в Нижнем мире -> respawn в обычном, зеркало переезжает за ним."""
        self.l.send({"t": "snap", "r": 1})  # чанки Нижнего мира плотные: снапшот поменьше
        self.l.wait(lambda m: m.get("t") == "snap_end", 5)
        self.quiet(0.5)
        n0 = len(self.l.mirror.chunks)
        pt1, un1, se1 = self._tp("execute in minecraft:the_nether run tp Geralt 0.5 200 0.5")
        self.quiet(1.5, 8.0)
        n_nether = len(self.l.mirror.chunks)
        pt2, un2, se2 = self._tp("execute in minecraft:overworld run tp Geralt 0.5 70 0.5")
        self.quiet(0.5)
        deaths1, r1, _ = self._kill()
        self.check("Нижний мир и обратно -> pteleport с dim, unchunk всех старых чанков, снапшот нового уровня",
                   bool(pt1 and pt1.get("dim") == "minecraft:the_nether" and un1 == n0 and se1
                        and pt2 and pt2.get("dim") == "minecraft:overworld" and un2 == n_nether and se2),
                   f"туда: {pt1}, unchunk {un1} из {n0}, {se1}; там чанков {n_nether}; "
                   f"обратно: {pt2 and pt2.get('dim')}, unchunk {un2}, {se2}")
        self.check("после смены измерения kill убивает (флаг isChangingDimension снят)",
                   bool(deaths1 == ["Geralt was killed"] and r1), f"death {deaths1}; respawn {r1}")

        self.pos(*G)
        self.quiet(0.5)
        pt3, _, _ = self._tp("execute in minecraft:the_nether run tp Geralt 0.5 200 0.5")
        self.quiet(1.0, 6.0)
        n3 = len(self.l.mirror.chunks)
        deaths2, r2, acc = self._kill()
        un3 = sum(1 for m in acc if m.get("t") == "unchunk")
        se3 = any(m.get("t") == "snap_end" for m in acc)
        self.pos(*G)
        self.l.send({"t": "snap", "r": 4})
        self.l.wait(lambda m: m.get("t") == "snap_end", 5)
        self.quiet(1.0)
        self.check("смерть в Нижнем мире -> respawn с dim overworld, зеркало переехало",
                   bool(pt3 and deaths2 == ["Geralt was killed"] and r2 and r2.get("dim") == "minecraft:overworld"
                        and un3 == n3 and se3),
                   f"death {deaths2}; respawn {r2}; unchunk {un3} из {n3}; snap_end {se3}")

    def restart_ground(self, pid: int) -> int:
        """ground -> рестарт сервера -> ground_clear снимает свой рельеф (позиции в SavedData); чужой barrier —
        только с any. Колонна в незагруженном чанке -> skip. Возвращает pid нового сервера."""
        self.l.send({"t": "ground_clear", "box": [30, 0, 32, 1], "any": True})
        self.l.wait(lambda m: m.get("t") == "ground_ok", 3)
        cols = [v for x in (30, 31) for z in (0, 1) for v in (x, z, 60, 62)]
        self.l.send({"t": "ground", "c": cols + [1000, 1000, 60, 62]})
        ok1 = self.l.wait(lambda m: m.get("t") == "ground_ok", 3)
        self.l.cmd("setblock 32 61 0 minecraft:barrier")
        self.give_mark()
        self.l.send({"t": "cmd", "c": "stop"})
        stopped = wait_pid(pid, 40)
        if not stopped:
            subprocess.run(["powershell", "-NoProfile", "-File", str(HERE / "run_server.ps1"), "-Stop"], check=False)
        (RUN / "server.pid").unlink(missing_ok=True)
        self.l.close()
        info = start_server(-1)
        self.metrics["server_restart"] = info
        self.link = fb.connect(wait=30)
        self.l.hello(radius=4)
        inv = self.l.wait(lambda m: m.get("t") == "inv", 3)
        kept = bool(inv and any(it[1] == MARK for it in inv[1]["s"]))
        near = lambda m=None: (1, 0) in self.l.mirror.chunks and (2, 0) in self.l.mirror.chunks  # noqa: E731
        if not near():
            self.l.wait(near, 15)
        self.quiet(0.5)
        self.l.send({"t": "ground_clear", "box": [30, 0, 31, 1]})
        ok2 = self.l.wait(lambda m: m.get("t") == "ground_ok", 3)
        air = self.l.cmd("execute if block 30 61 0 minecraft:air")
        self.l.send({"t": "ground_clear", "box": [32, 0, 32, 0]})
        ok3 = self.l.wait(lambda m: m.get("t") == "ground_ok", 3)
        self.l.send({"t": "ground_clear", "box": [32, 0, 32, 0], "any": True})
        ok4 = self.l.wait(lambda m: m.get("t") == "ground_ok", 3)
        g1, g2, g3, g4 = (x[1] if x else {} for x in (ok1, ok2, ok3, ok4))
        self.check("рестарт сервера -> инвентарь из playerdata; ground_clear находит свой рельеф; чужой barrier — "
                   "только any; skip незагруженных",
                   bool(stopped and kept and g1.get("placed") == 12 and g1.get("unloaded") == 1 and g1.get("skip") == [1000, 1000]
                        and g2.get("removed") == 12 and g2.get("left") == 0 and "passed" in air.lower()
                        and g3.get("removed") == 0 and g4.get("removed") == 1),
                   f"до: {g1}; stop: {stopped}; {MARK} после рестарта: {kept}; новый сервер pid {info['pid']} за {info['wall_s']} с; после: {g2}; {air}; "
                   f"чужой без any: {g3.get('removed')}, с any: {g4.get('removed')}")
        return info["pid"]

    def reconnect(self) -> None:
        self.quiet(1.5)
        old = self.l.mirror_copy()
        self.l.close()
        time.sleep(0.3)
        self.link = fb.connect(wait=5)
        _, e = self.l.hello(radius=4)
        self.quiet(0.5)
        new = self.l.mirror_copy()
        d = old.diff(new)
        self.check("переподключение -> снапшот = зеркалу", not d and len(old.blocks) > 0,
                   f"зеркало {len(old.blocks)} блоков / {len(old.chunks)} чанков, снапшот {e['blocks']} / {e['chunks']}; "
                   f"расхождений {len(d)} {d[:3]}")

    def ping(self, n: int = 10) -> dict:
        rtts, last = [], None
        for i in range(n):
            t0 = self.l.send({"t": "ping", "ts": i})
            got = self.l.wait(lambda m: m.get("t") == "pong" and m.get("ts") == i, 3)
            if got:
                rtts.append((got[0] - t0) * 1000)
                last = got[1]
        self.metrics["ping_ms"] = {"p50": round(statistics.median(rtts), 2), "max": round(max(rtts), 2)} if rtts else None
        return last or {}

    def idle(self, secs: float) -> None:
        p0 = self.ping(3)
        t0 = time.perf_counter()
        print(f"   простой {secs:.0f} с без сообщений моста...", flush=True)
        self.l.drain(secs, secs + 1)  # только читаем
        p1 = self.ping(3)
        dt = time.perf_counter() - t0
        ticks = p1.get("tick", 0) - p0.get("tick", 0)
        self.metrics["idle"] = {"seconds": round(dt, 1), "ticks": ticks, "players": p1.get("players"), "mspt": p1.get("mspt")}
        self.check(f"{secs:.0f} с без игроков: сервер тикает", ticks >= dt * 20 * 0.9 and p1.get("players") == 1,
                   f"{ticks} тиков за {dt:.1f} с, players={p1.get('players')} (Геральт), mspt {p1.get('mspt')}")
        self.sand(catcher_y=58, label=f"после {secs:.0f} с простоя песок падает (сущности не замёрзли)")

    def give_mark(self) -> None:
        self.l.send({"t": "give", "item": MARK, "n": 1})
        self.l.wait(lambda m: m.get("t") == "inv" and any(it[1] == MARK for it in m["s"]), 3)

    def stop_server(self, pid: int | None) -> None:
        try:
            self.give_mark()
            self.l.send({"t": "cmd", "c": "stop"})
        except OSError:
            pass
        ok = wait_pid(pid, 40) if pid else False
        how = "stop через мост"
        if pid and not ok:
            subprocess.run(["powershell", "-NoProfile", "-File", str(HERE / "run_server.ps1"), "-Stop"], check=False)
            how = "Stop-Process по PID"
        (RUN / "server.pid").unlink(missing_ok=True)
        print(f"   сервер pid {pid} погашен: {how}")
        self.metrics["stopped"] = how


def fmt(v: list[float]) -> str:
    if not v:
        return "-"
    return f"p50 {statistics.median(v):.0f}, min {min(v):.0f}, max {max(v):.0f}"


def wait_pid(pid: int, secs: float) -> bool:
    end = time.time() + secs
    while time.time() < end:
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
        if str(pid) not in r.stdout:
            return True
        time.sleep(0.5)
    return False


def start_server(pause: int) -> dict:
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / "run_server.ps1")]
    if pause >= 0:
        args += ["-PauseWhenEmpty", str(pause)]
    # вывод в файл, не в pipe: java наследует дескрипторы PowerShell, и pipe не закрылся бы, пока жив сервер
    log = RUN / "logs" / "run_server.out.txt"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w", encoding="utf-8") as f:
        rc = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT).returncode
    text = log.read_text(encoding="utf-8", errors="replace")
    if rc != 0:
        raise SystemExit(f"run_server.ps1 ({rc}): {text}")
    return json.loads(text.strip().splitlines()[-1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", action="store_true", help="запустить сервер через run_server.ps1")
    ap.add_argument("--stop", action="store_true", help="в конце погасить сервер")
    ap.add_argument("--pause", type=int, default=-1, help="pause-when-empty-seconds для --start")
    ap.add_argument("--idle", type=float, default=70.0)
    ap.add_argument("--idle-only", action="store_true")
    a = ap.parse_args()

    s = Smoke()
    pid = None
    had_playerdata = any((RUN / "world" / "playerdata").glob("*.dat"))
    if a.start:
        info = start_server(a.pause)
        pid = info["pid"]
        s.metrics["server_start"] = info
        s.check("сервер до Done", True, f"pid {pid}, {info['wall_s']} с от запуска процесса (сервер пишет Done за {info['done_s']} с)")
    elif (RUN / "server.pid").exists():
        pid = int((RUN / "server.pid").read_text().strip())
    try:
        s.connect_hello(wait=30, had_playerdata=had_playerdata and a.start)
        if a.idle_only:
            s.setup_area()
            s.give_kit()
            s.idle(a.idle)
        else:
            s.setup_area()
            s.check_pos()
            s.give_kit()
            s.place_break()
            s.ground()
            s.sand()
            s.tnt()
            s.water()
            s.crafting()
            s.debug_peer()
            s.limits()
            s.far_move()
            s.snap_radius()
            s.reconnect()
            s.ping(20)
            s.idle(a.idle)
            s.death()
            s.dimension()
            s.reconnect()
            if a.start:
                pid = s.restart_ground(pid)
    except Exception as e:  # noqa: BLE001 — итог печатаем всегда, сервер гасим всегда
        s.check("исключение в smoke", False, repr(e))
    finally:
        if a.stop and s.link:
            s.stop_server(pid)
        elif s.link:
            s.l.close()

    res = {"rows": [{"check": n, "ok": ok, "detail": d} for n, ok, d in s.rows], "metrics": s.metrics,
           "pause_when_empty": a.pause, "when": time.strftime("%Y-%m-%d %H:%M:%S")}
    out = RUN / "logs" / ("smoke_idle_result.json" if a.idle_only else "smoke_result.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n| Проверка | Итог | Подробности |\n|---|---|---|")
    for n, ok, d in s.rows:
        print(f"| {n} | {'прошёл' if ok else 'НЕ прошёл'} | {d} |")
    print("\nmetrics:", json.dumps(s.metrics, ensure_ascii=False))
    return 0 if all(ok for _, ok, _ in s.rows) else 1


if __name__ == "__main__":
    sys.exit(main())
