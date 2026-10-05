"""Тесты моста со скрытым клиентом Майнкрафта (client.py):  python -m pytest test_client.py -q   (из папки bridge).

Без Майнкрафта и без Ведьмака: клиент — FakeClient ниже (TCP-сервер, как мод mcwc: hello, state, запись входящего),
Ведьмак и серверный мод — fake_w3 и FakeMod из test_bridge.
"""
from __future__ import annotations

import os
import socket
import threading
import time
import unittest

import spike_path  # noqa: F401
import coords

import config
import hotkeys
from bridge import Bridge, Pose
from client import ClientSide, parse_pass
from links import LineFramer, encode
from test_bridge import CUBE, EXPECTED, WALL, Loop, Res, wait_until


class FakeClient:
    """Мод mcwc в объёме моста: на подключение — hello, раз в 50 мс — state; всё входящее — в got."""

    def __init__(self, world: bool = True) -> None:
        self.world = world
        self.walking = False                      # мост сказал follow off: шлём eye, как мод каждый кадр
        self.eye = [-299.5, 71.62, 300.5]
        self.got: list[dict] = []
        self.lock = threading.Lock()
        self.conn = None
        self.alive = True
        self.connects = 0
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(2)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while self.alive:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            self.conn = c
            self.connects += 1
            c.sendall(encode({"t": "hello", "v": 1, "mc": "1.21.11", "mod": "test", "name": "Geralt",
                              "title": "MCW Client", "key": "magenta", "world": self.world, "win": [1920, 1080]}))
            threading.Thread(target=self._read, args=(c,), daemon=True).start()
            threading.Thread(target=self._state, args=(c,), daemon=True).start()

    def _read(self, c) -> None:
        fr = LineFramer()
        while True:
            try:
                data = c.recv(65536)
            except OSError:
                return
            if not data:
                return
            with self.lock:
                for m in fr.feed(data):
                    self.got.append(m)
                    if m.get("t") == "follow":
                        self.walking = not m["on"]

    def _state(self, c) -> None:
        tick = 0
        while self.alive and self.conn is c:
            tick += 1
            try:
                c.sendall(encode({"t": "state", "tick": tick, "world": self.world, "fps": 60, "win": [1920, 1080]}))
                if self.walking:
                    c.sendall(encode({"t": "eye", "p": self.eye, "v": [1.0, 0.0, 0.0], "h": 1.62, "ground": True}))
            except OSError:
                return
            time.sleep(0.05)

    def kinds(self, t: str) -> list[dict]:
        with self.lock:
            return [m for m in self.got if m.get("t") == t]

    def drop(self) -> None:
        c, self.conn = self.conn, None
        if c is not None:
            c.close()

    def close(self) -> None:
        self.alive = False
        self.drop()
        self.srv.close()


class StubLink:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.up = True
        self.connecting = False
        self.gen = 1

    def send(self, m) -> bool:
        self.sent.append(m)
        return True

    def close(self) -> None:
        self.up = False


def stub_client(**over) -> ClientSide:
    cfg = config.load(os.devnull, {"client": {"overlay": "", **over}})
    cs = ClientSide(cfg, None)
    cs.link = StubLink()
    cs.hello = {"t": "hello"}
    cs.state = {"t": "state", "world": True}
    return cs


