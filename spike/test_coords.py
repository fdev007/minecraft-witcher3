"""Тесты coords.py:  python -m unittest test_coords -v   (из папки spike)."""
import contextlib
import io
import itertools
import math
import random
import unittest

import coords as C


class Blocks(unittest.TestCase):
    def test_plan_formula(self):
        self.assertEqual(C.w_to_block(0.5, 0.5, 0.5, y_offset=0), (0, 0, 0))
        self.assertEqual(C.w_to_block(-0.5, -0.5, -0.5, y_offset=0), (-1, -1, 1))
        self.assertEqual(C.w_to_block(10.2, -3.7, 64.9, y_offset=-30), (10, 34, 4))
        # с PLAN (mc.z = -floor(w.y)) расходится только на целой w.y: клетка (-m, 1-m] вместо [-m, 1-m)
        rnd = random.Random(3)
        for _ in range(2000):
            w = [rnd.uniform(-500, 500) for _ in range(3)]
            if w[1] != math.floor(w[1]):
                self.assertEqual(C.w_to_block(*w, y_offset=5),
                                 (math.floor(w[0]), math.floor(w[2]) + 5, -math.floor(w[1])))
        self.assertEqual(C.w_to_block(0.5, 5.0, 0.5, y_offset=0), (0, 0, -4))   # PLAN дал бы -5
        self.assertEqual(C.w_to_block(0.5, 5.01, 0.5, y_offset=0), (0, 0, -5))
        self.assertEqual(C.w_to_block(0.5, 4.99, 0.5, y_offset=0), (0, 0, -4))

    def test_center_roundtrip(self):
        for y_off in (0, 64, -100):
            for b in itertools.product((-3, -1, 0, 1, 7), repeat=3):
                self.assertEqual(C.w_to_block(*C.block_center_w(*b, y_offset=y_off), y_offset=y_off), b)

    def test_center_is_middle_of_cell(self):
        wx, wy, wz = C.block_center_w(2, 5, 3, y_offset=0)
        self.assertEqual((wx, wy, wz), (2.5, -2.5, 5.5))

    def test_continuous_pos_lands_in_same_block(self):
        rnd = random.Random(1)
        for _ in range(2000):
            w = [rnd.uniform(-500, 500) for _ in range(3)]
            mx, my, mz = C.w_to_mc_pos(*w, y_offset=17)
            self.assertEqual((math.floor(mx), math.floor(my), math.floor(mz)), C.w_to_block(*w, y_offset=17))
            back = C.mc_pos_to_w(mx, my, mz, y_offset=17)
            for a in range(3):
                self.assertAlmostEqual(back[a], w[a], places=9)

    def test_cm_quantized_including_whole_meters(self):
        """Протокол шлёт целые см: каждое 100-е значение — точно целые метры, граница клетки."""
        for ycm in range(-20000, 20001, 7):
            for xcm, zcm in ((0, 0), (-1, 199), (250, -300)):
                w = (xcm / 100, ycm / 100, zcm / 100)
                b = C.cm_to_block(xcm, ycm, zcm, y_offset=-64)
                self.assertEqual(C.w_to_block(*w, y_offset=-64), b)
                mx, my, mz = C.w_to_mc_pos(*w, y_offset=-64)
                self.assertEqual((math.floor(mx), math.floor(my), math.floor(mz)), b)
        for ycm in range(-1000, 1001, 100):   # ровно целые метры отдельно
            self.assertEqual(C.Pos(150, ycm, 4210, 0).block(0), (1, 42, math.floor(1 - ycm / 100)))
        p = C.parse_pos(C.parse_line("[MCW#1] pos 1234 -500 4210 0"))   # пример из ревью
        mx, my, mz, _ = p.mc(y_offset=0)
        self.assertEqual(p.block(0), (math.floor(mx), math.floor(my), math.floor(mz)))
        self.assertEqual(p.block(0), (12, 42, 6))


