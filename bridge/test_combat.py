"""Тесты боя и стрел (combat.py и шаги моста):  python -m pytest test_combat.py -q   (из папки bridge).

Без Майнкрафта и без Ведьмака: ответы игры — Res из test_bridge, клиент — StubLink из test_client.
"""
from __future__ import annotations

import os
import time
import unittest

import spike_path  # noqa: F401
import coords

import combat
import config
from bridge import Bridge
from test_bridge import Res
from test_client import StubLink


class CombatUnitTest(unittest.TestCase):
    def test_parse_npcs_and_hurts(self):
        body = ["t 12500", "h 3 512", "n 3 1050 -2000 250 90 40 180 7", "n 4 0 0 0 0 50 90 9", "n x 1", "h 5 0", "pong 1"]
        npcs, hurts, t = combat.parse_npcs(body, y_offset=0)
        self.assertEqual(t, 12.5)
        self.assertEqual(hurts, [(3, 0.0512)])                    # нулевой удар отброшен
        self.assertEqual([n.id for n in npcs], [3, 4])
        n = npcs[0]
        self.assertEqual(n.p, coords.w_to_mc_pos(10.5, -20.0, 2.5, 0))   # W3 (x, y, z) -> MC (x, z, 1 - y)
        self.assertEqual(n.yaw, coords.w_yaw_to_mc(90))
        self.assertEqual((n.w, n.h, n.flags), (0.8, 1.8, 7))
        msg = combat.npcs_msg(npcs)
        self.assertEqual(msg["t"], "npcs")
        self.assertEqual(msg["l"][1], [4, 0.0, 0.0, 1.0, 180.0, 1.0, 0.9, 9, 0.0, 0.0, 0.0])
        self.assertEqual(combat.client_npcs_msg(npcs, {4: 77})["l"], [[77, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.9]])

    def test_npc_velocity(self):
        tr = combat.NpcTracker()
        a = combat.Npc(1, (0.0, 70.0, 0.0), 0.0, 0.8, 1.8, 1)
        tr.update([a], 10.0)
        self.assertEqual(a.v, (0.0, 0.0, 0.0))                   # первый снимок — скорости нет
        b = combat.Npc(1, (1.0, 70.0, 0.0), 0.0, 0.8, 1.8, 1)
        tr.update([b], 10.1)
        self.assertAlmostEqual(b.v[0], 5.0)                      # 10 м/с, сглажено с прошлой 0
        c = combat.Npc(1, (100.0, 70.0, 0.0), 0.0, 0.8, 1.8, 1)
        tr.update([c], 10.2)
        self.assertLessEqual(sum(x * x for x in c.v) ** 0.5, combat.NpcTracker.MAX_SPEED + 1e-6)   # телепорт — не скорость
        d = combat.Npc(1, (100.0, 70.0, 0.0), 0.0, 0.8, 1.8, 1)
        tr.update([d], 11.0)
        self.assertEqual(d.v, (0.0, 0.0, 0.0))                   # снимки дальше 0,5 с

    def test_arrow_path(self):
        cmds = combat.path_cmds([[1.0, 70.0, -2.0], [2.0, 70.5, -2.0]], y_offset=0)
        w0 = coords.mc_pos_to_w(1.0, 70.0, -2.0, 0)
        self.assertTrue(cmds.startswith(":".join(str(coords.to_cm(c)) for c in w0) + "|"))
        self.assertIsNone(combat.path_cmds([[1, 2, 3]]))           # одной точки мало
        self.assertIsNone(combat.path_cmds([[1, 2], [3, 4, 5]]))
        i, p = combat.wall_point(["3", "150", "-250", "7100"], y_offset=0)
        self.assertEqual((i, p), (3, [round(c, 3) for c in coords.w_to_mc_pos(1.5, -2.5, 71.0, 0)]))
        self.assertEqual(combat.wall_point(["-1"]), (-1, None))
        self.assertEqual(combat.wall_point(None), (-1, None))

    def test_hit_args(self):
        self.assertEqual(combat.hit_args({"id": 3, "dmg": 6.0, "kind": "melee", "crit": False}, 20.0, 7.0), (3, 3000, 0, 0, 1000))
        self.assertEqual(combat.hit_args({"id": 3, "dmg": 9.0, "kind": "melee"}, 20.0, 7.0), (3, 4500, 1, 0, 1500))
        self.assertEqual(combat.hit_args({"id": 3, "dmg": 2.0, "kind": "arrow", "crit": True}, 20.0, 7.0), (3, 1000, 1, 1, 333))
        self.assertEqual(combat.hit_args({"id": 3, "dmg": 80.0}, 20.0, 7.0)[1], 10000)   # не больше всего здоровья
        for bad in ({"id": 3, "dmg": 0}, {"id": 3}, {"dmg": 5}, {"id": "x", "dmg": 5}):
            self.assertIsNone(combat.hit_args(bad, 20.0, 7.0), bad)
        self.assertAlmostEqual(combat.hurt_dmg(0.05, 2.5), 2.5)

    def test_arrow_slots(self):
        a = combat.ArrowSlots(slots=2)
        fly = [7, 1.0, 70.0, -2.0, 0.0, 0.0, 1.0, 0.0, -1.0, 30.0, 0]
        cmds, bad = a.ops([fly, [8, "bad"]], y_offset=0)
        self.assertEqual(bad, 1)
        wx, wy, wz = coords.mc_pos_to_w(1.0, 70.0, -2.0, 0)
        # направление MC (0, 0, 1) — на юг -> W3 (0, -1, 0) x1000; скорость (0, -1, 30) -> (0, -30, -1) м/с в см/с
        self.assertEqual(cmds, [f"0:{coords.to_cm(wx)}:{coords.to_cm(wy)}:{coords.to_cm(wz)}:0:-1000:0:0:-3000:-100:1"])
        stuck = fly[:7] + [0.0, 0.0, 0.0, 1]
        cmds, _ = a.ops([stuck], y_offset=0)
        self.assertTrue(cmds[0].endswith(":0") and cmds[0].startswith("0:"))   # воткнулась — тот же слот
        cmds, _ = a.ops([stuck], y_offset=0)
        self.assertEqual(cmds, [])                               # торчит на месте — не шлём повторно
        cmds, _ = a.ops([[9] + fly[1:], [10] + fly[1:], [11] + fly[1:]], y_offset=0)
        self.assertEqual([c.split(":")[0] for c in cmds], ["1", "0"])   # 9 — слот 1; 10 и 11 — мест нет; 7 ушла
        self.assertEqual(cmds[-1], "0:off")


