"""Сквозной прогон моста: настоящий Fabric-сервер из mc\\ + фейковая игра spike/fake_w3.py + bridge.py, клавиши —
подменой GetAsyncKeyState (в настоящие окна ничего не шлём).

    python smoke_bridge.py --start --stop          # поднять сервер (mc\\run_server.ps1), прогнать, погасить
    python smoke_bridge.py                         # сервер уже запущен

Ведьмак — только фейк на 127.0.0.1:37101 (порт --w3-port, не ниже 37101: на 37000/37001 настоящая игра).
Сервер — только mc\\run\\ и только 127.0.0.1. Итог — таблица в консоль и logs\\smoke_result.json, журнал моста —
logs\\smoke_bridge.log.

Сценарий: подготовка области у Геральта (стена 2x3 из земли и каменный куб 3x3x3) -> мост: hello, снапшот, рельеф
-> в фейке ровно открытые блоки (31); /setblock -> появился; клавиши F2 + 8 -> mcw_look -> act break -> исчез в
Майнкрафте и в фейке; MOUSE5 -> place на barrier-землю; рестарт моста -> восстановилось; рестарт фейка (мир пуст)
-> ресинк; «загрузка сейва» (сущности фейка пропали) -> mcw_count = 0 -> ресинк; TNT рядом -> mcw_boom.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
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

HERE = Path(__file__).resolve().parent
MC = HERE.parent / "mc"
RUN = MC / "run"
sys.path.insert(0, str(MC))
import fake_bridge as fb  # noqa: E402  (только как клиент cmd/pos: hello не шлёт, мост не вытесняет)

LOGS = HERE / "logs"
PLAYER_CM = [-30050, -29950, 7000]                # Ведьмак (-300.5, -299.5, 70.0) м -> MC (-300.5, 70, 300.5)
G = (-300.5, 70.0, 300.5)
WALL = {(-304, y, z) for y in (70, 71) for z in (299, 300, 301)}
CUBE = {(-296 + i, 70 + j, 298 + k) for i in range(3) for j in range(3) for k in range(3)}
EXPECTED = WALL | (CUBE - {(-295, 71, 299), (-295, 70, 299)})   # середина куба и низ середины (на barrier) закрыты
AREA = "-312 60 288 -284 80 316"
CRATE = coords.by_batch_t(coords.load_templates())[0]


def origins(cells):
    return sorted(tuple(int(v) for v in coords.set_op(*p, CRATE).split(":")[1:4]) for p in cells)


def fake_cells(fw):
    return sorted((e.x, e.y, e.z) for e in list(fw.world.ents.values()) if "MCW_TEST" not in e.tags)


def wait_until(pred, timeout, step=0.005):
    t0 = time.perf_counter()
    end = t0 + timeout
    while time.perf_counter() < end:
        if pred():
            return time.perf_counter() - t0
        time.sleep(step)
    return (time.perf_counter() - t0) if pred() else None


def wait_pid(pid: int, secs: float) -> bool:
    end = time.time() + secs
    while time.time() < end:
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
        if str(pid) not in r.stdout:
            return True
        time.sleep(0.5)
    return False


def start_server() -> dict:
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(MC / "run_server.ps1")]
    log = LOGS / "run_server.out.txt"
    LOGS.mkdir(exist_ok=True)
    with open(log, "w", encoding="utf-8") as f:   # не pipe: java наследует дескрипторы PowerShell
        rc = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT).returncode
    text = log.read_text(encoding="utf-8", errors="replace")
    if rc != 0:
        raise SystemExit(f"run_server.ps1 ({rc}): {text}")
    return json.loads(text.strip().splitlines()[-1])


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
        self.t_bridge = 0.0

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.rows.append((name, bool(ok), detail))
        print(f"[{'ok' if ok else 'FAIL'}] {name}: {detail}", flush=True)
        return ok

    # -- части стенда
    def new_fake(self):
        fw = fake_w3.FakeWitcher("127.0.0.1", self.w3_port, frame_time=1 / 60)
        fw.world.player = list(PLAYER_CM)
        return fw.start()

    def cfg(self):
        return config.load(None, {"w3": {"port": self.w3_port}, "log": {"file": str(LOGS / "smoke_bridge.log"),
                                                                       "metrics_every_s": 5.0},
                                  "supervise": {"enabled": False}})     # сервер и клиент смоука — не сторожа

    def start_bridge(self):
        self.br = bridge_mod.Bridge(self.cfg(), key_down=lambda vk: vk in self.down, foreground=lambda: True)
        self.th = threading.Thread(target=self.br.run, name="bridge", daemon=True)
        self.th.start()
        return time.perf_counter()

    def stop_bridge(self):
        self.br.stop()
        self.th.join(5)

    def press(self, name: str) -> float:
        t = time.perf_counter()
        self.down.add(hotkeys.VK[name])
        time.sleep(0.05)
        self.down.discard(hotkeys.VK[name])
        time.sleep(0.03)
        return t

    def cmd(self, c: str) -> str:
        return self.dbg.cmd(c, timeout=10)

    def synced(self, cells) -> bool:
        return fake_cells(self.fw) == origins(cells) and self.br.snapshot.get("pending_ops") == 0 \
            and self.br.snapshot.get("batch_inflight") == 0

    def has(self, cell) -> bool:
        return tuple(int(v) for v in coords.set_op(*cell, CRATE).split(":")[1:4]) in set(fake_cells(self.fw))

    # -- шаги
    def setup_area(self) -> None:
        self.dbg = fb.connect(wait=10.0)
        self.dbg.send({"t": "pos", "seq": 1, "p": list(G), "yaw": 90.0})
        time.sleep(1.5)                                    # чанки вокруг новой точки грузятся
        outs = []
        for c in ("time set day", "gamemode survival Geralt", "clear Geralt", "kill @e[type=minecraft:item]",
                  f"fill {AREA} minecraft:air", "fill -304 70 299 -304 71 301 minecraft:dirt",
                  "fill -296 70 298 -294 72 300 minecraft:stone", "give Geralt minecraft:dirt 8"):
            for _ in range(10):
                out = self.cmd(c)
                if "not loaded" not in out:
                    break
                time.sleep(0.5)
            outs.append(f"{c!r}: {out[:60]}")
        self.dbg.send({"t": "ground_clear", "box": [-312, 288, -284, 316]})   # остатки прошлых прогонов
        self.dbg.wait(lambda m: m.get("t") == "ground_ok", 3)
        ok = all("passed" in self.cmd(f"execute if block {b} minecraft:{n}").lower()
                 for b, n in (("-304 71 301", "dirt"), ("-295 71 299", "stone"), ("-305 70 300", "air")))
        self.check("подготовка области", ok, "; ".join(outs[-4:]))

    def first_sync(self) -> None:
        self.fw = self.new_fake()
        t0 = self.start_bridge()
        first = wait_until(lambda: self.fw.world.count() > 0, 30)
        dt = wait_until(lambda: self.synced(EXPECTED), 30)
        snap = self.br.snapshot
        self.metrics["first_block_s"] = None if first is None else round(first, 2)
        self.metrics["start_to_synced_s"] = None if dt is None else round(time.perf_counter() - t0, 2)
        self.check("hello -> снапшот -> блоки в фейке", dt is not None,
                   f"первые блоки через {self.metrics['first_block_s']} с от старта моста, ровно открытые — через "
                   f"{self.metrics['start_to_synced_s']} с (низ середины куба закрывается, когда известен рельеф под ним); "
                   f"mcw_count {self.fw.world.count()} (ждали {len(EXPECTED)}: 6 стена + 25 открытых у куба); "
                   f"зеркало {snap.get('mirror')} бл., {snap.get('chunks')} чанков")
        # рельеф: колонна под Геральтом barrier 67..69, над ней воздух
        r69 = wait_until(lambda: "passed" in self.cmd("execute if block -301 69 300 minecraft:barrier").lower(), 10, 0.2)
        r67 = "passed" in self.cmd("execute if block -301 67 300 minecraft:barrier").lower()
        r70 = "passed" in self.cmd("execute if block -301 70 300 minecraft:air").lower()
        self.check("рельеф под Геральтом -> barrier", r69 is not None and r67 and r70,
                   f"y69 barrier {r69 is not None}, y67 {r67}, y70 воздух {r70}; ground_sent "
                   f"{self.br.snapshot.get('ground_sent')} колонн, известно {self.br.snapshot.get('ground_known')}")

    def setblock(self) -> None:
        t = time.perf_counter()
        out = self.cmd("setblock -300 72 296 minecraft:oak_planks")
        dt = wait_until(lambda: self.has((-300, 72, 296)), 5)
        self.metrics["setblock_to_fake_ms"] = None if dt is None else round((time.perf_counter() - t) * 1000)
        self.check("/setblock -> появился в фейке", dt is not None and self.fw.world.count() == len(EXPECTED) + 1,
                   f"{out[:50]}; {self.metrics['setblock_to_fake_ms']} мс от cmd до сущности; count {self.fw.world.count()}")

    def break_place(self) -> None:
        self.press("F2")
        ok_mode = wait_until(lambda: self.br.build_mode, 2) is not None
        t = self.press("8")
        dt = wait_until(lambda: not self.has((-304, 70, 300)), 8)
        lat = None if dt is None else round((time.perf_counter() - t) * 1000)
        air = "passed" in self.cmd("execute if block -304 70 300 minecraft:air").lower()
        acts = {k: v for k, v in self.br.stats.items() if k.startswith(("act_", "normal_"))}
        self.metrics["key_break_to_fake_ms"] = lat
        self.check("F2 + 8 -> act break -> исчез в MC и в фейке", ok_mode and dt is not None and air,
                   f"режим {ok_mode}; от нажатия до исчезновения в фейке {lat} мс (копание земли рукой 15 тиков); "
                   f"в MC воздух: {air}; {acts}")
        t = self.press("MOUSE5")
        dt = wait_until(lambda: self.has((-305, 70, 300)), 5)
        lat = None if dt is None else round((time.perf_counter() - t) * 1000)
        dirt = "passed" in self.cmd("execute if block -305 70 300 minecraft:dirt").lower()
        self.metrics["key_place_to_fake_ms"] = lat
        self.check("MOUSE5 -> act place на barrier-землю -> в MC и в фейке", dt is not None and dirt,
                   f"{lat} мс от нажатия; в MC dirt на (-305,70,300): {dirt}")
        self.expected = (EXPECTED | {(-300, 72, 296), (-305, 70, 300)}) - {(-304, 70, 300)}
        ok = wait_until(lambda: self.synced(self.expected), 5) is not None
        self.check("после act: фейк = открытые блоки MC", ok, f"count {self.fw.world.count()}")

    def restart_bridge(self) -> None:
        self.stop_bridge()
        before = self.fw.world.count()
        t0 = self.start_bridge()
        self.t_bridge = t0
        dt = wait_until(lambda: self.br.stats["w3_connects"] >= 1 and self.br.w3_state == "synced"
                        and self.synced(self.expected), 30)
        self.metrics["bridge_restart_s"] = None if dt is None else round(time.perf_counter() - t0, 2)
        self.check("рестарт моста -> восстановилось", dt is not None,
                   f"в игре до {before}, mcw_clearall -> {self.fw.world.count()} за {self.metrics['bridge_restart_s']} с")

    def restart_fake(self) -> None:
        self.fw.stop()
        t0 = time.perf_counter()
        self.fw = self.new_fake()
        dt = wait_until(lambda: self.synced(self.expected), 30)
        self.metrics["fake_restart_s"] = None if dt is None else round(time.perf_counter() - t0, 2)
        self.check("рестарт фейка (мир пуст) -> ресинк", dt is not None,
                   f"переподключение и {self.fw.world.count()} блоков за {self.metrics['fake_restart_s']} с; "
                   f"w3_connects {self.br.stats['w3_connects']}")

    def save_load(self) -> None:
        r0 = self.br.stats["resyncs"]
        t0 = time.perf_counter()
        self.fw.world.clearall()
        dt = wait_until(lambda: self.br.stats["resyncs"] > r0 and self.synced(self.expected), 15)
        self.metrics["save_load_resync_s"] = None if dt is None else round(time.perf_counter() - t0, 2)
        self.check("«загрузка сейва»: mcw_countblocks 0 -> ресинк", dt is not None,
                   f"{self.metrics['save_load_resync_s']} с, ресинков {self.br.stats['resyncs'] - r0}")

    def tnt(self) -> None:
        out = self.cmd("summon minecraft:tnt -288 71 310 {fuse:1}")
        dt = wait_until(lambda: any(e.on_fire for e in list(self.fw.world.ents.values())), 5)
        time.sleep(0.5)
        fire = [e for e in list(self.fw.world.ents.values()) if e.on_fire]
        # summon с целыми x, z ставит TNT в центр клетки: MC (-287.5, ~71, 310.5) -> Ведьмак (-28750, -30950) см;
        # mcw_boomat опускает бочку на опору под точкой (земля фейка 7000 см)
        want = (-28750, -30950, 7000)
        at = (fire[0].x, fire[0].y, fire[0].z) if fire else None
        near = at is not None and abs(at[0] - want[0]) <= 60 and abs(at[1] - want[1]) <= 60 and at[2] == want[2]
        boomat = sum(1 for c in self.fw.execs_seen if c.startswith("mcw_boomat("))
        self.metrics["boom_at"] = at
        self.check("TNT рядом -> explosion -> mcw_boomat в точке взрыва", dt is not None and near and boomat == 1,
                   f"{out[:40]}; бочка в фейке через {None if dt is None else round(dt * 1000)} мс в {at} "
                   f"(ждали ~{want}); вызовов mcw_boomat {boomat}")
        ok = wait_until(lambda: self.synced(self.expected), 5) is not None
        self.check("после взрыва блоки на месте, ресинка из-за бочки нет", ok,
                   f"count {self.fw.world.count()} (бочка +1, MCW_TEST), ресинков {self.br.stats['resyncs']}")

    def ground_full(self) -> None:
        """Весь круг рельефа (ground.radius_m) у мода: сколько это занимает при ground.rate_hz."""
        n = len(self.br.ground.offsets)
        dt = wait_until(lambda: self.br.snapshot.get("ground_sent", 0) >= n, 120, 0.05)
        took = round(time.perf_counter() - self.t_bridge, 1)
        far = "passed" in self.cmd("execute if block -324 69 300 minecraft:barrier").lower()   # 23 м от Геральта
        out = "passed" in self.cmd("execute if block -326 69 300 minecraft:air").lower()       # 25 м: вне радиуса
        self.metrics["ground_full_s"] = took
        self.metrics["ground_columns"] = n
        self.check("рельеф: весь радиус 24 м у мода", dt is not None and far and out,
                   f"{n} колонн за {took} с от старта моста ({round(n / took, 1)} колонн/с при rate_hz "
                   f"{self.br.cfg.ground.rate_hz}); barrier в 23 м {far}, в 25 м воздух {out}")

    def channel(self) -> None:
        s = self.br.snapshot
        self.metrics["bridge"] = s
        self.metrics["fake_execs"] = len(self.fw.execs_seen)
        batches = [c for c in self.fw.execs_seen if c.startswith("mcw_batch(")]
        mx = max((coords.utf8_len(c) for c in batches), default=0)
        self.metrics["batch_max_bytes"] = mx
        self.check("канал Ведьмака", s.get("poll_rtt_p50_ms") is not None and mx <= 4096,
                   f"poll RTT p50 {s.get('poll_rtt_p50_ms')} мс, p95 {s.get('poll_rtt_p95_ms')} мс (фейк, кадр 16,7 мс); "
                   f"пакетов в этом фейке {len(batches)}, самый длинный {mx} Б")
        n_new = sum(1 for c in self.fw.execs_seen if c.startswith("mcw_countblocks("))
        n_old = sum(1 for c in self.fw.execs_seen if c.startswith("mcw_count("))
        self.check("расширения мода: countblocks и boomat вместо старых", self.br.caps == {"countblocks": True, "boomat": True}
                   and n_new > 0 and n_old == 0,
                   f"caps {self.br.caps}; mcw_countblocks {n_new}, mcw_count {n_old} (второй экземпляр моста и фейка)")

    def cleanup(self) -> None:
        if self.br is not None:
            self.stop_bridge()
        if self.fw is not None:
            self.fw.stop()
        if self.dbg is None:
            return
        try:
            self.cmd("kill @e[type=minecraft:item]")
            self.cmd(f"fill {AREA} minecraft:air")
            self.dbg.send({"t": "ground_clear", "box": [-340, 260, -260, 340]})
            got = self.dbg.wait(lambda m: m.get("t") == "ground_ok", 3)
            self.metrics["cleanup_ground"] = got[1] if got else None
            self.dbg.send({"t": "pos", "seq": 2, "p": [0.5, 70.0, 0.5], "yaw": 0.0})
            time.sleep(0.3)
        except (OSError, TimeoutError) as e:
            print("уборка:", e)

    def stop_server(self, pid) -> None:
        how = "уже не работал"
        try:
            self.dbg.send({"t": "cmd", "c": "stop"})
            how = "stop через мод"
        except (OSError, AttributeError):
            pass
        ok = wait_pid(pid, 40) if pid else True
        if pid and not ok:
            subprocess.run(["powershell", "-NoProfile", "-File", str(MC / "run_server.ps1"), "-Stop"], check=False)
            how = "Stop-Process по PID"
        (RUN / "server.pid").unlink(missing_ok=True)
        self.metrics["server_stopped"] = how
        print(f"сервер pid {pid} погашен: {how}")


def main() -> int:
    w3net.utf8_stdio()
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", action="store_true", help="поднять сервер через mc\\run_server.ps1")
    ap.add_argument("--stop", action="store_true", help="в конце погасить сервер")
    ap.add_argument("--w3-port", type=int, default=37101)
    a = ap.parse_args()
    if a.w3_port < 37101:
        raise SystemExit("--w3-port не ниже 37101: 37000/37001 — настоящая игра")
    LOGS.mkdir(exist_ok=True)
    if (LOGS / "smoke_bridge.log").exists():
        (LOGS / "smoke_bridge.log").unlink()
    bridge_mod.setup_logging(str(LOGS / "smoke_bridge.log"), "INFO", console=False)
    s = Smoke(a.w3_port)
    pid = None
    t_all = time.perf_counter()
    try:
        if a.start:
            info = start_server()
            pid = info["pid"]
            s.metrics["server"] = info
            s.check("сервер до Done", True, f"pid {pid}, {info['wall_s']} с")
        elif (RUN / "server.pid").exists():
            pid = int((RUN / "server.pid").read_text().strip())
        s.setup_area()
        s.first_sync()
        s.setblock()
        s.break_place()
        s.restart_bridge()
        s.restart_fake()
        s.save_load()
        s.tnt()
        s.ground_full()
        s.channel()
    except Exception as e:  # noqa: BLE001 — итог пишем при любом падении
        import traceback
        traceback.print_exc()
        s.check("прогон без исключений", False, repr(e))
    finally:
        s.cleanup()
        if a.stop:
            s.stop_server(pid)
    s.metrics["wall_s"] = round(time.perf_counter() - t_all, 1)
    errors = [ln for ln in (LOGS / "smoke_bridge.log").read_text(encoding="utf-8").splitlines()
              if " ERROR " in ln or "Traceback" in ln] if (LOGS / "smoke_bridge.log").exists() else []
    s.check("журнал моста без ERROR", not errors, f"{len(errors)} строк" + (f": {errors[0][:120]}" if errors else ""))
    n_ok = sum(1 for _, ok, _ in s.rows if ok)
    print(f"\nитог {n_ok}/{len(s.rows)}, {s.metrics['wall_s']} с")
    out = {"rows": [{"check": n, "ok": ok, "detail": d} for n, ok, d in s.rows], "metrics": s.metrics,
           "when": time.strftime("%Y-%m-%d %H:%M:%S")}
    (LOGS / "smoke_result.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return 0 if n_ok == len(s.rows) else 1


if __name__ == "__main__":
    if os.name != "nt":
        raise SystemExit("только Windows (mc\\run_server.ps1)")
    sys.exit(main())