class ClientUnitTest(unittest.TestCase):
    def test_parse_pass(self):
        self.assertEqual(parse_pass({"MOUSE1": "btn:0", "f": "key:70"}),
                         {"MOUSE1": (0x01, "btn", 0), "F": (ord("F"), "key", 70)})
        for bad in ({"MOUSE1": "btn"}, {"MOUSE1": "mouse:0"}, {"MOUSE1": "btn:9"}, {"Q": "key:5"}, {"NOPE": "btn:0"}):
            with self.assertRaises(ValueError, msg=bad):
                parse_pass(bad)
        with self.assertRaises(ValueError):
            config.load(os.devnull, {"client": {"pass": {"MOUSE1": 0}}})
        # таблица заменяется целиком: средняя кнопка из значений по умолчанию не остаётся
        self.assertEqual(config.load(os.devnull, {"client": {"pass": {"MOUSE1": "btn:0"}}}).client["pass"],
                         {"MOUSE1": "btn:0"})

    def test_passthrough_edges(self):
        down, fg = set(), [True]
        p = hotkeys.Passthrough({"MOUSE1": 0x01, "MOUSE2": 0x02}, lambda vk: vk in down, lambda: fg[0])
        self.assertEqual(p.step(), [])
        down.add(0x01)
        self.assertEqual(p.step(), [("MOUSE1", True)])
        self.assertEqual(p.step(), [])                          # держим — повтора нет
        down.discard(0x01)
        self.assertEqual(p.step(), [("MOUSE1", False)])
        fg[0] = False
        down.add(0x02)
        self.assertEqual(p.step(), [])                          # нажата в другом окне
        fg[0] = True
        self.assertEqual(p.step(), [])                          # и клик, которым вернулись в игру, не нажимает
        down.clear()
        p.step()
        down.add(0x02)
        self.assertEqual(p.step(), [("MOUSE2", True)])
        fg[0] = False
        self.assertEqual(p.step(), [("MOUSE2", False)])         # игра ушла с переднего плана — отпускаем

    def test_edge_only_when_active(self):
        cs = stub_client(drive=False)
        cs.edge("MOUSE1", True)
        self.assertEqual(cs.link.sent, [])                      # режим Стива выключен: не нажимаем
        cs.edge("MOUSE1", False)
        self.assertEqual(cs.link.sent, [{"t": "btn", "b": 0, "a": 0}])   # отпускание — всегда (клиент сам отфильтрует)
        cs.link.sent.clear()
        self.assertTrue(cs.step(0.0, True))
        self.assertEqual(cs.link.sent, [{"t": "follow", "on": True}])     # без drive клиент идёт за Ведьмаком
        cs.link.sent.clear()
        cs.edge("MOUSE2", True)
        cs.edge("W", True)                                      # движение — только когда ходит клиент
        cs.tap_key(51)
        cs.scroll(-1.0)
        self.assertEqual(cs.link.sent, [{"t": "btn", "b": 1, "a": 1}, {"t": "key", "k": 51, "a": 1},
                                        {"t": "key", "k": 51, "a": 0}, {"t": "scroll", "d": -1.0}])
        cs.link.sent.clear()
        cs.state = {"t": "state", "world": False}               # клиент вышел из мира — режим гаснет, всё отпущено
        self.assertFalse(cs.step(0.0, True))
        self.assertEqual(cs.link.sent, [{"t": "release"}])

    def test_drive_mode(self):
        cs = stub_client()
        self.assertTrue(cs.drive)
        cs.edge("W", True)
        self.assertEqual(cs.link.sent, [])                      # режим Стива выключен
        cs.step(0.0, False)
        self.assertEqual(cs.link.sent, [{"t": "follow", "on": True}])
        cs.link.sent.clear()
        cs.step(0.0, True)                                      # режим Стива: ходит клиент
        self.assertTrue(cs.driving)
        cs.edge("W", True)
        cs.edge("LSHIFT", True)
        cs.pose(1.0, 2.0, 3.0, 0.0, 0.0)                        # позу клиенту, который ходит сам, не шлём
        self.assertEqual(cs.link.sent, [{"t": "follow", "on": False}, {"t": "key", "k": 87, "a": 1},
                                        {"t": "key", "k": 340, "a": 1}])
        cs.on_msg(1, {"t": "eye", "p": [1, 2, 3], "v": [0, 0, 0], "h": 1.62})
        self.assertTrue(cs.eye_fresh(time.perf_counter()) and cs.eye_new)
        cs.on_msg(1, {"t": "hello"})
        self.assertIsNone(cs.follow)                            # клиент перезапустился — follow заново
        with self.assertRaises(ValueError):
            stub_client(pass_drive={"MOUSE1": "key:87"})        # одна клавиша и в pass, и в pass_drive

    def test_down_and_messages(self):
        cs = stub_client()
        cs.on_msg(1, {"t": "state", "world": True, "fps": 60})
        self.assertTrue(cs.in_world)
        cs.on_msg(2, {"t": "state", "world": False})            # старое поколение соединения — мимо
        self.assertTrue(cs.in_world)
        cs.step(0.0, True)
        cs.on_down(1, ConnectionError("x"))
        self.assertFalse(cs.up)
        self.assertFalse(cs.active)
        self.assertEqual(cs.stats["lost"], 1)