class Angles(unittest.TestCase):
    def w_forward(self, yaw):
        r = math.radians(yaw)
        return -math.sin(r), math.cos(r), 0.0

    def mc_forward(self, yaw, pitch=0.0):
        y, p = math.radians(yaw), math.radians(pitch)
        return -math.sin(y) * math.cos(p), -math.sin(p), math.cos(y) * math.cos(p)

    def test_cardinal(self):
        self.assertAlmostEqual(C.w_yaw_to_mc(0), 180)    # север
        self.assertAlmostEqual(C.w_yaw_to_mc(90), 90)    # запад
        self.assertAlmostEqual(C.w_yaw_to_mc(-90), -90)  # восток
        self.assertAlmostEqual(C.w_yaw_to_mc(180), 0)    # юг
        self.assertAlmostEqual(C.w_yaw_to_mc(270), -90)

    def test_yaw_matches_direction_vectors(self):
        for yaw in range(-360, 361, 15):
            wd = self.w_forward(yaw)
            mc_from_w = (wd[0], wd[2], -wd[1])   # оси: (x, z, -y)
            md = self.mc_forward(C.w_yaw_to_mc(yaw))
            for a in range(3):
                self.assertAlmostEqual(mc_from_w[a], md[a], places=9)
            self.assertAlmostEqual(math.cos(math.radians(C.mc_yaw_to_w(C.w_yaw_to_mc(yaw)) - yaw)), 1.0)

    def test_dir_to_angles(self):
        yaw, pitch = C.w_dir_to_mc_angles(0, 1, 0)
        self.assertAlmostEqual(yaw, 180)
        self.assertAlmostEqual(pitch, 0)
        self.assertAlmostEqual(C.w_dir_to_mc_angles(0, 0, -1)[1], 90)    # вниз
        self.assertAlmostEqual(C.w_dir_to_mc_angles(0, 0, 10000)[1], -90)
        for yaw in range(-180, 180, 20):
            for pitch in (-60, -10, 0, 30, 80):
                mx, my, mz = self.mc_forward(yaw, pitch)
                wdir = (mx * 10000, -mz * 10000, my * 10000)
                y2, p2 = C.w_dir_to_mc_angles(*wdir)
                self.assertAlmostEqual(math.cos(math.radians(y2 - yaw)), 1.0, places=9)
                self.assertAlmostEqual(p2, pitch, places=6)
        with self.assertRaises(ValueError):
            C.w_dir_to_mc_angles(0, 0, 0)


