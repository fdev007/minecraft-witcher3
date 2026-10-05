"""solid.py: разбор ответа mcw_cols и планировщик колонн коллизии для клиента MC. Без сети."""
import unittest

from solid import SolidPlanner, parse_line, parse_spans

WALL16 = "/".join(["-,331", "=", "=", "="] + ["-,88"] + ["="] * 11)


class ParseTest(unittest.TestCase):
    def test_spans(self):
        self.assertEqual(parse_spans("", 100, 0), [])
        self.assertEqual(parse_spans("604,662;425,467;-,331", 114, 0), [(718, 776), (539, 581), (None, 445)])
        self.assertEqual(parse_spans("-,88", 114, 300), [(None, 502)])

    def test_terrain_line(self):
        col, c = parse_line("t 5 -3 -12", 114, 0, 1.0)
        self.assertEqual(col, (5, -3))
        self.assertEqual((c.h, c.subs, c.door, c.z0), (102, None, False, 114))
        col, c = parse_line("t 5 -3 n", 114, 0, 1.0)
        self.assertIsNone(c.h)

    def test_statics_line(self):
        col, c = parse_line(f"c 8 0 88 1 {WALL16}", 114, 0, 2.0)
        self.assertEqual(col, (8, 0))
        self.assertTrue(c.door)
        self.assertEqual(c.h, 202)
        self.assertEqual(len(c.subs), 16)
        self.assertEqual(c.subs[0], [(None, 445)])
        self.assertEqual(c.subs[3], [(None, 445)])                   # «=» — как предыдущая
        self.assertEqual(c.subs[15], [(None, 202)])

    def test_bad_lines(self):
        self.assertIsNone(parse_line("cms 4", 0, 0, 0.0))
        self.assertIsNone(parse_line("stop 3", 0, 0, 0.0))
        with self.assertRaises(ValueError):
            parse_line("c 1 1 0 0 -,1/=", 0, 0, 0.0)                   # не 16 подколонн


class PlannerTest(unittest.TestCase):
    def setUp(self):
        self.p = SolidPlanner(radius_m=3.0, rescan_dz_m=4.0, door_radius_m=2.0, door_every_s=0.5)

    def test_nearest_first_and_pending(self):
        p = self.p
        w = p.want((0.5, 70.0, 0.5), (0.0, 0.0), 5, 0.0)
        self.assertEqual(w[0], (0, 0))
        self.assertEqual(len(w), 5)
        w2 = p.want((0.5, 70.0, 0.5), (0.0, 0.0), 100, 0.0)
        self.assertFalse(set(w) & set(w2))                               # ждущие ответа второй раз не просятся
        self.assertTrue(all((x + 0.5 - 0.5) ** 2 + (z + 0.5 - 0.5) ** 2 <= 9.0 for x, z in w + w2))

    def test_lead_along_velocity(self):
        w = self.p.want((0.5, 70.0, 0.5), (10.0, 0.0), 1, 0.0)        # бег на восток: первой — колонна впереди
        self.assertEqual(w, [(3, 0)])

    def test_reply_stop_and_resend(self):
        p = self.p
        asked = p.want((0.5, 70.0, 0.5), (0.0, 0.0), 3, 0.0)
        n = p.on_reply(asked, 7000, [f"t {asked[0][0]} {asked[0][1]} 0", "stop 1", "cms 1"], 1.0)
        self.assertEqual(n, 1)
        self.assertEqual(p.pending, set())
        again = p.want((0.5, 70.0, 0.5), (0.0, 0.0), 10, 1.0)
        self.assertNotIn(asked[0], again)
        self.assertIn(asked[1], again)                                   # не успела — снова в очереди

    def test_rescan_on_height_and_doors(self):
        p = self.p
        p.on_reply([], 7000, ["t 0 0 0", f"c 1 0 0 1 {WALL16}", f"c 2 2 0 1 {WALL16}"], 0.0)
        self.assertEqual(p.want((0.5, 70.0, 0.5), (0.0, 0.0), 100, 0.1).count((1, 0)), 0)
        p.pending.clear()
        w = p.want((0.5, 70.0, 0.5), (0.0, 0.0), 100, 0.6)              # дверь рядом, ответ старше 0,5 с
        self.assertIn((1, 0), w)
        self.assertNotIn((2, 2), w)                                      # дверь дальше door_radius_m
        self.assertNotIn((0, 0), w)
        p.pending.clear()
        w = p.want((0.5, 75.0, 0.5), (0.0, 0.0), 100, 0.6)              # поднялся на 5 м: окно ответов не годится
        self.assertIn((0, 0), w)

    def test_take_client_and_resend_on_change(self):
        p = self.p
        p.on_reply([], 7000, ["t 0 0 0", f"c 1 0 0 0 {WALL16}"], 0.0)
        c = p.take_client((0.5, 70.0, 0.5), 10)
        self.assertEqual(c[0], [0, 0, 7000, 0, None])
        self.assertEqual(c[1][:4], [1, 0, 7000, 0])
        self.assertEqual(c[1][4][0], [None, 7331])
        self.assertEqual(p.take_client((0.5, 70.0, 0.5), 10), [])
        p.on_reply([], 7000, ["t 0 0 0"], 1.0)                          # то же — не шлём
        self.assertEqual(p.take_client((0.5, 70.0, 0.5), 10), [])
        p.on_reply([], 7000, ["t 0 0 5"], 2.0)                          # поменялось — шлём
        self.assertEqual(p.take_client((0.5, 70.0, 0.5), 10), [[0, 0, 7005, 0, None]])
        p.client_reset()
        self.assertEqual(len(p.take_client((0.5, 70.0, 0.5), 10)), 2)

    def test_ground_at_ignores_roofs(self):
        p = self.p
        p.on_reply([], 114, ["c 3 3 -20 0 " + "/".join(["604,662;-,88"] + ["="] * 15)], 0.0)
        self.assertEqual(p.ground_at((3, 3)), 202)                        # пол, а не крыша
        p.on_reply([], 114, ["t 4 4 -14"], 0.0)
        self.assertEqual(p.ground_at((4, 4)), 100)
        self.assertIsNone(p.ground_at((9, 9)))

    def test_trim(self):
        p = self.p
        p.on_reply([], 0, ["t 0 0 0", "t 100 0 0"], 0.0)
        self.assertEqual(p.trim((0.5, 0.0, 0.5), 40.0), 1)
        self.assertIn((0, 0), p.cols)
        self.assertNotIn((100, 0), p.cols)


if __name__ == "__main__":
    unittest.main()
