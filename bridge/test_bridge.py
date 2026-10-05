"""Тесты моста:  python -m pytest test_bridge.py -q   или   python -m unittest test_bridge -v   (из папки bridge).

Без Майнкрафта и без Ведьмака: Ведьмак — spike/fake_w3.FakeWitcher на эфемерном порту (никогда 37000/37001), мод —
FakeMod ниже (TCP-сервер с протоколом mc/protocol_examples.jsonl в объёме, который нужен мосту).
"""
from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
import unittest

import spike_path  # noqa: F401
import coords
import fake_w3

import config
import hotkeys
from bridge import Bridge, safe_text, world_name
from craft import CraftMenu
from ground import GroundPlanner, col_center_cm, top_from_cm
from links import LineFramer, encode
from mobs import EntDisplay, EntTable, pack_ents
from world import BlockRules, Display, Mirror, Selector, base_name, look_target, pack

TPL = coords.by_batch_t(coords.load_templates())
CRATE = TPL[0]


def wait_until(pred, timeout=10.0, step=0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()


# ---------------------------------------------------------------------------------------------------------- конфиг
class ConfigTest(unittest.TestCase):
    def test_defaults_and_file_agree(self):
        a = config.load(None, local=False)
        self.assertEqual(a.w3.port, 37001)
        self.assertEqual(a.batch.bytes, 1900)
        self.assertEqual(a.display.radius_m, 32.0)
        self.assertEqual(a.display.max_entities, 2000)
        self.assertEqual(a.input.bind["break"], ["MOUSE4", "8"])
        # bridge.toml повторяет значения по умолчанию; намеренно иначе одно: сторож процессов (по умолчанию выключен —
        # тесты и смоуки не перезапускают настоящие сервер и клиент, включает его bridge.toml)
        b = config.load(os.devnull)
        self.assertTrue(a.supervise.enabled)
        self.assertFalse(b.supervise.enabled)
        a["supervise"]["enabled"] = False
        self.assertEqual({k: v for k, v in a.items() if k != "_file"}, {k: v for k, v in b.items() if k != "_file"})

    def test_unknown_key_and_types(self):
        with self.assertRaises(ValueError):
            config.load(os.devnull, {"w3": {"prot": 1}})
        with self.assertRaises(ValueError):
            config.load(os.devnull, {"w3": {"port": "37101"}})
        c = config.load(os.devnull, {"w3": {"port": 37101}, "poll": {"hz": 20}})
        self.assertEqual(c.w3.port, 37101)
        self.assertEqual(c.poll.hz, 20.0)

    def test_batch_cap(self):
        # замер 5.00c: команду exec от 2048 байт игра молча выбрасывает - больше 2047 конфиг не принимает
        for bad in (40000, 32767, 4096, 2048, 255):
            with self.assertRaises(ValueError, msg=bad):
                config.load(os.devnull, {"batch": {"bytes": bad}})
        self.assertEqual(config.load(os.devnull, {"batch": {"bytes": 2047}}).batch.bytes, 2047)
        self.assertEqual(coords.MAX_CMD, 2047)

    def test_local_file(self):
        # bridge.local.toml (mcw.ps1 install, лайт-режим) — поверх bridge.toml; опечатка в нём — ошибка с именем файла
        old = config.LOCAL_FILE
        with tempfile.TemporaryDirectory() as d:
            config.LOCAL_FILE = os.path.join(d, "bridge.local.toml")
            try:
                with open(config.LOCAL_FILE, "w", encoding="utf-8") as f:
                    f.write("[display]\ndefault_template = 0\n[display.templates]\n[skin]\nenabled = false\n")
                c = config.load(None)
                self.assertEqual(c.display.default_template, 0)
                self.assertEqual(c.display.templates, {})
                self.assertFalse(c.skin.enabled)
                self.assertTrue(c._file.endswith("bridge.local.toml"))
                self.assertEqual(config.load(None, local=False).display.default_template, 12)
                self.assertEqual(config.load(os.devnull).display.default_template, 12)   # свой --config — без него
                with open(config.LOCAL_FILE, "w", encoding="utf-8") as f:
                    f.write("[display]\ndefault_tmplate = 0\n")
                with self.assertRaisesRegex(ValueError, "bridge.local.toml"):
                    config.load(None)
            finally:
                config.LOCAL_FILE = old

    def test_free_tables(self):
        c = config.load(os.devnull, {"display": {"templates": {"minecraft:tnt": 1}}, "input": {"bind": {"toggle": ["F3"]}}})
        self.assertEqual(c.display.templates, {"minecraft:tnt": 1})
        self.assertEqual(c.input.bind["toggle"], ["F3"])
        self.assertEqual(c.input.bind["break"], ["MOUSE4", "8"])     # остальные привязки на месте


# ---------------------------------------------------------------------------------------------------------- зеркало
def chunk_msg(cx, cz, blocks):
    pal, b = [], []
    for (x, y, z), name in blocks.items():
        if name not in pal:
            pal.append(name)
        b += [x, y, z, pal.index(name)]
    return {"t": "chunk", "c": [cx, cz], "pal": pal, "b": b}


def blocks_msg(set_=None, clear=()):
    pal, s = [], []
    for (x, y, z), name in (set_ or {}).items():
        if name not in pal:
            pal.append(name)
        s += [x, y, z, pal.index(name)]
    c = [v for p in clear for v in p]
    return {"t": "blocks", "tick": 1, "pal": pal, "set": s, "clear": c}


class MirrorTest(unittest.TestCase):
    def test_chunk_blocks_unchunk(self):
        m = Mirror()
        m.apply({"t": "hello"})
        self.assertFalse(m.snap_done)
        m.apply(chunk_msg(0, -3, {(1, 70, -40): "minecraft:stone", (1, 71, -40): "minecraft:oak_log[axis=y]"}))
        m.apply({"t": "snap_end"})
        self.assertTrue(m.snap_done)
        self.assertEqual(m.get(1, 71, -40), "minecraft:oak_log[axis=y]")
        m.apply(blocks_msg({(13, 72, -40): "minecraft:sand"}, [(1, 70, -40)]))
        self.assertIsNone(m.get(1, 70, -40))
        self.assertEqual(m.get(13, 72, -40), "minecraft:sand")
        self.assertEqual(m.size(), 2)
        m.apply({"t": "unchunk", "c": [0, -3]})
        self.assertEqual(m.size(), 0)

    def test_changed_marks_border_neighbors(self):
        m = Mirror()
        m.take_changed()
        m.apply(blocks_msg({(16, 5, 31): "minecraft:stone"}))     # x&15 == 0, z&15 == 15
        self.assertEqual(m.take_changed(), {(1, 1), (0, 1), (1, 2)})
        m.apply(blocks_msg({(20, 5, 20): "minecraft:stone"}))
        self.assertEqual(m.take_changed(), {(1, 1)})

    def test_protocol_examples(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mc", "protocol_examples.jsonl")
        m = Mirror()
        with open(path, encoding="utf-8") as f:
            for line in f:
                msg = json.loads(line)
                if msg.get("_dir") == "mc->bridge":
                    m.apply(msg)
        # chunk (2 блока) + blocks (sand и water; clear двух) - unchunk чужого чанка
        self.assertEqual(m.get(13, 72, -40), "minecraft:sand")
        self.assertIsNone(m.get(13, 70, -40))


# ---------------------------------------------------------------------------------------------------------- отбор
def make_selector(blocks, ground=None, rules=None):
    m = Mirror()
    by = {}
    for p, n in blocks.items():
        by.setdefault((p[0] >> 4, p[2] >> 4), {})[p] = n
    for (cx, cz), bl in by.items():
        m.apply(chunk_msg(cx, cz, bl))
    m.apply({"t": "snap_end"})
    g = ground or {}
    return m, Selector(m, rules or BlockRules(config.DEFAULTS["display"]["hide"],
                                              config.DEFAULTS["display"]["transparent"]), lambda x, z: g.get((x, z)))


def cube(x0, y0, z0, n=3, name="minecraft:stone"):
    return {(x0 + i, y0 + j, z0 + k): name for i in range(n) for j in range(n) for k in range(n)}


class SelectorTest(unittest.TestCase):
    def test_hidden_center_and_ground(self):
        bl = cube(0, 70, 0)
        _, s = make_selector(bl)
        got = {p for _, p, _ in s.select((1.5, 71.5, -5.0), 32, 2000)}
        self.assertEqual(len(got), 26)
        self.assertNotIn((1, 71, 1), got)
        # рельеф под кубом: низ середины закрыт barrier'ом
        ground = {(x, z): 69 for x in range(-2, 5) for z in range(-2, 5)}
        _, s = make_selector(bl, ground)
        got = {p for _, p, _ in s.select((1.5, 71.5, -5.0), 32, 2000)}
        self.assertEqual(len(got), 25)
        self.assertNotIn((1, 70, 1), got)

    def test_transparent_neighbor_and_hide(self):
        bl = cube(0, 70, 0)
        bl[(1, 72, 1)] = "minecraft:glass"       # стекло сверху открывает середину
        bl[(5, 70, 5)] = "minecraft:water[level=0]"
        bl[(6, 70, 5)] = "minecraft:barrier"
        _, s = make_selector(bl)
        got = {p for _, p, _ in s.select((1.5, 71.5, -5.0), 32, 2000)}
        self.assertIn((1, 71, 1), got)
        self.assertIn((1, 72, 1), got)          # само стекло показываем (шаблоном по умолчанию)
        self.assertNotIn((5, 70, 5), got)
        self.assertNotIn((6, 70, 5), got)

    def test_radius_limit_nearest_first_across_chunks(self):
        bl = {(x, 64, 0): "minecraft:dirt" for x in range(-40, 41)}
        _, s = make_selector(bl)
        sel = s.select((0.5, 64.5, 0.5), 10.0, 1000)
        self.assertEqual({p[0] for _, p, _ in sel}, set(range(-10, 11)))
        sel = s.select((0.5, 64.5, 0.5), 32.0, 5)
        self.assertEqual([p[0] for _, p, _ in sel], [0, -1, 1, -2, 2])
        self.assertEqual([d for d, _, _ in sel], sorted(d for d, _, _ in sel))

    def test_cache_invalidation(self):
        m, s = make_selector(cube(0, 70, 0))
        self.assertEqual(len(s.select((1.5, 71.5, -5.0), 32, 2000)), 26)
        m.apply(blocks_msg(clear=[(1, 72, 1)]))   # сняли верх середины: открылась середина
        s.invalidate(m.take_changed())
        got = {p for _, p, _ in s.select((1.5, 71.5, -5.0), 32, 2000)}
        self.assertIn((1, 71, 1), got)
        self.assertEqual(len(got), 26)

    def test_rules(self):
        r = BlockRules(["minecraft:water"], ["minecraft:*glass*"], {"minecraft:tnt": 1, "minecraft:oak_log[axis=x]": 1})
        self.assertEqual(base_name("minecraft:oak_log[axis=y]"), "minecraft:oak_log")
        self.assertTrue(r.hidden("minecraft:water[level=3]"))
        self.assertTrue(r.hidden("minecraft:barrier"))
        self.assertFalse(r.opaque("minecraft:red_stained_glass_pane[east=true]"))
        self.assertTrue(r.opaque("minecraft:stone"))
        self.assertEqual(r.template("minecraft:tnt[unstable=false]"), 1)
        self.assertEqual(r.template("minecraft:oak_log[axis=x]"), 1)
        self.assertEqual(r.template("minecraft:oak_log[axis=y]"), 0)


# ---------------------------------------------------------------------------------------------------------- дифф
class DisplayTest(unittest.TestCase):
    def test_diff_replace_remove(self):
        d = Display(TPL)
        want = [(0.0, (0, 70, 0), 0), (1.0, (1, 70, 0), 0)]
        ops = d.ops(want)
        self.assertEqual([o[2] for o in ops], [0, 0])
        d.commit(ops)
        self.assertEqual(d.ops(want), [])
        # (0,70,0) меняет шаблон, (1,70,0) пропал, (2,70,0) появился
        ops = d.ops([(0.0, (0, 70, 0), 1), (2.0, (2, 70, 0), 0)])
        kinds = [(o[1], o[2]) for o in ops]
        self.assertEqual(kinds[:2], [((0, 70, 0), None), ((1, 70, 0), None)])   # clear раньше set
        self.assertEqual(kinds[2:], [((0, 70, 0), 1), ((2, 70, 0), 0)])
        d.commit(ops)
        self.assertEqual(set(d.shown), {(0, 70, 0), (2, 70, 0)})
        self.assertEqual(d.shown[(0, 70, 0)][0], 1)

    def test_clear_origin_matches_set(self):
        d = Display(TPL, y_offset=7)
        for p in [(0, 0, 0), (-301, 70, 300), (12345, -10, -6790)]:
            d.commit(d.ops([(0.0, p, 0)]))
            set_xyz = coords.set_op(*p, CRATE, y_offset=7).split(":")[1:4]
            self.assertEqual(d.shown[p][1], "clear:" + ":".join(set_xyz))
            self.assertEqual(d.shown[p][1], coords.clear_op(*p, CRATE, y_offset=7))

    def test_batch_semantics_on_fake(self):
        """Пакеты из диффа, исполненные по контракту mcw_batch фейка, дают ровно показанное: замена без дублей."""
        fw = fake_w3.FakeWitcher("127.0.0.1", 0)
        d = Display(TPL)
        rnd = __import__("random").Random(5)
        for step in range(30):
            want = sorted({(float(i), (rnd.randrange(-8, 8), 70, rnd.randrange(-8, 8)), rnd.choice((0, 0, 0, 1)))
                           for i in range(rnd.randrange(0, 60))}, key=lambda w: w[0])
            uniq, seen = [], set()
            for w in want:
                if w[1] not in seen:
                    seen.add(w[1])
                    uniq.append(w)
            ops = d.ops(uniq)
            strs = [o[0] for o in ops]
            i = 0
            while i < len(strs):
                n = pack(strs[i:], 1000 + step, 300)
                out, _ = fw._x_mcw_batch(0, "|".join(strs[i:i + n]))
                self.assertEqual(out[0].split()[3], "0", out)          # ни одной ошибки
                d.commit(ops[i:i + n])
                i += n
            self.assertEqual(fw.world.count(), len(uniq))
            got = sorted((e.x, e.y, e.z) for e in fw.world.ents.values())
            exp = sorted(tuple(int(v) for v in coords.set_op(*p, TPL[t]).split(":")[1:4]) for _, p, t in uniq)
            self.assertEqual(got, exp)
        fw._listener.close()


class PackTest(unittest.TestCase):
    def test_limit_and_equivalence(self):
        ops = [coords.set_op(x, 70, -x * 37, CRATE) for x in range(-400, 400)]
        for limit in (120, 1024, 1900, 2047):
            i, seq, cmds = 0, 77, []
            while i < len(ops):
                n = pack(ops[i:], seq, limit)
                cmd = coords.batch_call(seq, ops[i:i + n])
                self.assertLessEqual(coords.utf8_len(cmd), limit)
                if i + n < len(ops):     # следующая op уже не влезла бы
                    self.assertGreater(coords.utf8_len(coords.batch_call(seq, ops[i:i + n + 1])), limit)
                cmds.append((seq, cmd))
                i += n
                seq += 1
            self.assertEqual(cmds, coords.build_batches(ops, 77, limit))

    def test_errors(self):
        with self.assertRaises(ValueError):
            pack(["set:1:2:3:0"], 1, 2048)                  # предел команды exec 2047 (замер 5.00c)
        with self.assertRaises(ValueError):
            pack(["set:100000:200000:300000:0"], 1, 30)
        self.assertEqual(pack([], 1, 100), 0)
        # пустую строку "" парсер exec не разбирает: пустые mcw_batch/mcw_ents не собираются вовсе
        with self.assertRaises(ValueError):
            coords.batch_call(1, [])
        with self.assertRaises(ValueError):
            coords.ents_call(1, [])
        self.assertEqual(pack_ents([], 1, 100), 0)


# ---------------------------------------------------------------------------------------------------------- луч
class LookTest(unittest.TestCase):
    def hit_cm(self, mc_point):
        w = coords.mc_pos_to_w(*mc_point)
        return tuple(coords.to_cm(v) for v in w)

    @staticmethod
    def w_dir(mx, my, mz):
        """Направление Майнкрафта -> Ведьмака (обратное world.mc_dir)."""
        return mx, -mz, my

    def test_faces_of_own_block(self):
        cell = (-304, 70, 300)
        shown = {cell: (0, "")}
        cx, cy, cz = (c + 0.5 for c in cell)
        # точка на грани ящика (1.03 м) и луч, который в эту грань входит
        cases = {"east": ((cx + 0.515, cy + 0.1, cz - 0.2), (-1.0, -0.2, 0.1)),
                 "west": ((cx - 0.515, cy, cz + 0.3), (1.0, -0.3, 0.0)),
                 "up": ((cx + 0.2, cy + 0.515, cz), (0.3, -1.0, 0.2)),
                 "down": ((cx, cy - 0.515, cz - 0.1), (0.1, 1.0, 0.0)),
                 "south": ((cx - 0.3, cy, cz + 0.515), (0.0, -0.2, -1.0)),
                 "north": ((cx, cy + 0.2, cz - 0.515), (0.2, 0.0, 1.0))}
        for face, (pt, d) in cases.items():
            got = look_target(self.hit_cm(pt), True, self.w_dir(*d), shown)
            self.assertEqual(got[0], cell, face)
            self.assertEqual(got[1], face)
            self.assertTrue(got[3])
            self.assertTrue(all(0.0 <= v <= 1.0 for v in got[2]))

    def test_neighbors_overlap(self):
        # ящики соседей перекрываются: точка x=-304.015 лежит и на западной грани (-304), и внутри (-305);
        # луч на запад входит в восточную грань (-305) — значит, попали в (-305)
        shown = {(-304, 70, 300): (0, ""), (-305, 70, 300): (0, "")}
        got = look_target(self.hit_cm((-304.015, 70.5, 300.5)), True, self.w_dir(-1.0, -0.1, 0.0), shown)
        self.assertEqual(got[:2], ((-305, 70, 300), "east"))
        got = look_target(self.hit_cm((-303.985, 70.5, 300.5)), True, self.w_dir(1.0, -0.1, 0.0), shown)
        self.assertEqual(got[:2], ((-304, 70, 300), "west"))

    def test_game_normal(self):
        cell = (-304, 70, 300)
        shown = {cell: (0, ""), (-305, 70, 300): (0, "")}
        cx, cy, cz = (c + 0.5 for c in cell)
        # точка у ребра: с нормалью из игры грань — ровно её ось
        pt = self.hit_cm((cx + 0.2, cy + 0.515, cz - 0.3))
        got = look_target(pt, True, self.w_dir(-0.6, -0.8, 0.0), shown, normal_w=self.w_dir(0.0, 1.0, 0.0))
        self.assertEqual(got[:2], (cell, "up"))
        pt = self.hit_cm((cx - 0.5155, cy + 0.1, cz))         # перекрытие соседних ящиков, луч с запада
        got = look_target(pt, True, self.w_dir(1.0, -0.3, 0.0), shown, normal_w=self.w_dir(-1.0, 0.0, 0.0))
        self.assertEqual(got[:2], (cell, "west"))             # клетка — на eps внутрь поверхности по нормали
        # нормаль от камеры — не верим, работает вывод
        got = look_target(self.hit_cm((cx + 0.515, cy, cz)), True, self.w_dir(-1.0, -0.1, 0.0), shown,
                          normal_w=self.w_dir(-1.0, 0.0, 0.0))
        self.assertEqual(got[:2], (cell, "east"))
        # стена (не свой блок): клетка перед стеной по нормали, грань — нормаль
        got = look_target(self.hit_cm((-310.0, 73.4, 300.5)), False, self.w_dir(-1.0, -0.2, 0.1), {},
                          normal_w=self.w_dir(1.0, 0.0, 0.0))
        self.assertEqual(got[:2], ((-310, 73, 300), "east"))
        # земля с нормалью вверх и известным верхом колонны — клик по barrier
        got = look_target(self.hit_cm((-304.22, 70.0, 300.5)), False, (-0.9659, 0.0, -0.2588), {},
                          ground_top=lambda x, z: 69, normal_w=(0.0, 0.0, 1.0))
        self.assertEqual(got[:2], ((-305, 69, 300), "up"))
        # крутой склон, нормаль почти вбок: не barrier, а клетка перед поверхностью
        got = look_target(self.hit_cm((-304.22, 70.0, 300.5)), False, (-0.9659, 0.0, -0.2588), {},
                          ground_top=lambda x, z: 69, normal_w=(0.9, 0.0, 0.43))
        self.assertEqual(got[:2], ((-305, 70, 300), "east"))

    def test_terrain_place_and_fallback(self):
        # взгляд вниз-на-запад, попали в землю на y = 70.0: клетка перед поверхностью — воздух над землёй, грань up
        d_w = (-0.9659, 0.0, -0.2588)
        top = {(-305, 300): 69}
        got = look_target(self.hit_cm((-304.22, 70.0, 300.5)), False, d_w, {}, ground_top=lambda x, z: top.get((x, z)))
        self.assertEqual(got[:2], ((-305, 69, 300), "up"))      # клик по верхнему barrier: блок встанет на y=70
        self.assertEqual(got[2][1], 1.0)
        self.assertFalse(got[3])
        # рельеф ещё не спрошен (или попали в стену выше земли): клетка перед поверхностью, грань к камере
        got = look_target(self.hit_cm((-304.22, 70.0, 300.5)), False, d_w, {})
        self.assertEqual(got[:2], ((-305, 70, 300), "east"))
        got = look_target(self.hit_cm((-304.22, 73.4, 300.5)), False, d_w, {}, ground_top=lambda x, z: top.get((x, z)))
        self.assertEqual(got[:2], ((-305, 73, 300), "east"))
        # isBlock=1, но клетки нет среди показанных (бочка mcw_boom): шаг внутрь по лучу
        got = look_target(self.hit_cm((10.0, 70.4, 0.5)), True, (-1.0, 0.0, 0.0), {})
        self.assertEqual(got[0], (9, 70, 0))


# ---------------------------------------------------------------------------------------------------------- рельеф
class GroundTest(unittest.TestCase):
    def test_top_formula(self):
        self.assertEqual(top_from_cm(0, 0, 7000), 69)
        self.assertEqual(top_from_cm(0, 0, 7049), 69)
        self.assertEqual(top_from_cm(0, 0, 7050), 70)
        self.assertEqual(top_from_cm(0, 0, -130), -2)          # опора -1.3 м -> верх -2 (блок -1 утоплен на 0.3)
        self.assertEqual(top_from_cm(0, 0, 7000, y_offset=10), 79)
        x, y = col_center_cm(-301, 300)
        self.assertEqual((x, y), (-30050, -29950))
        self.assertEqual(coords.cm_to_block(x, y, 0)[0::2], (-301, 300))

    def test_want_nearest_first_pending_and_retry(self):
        g = GroundPlanner(radius_m=3.0, retry_none_s=5.0)
        first = g.want((10, 10), 5, 0.0)
        self.assertEqual(first[0], (10, 10))
        self.assertEqual(set(first[1:]), {(11, 10), (9, 10), (10, 11), (10, 9)})
        nxt = g.want((10, 10), 100, 0.0)
        self.assertFalse(set(first) & set(nxt))                # ждущие ответа не повторяются
        self.assertEqual(len(first) + len(nxt), len(g.offsets))
        for c in first + nxt:
            g.on_height(c, c != (10, 10), 7000, 0.0)
        self.assertEqual(g.want((10, 10), 100, 1.0), [])       # без опоры — переспросим позже
        self.assertEqual(g.want((10, 10), 100, 6.0), [(10, 10)])
        g.on_fail((10, 10))
        self.assertEqual(g.want((10, 10), 100, 6.0), [(10, 10)])

    def test_send_skip_chunk_clear(self):
        g = GroundPlanner(radius_m=2.0, depth=3, clear_dist_m=40.0)
        for c in g.want((0, 0), 100, 0.0):
            g.on_height(c, True, 7000, 0.0)
        c = g.take_ground((0, 0), 1000)
        cols = {(c[i], c[i + 1]): (c[i + 2], c[i + 3]) for i in range(0, len(c), 4)}
        self.assertEqual(len(cols), len(g.offsets))
        self.assertEqual(set(cols.values()), {(67, 69)})
        self.assertIsNone(g.take_ground((0, 0), 1000))         # всё уже у мода
        g.on_ground_ok({"t": "ground_ok", "skip": [-1, 0, -2, 0]})   # чанк (-1, 0) не загружен
        self.assertIsNone(g.take_ground((0, 0), 1000))
        g.on_chunk((-1, 0))
        c = g.take_ground((0, 0), 1000)
        self.assertEqual(sorted((c[i], c[i + 1]) for i in range(0, len(c), 4)), [(-2, 0), (-1, 0)])
        self.assertEqual(g.take_clears((5.0, 5.0)), [])
        boxes = g.take_clears((100.0, 0.5))
        self.assertIn([-16, -16, -1, -1], boxes)
        self.assertIn([0, 0, 15, 15], boxes)
        self.assertEqual(len(g.sent), 0)
        c = g.take_ground((0, 0), 1000)                         # вернулись — высоты из кеша, без вопросов игре
        self.assertEqual(len(c) // 4, len(g.offsets))
        g.mc_reset()
        self.assertEqual(len(g.take_ground((0, 0), 1000)) // 4, len(g.offsets))
        self.assertEqual(g.trim((1000.0, 1000.0), 50.0), 0)    # отправленное не забываем
        g.mc_reset()
        self.assertEqual(g.trim((1000.0, 1000.0), 50.0), len(g.offsets))


# ---------------------------------------------------------------------------------------------------------- клавиши
class KeysTest(unittest.TestCase):
    def setUp(self):
        self.down = set()
        self.fg = [True]
        self.hk = hotkeys.Hotkeys(hotkeys.parse_bindings({"toggle": ["F2"], "break": ["MOUSE4", "8"],
                                                         "slot3": ["NUMPAD3"]}),
                                  lambda vk: vk in self.down, lambda: self.fg[0])

    def test_edges(self):
        self.assertEqual(self.hk.step(), [])
        self.down.add(hotkeys.VK["F2"])
        self.assertEqual(self.hk.step(), ["toggle"])
        self.assertEqual(self.hk.step(), [])                   # держим — повтора нет
        self.down.clear()
        self.assertEqual(self.hk.step(), [])
        self.down.add(0x38)                                    # '8' — вторая клавиша break
        self.assertEqual(self.hk.step(), ["break"])
        self.down.add(0x05)
        self.assertEqual(self.hk.step(), [])                   # break уже зажат другой клавишей
        self.down.clear()
        self.hk.step()
        self.down.add(hotkeys.VK["NUMPAD3"])
        self.assertEqual(self.hk.step(), ["slot3"])

    def test_foreground_gate(self):
        self.fg[0] = False
        self.down.add(hotkeys.VK["F2"])
        self.assertEqual(self.hk.step(), [])
        self.fg[0] = True
        self.assertEqual(self.hk.step(), [])                   # зажали в другом окне — в игре не сработает
        self.down.clear()
        self.hk.step()
        self.down.add(hotkeys.VK["F2"])
        self.assertEqual(self.hk.step(), ["toggle"])

    def test_names_and_duplicates(self):
        self.assertEqual(hotkeys.vk_of("f2"), 0x71)
        self.assertEqual(hotkeys.vk_of("MOUSE5"), 0x06)
        self.assertEqual(hotkeys.vk_of("0xDB"), 0xDB)
        self.assertEqual(hotkeys.vk_of("lbracket"), 0xDB)
        with self.assertRaises(ValueError):
            hotkeys.vk_of("NOPE")
        with self.assertRaises(ValueError):
            hotkeys.parse_bindings({"break": ["8"], "place": ["8"]})
        with self.assertRaises(ValueError):
            hotkeys.parse_bindings({"jump": ["8"]})
        hotkeys.parse_bindings(config.DEFAULTS["input"]["bind"])

    def test_conflicts(self):
        text = ("[Exploration]\nIK_E=(Action=Interaction)\nIK_8=(Action=Something)\nIK_NumPad1=(Action=Debug_KillAllEnemies)\n"
                "IK_None=(Action=Foo)\n")
        with tempfile.NamedTemporaryFile("w", suffix=".settings", delete=False, encoding="utf-8") as f:
            f.write(text)
        try:
            got = hotkeys.check_conflicts({"break": ["8", "MOUSE4"], "slot1": ["NUMPAD1"], "toggle": ["F5"]}, f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(len(got), 2)
        self.assertIn("IK_8", got[0])
        self.assertIn("F5", got[1])
        self.assertIsNone(hotkeys.check_conflicts({}, os.path.join(tempfile.gettempdir(), "no-such-input.settings")))

    def test_safe_text(self):
        # замер 5.00c: кавычки внутри строки ломают разбор exec, '\' не экранирует, кириллица проходит
        self.assertEqual(safe_text('MCW: "Геральт" \\ +1 dirt'), "MCW: ?Геральт? ? +1 dirt")
        self.assertEqual(safe_text("it's <b>&ok\t"), "it?s ?b??ok?")
        self.assertEqual(len(safe_text("Ж" * 200).encode()), 120)                 # предел в байтах UTF-8, не в буквах
        self.assertEqual(safe_text("a" + "Ж" * 200), "a" + "Ж" * 59)              # 119 байт: букву пополам не режем
        self.assertEqual(safe_text("x" * 200), "x" * 120)
        self.assertEqual(world_name(r"levels\novigrad\novigrad.w2w"), "novigrad")


# ---------------------------------------------------------------------------------------------------------- фейковый мод
class FakeMod:
    """TCP-сервер на эфемерном порту с протоколом мода в объёме моста: hello -> снапшот из self.blocks, blocks по
    set_block/clear_block, act break/place/select с ack, ground/ground_clear -> ground_ok, explosion/inv/hp по вызову."""

    def __init__(self, blocks=None, skip_chunks=()):
        self.blocks = dict(blocks or {})
        self.skip_chunks = set(skip_chunks)
        self.got: list[dict] = []
        self.barrier: set = set()
        self.conn = None
        self.lock = threading.Lock()
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(4)
        self.port = self.srv.getsockname()[1]
        self.hellos = 0
        self.reject = None
        self.alive = True
        # этапы 3 и 5: список рецептов, отказ craft (why) и ack на spawn
        self.recipes = [["minecraft:oak_planks", "minecraft:oak_planks", 4, 2, 2],
                        ["minecraft:wooden_pickaxe", "minecraft:wooden_pickaxe", 1, 1, 3]]
        self.table = None
        self.craft_why: dict[str, str] = {"minecraft:wooden_pickaxe": "no_table"}
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while self.alive:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def send(self, msg):
        with self.lock:
            if self.conn is not None:
                try:
                    self.conn.sendall(encode(msg))
                except OSError:
                    pass

    def _serve(self, c):
        fr = LineFramer()
        try:
            while True:
                data = c.recv(65536)
                if not data:
                    break
                for m in fr.feed(data):
                    self.got.append(m)
                    self._handle(c, m)
        except OSError:
            pass

    def _handle(self, c, m):
        t = m.get("t")
        if t == "hello":
            with self.lock:
                self.conn = c
            self.hellos += 1
            self.send({"t": "hello", "v": 1, "radius": 4, "tick": 1})
            by = {}
            for p, n in self.blocks.items():
                by.setdefault((p[0] >> 4, p[2] >> 4), {})[p] = n
            for (cx, cz), bl in by.items():
                self.send(chunk_msg(cx, cz, bl) | {"tick": 1})
            self.send({"t": "snap_end", "tick": 1, "chunks": len(by), "blocks": len(self.blocks)})
            self.send({"t": "inv", "tick": 1, "sel": 0, "s": [[0, "minecraft:dirt", 8]]})
            self.send({"t": "hp", "tick": 1, "hp": 20.0, "food": 20})
        elif t == "ground":
            cc, skip = m["c"], []
            for i in range(0, len(cc), 4):
                if (cc[i] >> 4, cc[i + 1] >> 4) in self.skip_chunks:
                    skip += [cc[i], cc[i + 1]]
                    continue
                for y in range(cc[i + 2], cc[i + 3] + 1):
                    self.barrier.add((cc[i], y, cc[i + 1]))
            self.send({"t": "ground_ok", "tick": 1, "placed": 0, "total": len(self.barrier), "unloaded": len(skip) // 2,
                       "skip": skip})
        elif t == "ground_clear":
            box = m.get("box")
            self.barrier = {b for b in self.barrier if box and not (box[0] <= b[0] <= box[2] and box[1] <= b[2] <= box[3])}
            self.send({"t": "ground_ok", "tick": 1, "removed": 0, "left": 0, "total": len(self.barrier)})
        elif t == "act":
            a, i = m.get("a"), m.get("id")
            if self.reject and a in ("break", "place"):
                self.send({"t": "ack", "tick": 1, "id": i, "ok": False, "why": self.reject})
            elif a == "break":
                p = tuple(m["b"])
                if p in self.blocks:
                    self.clear_block(p)
                    self.send({"t": "ack", "tick": 1, "id": i, "ok": True})
                else:
                    self.send({"t": "ack", "tick": 1, "id": i, "ok": False, "why": "air"})
            elif a == "place":
                p = tuple(m["b"])
                if p in self.blocks or p in self.barrier:
                    d = {"up": (0, 1, 0), "down": (0, -1, 0), "east": (1, 0, 0), "west": (-1, 0, 0), "south": (0, 0, 1),
                         "north": (0, 0, -1)}[m.get("face", "up")]
                    p = (p[0] + d[0], p[1] + d[1], p[2] + d[2])
                if p in self.blocks:
                    self.send({"t": "ack", "tick": 1, "id": i, "ok": False, "why": "occupied"})
                else:
                    self.set_block(p, "minecraft:dirt")
                    self.send({"t": "ack", "tick": 1, "id": i, "ok": True})
            else:
                self.send({"t": "ack", "tick": 1, "id": i, "ok": True})
        elif t == "recipes":
            self.send({"t": "recipes", "tick": 1, "id": m.get("id"), "table": self.table, "r": self.recipes})
        elif t == "craft":
            why = self.craft_why.get(m.get("recipe"))
            r = next((r for r in self.recipes if r[0] == m.get("recipe")), None)
            if why or r is None:
                self.send({"t": "ack", "tick": 1, "id": m["id"], "ok": False, "why": why or "bad_recipe", "made": 0,
                           "crafts": 0, "table": None})
            else:
                self.send({"t": "ack", "tick": 1, "id": m["id"], "ok": True, "made": r[2] * m.get("n", 1),
                           "crafts": m.get("n", 1), "item": r[1], "table": None})
        elif t == "spawn":
            self.send({"t": "ack", "tick": 1, "id": m["id"], "ok": True, "ent": 900, "p": [-296.5, 70.0, 300.5]})

    def set_block(self, p, name):
        self.blocks[p] = name
        self.send(blocks_msg({p: name}))

    def clear_block(self, p):
        self.blocks.pop(p, None)
        self.send(blocks_msg(clear=[p]))

    def kinds(self, t):
        return [m for m in list(self.got) if m.get("t") == t]

    def drop(self):
        with self.lock:
            if self.conn is not None:
                try:
                    self.conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.conn.close()
                self.conn = None

    def close(self):
        self.alive = False
        self.drop()
        self.srv.close()


# Геральт в Ведьмаке (-300.5, -299.5, 70.0) м -> в Майнкрафте (-300.5, 70.0, 300.5), блок (-301, 70, 300).
# Камера фейка: 3 м за спиной, 1.8 м выше, смотрит на запад (-x) и на 15° вниз: луч упирается в стену x=-304.
PLAYER_CM = [-30050, -29950, 7000]
WALL = {(-304, y, z): "minecraft:dirt" for y in (70, 71) for z in (299, 300, 301)}
CUBE = cube(-296, 70, 298)
EXPECTED = set(WALL) | (set(CUBE) - {(-295, 71, 299), (-295, 70, 299)})    # середина и низ середины закрыты


def origins(cells):
    return sorted(tuple(int(v) for v in coords.set_op(*p, CRATE).split(":")[1:4]) for p in cells)


def fake_cells(fw):
    return sorted((e.x, e.y, e.z) for e in list(fw.world.ents.values()) if "MCW_TEST" not in e.tags)


class Loop:
    """Мост в потоке + fake_w3 + FakeMod. key_down/foreground — подмена Win32."""

    def __init__(self, blocks, w3_port=0, fake_kw=None, **over):
        self.fake_kw = dict(fake_kw or {})
        self.fw = self.new_fake(w3_port, **self.fake_kw)
        self.mod = FakeMod(blocks)
        self.down = set()
        o = {"w3": {"port": self.fw.port, "reconnect_min_s": 0.1, "reconnect_max_s": 0.5},
             "mc": {"port": self.mod.port, "reconnect_min_s": 0.1, "reconnect_max_s": 0.5},
             "poll": {"hz": 20.0}, "ground": {"radius_m": 10.0, "rate_hz": 400.0, "inflight": 6, "send_interval_s": 0.05},
             "resync": {"count_every_s": 0.3, "min_interval_s": 0.2}, "log": {"file": "", "metrics_every_s": 3600.0},
             "notify": {"min_interval_s": 0.05},
             # ожидания сценариев посчитаны по cont_crate (CRATE), а не по блокам modMCWBlocks из bridge.toml
             "display": {"default_template": 0, "templates": {}},
             # настоящий клиент MC (25601) тестам не нужен: ClientLoopTest поднимает свой FakeClient
             "client": {"enabled": False, "overlay": ""}}
        for k, v in over.items():
            o.setdefault(k, {}).update(v)
        self.cfg = config.load(os.devnull, o)
        self.start()

    @staticmethod
    def new_fake(port=0, **kw):
        fw = fake_w3.FakeWitcher("127.0.0.1", port, frame_time=1 / 60, **kw)
        fw.world.player = list(PLAYER_CM)
        return fw.start()

    def start(self):
        self.br = Bridge(self.cfg, key_down=lambda vk: vk in self.down, foreground=lambda: True)
        self.th = threading.Thread(target=self.br.run, daemon=True)
        self.th.start()

    def stop_bridge(self):
        self.br.stop()
        self.th.join(5)

    def press(self, name):
        self.down.add(hotkeys.VK[name])
        time.sleep(0.06)
        self.down.discard(hotkeys.VK[name])
        time.sleep(0.06)

    def synced(self, cells):
        return fake_cells(self.fw) == origins(cells) and self.br.snapshot.get("pending_ops") == 0

    def close(self):
        self.stop_bridge()
        self.fw.stop()
        self.mod.close()


class LoopTest(unittest.TestCase):
    def setUp(self):
        self.L = Loop({**WALL, **CUBE})
        self.addCleanup(self.L.close)

    def test_end_to_end(self):
        L = self.L
        self.assertTrue(wait_until(lambda: L.synced(EXPECTED), 15), (len(fake_cells(L.fw)), L.br.snapshot))
        self.assertEqual(L.fw.world.count(), len(EXPECTED))
        # рельеф: колонны вокруг Геральта ушли моду, barrier 67..69
        self.assertTrue(any((-301, 69, 300) == b for b in L.mod.barrier))
        self.assertIn((-301, 67, 300), L.mod.barrier)
        self.assertNotIn((-301, 70, 300), L.mod.barrier)
        # pos: Геральт в координатах Майнкрафта
        pos = L.mod.kinds("pos")[-1]
        self.assertEqual(pos["p"], [-300.5, 70.0, 300.5])
        self.assertAlmostEqual(pos["yaw"], 90.0, places=1)      # курс Ведьмака 90 (запад) -> yaw MC 90
        self.assertAlmostEqual(pos["pitch"], 15.0, delta=0.1)
        # новый блок у мода -> появился в Ведьмаке
        L.mod.set_block((-300, 72, 296), "minecraft:oak_planks")
        exp = EXPECTED | {(-300, 72, 296)}
        self.assertTrue(wait_until(lambda: L.synced(exp), 5))
        # ломка клавишей: режим строительства, break -> mcw_look -> act -> blocks clear -> исчез в Ведьмаке
        L.press("F2")
        self.assertTrue(wait_until(lambda: L.br.build_mode, 2))
        L.press("8")
        self.assertTrue(wait_until(lambda: L.mod.kinds("act"), 3))
        act = L.mod.kinds("act")[-1]
        self.assertEqual((act["a"], act["b"], act["face"]), ("break", [-304, 70, 300], "east"))
        self.assertEqual(act["p"], [-300.5, 70.0, 300.5])
        exp -= {(-304, 70, 300)}
        self.assertTrue(wait_until(lambda: L.synced(exp), 5))
        # установка: луч теперь уходит в землю за стеной -> на barrier (-305, 69, 300), блок встаёт на (-305, 70, 300)
        L.press("MOUSE5")
        self.assertTrue(wait_until(lambda: len(L.mod.kinds("act")) >= 2, 3))
        act = L.mod.kinds("act")[-1]
        self.assertEqual((act["a"], act["b"], act["face"]), ("place", [-305, 69, 300], "up"))   # barrier-земля
        exp |= {(-305, 70, 300)}
        self.assertTrue(wait_until(lambda: L.synced(exp), 5))
        # отказ мода -> mcw_notify; слот
        L.mod.reject = "too_far"
        L.press("MOUSE5")
        self.assertTrue(wait_until(lambda: any("cannot place: too far" in n for n in L.fw.world.notifications), 3),
                        L.fw.world.notifications)
        L.press("NUMPAD1")
        self.assertTrue(wait_until(lambda: any("slot 1 - dirt" in n for n in L.fw.world.notifications), 3))
        # explosion рядом -> mcw_boom (бочка с MCW_BLOCK: счётчик учитывает её, ресинка нет)
        resyncs = L.br.stats["resyncs"]
        L.mod.send({"t": "explosion", "tick": 2, "p": [-298.0, 70.0, 305.0], "r": 4.0, "src": "tnt"})
        self.assertTrue(wait_until(lambda: any(e.on_fire for e in L.fw.world.ents.values()), 3))
        barrel = next(e for e in L.fw.world.ents.values() if e.on_fire)
        self.assertEqual((barrel.x, barrel.y, barrel.z), (-29800, -30400, 7000))     # mcw_boomat: в точке, на земле
        time.sleep(1.0)
        self.assertEqual(L.br.stats["resyncs"], resyncs)
        self.assertEqual(L.br.caps, {"countblocks": True, "boomat": True})
        self.assertGreater(L.br.stats["sent_count"], 0)
        self.assertNotIn("mcw_count(", " ".join(L.fw.execs_seen))                   # сверка только countblocks
        # все команды mcw_batch — в пределах batch.bytes
        batches = [c for c in L.fw.execs_seen if c.startswith("mcw_batch(")]
        self.assertTrue(batches)
        self.assertLessEqual(max(coords.utf8_len(c) for c in batches), L.cfg.batch.bytes)
        # фейк с пределами exec 5.00c: ни одна команда моста не выброшена, ни один ответ не потерян и не обрезан
        self.assertLessEqual(max(coords.utf8_len(c) for c in L.fw.execs_seen), coords.MAX_CMD)
        self.assertEqual((L.fw.too_long_cmds, L.fw.replies_lost, L.fw.lines_truncated), ([], 0, 0))
        # и каждая разбирается парсером exec 5.00c: без "" и без кавычек внутри строк
        self.assertEqual([c for c in L.fw.execs_seen if fake_w3.parse_exec(c)[1] is None], [])

    def test_resync_paths(self):
        L = self.L
        self.assertTrue(wait_until(lambda: L.synced(EXPECTED), 15))
        # загрузка сейва: сущности пропали, соединение то же -> mcw_count = 0 -> clearall и всё заново
        r0 = L.br.stats["resyncs"]
        L.fw.world.clearall()
        self.assertTrue(wait_until(lambda: L.br.stats["resyncs"] > r0 and L.synced(EXPECTED), 5))
        # лишняя сущность (чужой MCW_BLOCK) -> расхождение счётчика -> ресинк убирает её
        L.fw.world.spawn("x.w2ent", 0, 0, 0)
        self.assertTrue(wait_until(lambda: L.fw.world.count() == len(EXPECTED) and L.synced(EXPECTED), 5))
        # смена мира -> ground_clear всего рельефа у мода, рельеф заново, блоки заново
        n_clear = len(L.mod.kinds("ground_clear"))
        L.fw.world.world = r"levels\skellige\skellige.w2w"
        L.fw.world.area = "AN_Skellige_ArdSkellig"
        self.assertTrue(wait_until(lambda: len(L.mod.kinds("ground_clear")) > n_clear, 3))
        self.assertNotIn("box", L.mod.kinds("ground_clear")[-1])
        self.assertTrue(wait_until(lambda: L.synced(EXPECTED), 5))
        # рестарт фейка: новый процесс игры, мир пуст -> мост переподключается, clearall, всё заново
        port = L.fw.port
        L.fw.stop()
        L.fw = Loop.new_fake(port)
        self.assertTrue(wait_until(lambda: L.synced(EXPECTED), 10))
        self.assertGreaterEqual(L.br.stats["w3_connects"], 2)
        # обрыв мода: показ замирает (ничего не снимается), после переподключения — новый hello и снапшот
        before = fake_cells(L.fw)
        L.mod.drop()
        time.sleep(0.3)
        self.assertEqual(fake_cells(L.fw), before)
        self.assertTrue(wait_until(lambda: L.mod.hellos >= 2 and L.br.mirror.snap_done, 5))
        L.mod.set_block((-299, 70, 304), "minecraft:stone")
        self.assertTrue(wait_until(lambda: L.synced(EXPECTED | {(-299, 70, 304)}), 5))
        # рестарт моста: старые сущности в игре -> clearall и то же самое заново
        L.stop_bridge()
        L.start()
        self.assertTrue(wait_until(lambda: L.br.stats["resyncs"] == 0 and L.br.w3_state == "synced", 5))
        self.assertTrue(wait_until(lambda: L.synced(EXPECTED | {(-299, 70, 304)}), 10))
        self.assertEqual(L.fw.world.count(), len(EXPECTED) + 1)


class Res:
    """Подмена w3net.ExecResult для прямых вызовов обработчиков."""

    def __init__(self, seq, *lines, ok=True):
        self.seq, self.ok, self.rtt = seq, ok, 0.01
        self.failed = not ok
        self.text = "\n".join([f"[MCW#{seq}] {ln}" for ln in lines] + [f"[MCW#{seq}] end"])
        if not ok:
            self.text = "Warn: Failed to process command"

    @property
    def body(self):
        return [ln.split(None, 1)[1] for ln in self.text.splitlines()
                if ln.startswith("[MCW#") and ln.split(None, 1)[1:] != ["end"] and len(ln.split(None, 1)) > 1]

    def ints(self, word):
        t = self.tokens(word)
        return None if t is None else [int(x) for x in t]

    def tokens(self, word):
        for ln in self.text.splitlines():
            parts = ln.split()[1:]
            if parts and parts[0] == word:
                return parts[1:]
        return None


class BridgeLogicTest(unittest.TestCase):
    """Обработчики ответов напрямую, без сети: решения о ресинке и уведомлениях."""

    def setUp(self):
        self.br = Bridge(config.load(os.devnull, {"input": {"enabled": False}, "log": {"file": ""}}))
        self.br.w3_state = "synced"
        self.br.in_world = True

    def req(self, kind, epoch=None, **ctx):
        from bridge import Req
        return Req(kind, ctx, time.monotonic(), self.br.epoch if epoch is None else epoch)

    def test_lost_batch_forces_resync(self):
        import w3net
        self.br._res_batch(self.req("batch"), 5, None, w3net.ReplyLost("lost"))
        self.assertTrue(self.br.resync_force)
        self.assertIn("mcw_batch", self.br.resync_reason)

    def test_batch_before_clearall_ignored(self):
        self.br.epoch = 3
        self.br._res_batch(self.req("batch", epoch=2), 5, None, TimeoutError("late"))
        self.assertIsNone(self.br.resync_reason)

    def test_batch_errors_check_count_now(self):
        self.br.next_count = 1e9
        self.br._res_batch(self.req("batch"), 5, Res(5, "batch 3 0 2 1"), None)
        self.assertEqual(self.br.next_count, 0.0)
        self.assertIsNone(self.br.resync_reason)

    def test_count(self):
        br = self.br
        br._res_count(self.req("count", min=10, max=12, word="count"), 7, Res(7, "count 11"), None)
        self.assertIsNone(br.resync_reason)
        br._res_count(self.req("count", min=10, max=10, word="count"), 7, Res(7, "count 0"), None)
        self.assertIn("0 блоков", br.resync_reason)
        self.assertFalse(br.resync_force)
        br.resync_reason = None
        br._res_count(self.req("count", epoch=br.epoch - 1, min=10, max=10, word="count"), 7, Res(7, "count 0"), None)
        self.assertIsNone(br.resync_reason)                  # ответ до последнего clearall

    def test_countblocks_exact_and_fallback(self):
        br = self.br
        br._res_count(self.req("count", min=10, max=10, word="countblocks"), 7, Res(7, "countblocks 10 3"), None)
        self.assertIsNone(br.resync_reason)                  # 3 бочки-пробы в сверке не участвуют
        self.assertTrue(br.caps["countblocks"])
        br._res_count(self.req("count", min=10, max=10, word="countblocks"), 7, Res(7, "countblocks 11 0"), None)
        self.assertIn("countblocks 11", br.resync_reason)
        br.resync_reason = None
        br.caps["countblocks"] = None
        br.next_count = 1e9
        br._res_count(self.req("count", min=10, max=10, word="countblocks"), 7, Res(7, ok=False), None)
        self.assertIs(br.caps["countblocks"], False)          # старый мод: дальше mcw_count с допуском
        self.assertEqual(br.next_count, 0.0)
        self.assertIsNone(br.resync_reason)
        calls = []
        br._call = lambda kind, func, args, timeout, ctx: calls.append((func, ctx)) or 1
        br.booms = 2
        br.display.shown = {(0, 0, i): (0, "") for i in range(10)}
        br._step_count(1.0)
        self.assertEqual(calls, [("mcw_count", {"min": 10, "max": 12, "word": "count"})])

    def test_boomat_and_fallback(self):
        br = self.br
        calls = []
        br._call = lambda kind, func, args, timeout, ctx: calls.append((func, args, ctx)) or 1
        br._boom([-298.0, 70.0, 305.0])
        self.assertEqual(calls[-1][:2], ("mcw_boomat", (-29800, -30400, 7000)))   # MC -> мировые см Ведьмака
        br._res_boom(self.req("boom", **calls[-1][2]), 9, Res(9, ok=False), None)
        self.assertIs(br.caps["boomat"], False)
        self.assertEqual(calls[-1][:2], ("mcw_boom", ()))      # тот же взрыв — старой функцией
        br._res_boom(self.req("boom", **calls[-1][2]), 10, Res(10, "boom 1"), None)
        self.assertEqual(br.booms, 1)

    def test_resync_backoff(self):
        br = self.br
        sent = []
        br._send_clearall = lambda: sent.append(time.monotonic())
        t = 1000.0
        for i in range(4):
            br.w3_state = "synced"
            br.request_resync("тест")
            br._step_clearall(t)
            t += 1.0
        self.assertEqual(len(sent), 1)                        # 5 с не прошло
        intervals = []
        for i in range(4):
            br.w3_state = "synced"
            br.request_resync("тест")
            t = br.last_resync + br.resync_interval
            br._step_clearall(t)
            intervals.append(br.resync_interval)
        self.assertEqual(len(sent), 5)
        self.assertEqual(intervals, [10.0, 20.0, 40.0, 60.0])  # подряд — вдвое, до max_interval_s
        br.w3_state = "synced"
        br.request_resync("сокет", force=True)
        br._step_clearall(t + 0.1)
        self.assertEqual(len(sent), 6)                        # force — без ожидания

    def test_ground_pauses_when_game_is_slow(self):
        br = self.br
        br.grounds_cap = False                                # мод без mcw_grounds: по одной колонне
        calls = []
        br._call = lambda kind, func, args, timeout, ctx: calls.append((func, args)) or len(calls)
        br.pose = __import__("bridge").Pose(coords.Pos(-30050, -29950, 7000, 9000), None, 0.0)
        br.ground_tokens = 10.0
        br.poll_rtt.append(0.4)
        br._step_ground_queries(br.ground_tick)
        self.assertEqual(calls, [])
        br.poll_rtt.append(0.02)
        br._step_ground_queries(br.ground_tick)
        self.assertEqual(len(calls), 4)                       # ground.inflight
        self.assertEqual(calls[0], ("mcw_ground", (-30050, -29950)))   # колонна под Геральтом первой

    def test_notify_text_is_valid_exec_argument(self):
        # замер 5.00c: кавычка внутри строки и пустая строка "" ломают разбор всей команды exec
        import w3net
        br = self.br
        br.notify("MCW: \"Геральт\" сказал 'да' \\ ok")
        text = br.notes[-1][1]
        self.assertFalse(set(text) & set("\"'\\"), text)
        self.assertIn("Геральт", text)
        self.assertEqual(w3net.build_call("mcw_notify", 7, text), f'mcw_notify(7, "{text}")')   # fmt_arg пропускает
        n = len(br.notes)
        br.notify("")
        br.notify("   ")
        self.assertEqual(len(br.notes), n)                    # пустое в очередь не ставится, в игру не уходит
        br.notify("Ж" * 300)
        self.assertEqual(len(br.notes[-1][1].encode()), 120)
        # аргумент, который fmt_arg не пропустит, мост не роняет: команда не уходит, отказ в счётчике
        br.w3.client = type("Client", (), {"next_seq": lambda self: 5})()
        try:
            self.assertIsNone(br._call("notify", "mcw_notify", ('a"b',), 1.0, {}))
            self.assertIsNone(br._call("notify", "mcw_notify", ("",), 1.0, {}))
        finally:
            br.w3.client = None
        self.assertEqual(br.stats["refused_notify"], 2)

    def test_inv_hp_notify(self):
        br = self.br
        br.on_inv({"t": "inv", "sel": 2, "s": [[0, "minecraft:dirt", 8]]})
        self.assertEqual(br.slot, 2)
        self.assertEqual(len(br.notes), 0)                    # первый inv — только база
        br.on_inv({"t": "inv", "sel": 2, "s": [[0, "minecraft:dirt", 7], [1, "minecraft:oak_log", 2]]})
        br.next_note = 1e18                                   # только копим, в игру не шлём (сети здесь нет)
        br._step_notify(br.inv_flush_at + 0.01)
        self.assertIn("-1 dirt", br.notes[-1][1])
        self.assertIn("+2 oak_log", br.notes[-1][1])
        br.on_hp({"t": "hp", "hp": 20.0, "food": 20})
        br.on_hp({"t": "hp", "hp": 20.0, "food": 19})
        br.on_hp({"t": "hp", "hp": 14.5, "food": 19})
        br.on_hp({"t": "hp", "hp": 12.0, "food": 19})
        hp = [t for k, t in br.notes if k == "hp"]
        self.assertEqual(hp, ["MCW: HP 12/20, food 19"])      # одно сообщение рода hp в очереди


class LegacyModTest(unittest.TestCase):
    """Мод без mcw_countblocks, mcw_boomat и строки normal: мост откатывается на mcw_count (с допуском на бочки),
    mcw_boom и свой вывод грани — и всё работает."""

    def test_fallbacks(self):
        L = Loop({**WALL, **CUBE}, fake_kw={"legacy": True})
        self.addCleanup(L.close)
        self.assertTrue(wait_until(lambda: L.synced(EXPECTED), 15))
        self.assertTrue(wait_until(lambda: L.br.caps["countblocks"] is False, 3))
        L.press("F2")
        L.press("8")
        self.assertTrue(wait_until(lambda: L.mod.kinds("act"), 3))
        act = L.mod.kinds("act")[-1]
        self.assertEqual((act["b"], act["face"]), ([-304, 70, 300], "east"))      # грань выведена мостом
        exp = EXPECTED - {(-304, 70, 300)}
        self.assertTrue(wait_until(lambda: L.synced(exp), 5))
        resyncs = L.br.stats["resyncs"]
        L.mod.send({"t": "explosion", "tick": 2, "p": [-298.0, 70.0, 305.0], "r": 4.0, "src": "tnt"})
        self.assertTrue(wait_until(lambda: any(e.on_fire for e in L.fw.world.ents.values()), 3))
        self.assertIs(L.br.caps["boomat"], False)
        time.sleep(1.0)                                       # пара сверок mcw_count: бочка в допуске
        self.assertEqual(L.br.stats["resyncs"], resyncs)
        self.assertEqual(L.br.booms, 1)
        seen = " ".join(L.fw.execs_seen)
        self.assertIn("mcw_count(", seen)
        self.assertIn("mcw_boom(", seen)


class LimitsTest(unittest.TestCase):
    def test_limit_radius_and_slow_game(self):
        """Медленная игра (5 кадров/с, 1 exec за кадр): опрос деградирует, в полёте не больше лимитов, показ
        упирается в max_entities, все пакеты <= batch.bytes."""
        blocks = {(x, 70, z): "minecraft:stone" for x in range(-330, -270) for z in range(270, 331, 2)}
        L = Loop(blocks, display={"max_entities": 150, "radius_m": 16.0}, batch={"bytes": 600},
                 ground={"enabled": False})
        self.addCleanup(L.close)
        L.fw.frame_time = 0.2
        L.fw.max_per_frame = 1
        peak = {"all": 0, "poll": 0}

        def watch():
            peak["all"] = max(peak["all"], len(L.br.inflight))
            peak["poll"] = max(peak["poll"], sum(1 for r in list(L.br.inflight.values()) if r.kind == "poll"))
            return False

        wait_until(watch, 4.0, step=0.005)
        self.assertLessEqual(peak["all"], L.cfg.w3.max_inflight)
        self.assertLessEqual(peak["poll"], L.cfg.poll.depth)
        polls = L.br.stats["poll_ok"]
        self.assertLess(polls, 4.0 * 6)                         # 20 Гц не держит: игра отвечает раз в 200 мс
        L.fw.frame_time = 1 / 60
        L.fw.max_per_frame = 0
        self.assertTrue(wait_until(lambda: L.br.snapshot.get("shown") == 150 and L.br.snapshot.get("pending_ops") == 0
                                   and L.br.snapshot.get("batch_inflight") == 0, 15), L.br.snapshot)
        cells = [p for _, p, _ in L.br.desired]
        center = (-300.5, 70.0, 300.5)
        far = max(sum((p[a] + 0.5 - center[a]) ** 2 for a in range(3)) ** 0.5 for p in cells)
        self.assertLessEqual(far, 16.0)
        batches = [c for c in L.fw.execs_seen if c.startswith("mcw_batch(")]
        self.assertLessEqual(max(coords.utf8_len(c) for c in batches), 600)
        self.assertTrue(wait_until(lambda: L.fw.world.count() == 150, 3), (L.fw.world.count(), L.br.snapshot))
        self.assertEqual(fake_cells(L.fw), origins(cells))


# ---------------------------------------------------------------------------------------------------------- этапы 3 и 5
def ent_msg(spawn=(), move=(), hp=(), fuse=(), item=(), remove=()):
    return {"t": "ent", "tick": 1, "spawn": [list(s) for s in spawn], "move": [list(s) for s in move],
            "hp": [list(s) for s in hp], "fuse": [list(s) for s in fuse], "item": [list(s) for s in item],
            "remove": list(remove)}


def creeper(i, x, y, z, yaw=0.0, hp=20.0):
    return (i, "minecraft:creeper", x, y, z, yaw, hp)


def mob_origins(fw):
    return {slot: (e.x, e.y, e.z) for slot, e in fw.mobs().items()}


class MobsTest(unittest.TestCase):
    def test_table_events(self):
        t = EntTable()
        ev = t.apply(ent_msg(spawn=[creeper(1, 0.5, 70, 0.5), (2, "minecraft:item", 1.0, 70, 1.0, 0.0, None)],
                             item=[(2, "minecraft:oak_log", 3)]))
        self.assertEqual(ev.new, [1, 2])
        self.assertEqual((t.ents[2].item, t.ents[2].count, t.ents[2].hp), ("minecraft:oak_log", 3, None))
        ev = t.apply(ent_msg(move=[(1, 1.5, 70, 0.5, 45.0)], hp=[(1, 15.0)], fuse=[(1, 1)], remove=[2, 99]))
        self.assertEqual((ev.fuse_on, ev.hurt, ev.gone), ([1], [1], [2]))
        self.assertEqual((t.ents[1].p, t.ents[1].yaw, t.ents[1].hp, t.ents[1].fuse), ((1.5, 70, 0.5), 45.0, 15.0, 1))
        self.assertEqual(t.apply(ent_msg(fuse=[(1, 1)])).fuse_on, [])          # уже шипел — второго события нет
        t.apply(ent_msg(spawn=[(5, "minecraft:item", 3.0, 70, 0.5, 0.0, None)], item=[(5, "minecraft:dirt", 2)]))
        self.assertEqual(t.items_near((0.5, 70, 0.5), 8.0), {"minecraft:dirt": 2})
        self.assertEqual(t.items_near((50.0, 70, 0.5), 8.0), {})

    def test_display_slots_moves_kills(self):
        t, d = EntTable(), EntDisplay(TPL)
        t.apply(ent_msg(spawn=[creeper(10, 3.0, 70, 0.5), creeper(11, 1.0, 70, 0.5),
                               (12, "minecraft:item", 0.5, 70, 0.5, 0.0, None)]))
        ops = d.ops(t, (0.5, 70, 0.5), 32.0, 32)
        self.assertEqual([(k, i) for _, i, k in ops], [("wipe", -1), ("spawn", 11), ("spawn", 10)])   # ближние первыми
        self.assertEqual(ops[1][0], coords.ent_spawn_op(0, 1.0, 70, 0.5, 0.0, CRATE))
        d.commit(ops, t)
        self.assertEqual({i: s.slot for i, s in d.shown.items()}, {11: 0, 10: 1})              # предмет не показан
        self.assertEqual(d.ops(t, (0.5, 70, 0.5), 32.0, 32), [])
        t.apply(ent_msg(move=[(10, 3.03, 70, 0.5, 2.0), (11, 1.2, 70, 0.5, 0.0)]))           # 3 см и 2° — в допуске
        ops = d.ops(t, (0.5, 70, 0.5), 32.0, 32)
        self.assertEqual(ops, [(coords.ent_move_op(0, 1.2, 70, 0.5, 0.0, CRATE), 11, "move")])
        d.commit(ops, t)
        # 11 пропал, 13 появился: kill отдаёт слот 0 в конец очереди, 13 берёт слот 2
        t.apply(ent_msg(spawn=[creeper(13, 2.0, 70, 2.0)], remove=[11]))
        ops = d.ops(t, (0.5, 70, 0.5), 32.0, 32)
        self.assertEqual([op for op, _, _ in ops], ["kill:0", coords.ent_spawn_op(2, 2.0, 70, 2.0, 0.0, CRATE)])
        d.commit(ops, t)
        self.assertEqual(list(d.free)[-1], 0)
        # дальше радиуса и сверх лимита — kill
        ops = d.ops(t, (0.5, 70, 0.5), 2.5, 1)
        self.assertEqual(sorted(i for _, i, k in ops if k == "kill"), [10])
        d.reset()
        self.assertEqual(d.ops(t, (0.5, 70, 0.5), 32.0, 32)[0], ("wipe", -1, "wipe"))

    def test_templates_and_room(self):
        t = EntTable()
        d = EntDisplay(TPL, {"minecraft:zombie": -1, "minecraft:creeper": 1}, default_t=0)
        t.apply(ent_msg(spawn=[creeper(1, 0.5, 70, 0.5), (2, "minecraft:zombie", 1, 70, 1, 0.0, 20.0),
                               (3, "minecraft:pig", 2, 70, 2, 0.0, 10.0)]))
        ops = d.ops(t, (0.5, 70, 0.5), 32.0, 32)
        self.assertEqual(sorted((i, op.split(":")[-1]) for op, i, k in ops if k == "spawn"), [(1, "1"), (3, "0")])
        d.commit(ops, t)
        self.assertTrue(d.near_column(0, 0))                    # колонна (0, 0) под крипером
        self.assertFalse(d.near_column(5, 0))
        self.assertIsNotNone(d.hit((0.6, 70.8, 0.4)))           # точка в ящике
        self.assertIsNone(d.hit((0.6, 72.0, 0.4)))

    def test_pack_ents(self):
        ops = [coords.ent_spawn_op(i % 64, i * 1.5, 70.0, -i * 2.5, i * 7.0, CRATE) for i in range(300)]
        for limit in (80, 1024, 2047):
            i = 0
            while i < len(ops):
                n = pack_ents(ops[i:], 5, limit)
                self.assertLessEqual(coords.utf8_len(coords.ents_call(5, ops[i:i + n])), limit)
                if i + n < len(ops):
                    self.assertGreater(coords.utf8_len(coords.ents_call(5, ops[i:i + n + 1])), limit)
                i += n
        with self.assertRaises(ValueError):
            pack_ents(["spawn:1:100000:200000:300000:0:0"], 1, 30)
        with self.assertRaises(ValueError):
            pack_ents(ops, 5, 2048)                        # предел команды exec 2047 (замер 5.00c)

    def test_ents_semantics_on_fake(self):
        """Пакеты EntDisplay, исполненные по контракту mcw_ents фейка, дают ровно показанное: слоты, позиции, без
        дублей и ошибок; блоки mcw_batch рядом не мешают."""
        fw = fake_w3.FakeWitcher("127.0.0.1", 0)
        fw.world.spawn(coords.load_templates()["cont_crate"].path, 46, 50, 51)      # блок рядом
        t, d = EntTable(), EntDisplay(TPL)
        rnd = __import__("random").Random(7)
        alive: set[int] = set()
        next_id = 100
        for step in range(40):
            spawn, move, remove = [], [], []
            for i in list(alive):
                r = rnd.random()
                if r < 0.15:
                    remove.append(i)
                    alive.discard(i)
                elif r < 0.7:
                    move.append((i, rnd.uniform(-20, 20), 70.0, rnd.uniform(-20, 20), rnd.uniform(-180, 180)))
            for _ in range(rnd.randrange(0, 4)):
                next_id += 1
                alive.add(next_id)
                spawn.append(creeper(next_id, rnd.uniform(-20, 20), 70.0, rnd.uniform(-20, 20)))
            t.apply(ent_msg(spawn=spawn, move=move, remove=remove))
            ops = d.ops(t, (0.0, 70.0, 0.0), 24.0, 8)
            strs = [o[0] for o in ops]
            i = 0
            while i < len(strs):
                n = pack_ents(strs[i:], 1000 + step, 200)
                out, _ = fw._x_mcw_ents(0, "|".join(strs[i:i + n]))
                self.assertEqual(out[0].split()[4], "0", (out, strs[i:i + n]))     # ни одной ошибки
                d.commit(ops[i:i + n], t)
                i += n
            exp = {s.slot: coords.ent_origin_cm(*t.ents[i].p, CRATE) for i, s in d.shown.items()}
            self.assertEqual(mob_origins(fw), exp)
            self.assertLessEqual(len(d.shown), 8)
        self.assertEqual(fw.world.count(), 1)                                       # блок не тронут
        fw._listener.close()


class CraftMenuTest(unittest.TestCase):
    def test_cursor_and_text(self):
        c = CraftMenu()
        self.assertEqual(c.text(), "MCW craft: nothing to craft from the inventory")
        c.set_list({"t": "recipes", "table": None, "r": [["minecraft:crafting_table", "minecraft:crafting_table", 1, 2, 2],
                                                         ["minecraft:oak_planks", "minecraft:oak_planks", 4, 3, 2],
                                                         ["minecraft:wooden_pickaxe", "minecraft:wooden_pickaxe", 1, 1, 3]]})
        self.assertEqual(c.text(), "MCW craft 1/3: crafting_table x1, can 2")
        c.move(-1)
        self.assertEqual(c.text(), "MCW craft 3/3: wooden_pickaxe x1, can 1 (needs table)")
        # новый список: курсор остаётся на рецепте, даже если он сменил место; есть верстак
        c.set_list({"t": "recipes", "table": [1, 70, 2], "r": [["minecraft:stick", "minecraft:stick", 4, 1, 2],
                                                               ["minecraft:wooden_pickaxe", "minecraft:wooden_pickaxe", 1, 1, 3]]})
        self.assertEqual(c.text(), "MCW craft 2/2: wooden_pickaxe x1, can 1 (table)")
        c.set_list({"t": "recipes", "table": None, "r": [["minecraft:stick", "minecraft:stick", 4, 1, 2]]})
        self.assertEqual(c.current().id, "minecraft:stick")                         # рецепта больше нет — ближайший
        self.assertEqual(safe_text(c.text()), c.text())                             # безопасный алфавит mcw_notify


class StageLogicTest(unittest.TestCase):
    """Обработчики этапов 3 и 5 напрямую: защита рельефа и луча от ящиков мобов, слот без мода, сбой mcw_ents."""

    setUp = BridgeLogicTest.setUp
    req = BridgeLogicTest.req

    def test_ground_skips_column_under_mob(self):
        br = self.br
        br.grounds_cap = False
        sent = []
        br._call = lambda kind, func, args, timeout, ctx: sent.append(ctx) or len(sent)
        br.pose = __import__("bridge").Pose(coords.Pos(-30050, -29950, 7000, 9000), None, 0.0)
        br.entd.shown[5] = __import__("mobs").Shown(0, 0, (-300.5, 70.0, 300.5), 0.0, "minecraft:creeper")
        br.ground_tokens = 10.0
        br._step_ground_queries(br.ground_tick)
        self.assertTrue(sent[0]["mob"])                                         # колонна под Геральтом — под мобом
        self.assertFalse(br.entd.near_column(-304, 300))                        # в 3 м — уже нет
        br._res_ground(self.req("ground", **sent[0]), 1, Res(1, "ground 1 7200"), None)
        self.assertIsNone(br.ground.top_at(*sent[0]["col"]))                     # высоте не поверили
        self.assertIn(sent[0]["col"], br.ground.none_until)                     # спросим позже
        self.assertEqual(br.stats["ground_mob_blocked"], 1)

    def test_grounds_batch_and_fallback(self):
        br = self.br
        sent = []
        br._call = lambda kind, func, args, timeout, ctx: sent.append((func, args, ctx)) or len(sent)
        br.pose = __import__("bridge").Pose(coords.Pos(-30050, -29950, 7000, 9000), None, 0.0)
        br._step_ground_queries(br.ground_tick)
        func, args, ctx = sent[0]
        self.assertEqual(func, "mcw_grounds")
        self.assertEqual(len(ctx["cols"]), br.cfg.ground.batch_cols)
        self.assertTrue(args[0].startswith("-30050:-29950|"))                   # колонна под Геральтом первой
        n = len(ctx["cols"])
        lines = [" ".join(["1:7200"] * min(16, n - k)) for k in range(0, n, 16)]
        br._res_grounds(self.req("grounds", **ctx), 1, Res(1, *[f"gs {ln}" for ln in lines], "gms 1"), None)
        self.assertIs(br.grounds_cap, True)
        self.assertEqual(br.ground.height.get(ctx["cols"][0]), 7200)
        sent.clear()
        br._step_ground_queries(br.ground_tick)
        self.assertEqual(sent, [])                                               # batch_hz: следующий — позже
        br.next_grounds = 0.0
        br._step_ground_queries(br.ground_tick)
        ctx2 = sent[0][2]
        br._res_grounds(self.req("grounds", **ctx2), 2, Res(2, ok=False), None)   # старый мод
        self.assertIs(br.grounds_cap, False)
        self.assertFalse(any(c in br.ground.pending for c in ctx2["cols"]))      # колонны снова свободны
        sent.clear()
        br.ground_tokens = 10.0
        br._step_ground_queries(br.ground_tick)
        self.assertEqual(sent[0][0], "mcw_ground")

    def test_look_into_mob(self):
        br = self.br
        br.mc.send = lambda m: self.fail(f"act ушёл моду: {m}")
        br.entd.shown[5] = __import__("mobs").Shown(0, 0, (-300.5, 70.0, 296.5), 0.0, "minecraft:creeper")
        pose = __import__("bridge").Pose(coords.Pos(-30050, -29950, 7000, 9000), None, 0.0)
        hit = coords.mc_pos_to_w(-300.4, 70.6, 296.0)                            # передняя грань ящика
        r = Res(3, f"look 1 {coords.to_cm(hit[0])} {coords.to_cm(hit[1])} {coords.to_cm(hit[2])} 0")
        br._res_look(self.req("look", action="place", pose=pose, slot=0, t=0.0), 3, r, None)
        self.assertEqual(br.notes[-1][1], "MCW: place: creeper in the way")

    def test_select_needs_minecraft(self):
        br = self.br
        br.slot = 2
        br.select_slot(5)
        self.assertEqual(br.slot, 2)                                            # без мода слот не меняется
        self.assertEqual(br.notes[-1][1], "MCW: Minecraft is offline")

    def test_ents_failures(self):
        br = self.br
        br.entd.need_wipe = False
        br._res_ents(self.req("ents"), 4, Res(4, "ents 1 0 0 0 1"), None)
        self.assertTrue(br.ents_cap and not br.entd.need_wipe)
        br._res_ents(self.req("ents"), 5, Res(5, "ents 0 1 0 1 1"), None)      # move пустого слота: разошлись
        self.assertTrue(br.entd.need_wipe)
        br.entd.need_wipe = False
        br._res_ents(self.req("ents"), 6, None, TimeoutError("lost"))
        self.assertTrue(br.entd.need_wipe)
        br._res_ents(self.req("ents", epoch=br.epoch - 1), 7, None, TimeoutError("old"))
        br._res_ents(self.req("ents"), 8, Res(8, ok=False), None)             # старый мод
        self.assertIs(br.ents_cap, False)
        br._step_ents(1e9)                                                      # больше не шлём
        self.assertEqual(br._n("ents"), 0)

    def test_hiss_and_items_notify(self):
        br = self.br
        br.pose = __import__("bridge").Pose(coords.Pos(-30050, -29950, 7000, 9000), None, 0.0)
        br.on_mc(ent_msg(spawn=[creeper(1, -297.5, 70, 300.5)]))
        br.on_mc(ent_msg(fuse=[(1, 1)]))
        self.assertEqual(br.notes[-1], ("fuse", "MCW: creeper is hissing!"))
        br.on_mc(ent_msg(spawn=[(2, "minecraft:item", -299.5, 70, 300.5, 0.0, None)], item=[(2, "minecraft:oak_log", 2)]))
        self.assertEqual(br.notes[-1], ("items", "MCW: on the ground: 2 oak_log"))
        br.on_mc(ent_msg(remove=[2]))                                           # подобрали — нового сообщения нет
        self.assertEqual(br.notes[-1][0], "items")
        self.assertEqual(br.items_near, 0)


class StageLoopTest(unittest.TestCase):
    """Мост в потоке + fake_w3 + FakeMod: крафт клавишами, призыв, крипер-плейсхолдер в фейке."""

    def setUp(self):
        self.L = Loop({**WALL, **CUBE}, ents={"hz": 50.0})
        self.addCleanup(self.L.close)

    def notified(self, text, timeout=3.0):
        return wait_until(lambda: any(text in n for n in self.L.fw.world.notifications), timeout)

    def test_craft_keys(self):
        L = self.L
        self.assertTrue(wait_until(lambda: L.synced(EXPECTED), 15))
        L.press("F3")                                                           # без режима строительства — ничего
        time.sleep(0.2)
        self.assertEqual(L.mod.kinds("recipes"), [])
        L.press("F2")
        L.press("F3")
        self.assertTrue(self.notified("MCW craft 1/2: oak_planks x4, can 2"), L.fw.world.notifications)
        L.press("NUMPAD0")
        self.assertTrue(wait_until(lambda: L.mod.kinds("craft"), 3))
        self.assertEqual(L.mod.kinds("craft")[-1] | {"id": 0}, {"t": "craft", "id": 0, "recipe": "minecraft:oak_planks", "n": 1})
        self.assertTrue(self.notified("MCW: crafted 4 oak_planks"))
        self.assertTrue(wait_until(lambda: len(L.mod.kinds("recipes")) >= 2, 3))   # после крафта список заново
        L.press("PAGEDOWN")
        self.assertTrue(self.notified("MCW craft 2/2: wooden_pickaxe x1, can 1 (needs table)"), L.fw.world.notifications)
        L.press("0")
        self.assertTrue(self.notified("MCW: cannot craft wooden_pickaxe: needs a crafting table in reach"))
        n = len(L.mod.kinds("recipes"))
        L.mod.send({"t": "inv", "tick": 2, "sel": 0, "s": [[0, "minecraft:dirt", 8], [1, "minecraft:oak_planks", 4]]})
        self.assertTrue(wait_until(lambda: len(L.mod.kinds("recipes")) > n, 3))    # инвентарь сменился — список заново
        L.press("F3")
        self.assertTrue(self.notified("MCW: craft list closed"))
        self.assertFalse(L.br.craft.open)

    def test_first_person_toggle(self):
        """F4 — вид от первого лица (mcwf_on): работает и вне режима строительства, второе нажатие выключает."""
        L = self.L
        self.assertTrue(wait_until(lambda: L.synced(EXPECTED), 15))
        self.assertFalse(L.br.build_mode)
        L.press("F4")
        self.assertTrue(self.notified("MCW: first person ON"))
        self.assertTrue(L.fw.fp["on"])
        self.assertEqual(L.fw.fp["src"], 0)
        self.assertTrue(L.br.fp_on)
        L.press("F4")
        self.assertTrue(self.notified("MCW: first person OFF"))
        self.assertFalse(L.fw.fp["on"])
        self.assertFalse(L.br.fp_on)

    def test_summon_and_creeper_placeholder(self):
        L = self.L
        self.assertTrue(wait_until(lambda: L.synced(EXPECTED), 15))
        L.press("F2")
        L.press("F6")
        self.assertTrue(wait_until(lambda: L.mod.kinds("spawn"), 3))
        self.assertEqual(L.mod.kinds("spawn")[-1]["type"], "minecraft:creeper")
        self.assertTrue(self.notified("MCW: creeper summoned"))
        # мод: крипер в 4 м за спиной Геральта идёт к нему, шипит и взрывается
        L.mod.send(ent_msg(spawn=[creeper(900, -296.5, 70.0, 300.5, 90.0)]))
        self.assertTrue(wait_until(lambda: len(L.fw.mobs()) == 1, 3))
        slot, e = next(iter(L.fw.mobs().items()))
        self.assertEqual((e.x, e.y, e.z), coords.ent_origin_cm(-296.5, 70.0, 300.5, CRATE))
        self.assertEqual((e.yaw, e.tags), (coords.yaw_cdeg(90.0), {"MCW_ENT", f"MCW_E{slot}"}))
        for k in range(1, 5):
            L.mod.send(ent_msg(move=[(900, -296.5 - k * 0.5, 70.0, 300.5, 90.0)]))
            time.sleep(0.05)
        want = coords.ent_origin_cm(-298.5, 70.0, 300.5, CRATE)
        self.assertTrue(wait_until(lambda: (e.x, e.y, e.z) == want, 3), (e.x, e.y, e.z, want))
        self.assertGreaterEqual(e.moves, 1)
        L.mod.send(ent_msg(fuse=[(900, 1)]))
        self.assertTrue(self.notified("MCW: creeper is hissing!"))
        L.mod.send({"t": "explosion", "tick": 3, "p": [-298.5, 70.0, 300.5], "r": 3.0, "src": "creeper"})
        L.mod.send(ent_msg(remove=[900]))
        self.assertTrue(wait_until(lambda: not L.fw.mobs() and any(x.on_fire for x in L.fw.world.ents.values()), 3))
        self.assertEqual(L.fw.world.count(), len(EXPECTED) + 1)                  # блоки + бочка-проба, мобов нет
        self.assertEqual(L.br.stats["ents_errors"], 0)
        self.assertTrue(L.br.ents_cap)

    def test_resync_respawns_mobs(self):
        L = self.L
        self.assertTrue(wait_until(lambda: L.synced(EXPECTED), 15))
        L.mod.send(ent_msg(spawn=[creeper(901, -296.5, 70.0, 302.5), creeper(902, -295.5, 70.0, 298.5)]))
        self.assertTrue(wait_until(lambda: len(L.fw.mobs()) == 2, 3))
        # «загрузка сейва»: в Ведьмаке пропало всё (и блоки, и мобы) -> ресинк блоков -> wipe и мобы заново
        L.fw.world.ents.clear()
        self.assertTrue(wait_until(lambda: len(L.fw.mobs()) == 2 and L.br.stats["resyncs"] >= 1, 5))
        # рестарт моста: старые мобы в игре -> wipe первым пакетом, дублей нет (мод после hello шлёт всех заново)
        hellos = L.mod.hellos
        L.stop_bridge()
        L.start()
        self.assertTrue(wait_until(lambda: L.mod.hellos > hellos, 5))
        L.mod.send(ent_msg(spawn=[creeper(901, -296.5, 70.0, 302.5), creeper(902, -295.5, 70.0, 298.5)]))
        self.assertTrue(wait_until(lambda: L.br.stats.get("ents_spawn", 0) >= 2, 10))
        self.assertTrue(wait_until(lambda: len([x for x in L.fw.world.ents.values() if "MCW_ENT" in x.tags]) == 2, 3))


class LegacyEntsTest(unittest.TestCase):
    def test_mod_without_mcw_ents(self):
        L = Loop({**WALL, **CUBE}, fake_kw={"legacy": True}, ents={"hz": 50.0})
        self.addCleanup(L.close)
        self.assertTrue(wait_until(lambda: L.synced(EXPECTED), 15))
        L.mod.send(ent_msg(spawn=[creeper(903, -296.5, 70.0, 302.5)]))
        self.assertTrue(wait_until(lambda: L.br.ents_cap is False, 3))
        time.sleep(0.3)
        self.assertEqual(L.fw.mobs(), {})
        self.assertEqual(sum(1 for c in L.fw.execs_seen if c.startswith("mcw_ents(")), 1)   # попробовал раз и бросил
        self.assertTrue(L.synced(EXPECTED))


if __name__ == "__main__":
    unittest.main()
