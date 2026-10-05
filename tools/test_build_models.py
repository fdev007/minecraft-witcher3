"""build_models.py: box-UV Майнкрафта, меш коробки, части Стива и лошади. Без wcc_lite; меш — из r4data REDkit."""
import json
import os
import struct
import unittest

import build_blocks as bb
import build_models as bm
import cr2w

HAVE_R4 = os.path.isfile(os.path.join(bb.REDKIT, "r4data", bb.SRC_MESH))


class BoxUvTest(unittest.TestCase):
    """Углы граней — по таблице _note mc_models.json для головы (U, V) = (0, 0), (w, h, d) = (8, 8, 8)."""
    U, V, W, H, D = 0, 0, 8, 8, 8

    def uv(self, face, xmax, ymax, zmax, **kw):
        return bm.box_uv(face, (xmax, ymax, zmax), (self.U, self.V), (self.W, self.H, self.D), **kw)

    def test_front_is_face_rect(self):
        # перёд меша (+Y) = -Z модели: правый бок существа (X меша max) — левый край картинки лица (8, 8)
        self.assertEqual(self.uv("+y", True, True, True), (8, 8))      # x0 модели, y0 (верх)
        self.assertEqual(self.uv("+y", False, True, True), (16, 8))
        self.assertEqual(self.uv("+y", True, True, False), (8, 16))    # низ
        self.assertEqual(self.uv("+y", False, True, False), (16, 16))

    def test_top_bottom_back_sides(self):
        self.assertEqual(self.uv("+z", True, True, True), (8, 8))       # верх: x0 модели, z0 (перёд) -> (U+d, V+d)
        self.assertEqual(self.uv("+z", False, False, True), (16, 0))    # x1, z1 -> (U+d+w, V)
        self.assertEqual(self.uv("-z", True, True, False), (16, 8))     # низ: x0, z0 -> (U+d+w, V+d)
        self.assertEqual(self.uv("-z", False, False, False), (24, 0))   # x1, z1 -> (U+d+2w, V)
        self.assertEqual(self.uv("-y", False, False, True), (24, 8))    # зад: x1 модели -> (U+2d+w, V+d)
        self.assertEqual(self.uv("-y", True, False, True), (32, 8))     # x0 -> (U+2d+2w, V+d)
        self.assertEqual(self.uv("+x", True, True, True), (8, 8))       # правый бок: z0 (перёд) -> (U+d, V+d)
        self.assertEqual(self.uv("+x", True, False, True), (0, 8))      # z1 (зад) -> (U, V+d)
        self.assertEqual(self.uv("-x", False, True, True), (16, 8))     # левый бок: z0 -> (U+d+w, V+d)
        self.assertEqual(self.uv("-x", False, False, False), (24, 16))  # z1, низ -> (U+2d+w, V+d+h)

    def test_rows_split_limb(self):
        up = bm.box_uv("+y", (True, True, True), (40, 16), (4, 12, 4), rows=(0, 6))
        lo = bm.box_uv("+y", (True, True, False), (40, 16), (4, 12, 4), rows=(6, 12))
        self.assertEqual(up, (44, 20))
        self.assertEqual(lo, (44, 32))                                   # низ нижней половины = низ руки

    def test_mirror_swaps_side_rects(self):
        a = bm.box_uv("+x", (True, True, True), (48, 21), (4, 11, 4))
        b = bm.box_uv("-x", (False, True, True), (48, 21), (4, 11, 4), mirror=True)
        self.assertEqual(b[1], a[1])
        self.assertNotEqual(bm.box_uv("+x", (True, True, True), (48, 21), (4, 11, 4), mirror=True), a)

    def test_inset_moves_inside(self):
        u, v = bm.inset_uv(8, 8, "+y", (True, True, True), (0, 0), (8, 8, 8), None, False)
        self.assertGreater(u, 8)
        self.assertGreater(v, 8)
        u, v = bm.inset_uv(16, 16, "+y", (False, True, False), (0, 0), (8, 8, 8), None, False)
        self.assertLess(u, 16)
        self.assertLess(v, 16)