class CombatBridgeTest(unittest.TestCase):
    def setUp(self):
        self.br = Bridge(config.load(os.devnull, {"input": {"enabled": False}, "log": {"file": ""},
                                                  "client": {"overlay": ""}}))
        br = self.br
        br.w3_state = "synced"
        br.in_world = True
        self.cs = br.client
        self.cs.link = StubLink()
        self.cs.hello = {"t": "hello"}
        self.cs.state = {"t": "state", "world": True}
        self.sent = []
        br.mc.send = lambda m: self.sent.append(m) or True
        br.mc.sock = object()                                   # mc.up
        self.calls = []
        br._call = lambda kind, func, args, timeout, ctx: self.calls.append((kind, func, args, ctx)) or 1

    def tearDown(self):
        self.br.mc.sock = None

    def steve(self, on=True):
        self.br.fp_on = on
        self.cs.step(0.0, self.br.steve_wanted())

    def test_npcs_only_in_steve_mode_and_cleared_after(self):
        br = self.br
        br._step_npcs(time.perf_counter())
        self.assertEqual(self.calls, [])                        # без режима Стива NPC не нужны
        self.steve()
        br._step_npcs(time.perf_counter())
        self.assertEqual([c[1:3] for c in self.calls], [("mcw_npcs", (24, 16))])
        br._res_npcs(None, 1, Res(1, "h 2 400", "n 2 100 200 300 0 40 180 1"), None)
        self.assertEqual(self.sent[0]["t"], "npcs")
        self.assertEqual(self.sent[0]["l"][0][0], 2)
        self.assertEqual(self.sent[1], {"t": "phurt", "id": 2, "dmg": 2.0})   # 4 % x 20 x 2.5
        self.steve(False)
        br._step_npcs(time.perf_counter())
        self.assertEqual(self.sent[-1], {"t": "npcs", "l": []})  # вышли из режима — прокси убрать
        n = len(self.sent)
        br._step_npcs(time.perf_counter())
        self.assertEqual(len(self.sent), n)                     # и один раз

    def test_old_mod_without_combat(self):
        br = self.br
        self.steve()
        br._res_npcs(None, 1, Res(1, ok=False), None)
        self.assertIs(br.npcs_cap, False)
        self.calls.clear()
        br._step_npcs(time.perf_counter() + 10)
        self.assertEqual(self.calls, [])

    def test_npcs_to_client_and_arrow_walls(self):
        br = self.br
        self.steve()
        br.on_mc({"t": "npc_map", "m": [[2, 501]]})
        br._res_npcs(None, 1, Res(1, "t 1000", "n 2 100 200 300 0 40 180 1"), None)
        br._res_npcs(None, 2, Res(2, "t 1100", "n 2 200 200 300 0 40 180 1"), None)
        cl = [m for m in self.cs.link.sent if m.get("t") == "npcs"]
        self.assertEqual(cl[-1]["l"][0][0], 501)                 # клиенту — сущностью MC
        self.assertAlmostEqual(cl[-1]["l"][0][4], 5.0)           # 1 м за 0,1 с, сглажено
        self.assertAlmostEqual(self.sent[-1]["l"][0][8], 5.0)    # и серверу
        br.on_mc({"t": "arrow_path", "id": 9, "p": [[1.0, 70.0, -2.0], [2.0, 70.0, -2.0]]})
        kind, func, args, ctx = self.calls[-1]
        self.assertEqual((kind, func, ctx), ("path", "mcw_path", {"id": 9}))
        br._res_path(type("R", (), {"ctx": ctx})(), 3, Res(3, "path 0 150 300 7000"), None)
        self.assertEqual(self.sent[-1]["t"], "arrow_wall")
        self.assertEqual((self.sent[-1]["id"], self.sent[-1]["i"]), (9, 0))
        br._res_path(type("R", (), {"ctx": ctx})(), 4, Res(4, "path -1"), None)
        self.assertEqual(self.sent[-1], {"t": "arrow_wall", "id": 9, "i": -1})
        br.w3_state = "down"
        br.on_mc({"t": "arrow_path", "id": 10, "p": [[1.0, 70.0, -2.0], [2.0, 70.0, -2.0]]})
        self.assertEqual(self.sent[-1], {"t": "arrow_wall", "id": 10, "i": -1})   # без Ведьмака — путь чист

    def test_npc_hit_to_witcher(self):
        br = self.br
        br.on_mc({"t": "npc_hit", "id": 5, "dmg": 6.0, "kind": "melee", "crit": True})
        self.assertEqual([c[1:3] for c in self.calls], [("mcw_npcwarn", (5, 1))])   # сначала замах: NPC может защититься
        br._step_npc_hits(time.perf_counter())
        self.assertEqual(len(self.calls), 1)                                        # удар — через impact_delay_s
        br._step_npc_hits(time.perf_counter() + 1.0)
        kind, func, args, ctx = self.calls[-1]
        self.assertEqual((func, args), ("mcw_npchit", (5, 3000, 1, 0, 1000)))
        R = type("R", (), {"ctx": ctx})()
        br._res_npchit(R, 1, Res(1, "npchit 1 14500 10150 0 0"), None)
        self.assertEqual(br.stats["npc_hit"], 1)
        br._res_npchit(R, 2, Res(2, "npchit 1 14500 14500 1 0"), None)
        self.assertEqual(self.sent[-1], {"t": "npc_block", "id": 5, "how": "parry"})
        br._res_npchit(R, 3, Res(3, "npchit 1 14500 14500 0 1"), None)
        self.assertEqual(self.sent[-1], {"t": "npc_block", "id": 5, "how": "dodge"})
        br._res_npchit(R, 4, Res(4, "npchit 0 0 0 0 0"), None)
        self.assertEqual(br.stats["npc_hit_miss"], 1)
        self.calls.clear()
        br.on_mc({"t": "npc_hit", "id": 6, "dmg": 4.0, "kind": "arrow"})
        self.assertEqual([c[1:3] for c in self.calls], [("mcw_npchit", (6, 2000, 0, 1, 667))])   # стрела — сразу

    def test_shield_guard(self):
        br, cs = self.br, self.cs
        self.steve()
        br._step_guard()
        self.assertEqual([c[1:3] for c in self.calls], [("mcwf_guard", (0,))])   # что в игре, неизвестно — снять
        br._res_guard(None, 1, Res(1, "fpguard 0"), None)
        self.calls.clear()
        br._step_guard()
        self.assertEqual(self.calls, [])
        cs.state = {"t": "state", "world": True, "block": True}
        br._step_guard()
        self.assertEqual([c[1:3] for c in self.calls], [("mcwf_guard", (1,))])
        br._res_guard(None, 1, Res(1, "fpguard 1"), None)
        self.assertIs(br.w3_guard, True)
        self.calls.clear()
        br._step_guard()
        self.assertEqual(self.calls, [])
        cs.state = {"t": "state", "world": True, "block": False}
        br._step_guard()
        self.assertEqual([c[1:3] for c in self.calls], [("mcwf_guard", (0,))])
        br._res_guard(None, 2, Res(2, ok=False), None)
        self.assertIs(br.guard_cap, False)

    def test_arrows_init_then_frames(self):
        br, cs = self.br, self.cs
        cs.on_msg(1, {"t": "arrows", "a": [[7, 1.0, 70.0, -2.0, 0.0, 0.0, 1.0, 0.0, 0.0, 30.0, 0]]})
        br._step_arrows()
        self.assertEqual([c[1:3] for c in self.calls], [("mcwa_arrows", ("init",))])   # сначала пул
        br._res_arrows(type("R", (), {"ctx": {"init": True}})(), 1, Res(1, "arrows 0 0"), None)
        self.assertTrue(br.arrows_ready)
        self.calls.clear()
        br._step_arrows()
        self.assertEqual(self.calls[0][1], "mcwa_arrows")
        self.assertTrue(self.calls[0][2][0].startswith("0:"))
        self.calls.clear()
        br._step_arrows()
        self.assertEqual(self.calls, [])                        # нового кадра нет

    def test_eye_guard_on_unknown_columns(self):
        br, cs = self.br, self.cs
        self.steve()
        br.fp_src = 1
        br.cam_fov = 60.0
        br.ground.height[(-300, 300)] = 7000                    # земля известна под игроком
        cs.on_msg(1, {"t": "eye", "p": [-299.5, 71.62, 300.5], "v": [0.0, 0.0, 0.0], "h": 1.62})
        br._step_drive(time.perf_counter())
        self.assertEqual(self.calls[-1][1], "mcwf_mc")
        self.calls.clear()
        # ускакал на колонну, о которой мост не знает, и падает: глаз на 5 м ниже последней известной земли
        cs.on_msg(1, {"t": "eye", "p": [-294.5, 66.62, 300.5], "v": [0.0, -20.0, 0.0], "h": 1.62})
        br._step_drive(time.perf_counter())
        self.assertEqual(self.calls, [])
        self.assertEqual(br.stats["eye_below_ground"], 1)


if __name__ == "__main__":
    unittest.main()