class Templates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = C.load_templates()

    def test_batch_indices_from_contract(self):
        bt = C.by_batch_t(self.t)
        self.assertEqual(bt[0].id, "cont_crate")
        self.assertEqual(bt[1].alias, "barrel")
        self.assertFalse(bt[1].known_bbox)

    def test_cont_crate_centered_in_block(self):
        tpl = self.t["cont_crate"]
        self.assertTrue(all(1.0 < s < 1.05 for s in tpl.size))
        for b in ((0, 0, 0), (-5, 70, 12)):
            o = C.entity_origin_w(*b, tpl=tpl, y_offset=0)
            c = C.block_center_w(*b, y_offset=0)
            mid = [o[a] + (tpl.bbox_min[a] + tpl.bbox_max[a]) / 2 for a in range(3)]
            for a in range(3):
                self.assertAlmostEqual(mid[a], c[a], places=9)

    def test_scaled_pivot_box250(self):
        tpl = self.t["denied_area"]   # 2.5 м, pivot снизу, transform компонента identity
        self.assertEqual(tpl.pivot_offset(0.4), (0.0, 0.0, 0.5))
        self.assertEqual(tpl.pivot_offset(), (0.0, 0.0, 1.25))
        self.assertEqual(C.entity_origin_cm(0, 0, 0, tpl=tpl, scale=0.4, y_offset=0), (50, 50, 0))

    def test_scale_replaces_component_scale(self):
        """SetScale мода заменяет scale компонента, pos компонента остаётся: comp_pos + s * центр меша."""
        box = self.t["box_05_05_1m"]          # компонент pos (0,0,0.5), scale (0.5,0.5,1), box.w2mesh 1 м
        self.assertEqual(box.pivot_offset(), (0.0, 0.0, 0.5))
        self.assertEqual(box.pivot_offset(2.0), (0.0, 0.0, 0.5))      # не 1.0, как давало s * центр bbox
        mn, mx = box.bbox_at(1.0)                                     # SetScale(1) -> куб 1 м
        self.assertEqual([round(mx[a] - mn[a], 6) for a in range(3)], [1.0, 1.0, 1.0])
        crate = self.t["carry_crate"]         # компонент pos (0,0,0.25), scale 0.5, меш crate.w2mesh
        c = crate.comps[0]
        for s in (0.5, 1.0, 2.0):
            want = [c.pos[a] + s * (c.mesh_min[a] + c.mesh_max[a]) / 2 for a in range(3)]
            for a in range(3):
                self.assertAlmostEqual(crate.pivot_offset(s)[a], want[a], places=9)
        for a in range(3):   # SetScale(0.5) = исходный масштаб компонента -> bbox из json
            self.assertAlmostEqual(crate.bbox_at(0.5)[0][a], crate.bbox_min[a], places=2)
        cont = self.t["cont_crate"]           # identity: старое и новое правило совпадают (bbox в json — до мм)
        for a in range(3):
            self.assertAlmostEqual(cont.pivot_offset(2.0)[a], 2.0 * cont.pivot_offset()[a], places=3)

    def test_scale_unknown_or_rotated(self):
        barrel = self.t["barrel"]             # APEX, bbox неизвестен: условный куб с центром в origin
        self.assertEqual(barrel.pivot_offset(), (0.0, 0.0, 0.0))
        self.assertEqual(barrel.pivot_offset(3.0), (0.0, 0.0, 0.0))
        with self.assertRaises(ValueError):
            self.t["water_barrier"].pivot_offset(1.0)   # у компонента есть поворот
        self.t["water_barrier"].pivot_offset()          # без масштаба — bbox из json

    def test_origin_cm_integers(self):
        x, y, z = C.entity_origin_cm(3, 1, -2, tpl=self.t["cont_crate"], y_offset=0)
        self.assertTrue(all(isinstance(v, int) for v in (x, y, z)))
        self.assertEqual((x, y, z), (346, 250, 151))


class Parsing(unittest.TestCase):
    REPLY = "\n".join([
        "[MCW#41] pos 12345 -6789 4210 -9000",
        "Script: [MCW#41] cam 12300 -6700 4390 -7071 7071 0",   # префикс перед тегом допустим
        "[MCW#41] area novigrad",
        "[MCW#41] world levels\\novigrad\\novigrad.w2w",
        "[MCW#40] pos 1 2 3 4",                                  # чужой seq
        "Spam: Command executed without errors",
        "[MCW#41] end",
    ])

    def test_parse_line(self):
        ln = C.parse_line("[MCW#7] echo 3 a b")
        self.assertEqual((ln.seq, ln.kind, ln.args, ln.rest), (7, "echo", ["3", "a", "b"], "3 a b"))
        self.assertIsNone(C.parse_line("Warn: Failed to process command"))
        self.assertEqual(C.parse_line("[MCW#9] end").kind, "end")

    def test_poll(self):
        r = C.parse_poll(self.REPLY, 41)
        self.assertTrue(r["complete"])
        self.assertEqual(r["pos"], C.Pos(12345, -6789, 4210, -9000))
        self.assertEqual(r["pos"].w, (123.45, -67.89, 42.1))
        self.assertEqual(r["pos"].block(y_offset=0), (123, 42, 68))
        x, y, z, yaw = r["pos"].mc(y_offset=0)
        self.assertAlmostEqual(z, 68.89)
        self.assertAlmostEqual(yaw, -90)                   # W3 yaw -90 = восток
        self.assertEqual(r["area"], "novigrad")
        self.assertEqual(r["world"], "levels\\novigrad\\novigrad.w2w")
        myaw, mpitch = r["cam"].mc_angles()
        self.assertAlmostEqual(myaw, 135, places=2)       # (-0.7071, 0.7071) = северо-запад
        self.assertAlmostEqual(mpitch, 0)

    def test_incomplete_and_bad(self):
        lines, complete = C.reply_lines("[MCW#5] pong 123", 5)
        self.assertFalse(complete)
        self.assertEqual(lines[0].args, ["123"])
        with self.assertRaises(ValueError):
            C.parse_pos(C.parse_line("[MCW#5] pos 1 2 3"))