class PartsTest(unittest.TestCase):
    def test_steve_parts(self):
        parts = bm.steve_parts()
        self.assertEqual(len(parts), 20)
        by = {p["name"]: p for p in parts}
        self.assertEqual(by["head"]["bone"], "head")
        self.assertAlmostEqual(by["hat"]["ext"][1] - by["head"]["ext"][1], 0.5 * bm.K)
        self.assertEqual(by["l_leg2"]["rows"], (6, 12))
        for p in parts:
            e = p["ext"]
            self.assertTrue(e[0] < e[1] and e[2] < e[3] and e[4] < e[5], p["name"])

    def test_horse_parts(self):
        with open(bm.MODELS_JSON, encoding="utf-8") as f:
            models = json.load(f)
        parts, groups = bm.horse_parts(models)
        self.assertEqual(len(parts), 17)
        self.assertEqual(set(groups), {"body", "tail", "head_parts", "left_hind_leg", "right_hind_leg",
                                       "left_front_leg", "right_front_leg"})
        # копыта на земле: низ ноги (z меша) + пивот ноги = 0 (на 1,1 * 0,01 px — запас MC против z-fighting)
        leg = next(p for p in parts if p["name"] == "right_front_leg")
        self.assertAlmostEqual(groups["right_front_leg"][2] + leg["ext"][4], 0.0, delta=0.01)
        # шея наклонена на 30° в покое, ноги — нет
        self.assertAlmostEqual(groups["head_parts"][3], 0.5235988, places=5)
        self.assertEqual(groups["left_front_leg"][3], 0)
        # перёд лошади — +Y меша: передние ноги впереди задних
        self.assertGreater(groups["left_front_leg"][1], groups["left_hind_leg"][1])
        # левые ноги — слева (X меша < 0)
        self.assertLess(groups["left_front_leg"][0], 0)
        self.assertTrue(next(p for p in parts if p["name"] == "left_front_leg")["mirror"])


class ScriptSyncTest(unittest.TestCase):
    """mcw_models.ws знает пивоты групп лошади и части тела наизусть — сверка с генератором."""
    WS = os.path.join(bm.PROJECT, "spike", "mod", "modMCW", "content", "scripts", "local", "mcw_models.ws")

    def test_horse_pivots(self):
        import re
        with open(bm.MODELS_JSON, encoding="utf-8") as f:
            _, groups = bm.horse_parts(json.load(f))
        with open(self.WS, encoding="utf-8") as f:
            src = f.read()
        got = {int(m.group(1)): tuple(float(v) for v in m.groups()[1:])
               for m in re.finditer(r"case (\d): return Vector\( ([-\d.]+), ([-\d.]+), ([-\d.]+) \);", src)}
        order = ["body", "tail", "head_parts", "left_hind_leg", "right_hind_leg", "left_front_leg", "right_front_leg"]
        for i, g in enumerate(order):
            for a, b in zip(got[i], groups[g][:3]):
                self.assertAlmostEqual(a, b, places=4, msg=g)

    def test_parts_listed(self):
        with open(self.WS, encoding="utf-8") as f:
            src = f.read()
        for p in bm.steve_parts():
            self.assertIn(r"mcw\models\steve" + "\\" + p["name"] + ".w2ent", src)
        for name in bm.HORSE_GROUP:
            self.assertIn(r"mcw\models\horse" + "\\" + name + ".w2ent", src)


@unittest.skipUnless(HAVE_R4, "нет r4data REDkit")
class MeshTest(unittest.TestCase):
    def test_box_mesh(self):
        ext = (-0.1, 0.1, -0.05, 0.05, -0.3, 0.0)
        data = bm.make_box_mesh(r"mcw\models\tex\skin.w2mi", ext, (40, 16), (4, 12, 4), (0, 6))
        doc = cr2w.CR2WDoc(data)
        mesh = doc.exports[0]
        _, h = doc.get_prop(mesh["data"], "collisionMesh")
        self.assertEqual(struct.unpack("<i", h)[0], 0)
        raw = bb._buffer(doc, mesh["data"], "rawVertices")["data"]
        xs, ys, zs, us, vs = set(), set(), set(), [], []
        for k in range(24):
            x, y, z = struct.unpack_from("<3f", raw, k * bb.VSTRIDE + bb.V_POS)
            u, v = struct.unpack_from("<2f", raw, k * bb.VSTRIDE + bb.V_UV)
            xs.add(round(x, 4))
            ys.add(round(y, 4))
            zs.add(round(z, 4))
            us.append(u * 64)
            vs.append(v * 64)
        self.assertEqual(xs, {-0.1, 0.1})
        self.assertEqual(ys, {-0.05, 0.05})
        self.assertEqual(zs, {-0.3, 0.0})
        self.assertTrue(all(40 <= u <= 56 for u in us))                  # прямоугольник руки (40..56)
        self.assertTrue(all(16 <= v <= 26 for v in vs))                  # верх + 6 рядов (16..20+6)

    def test_part_entity(self):
        data = bm.make_part_entity(r"mcw\models\steve\head.w2mesh", "steve_head", (-0.2, 0.2, -0.2, 0.2, -0.1, 0.3))
        doc = cr2w.CR2WDoc(data)
        comp = doc.exports[2]
        _, v = doc.get_prop(comp["data"], "pathLibCollisionType")
        self.assertEqual(doc.names[struct.unpack("<H", v)[0]], "PLC_Disabled")
        self.assertIn(r"mcw\models\steve\head.w2mesh", [i[0] for i in doc.imports])


if __name__ == "__main__":
    unittest.main()