class FakeRaw:
    def __init__(self):
        self.queue = []

    def take(self):
        return self.queue.pop(0) if self.queue else (0, 0, 0.0)


class TerrainTest(unittest.TestCase):
    def test_heights_for_client(self):
        from ground import GroundPlanner
        g = GroundPlanner(radius_m=3.0, y_offset=0)
        g.on_height((0, 0), True, 169, 0.0)
        g.on_height((2, 0), True, 250, 0.0)
        g.on_height((1, 1), False, 0, 0.0)                     # без опоры — высоты нет
        self.assertEqual(g.take_terrain((0, 0), 10), [0, 0, 169, 2, 0, 250])   # ближние первыми
        self.assertEqual(g.take_terrain((0, 0), 10), [])       # уже отправлены
        g.on_height((2, 0), True, 251, 0.0)
        self.assertEqual(g.take_terrain((0, 0), 10), [2, 0, 251])               # поменялась — заново
        g.client_reset()
        self.assertEqual(len(g.take_terrain((0, 0), 1)), 3)    # новый клиент — всё заново, не больше max_cols
        g.trim((100.0, 100.0), 10.0)
        self.assertEqual(g.height, {})


class BridgeClientLogicTest(unittest.TestCase):
    """Решения моста про клиента без сети: слоты, блокировка ударов Геральта, поза."""

    def setUp(self):
        self.br = Bridge(config.load(os.devnull, {"input": {"enabled": False}, "log": {"file": ""},
                                                  "client": {"overlay": ""}}))
        self.br.w3_state = "synced"
        self.br.in_world = True
        self.cs = self.br.client
        self.cs.link = StubLink()
        self.cs.hello = {"t": "hello"}
        self.cs.state = {"t": "state", "world": True}

    def test_slots_go_to_client_in_steve_mode(self):
        br = self.br
        sent = []
        br.mc.send = lambda m: sent.append(m) or True
        br.mc.sock = object()                                   # mc.up
        try:
            br.fp_on = True
            br.client.step(0.0, br.steve_wanted())
            br.on_key("slot3")
            br.on_key("slot_next")
            self.assertEqual(self.cs.link.sent[-3:], [{"t": "key", "k": 51, "a": 1}, {"t": "key", "k": 51, "a": 0},
                                                      {"t": "scroll", "d": -1.0}])
            self.assertEqual(sent, [])                          # серверу act select не шлём: слот выберет клиент
            br.fp_on = False
            br.client.step(0.0, br.steve_wanted())
            br.build_mode = True
            br.on_key("slot3")
            self.assertEqual(sent[-1]["a"], "select")           # без режима Стива — как раньше, через сервер
        finally:
            br.mc.sock = None

    def test_block_witcher_attacks(self):
        br = self.br
        calls = []
        br._call = lambda kind, func, args, timeout, ctx: calls.append((kind, func, args)) or 1
        br._step_block(False)
        self.assertEqual(calls, [])                                  # ещё не спросили mcwf_state — ждём
        br.fp_auto_done = True
        br._step_block(False)
        self.assertEqual(calls, [("block", "mcwf_block", (0,))])    # не знаем, что стоит в игре — снять своё
        br._res_block(None, 1, Res(1, "fpblock 0"), None)
        self.assertEqual(br.w3_block, 0)
        calls.clear()
        br._step_block(False)
        self.assertEqual(calls, [])
        br._step_block(True)
        self.assertEqual(calls, [("block", "mcwf_block", (1,))])     # клиент не ходит сам — только бой
        br._res_block(None, 2, Res(2, "fpblock 1"), None)
        self.assertEqual(br.w3_block, 1)
        calls.clear()
        br.fp_on = True
        self.cs.step(0.0, br.steve_wanted())                          # режим Стива, drive: ходит клиент
        br._step_block(True)
        self.assertEqual(calls, [("block", "mcwf_block", (2,))])
        br._res_block(None, 3, Res(3, "fpblock 2"), None)
        self.assertEqual(br.w3_block, 2)
        br._res_block(None, 3, Res(3, ok=False), None)          # старый мод без mcwf_block — больше не пробуем
        self.assertIs(br.block_cap, False)
        calls.clear()
        br._step_block(False)
        self.assertEqual(calls, [])

    def test_fp_state_adopted_after_bridge_restart(self):
        br = self.br
        calls = []
        br._call = lambda kind, func, args, timeout, ctx: calls.append((kind, func)) or 1
        br._step_fp_start()
        br._step_fp_start()
        self.assertEqual(calls, [("fpstate", "mcwf_state")])          # раз на подключение
        br._res_fpstate(None, 1, Res(1, "fpstate 1 0 12570 -1250 900 0 0"), None)
        self.assertTrue(br.fp_on)                                     # игра уже в виде от первого лица
        self.assertTrue(br.steve_wanted())

    def test_drive_eye_look_and_fov(self):
        br, cs = self.br, self.cs
        calls = []
        br._call = lambda kind, func, args, timeout, ctx: calls.append((kind, func, args)) or 1
        br.fp_on = True
        br.fp_auto_done = True
        br.fp_src = 0
        cs.step(0.0, br.steve_wanted())
        self.assertTrue(cs.driving)
        br._step_drive(time.perf_counter())
        self.assertEqual(calls, [("fov", "mcwf_fov", (0,))])         # FOV — сразу; глаза от клиента ещё нет
        cs.state = {"t": "state", "world": True, "win": [1920, 1080]}
        br._res_fov(None, 1, Res(1, "fov 9000 9000"), None)
        self.assertEqual(cs.link.sent[-1], {"t": "fov", "v": 90.0})    # FOV камеры Ведьмака — вертикальный
        calls.clear()
        cs.fov_sent = None                                            # клиент переподключился: FOV — снова, без запроса
        br._step_drive(time.perf_counter())
        self.assertEqual(calls, [])                                   # камера по-прежнему в голове Геральта
        self.assertEqual(cs.link.sent[-1], {"t": "fov", "v": 90.0})
        cs.on_msg(1, {"t": "eye", "p": [-299.5, 71.62, 300.5], "v": [4.0, -1.0, 2.0], "h": 1.62})
        br._step_drive(time.perf_counter())
        self.assertEqual(calls, [("fpsrc", "mcwf_on", (1, 1, 30))])   # глаз камеры — от клиента
        br._res_fpsrc(None, 2, Res(2, "fp 1 1 0 0"), None)
        self.assertEqual(br.fp_src, 1)
        calls.clear()
        br._step_drive(time.perf_counter())
        # MC (x, y, z) -> W3 (x, 1 - z, y); скорость (vx, -vz, vy); всё в см; высота глаза 162 см
        self.assertEqual(calls, [("mc", "mcwf_mc", (-29950, -29950, 7162, 400, -200, -100, 162))])
        br._res_mc(None, 3, Res(3, "look 9000 -1250 77 0"), None)
        self.assertEqual(cs.link.sent[-1], {"t": "look", "yaw": 90.0, "pitch": 12.5})   # W3 курс 90 = запад = MC 90
        calls.clear()
        br._step_drive(time.perf_counter())
        self.assertEqual(calls, [])                                   # нового eye ещё нет
        # игрок MC провалился сквозь землю Ведьмака (колонна под ним известна, ноги на 3 м ниже): камеру не ведём
        br.ground.height[(-300, 300)] = 7000
        cs.on_msg(1, {"t": "eye", "p": [-299.5, 68.62, 300.5], "v": [0.0, -20.0, 0.0], "h": 1.62})
        br._step_drive(time.perf_counter())
        self.assertEqual(calls, [])
        self.assertEqual(br.stats["eye_below_ground"], 1)

    def test_e_key_interaction_or_inventory(self):
        br, cs = self.br, self.cs
        calls = []
        br._call = lambda kind, func, args, timeout, ctx: calls.append((kind, func)) or 1
        br.on_key("inventory")
        self.assertEqual(calls, [])                                   # режим Стива выключен: E — Ведьмаку
        br.fp_on = True
        cs.step(0.0, br.steve_wanted())
        cs.link.sent.clear()
        br.on_key("inventory")
        self.assertEqual(calls, [("interact", "mcwf_interact")])      # спросить, есть ли подсказка взаимодействия
        br._res_interact(None, 1, Res(1, "interact 1"), None)
        self.assertEqual(cs.link.sent, [])                            # есть — дверь или NPC, E уже у Ведьмака
        br._res_interact(None, 2, Res(2, "interact 0"), None)
        self.assertEqual(cs.link.sent, [{"t": "key", "k": 69, "a": 1}, {"t": "key", "k": 69, "a": 0}])   # инвентарь MC
        cs.link.sent.clear()
        calls.clear()
        cs.state = {"t": "state", "world": True, "screen": "InventoryScreen"}
        br.on_key("inventory")                                        # экран MC открыт: E его закрывает, без вопроса
        self.assertEqual(calls, [])
        self.assertEqual(cs.link.sent[0], {"t": "key", "k": 69, "a": 1})

    def test_screen_freeze_cursor_and_wheel(self):
        br, cs = self.br, self.cs
        calls = []
        br._call = lambda kind, func, args, timeout, ctx: calls.append((func, args)) or 1
        br.raw = FakeRaw()
        br.fp_on = True
        cs.step(0.0, br.steve_wanted())
        br._step_freeze()
        self.assertEqual(calls, [("mcwf_freeze", (0,))])
        br._res_freeze(None, 1, Res(1, "fpfreeze 0"), None)
        cs.link.sent.clear()
        br.raw.queue = [(5, -3, 1.0)]
        br._step_mouse()                                              # экрана нет: колесо — слот, курсора нет
        self.assertEqual(cs.link.sent, [{"t": "scroll", "d": 1.0}])
        cs.state = {"t": "state", "world": True, "screen": "InventoryScreen"}
        calls.clear()
        br._step_freeze()
        self.assertEqual(calls, [("mcwf_freeze", (1,))])              # взгляд Ведьмака стоит, пока открыт инвентарь
        cs.link.sent.clear()
        br.raw.queue = [(12, 7, 0.0)]
        br._step_mouse()
        self.assertEqual(cs.link.sent, [{"t": "cursor", "dx": 12.0, "dy": 7.0}])

    def test_scene_suspends_steve_and_mc_actions_skipped(self):
        br, cs = self.br, self.cs
        br.fp_on = True
        self.assertTrue(br.steve_wanted())
        br._res_fpwatch(None, 1, Res(1, "fpstate 1 1 0 0 10 0 0 1"), None)
        self.assertTrue(br.w3_susp)
        self.assertFalse(br.steve_wanted())                           # диалог или катсцена: режим Стива ждёт
        br._res_fpwatch(None, 2, Res(2, "fpstate 1 1 0 0 10 0 0 0"), None)
        self.assertTrue(br.steve_wanted())
        cs.step(0.0, True)
        calls = []
        br._call = lambda kind, func, args, timeout, ctx: calls.append(func) or 1
        br.build_mode = True
        br.on_key("place")                                            # ставит сам MC (ПКМ), mcw_look не нужен
        self.assertEqual(calls, [])
        self.assertIn("Q", cs.passes)
        self.assertEqual(cs.passes["5"][1:], ("key", 53))

    def test_horses_to_slots_and_hide(self):
        br, cs = self.br, self.cs
        calls = []
        br._call = lambda kind, func, args, timeout, ctx: calls.append((func, args)) or 1
        cs.horses = [[77, 10.5, 70.0, -3.25, 90.0, 10.0, 1.5, 0.4, 7], [78, 0.0, 70.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1]]
        cs.horses_new = True
        br._step_horses()
        self.assertEqual([c[1][0] for c in calls], [0, 1])               # слоты по порядку
        func, args = calls[0]
        self.assertEqual(func, "mcwm_horse")
        wx, wy, wz = coords.mc_pos_to_w(10.5, 70.0, -3.25, y_offset=br.y_off)
        self.assertEqual(args[1:4], (coords.to_cm(wx), coords.to_cm(wy), coords.to_cm(wz)))
        self.assertEqual(args[4], 9000)                                   # курс W3 = 180 - 90
        self.assertEqual(args[5:], (1000, 1500, 400, 7))
        self.assertEqual(calls[1][1][4], 18000)
        calls.clear()
        br._step_horses()                                                 # нового кадра нет — тишина
        self.assertEqual(calls, [])
        cs.horses = [[78, 1.0, 70.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1]]
        cs.horses_new = True
        br._step_horses()
        self.assertEqual(calls[0][1][0], 1)                               # у лошади свой слот
        self.assertEqual(calls[1], ("mcwm_horse", (0, 0, 0, 0, 0, 0, 0, 0, 0)))   # ушедшая — спрятать
        self.assertEqual(br.horse_slots, {78: 1})
        calls.clear()
        cs.horses = [[78, 1.0, 70.0, 0.0, 0.0, 0.0, 0.0, 0.0, 7, 0.5, -1.2, -0.25]]   # под всадником: ноги от глаза
        cs.horses_new = True
        br._step_horses()
        self.assertEqual(calls[0][1][1:4], (50, 25, -120))                # MC (dx, dy, dz) -> W3 (dx, -dz, dy), см
        self.assertEqual(calls[0][1][8], 7 | 8)
        calls.clear()
        br.ground.height[(1, 0)] = 7030                                    # земля Ведьмака на 0,3 м выше barrier
        cs.horses = [[78, 1.0, 70.0, 0.0, 0.0, 0.0, 0.0, 0.0, 3]]         # без всадника — на настоящую землю
        cs.horses_new = True
        br._step_horses()
        self.assertEqual(calls[0][1][3], coords.to_cm(coords.mc_pos_to_w(1.0, 70.3, 0.0, y_offset=br.y_off)[2]))

    def test_horse_key_only_in_steve_mode(self):
        br = self.br
        sent = []
        br.mc.send = lambda m: sent.append(m) or True
        br.mc.sock = object()
        br.on_key("horse")
        self.assertEqual(sent, [])                                        # вне режима Стива X — Плотве
        br.fp_on = True
        br.client.step(0.0, br.steve_wanted())
        br.on_key("horse")
        self.assertEqual(sent, [{"t": "horse"}])

    def test_skin_state(self):
        br = self.br
        calls = []
        br._call = lambda kind, func, args, timeout, ctx: calls.append((func, args)) or 1
        br._step_skin(0.0)
        self.assertEqual(calls, [("mcwm_skin", (1,))])
        br._res_skin(None, 1, Res(1, "skin 0 0"), None)                  # шаблонов нет: игра без modMCWModels
        self.assertEqual(br.skin_state, 0)
        calls.clear()
        br._step_skin(10.0)
        self.assertEqual(calls, [])                                       # следующий вопрос — через 30 с
        br._step_skin(40.0)
        br._res_skin(None, 2, Res(2, "skin 1 20"), None)
        self.assertEqual(br.skin_state, 1)

    def test_mc_freeze_on_witcher_pause(self):
        br = self.br
        sent = []
        br.mc.send = lambda m: sent.append(m) or True
        br.mc.sock = object()                                             # mc.up
        br._step_mc_freeze()
        self.assertEqual(sent, [])                                        # состояния Ведьмака ещё не знаем
        br._res_w3pause(None, 1, Res(1, "state active 1 paused 0 apause 0 stopped 0 menu 0 gtime 1 ems 2"), None)
        br._step_mc_freeze()
        self.assertEqual(sent, [{"t": "cmd", "c": "tick unfreeze"}])
        br._step_mc_freeze()
        self.assertEqual(len(sent), 1)                                    # шлётся только смена
        br._res_w3pause(None, 2, Res(2, "state active 0 paused 1 apause 0 stopped 0 menu 1 gtime 1 ems 2"), None)
        br._step_mc_freeze()
        self.assertEqual(sent[-1], {"t": "cmd", "c": "tick freeze"})     # меню паузы: мобы MC стоят
        br._res_w3pause(None, 3, Res(3, "state active 1 paused 0 apause 0 stopped 0 menu 0 gtime 1 ems 2"), None)
        br.w3_susp = True
        br._step_mc_freeze()
        self.assertEqual(len(sent), 2)                                    # сцена — по-прежнему стоим
        br.w3_susp = False
        br._step_mc_freeze()
        self.assertEqual(sent[-1], {"t": "cmd", "c": "tick unfreeze"})
        br.on_mc_up = lambda: None
        br.mc_frozen = None                                               # новый сервер: состояние — заново
        br._step_mc_freeze()
        self.assertEqual(sent[-1], {"t": "cmd", "c": "tick unfreeze"})
        self.assertEqual(len(sent), 4)

    def test_supervise_counts_only_live_link(self):
        br = self.br
        runs = []
        from supervise import Restarter
        br.sup_server = Restarter("сервер", "s.ps1", 20.0, 90.0, 10.0, runner=lambda s, a, t: runs.append(a) or (0, ""))
        br.sup_client = None
        br.mc.connecting = True                                          # попытки подключения — не связь
        br._step_supervise(0.0)
        br.mc.connecting = False
        br._step_supervise(10.0)
        br.mc.connecting = True
        br._step_supervise(21.0)
        self.assertEqual(br.sup_server.restarts, 1)

    def test_geralt_swap_message(self):
        br = self.br
        br.last_pos_key = (1, 2, 3, 0, 0)
        br.on_mc({"t": "geralt", "tick": 5, "client": True, "p": [18.4, 2.7, 45.6], "dim": "minecraft:overworld"})
        self.assertIsNone(br.last_pos_key)                            # позу серверу — со следующим опросом
        self.assertEqual(br.stats["geralt_client"], 1)

    def test_pose_to_client(self):
        br = self.br
        br.pose = Pose(coords.Pos(-30050, -29950, 7000, 9000), None, 0.0)
        br._send_client_pose()
        m = self.cs.link.sent[-1]
        x, y, z, yaw = br.pose.mc(br.y_off)
        self.assertEqual(m, {"t": "pose", "p": [round(x, 3), round(y, 3), round(z, 3)], "yaw": round(yaw, 2),
                             "pitch": 0.0})