class Disk(unittest.TestCase):
    def test_disk_within_radius(self):
        pos = C.Pos(12345, -6789, 4210, 0)
        blocks = C.disk_blocks(pos, r_out=9.5, r_in=2.0, layers=4, y_offset=0)
        self.assertEqual(len(blocks), len(set(blocks)))
        self.assertTrue(900 <= len(blocks) <= 1200, len(blocks))
        self.assertNotIn(pos.block(0), blocks)
        wx, wy, _ = pos.w
        self.assertEqual({b[1] for b in blocks}, {42, 43, 44, 45})
        for bx, by, bz in blocks:
            cx, cy, _ = C.block_center_w(bx, by, bz, y_offset=0)
            self.assertTrue(2.0 <= math.hypot(cx - wx, cy - wy) <= 9.5)

    def test_cli_disk(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            C._main(["disk", "--pos", "[MCW#5] pos 12345 -6789 4210 0", "--layers", "2", "--seq", "500"])
        cmds = out.getvalue().splitlines()
        self.assertTrue(cmds and all(c.startswith("mcw_batch(") for c in cmds))
        self.assertTrue(all(C.utf8_len(c) <= C.DEFAULT_BATCH for c in cmds))
        self.assertTrue(cmds[0].startswith('mcw_batch(500, "set:'))
        n_ops = sum(c.count("set:") for c in cmds)
        self.assertIn(f"{n_ops} блоков", err.getvalue())


class Batch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.crate = C.load_templates()["cont_crate"]

    def test_ops_format(self):
        self.assertEqual(C.set_op(0, 0, 0, self.crate, y_offset=0), "set:46:50:51:0")
        self.assertEqual(C.clear_op(0, 0, 0, self.crate, y_offset=0), "clear:46:50:51")
        self.assertEqual(C.batch_call(3, ["set:1:-2:3:0", "clear:1:-2:3"]),
                         'mcw_batch(3, "set:1:-2:3:0|clear:1:-2:3")')
        with self.assertRaises(ValueError):
            C.batch_call(1, ['set:1:2:3:0"); mcw_clearall(1'])
        with self.assertRaises(ValueError):
            C.set_op(0, 0, 0, C.load_templates()["denied_area"])

    def test_split_respects_limit_and_order(self):
        ops = [C.set_op(x, 64, z, self.crate) for x in range(-20, 20) for z in range(-20, 20)]
        for limit in (64, 300, C.DEFAULT_BATCH, C.MAX_CMD):
            cmds = C.build_batches(ops, seq0=100, limit=limit)
            self.assertEqual([s for s, _ in cmds], list(range(100, 100 + len(cmds))))
            back = []
            for seq, cmd in cmds:
                self.assertLessEqual(C.utf8_len(cmd), limit)
                self.assertTrue(cmd.startswith(f'mcw_batch({seq}, "'))
                back += cmd[cmd.index('"') + 1:-2].split("|")
            self.assertEqual(back, ops)
        self.assertEqual(len(C.build_batches(ops, 1, C.MAX_CMD)), 17)   # 1600 op ~ 33 КБ -> 17 команд по 2047 байт

    def test_limits(self):
        import w3net
        self.assertEqual(C.MAX_CMD, w3net.EXEC_MAX_CMD)                 # число повторено в coords: должно совпадать
        self.assertLessEqual(C.DEFAULT_BATCH, C.MAX_CMD)
        self.assertEqual(len(C.build_batches(["set:1:2:3:0"] * 400, 1, limit=C.MAX_CMD)), 3)
        with self.assertRaises(ValueError):
            C.build_batches(["set:1:2:3:0"], 1, limit=C.MAX_CMD + 1)  # 2048: игра молча выбросила бы команду
        with self.assertRaises(ValueError):
            C.build_batches(["set:1:2:3:0"], 1, limit=32767)
        with self.assertRaises(ValueError):
            C.build_batches(["set:1000000:2000000:3000000:0"], 1, limit=30)
        self.assertEqual(C.build_batches([], 1), [])

    def test_exact_fill(self):
        op = "set:1:2:3:0"
        one = C.utf8_len(C.batch_call(9, [op]))
        two = C.utf8_len(C.batch_call(9, [op, op]))
        self.assertEqual(len(C.build_batches([op, op], 9, limit=two)), 1)
        self.assertEqual(len(C.build_batches([op, op], 9, limit=two - 1)), 2)
        self.assertEqual(len(C.build_batches([op], 9, limit=one)), 1)


class Ents(unittest.TestCase):
    """mcw_ents: моб стоит ногами на земле — низ bbox шаблона на высоте ног, центр bbox над ними."""

    @classmethod
    def setUpClass(cls):
        t = C.load_templates()
        cls.crate, cls.barrel = t["cont_crate"], t["barrel"]

    def test_ops_format(self):
        # крипер в MC (6.5, 70, 0.5) смотрит на запад (yaw 90) -> Ведьмак (6.5, 0.5, 70) м, курс 90
        self.assertEqual(C.ent_spawn_op(3, 6.5, 70.0, 0.5, 90.0, self.crate, 0), "spawn:3:646:50:7052:9000:0")
        self.assertEqual(C.ent_move_op(3, 6.5, 70.0, 0.5, -90.0, self.crate, 0), "move:3:646:50:7052:-9000")
        self.assertEqual(C.ent_kill_op(63), "kill:63")
        # бочка без bbox (APEX): условный куб 1 м с pivot в центре -> origin на 0.5 м выше ног; yaw 0 (юг) -> курс 180
        self.assertEqual(C.ent_spawn_op(0, 0.0, 64.0, 0.0, 0.0, self.barrel, 0), "spawn:0:0:100:6450:18000:1")
        self.assertEqual(C.ents_call(5, ["wipe", "kill:1", "move:2:-1:-2:-3:-4"]),
                         'mcw_ents(5, "wipe|kill:1|move:2:-1:-2:-3:-4")')

    def test_bottom_on_feet(self):
        mn, mx = self.crate.bbox_at()
        for feet in ((0.5, 64.0, 0.5), (-300.25, 70.0, 310.75), (12.0, -5.5, -40.0)):
            ox, oy, oz = C.ent_origin_w(*feet, self.crate, 0)
            w = C.mc_pos_to_w(*feet, y_offset=0)
            self.assertAlmostEqual(oz + mn[2], w[2], places=9)               # низ ящика = ноги
            self.assertAlmostEqual(ox + (mn[0] + mx[0]) / 2, w[0], places=9)  # центр ящика — над ногами
            self.assertAlmostEqual(oy + (mn[1] + mx[1]) / 2, w[1], places=9)

    def test_rejects(self):
        for bad in (["kill:64x"], ['wipe"); mcw_clearall(1'], ["spawn:1:2:3:4:5"], ["move:-1:0:0:0:0"]):
            with self.assertRaises(ValueError):
                C.ents_call(1, bad)
        with self.assertRaises(ValueError):
            C.ent_kill_op(64)
        with self.assertRaises(ValueError):
            C.ent_spawn_op(1, 0, 0, 0, 0, C.load_templates()["denied_area"])


if __name__ == "__main__":
    unittest.main()