class ClientLoopTest(unittest.TestCase):
    """Мост в потоке + fake_w3 + FakeMod + FakeClient: поза, режим Стива по F4, мышь и слоты клиенту, блокировка."""

    def setUp(self):
        self.fc = FakeClient()
        self.L = Loop({**WALL, **CUBE}, client={"enabled": True, "port": self.fc.port, "overlay": "",
                                               "reconnect_min_s": 0.1, "reconnect_max_s": 0.3})
        self.addCleanup(self.L.close)
        self.addCleanup(self.fc.close)

    def test_steve_mode(self):
        L, fc = self.L, self.fc
        self.assertTrue(wait_until(lambda: L.synced(EXPECTED), 15))
        self.assertTrue(wait_until(lambda: L.fw.world.skin, 5))             # тело Стива на Геральте (mcwm_skin)
        # стена дома в соседнем блоке: 4 подколонны по x — стена до 3,31 м, остальные — пол на 0,88 м
        L.fw.world.statics[(-299, 300)] = "0 -,331/=/=/=/" + "/".join(["-,88"] + ["="] * 11)
        self.assertTrue(wait_until(lambda: L.br.client.in_world and fc.kinds("pose"), 5))
        p = fc.kinds("pose")[-1]["p"]
        x, y, z, _ = L.br.pose.mc(L.br.y_off)
        self.assertAlmostEqual(p[0], x, places=2)
        self.assertAlmostEqual(p[2], z, places=2)
        # без вида от первого лица мышь у Ведьмака
        L.down.add(hotkeys.VK["MOUSE1"])
        time.sleep(0.1)
        L.down.clear()
        time.sleep(0.1)
        self.assertFalse([m for m in fc.kinds("btn") if m["a"] == 1])
        L.press("F4")
        self.assertTrue(wait_until(lambda: L.br.client.active and L.fw.fp["block"], 3))
        L.down.add(hotkeys.VK["MOUSE1"])
        self.assertTrue(wait_until(lambda: {"t": "btn", "b": 0, "a": 1} in fc.kinds("btn"), 2))
        L.down.clear()
        self.assertTrue(wait_until(lambda: fc.kinds("btn")[-1] == {"t": "btn", "b": 0, "a": 0}, 2))
        L.press("NUMPAD3")
        self.assertTrue(wait_until(lambda: {"t": "key", "k": 51, "a": 1} in fc.kinds("key"), 2))
        # ходит клиент: follow off, глаз клиента -> камера и кукла Ведьмака, движение Геральта заблокировано
        self.assertTrue(wait_until(lambda: L.fw.fp["src"] == 1 and L.fw.fp["mc_calls"] > 3, 3))
        self.assertEqual(L.fw.fp["block"], 2)
        self.assertEqual(L.fw.world.player[:3], [-29950, -29950, 7000])     # кукла в ногах игрока MC (глаз − 162 см)
        self.assertTrue(fc.kinds("look"))
        self.assertTrue(fc.kinds("fov"))
        # земля Ведьмака клиенту: сначала reset, потом колонны [x, z, h_cm] вокруг Геральта (fake: плоско на 70 м)
        self.assertTrue(wait_until(lambda: any(m.get("c") for m in fc.kinds("terrain")), 3))
        t = fc.kinds("terrain")
        self.assertTrue(t[0].get("reset"))
        cols = [m["c"] for m in t if m.get("c")][0]
        self.assertEqual(len(cols) % 3, 0)
        self.assertEqual(cols[2], 7000)
        # коллизия домов (mcw_cols): только в режиме Стива, сначала reset, колонны без статики — [x, z, h, 0, None]
        self.assertTrue(wait_until(lambda: any(m.get("c") for m in fc.kinds("solid")), 3))
        sol = fc.kinds("solid")
        self.assertTrue(sol[0].get("reset"))
        got = {(c[0], c[1]): c for m in sol for c in m.get("c") or []}
        self.assertEqual(got[(-300, 300)], [-300, 300, 7000, 0, None])
        wall = got[(-299, 300)]
        self.assertEqual(wall[:4], [-299, 300, 7000, 0])
        self.assertEqual(wall[4][0], [None, 7331])
        self.assertEqual(wall[4][3], [None, 7331])
        self.assertEqual(wall[4][4], [None, 7088])
        self.assertEqual(wall[4][15], [None, 7088])
        L.down.add(hotkeys.VK["W"])
        self.assertTrue(wait_until(lambda: {"t": "key", "k": 87, "a": 1} in fc.kinds("key"), 2))
        L.down.clear()
        L.press("F4")
        self.assertTrue(wait_until(lambda: not L.br.client.active and not L.fw.fp["block"], 3))
        self.assertTrue(fc.kinds("release"))
        self.assertTrue(wait_until(lambda: fc.kinds("follow")[-1]["on"], 2))   # снова идёт за Ведьмаком
        # клиент перезапустился: мост переподключается сам
        n = fc.connects
        fc.drop()
        self.assertTrue(wait_until(lambda: fc.connects > n and L.br.client.in_world, 5))


if __name__ == "__main__":
    unittest.main()
