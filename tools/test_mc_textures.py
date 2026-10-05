"""Тесты mc_textures.py и cube_mesh.py:  python -m unittest test_mc_textures -v   (из папки tools).

Синтетический мини-jar собирается во временной папке: blockstates, модели с parent и цепочкой #ссылок, текстуры,
mcmeta, colormap, биом, items. Блок RealJar запускается, только если есть настоящий 1.21.11.jar.
"""
import io
import json
import os
import re
import shutil
import tempfile
import unittest
import zipfile

from PIL import Image

import cube_mesh as CM
import mc_textures as T

RED, BLUE, WHITE, GREEN = (255, 0, 0, 255), (0, 0, 255, 255), (255, 255, 255, 255), (0, 255, 0, 255)
CLEAR = (0, 0, 0, 0)


def png(img):
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def solid(rgba, size=(16, 16)):
    return Image.new("RGBA", size, rgba)


def marked(base=WHITE, mark=RED):
    """Белая 16x16 с красным левым верхним пикселем: по нему видно поворот и отражение."""
    img = solid(base)
    img.putpixel((0, 0), mark)
    return img


def top_rows(top, rest, n=4):
    img = solid(rest)
    for y in range(n):
        for x in range(16):
            img.putpixel((x, y), top)
    return img


def strip(colors):
    img = Image.new("RGBA", (16, 16 * len(colors)))
    for i, c in enumerate(colors):
        img.paste(solid(c), (0, 16 * i))
    return img


def checker_alpha(rgb=(255, 255, 255)):
    img = Image.new("RGBA", (16, 16))
    for y in range(16):
        for x in range(16):
            img.putpixel((x, y), (*rgb, 255) if (x + y) % 2 else (0, 0, 0, 0))
    return img


def cube_el(faces, frm=(0, 0, 0), to=(16, 16, 16)):
    return {"from": list(frm), "to": list(to), "faces": faces}


def all_faces(tex, **extra):
    return {d: dict(texture=tex, **extra) for d in T.DIRS}


TEXTURES = {
    "t_all": solid((100, 50, 25, 255)),
    "t_end": solid((10, 200, 10, 255)),
    "t_side": top_rows(RED, BLUE),
    "g_top": solid((200, 200, 200, 255)),
    "g_side": solid((120, 80, 40, 255)),
    "g_overlay": top_rows((255, 255, 255, 255), CLEAR),
    "leaves": checker_alpha(),
    "anim": strip([RED, GREEN, BLUE]),
    "anim_plain": strip([GREEN, RED]),
    "glassy": solid((200, 220, 255, 128)),
    "arrow": marked(),
    "water_still": strip([(50, 50, 200, 180), (60, 60, 210, 180)]),
}
MCMETA = {"anim": {"animation": {"frametime": 2, "frames": [2, 0, 1]}},
          "anim_plain": {"animation": {}},
          "water_still": {"animation": {"frametime": 2}}}

MODELS = {
    "block/block": {"display": {}},
    "block/cube": {"parent": "block/block", "elements": [cube_el({d: {"texture": "#" + d} for d in T.DIRS})]},
    "block/cube_all": {"parent": "block/cube", "textures": {d: "#all" for d in T.DIRS}},
    "block/cube_column": {"parent": "block/cube", "textures": {"down": "#end", "up": "#end", "north": "#side",
                                                               "south": "#side", "west": "#side", "east": "#side"}},
    "block/t_stone": {"parent": "minecraft:block/cube_all", "textures": {"all": "minecraft:block/t_all"}},
    "block/t_stone2": {"parent": "block/cube_all", "textures": {"all": "block/t_all"}},
    "block/t_log": {"parent": "block/cube_column", "textures": {"end": "block/t_end", "side": "block/t_side"}},
    # три уровня parent и цепочка ссылок #a -> #b -> текстура
    "block/t_chain_base": {"parent": "block/cube", "textures": {"down": "#a", "up": "#a", "north": "#a", "south": "#a",
                                                                "west": "#a", "east": "#b"}},
    "block/t_chain_mid": {"parent": "block/t_chain_base", "textures": {"a": "#b"}},
    "block/t_chain": {"parent": "block/t_chain_mid", "textures": {"b": "block/t_end"}},
    "block/t_grass": {"parent": "block/block", "textures": {"bottom": "block/g_side", "top": "block/g_top",
                                                            "side": "block/g_side", "overlay": "block/g_overlay"},
                      "elements": [cube_el({"down": {"texture": "#bottom"}, "up": {"texture": "#top", "tintindex": 0},
                                            **{d: {"texture": "#side"} for d in ("north", "south", "west", "east")}}),
                                   cube_el({d: {"texture": "#overlay", "tintindex": 0}
                                            for d in ("north", "south", "west", "east")})]},
    "block/leaves": {"parent": "block/block", "elements": [cube_el(all_faces("#all", tintindex=0))]},
    "block/t_leaves": {"parent": "block/leaves", "textures": {"all": "block/leaves"}},
    "block/t_anim": {"parent": "block/cube_all", "textures": {"all": "block/anim"}},
    "block/t_anim_plain": {"parent": "block/cube_all", "textures": {"all": "block/anim_plain"}},
    "block/t_glassy": {"parent": "block/cube_all", "textures": {"all": "block/glassy"}},
    "block/t_arrow": {"parent": "block/cube", "textures": {"north": "block/arrow", "south": "block/t_all",
                                                           "east": "block/t_all", "west": "block/t_all",
                                                           "up": "block/t_all", "down": "block/t_all"}},
    "block/t_uv": {"parent": "block/block", "elements": [cube_el({
        "north": {"texture": "#x", "rotation": 90}, "south": {"texture": "#x", "uv": [16, 0, 0, 16]},
        "east": {"texture": "#x", "uv": [0, 0, 8, 8]}, "west": {"texture": "#x"}, "up": {"texture": "#x"},
        "down": {"texture": "#x"}})], "textures": {"x": "block/arrow"}},
    "block/t_slab": {"parent": "block/block", "elements": [cube_el(all_faces("#all"), to=(16, 8, 16))],
                     "textures": {"all": "block/t_all"}},
    "block/t_tilted": {"parent": "block/block", "textures": {"all": "block/t_all"},
                       "elements": [dict(cube_el(all_faces("#all")), rotation={"angle": 22.5, "axis": "y",
                                                                             "origin": [8, 8, 8]})]},
    "block/t_core": {"parent": "block/block", "textures": {"all": "block/glassy", "core": "block/t_all"},
                     "elements": [cube_el(all_faces("#core"), (3, 3, 3), (13, 13, 13)), cube_el(all_faces("#all"))]},
    "block/t_entity": {"textures": {"particle": "block/t_all"}},
    "block/water": {"textures": {"particle": "block/water_still"}},
    "block/t_badref": {"parent": "block/cube_all", "textures": {}},
}

BLOCKSTATES = {
    "t_stone": {"variants": {"": [{"model": "minecraft:block/t_stone"}, {"model": "minecraft:block/t_stone", "y": 90}]}},
    "t_stone2": {"variants": {"": {"model": "block/t_stone2"}}},
    "t_log": {"variants": {"axis=x": {"model": "block/t_log", "x": 90, "y": 90}, "axis=y": {"model": "block/t_log"},
                           "axis=z": {"model": "block/t_log", "x": 90}}},
    "t_chain": {"variants": {"": {"model": "block/t_chain"}}},
    "grass_block": {"variants": {"snowy=false": {"model": "block/t_grass"}, "snowy=true": {"model": "block/t_stone"}}},
    "oak_leaves": {"variants": {"": {"model": "block/t_leaves"}}},
    "spruce_leaves": {"variants": {"": {"model": "block/t_leaves"}}},
    "cherry_leaves": {"variants": {"": {"model": "block/t_leaves"}}},
    "t_itemtint": {"variants": {"": {"model": "block/t_leaves"}}},
    "t_notint": {"variants": {"": {"model": "block/t_leaves"}}},
    "t_anim": {"variants": {"": {"model": "block/t_anim"}}},
    "t_anim_plain": {"variants": {"": {"model": "block/t_anim_plain"}}},
    "t_glassy": {"variants": {"": {"model": "block/t_glassy"}}},
    "t_furnace": {"variants": {"facing=east,lit=false": {"model": "block/t_arrow", "y": 90},
                               "facing=east,lit=true": {"model": "block/t_arrow", "y": 90},
                               "facing=north,lit=false": {"model": "block/t_arrow"},
                               "facing=north,lit=true": {"model": "block/t_arrow"}}},
    "t_rot_y": {"variants": {"": {"model": "block/t_arrow", "y": 90}}},
    "t_rot_x": {"variants": {"": {"model": "block/t_arrow", "x": 90}}},
    "t_rot_lock": {"variants": {"": {"model": "block/t_arrow", "x": 90, "uvlock": True}}},
    "observer": {"variants": {"facing=north,powered=false": {"model": "block/t_arrow"},
                              "facing=south,powered=false": {"model": "block/t_arrow", "y": 180}}},
    "t_uv": {"variants": {"": {"model": "block/t_uv"}}},
    "t_slab": {"variants": {"type=bottom": {"model": "block/t_slab"}}},
    "t_tilted": {"variants": {"": {"model": "block/t_tilted"}}},
    "t_core": {"variants": {"": {"model": "block/t_core"}}},
    "t_entity": {"variants": {"": {"model": "block/t_entity"}}},
    "t_fence": {"multipart": [{"apply": {"model": "block/t_stone"}}]},
    "t_missing_model": {"variants": {"": {"model": "block/nope"}}},
    "t_badref": {"variants": {"": {"model": "block/t_badref"}}},
    "water": {"variants": {"": {"model": "block/water"}}},
}


def build_jar(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("version.json", json.dumps({"id": "test"}))
        for n, img in TEXTURES.items():
            z.writestr(f"assets/minecraft/textures/block/{n}.png", png(img))
        for n, meta in MCMETA.items():
            z.writestr(f"assets/minecraft/textures/block/{n}.png.mcmeta", json.dumps(meta))
        for n, m in MODELS.items():
            z.writestr(f"assets/minecraft/models/{n}.json", json.dumps(m))
        for n, bs in BLOCKSTATES.items():
            z.writestr(f"assets/minecraft/blockstates/{n}.json", json.dumps(bs))
        for kind, rgb in (("grass", (145, 189, 89)), ("foliage", (119, 171, 47)), ("dry_foliage", (1, 2, 3))):
            cm = Image.new("RGB", (256, 256), (0, 0, 0))
            cm.putpixel((50, 173), rgb)
            z.writestr(f"assets/minecraft/textures/colormap/{kind}.png", png(cm))
        z.writestr("data/minecraft/worldgen/biome/plains.json",
                   json.dumps({"temperature": 0.8, "downfall": 0.4, "effects": {"water_color": "#3f76e4"}}))
        z.writestr("data/minecraft/worldgen/biome/swampish.json",
                   json.dumps({"temperature": 0.8, "downfall": 0.9,
                               "effects": {"foliage_color": "#6a7039", "grass_color_modifier": "swamp"}}))
        z.writestr("assets/minecraft/items/t_itemtint.json",
                   json.dumps({"model": {"type": "minecraft:model", "model": "minecraft:block/t_leaves",
                                         "tints": [{"type": "minecraft:constant", "value": -8345771}]}}))


def load(path):
    with Image.open(path) as im:
        im.load()
        return im


def mul(c, t):
    return tuple(round(a * b / 255) for a, b in zip(c, t))


def close(a, b, tol=1):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


class Synthetic(unittest.TestCase):
    PX = 32  # клетка атласа в тестах (2x от 16)

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="mcw_tex_test_")
        cls.jar_path = os.path.join(cls.tmp, "mini.jar")
        build_jar(cls.jar_path)
        cls.jar = T.Jar(cls.jar_path)
        cls.biome = T.load_biome(cls.jar, "plains")
        cls.out = os.path.join(cls.tmp, "out")
        cls.m = T.convert(cls.jar, list(BLOCKSTATES), cls.out, face_px=cls.PX, biome=cls.biome, normal=True,
                          masks=True)
        cls.B, cls.S = cls.m["blocks"], cls.m["skipped"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def atlas(self, name):
        return load(os.path.join(self.out, self.B[name]["file"])).convert("RGBA")

    def face(self, name, f, px=(0, 0)):
        """Пиксель грани f в атласе: px - координаты в родных 16x16."""
        c, r = CM.CELLS[f]
        s = self.PX // 16
        return self.atlas(name).getpixel((c * self.PX + px[0] * s, r * self.PX + px[1] * s))

    # ---- биом, colormap, тинт
    def test_colormap_point_plains(self):
        self.assertEqual(T.colormap_xy(0.8, 0.4), (50, 173))
        self.assertEqual(T.colormap_xy(2.0, -1.0), (0, 255))  # clamp
        self.assertEqual(self.biome["water_color"], "#3F76E4")

    def test_grass_tint_and_overlay(self):
        e = self.B["grass_block"]
        self.assertEqual(e["variant"], "snowy=false")
        self.assertEqual(e["tint"]["rgb"], "#91BD59")
        self.assertEqual(e["tint"]["faces"], ["up", "north", "south", "west", "east"])
        tint = (145, 189, 89)
        self.assertTrue(close(self.face("grass_block", "up")[:3], mul((200, 200, 200), tint)))
        for f in ("north", "west", "south", "east"):
            self.assertTrue(close(self.face("grass_block", f, (5, 1))[:3], tint), f)          # оверлей, тонирован
            self.assertEqual(self.face("grass_block", f, (5, 10))[:3], (120, 80, 40), f)      # основа без тинта
        self.assertEqual(self.face("grass_block", "down")[:3], (120, 80, 40))
        self.assertEqual(e["alpha"], "opaque")
        self.assertIn("g_side + minecraft:block/g_overlay", e["faces"]["north"])

    def test_foliage_and_constant_tints(self):
        self.assertEqual(self.B["oak_leaves"]["tint"]["rgb"], "#77AB2F")
        self.assertEqual(self.B["spruce_leaves"]["tint"]["rgb"], "#619961")
        self.assertIsNone(self.B["cherry_leaves"]["tint"])
        self.assertIn("texture", self.B["cherry_leaves"]["tint_note"])
        it = self.B["t_itemtint"]
        self.assertEqual(it["tint"]["rgb"], "#80A755")
        self.assertTrue(any("items/" in w for w in it["warnings"]))
        self.assertIsNone(self.B["t_notint"]["tint"])
        self.assertTrue(any("untinted" in w for w in self.B["t_notint"]["warnings"]))

    def test_biome_overrides(self):
        b = T.load_biome(self.jar, "swampish")
        t = T.Tints(self.jar, b)
        self.assertEqual(t.for_block("oak_leaves")[0], (0x6A, 0x70, 0x39))
        self.assertIn("not applied", t.for_block("grass_block")[2])
        b2 = T.load_biome(self.jar, "plains", climate=(0.5, 1.0))
        self.assertEqual(b2["colormap_xy"], [127, 127])

    # ---- альфа
    def test_cutout_leaves(self):
        e = self.B["oak_leaves"]
        self.assertEqual(e["alpha"], "cutout")
        a = self.atlas("oak_leaves")
        self.assertEqual(load(os.path.join(self.out, e["file"])).mode, "RGBA")
        self.assertEqual(a.getpixel((0, 0))[3], 0)
        self.assertNotEqual(a.getpixel((0, 0))[:3], (0, 0, 0))  # bleed: под прозрачным цвет соседей, не чёрный
        self.assertTrue(os.path.isfile(os.path.join(self.out, e["mask_file"])))

    def test_translucent_and_opaque_modes(self):
        self.assertEqual(self.B["t_glassy"]["alpha"], "translucent")
        self.assertEqual(self.B["t_stone"]["alpha"], "opaque")
        self.assertEqual(load(os.path.join(self.out, self.B["t_stone"]["file"])).mode, "RGB")
        self.assertIsNone(self.B["t_stone"]["mask_file"])

    # ---- анимации
    def test_animation_first_frame(self):
        self.assertEqual(self.face("t_anim", "up"), BLUE)          # frames[0] = 2
        self.assertEqual(self.face("t_anim_plain", "up"), GREEN)   # кадр 0
        self.assertEqual(self.B["t_anim"]["animated"], ["minecraft:block/anim"])
        img = T.first_frame(Image.new("RGBA", (32, 64)), {"width": 16, "height": 16, "frames": [{"index": 3}]})
        self.assertEqual(img.size, (16, 16))

    # ---- модель
    def test_parent_chain_and_refs(self):
        e = self.B["t_chain"]
        self.assertEqual(e["parents"], ["minecraft:block/t_chain_mid", "minecraft:block/t_chain_base",
                                        "minecraft:block/cube", "minecraft:block/block"])
        self.assertTrue(all(v == "minecraft:block/t_end" for v in e["faces"].values()))
        self.assertTrue(e["uniform"])

    def test_dedup(self):
        s, s2 = self.B["t_stone"], self.B["t_stone2"]
        self.assertTrue(s["uniform"])
        self.assertEqual(s["same_faces"], [list(CM.FACES)])
        self.assertEqual(s["file"], "atlas/t_stone.png")      # короче имя - владелец файла
        self.assertEqual(s2["same_as"], "t_stone")
        self.assertEqual(s2["file"], s["file"])
        self.assertFalse(os.path.exists(os.path.join(self.out, "atlas", "t_stone2.png")))
        self.assertEqual(load(os.path.join(self.out, s["face_file"])).size, (self.PX, self.PX))
        self.assertEqual(s["random_models"], 2)

    def test_column_groups(self):
        e = self.B["t_log"]
        self.assertEqual(e["variant"], "axis=y")
        self.assertEqual(sorted(map(sorted, e["same_faces"])), [["down", "up"], ["east", "north", "south", "west"]])
        self.assertIsNone(e["face_file"])
        self.assertEqual(self.face("t_log", "north", (3, 0)), RED)    # верх боковой картинки сверху
        self.assertEqual(self.face("t_log", "north", (3, 8)), BLUE)

    def test_skips(self):
        self.assertTrue(self.S["t_slab"].startswith("not a full cube"))
        self.assertTrue(self.S["t_tilted"].startswith("element rotation"))
        self.assertTrue(self.S["t_entity"].startswith("no elements"))
        self.assertTrue(self.S["t_fence"].startswith("multipart"))
        self.assertTrue(self.S["t_missing_model"].startswith("missing model"))
        self.assertTrue(self.S["t_badref"].startswith("missing texture"))
        self.assertEqual(self.m["summary"]["skipped"], len(self.S))

    def test_inner_elements_dropped(self):
        e = self.B["t_core"]
        self.assertEqual(e["alpha"], "translucent")
        self.assertTrue(any("inner" in w for w in e["warnings"]))

    def test_fluid(self):
        e = self.B["water"]
        self.assertTrue(e["fluid"])
        self.assertEqual(e["tint"]["rgb"], "#3F76E4")
        self.assertEqual(e["alpha"], "translucent")

    def test_unknown_cube(self):
        e = self.B[T.UNKNOWN]
        self.assertTrue(e["generated"] and e["uniform"])
        self.assertEqual(self.face(T.UNKNOWN, "up", (5, 5))[:3], (128, 128, 128))

    # ---- uv, поворот грани, вариант
    def test_face_uv_rotation_flip_crop(self):
        s = self.PX // 16
        self.assertEqual(self.face("t_uv", "west", (0, 0)), RED)
        self.assertEqual(self.face("t_uv", "north", (15, 0)), RED)  # rotation 90: левый верх -> правый верх
        self.assertEqual(self.face("t_uv", "south", (15, 0)), RED)  # uv [16,0,0,16]: отражение по горизонтали
        c, r = CM.CELLS["east"]
        a = self.atlas("t_uv")
        self.assertEqual(a.getpixel((c * self.PX, r * self.PX)), RED)  # uv [0,0,8,8]: пиксель 2x2 родных
        self.assertEqual(a.getpixel((c * self.PX + 2 * s, r * self.PX)), WHITE)
        self.assertEqual(a.getpixel((c * self.PX + s, r * self.PX)), RED)

    def test_default_variant(self):
        self.assertEqual(self.B["t_furnace"]["variant"], "facing=north,lit=false")
        self.assertEqual(self.B["observer"]["variant"], "facing=south,powered=false")
        self.assertEqual(T.pick_variant({"a=1": {}, "a=0": {}}), "a=0")

    def test_variant_rotation(self):
        # y=90: north уходит на east, картинка не крутится (боковые грани остаются стоячими)
        e = self.B["t_rot_y"]
        self.assertEqual(e["faces"]["east"], "minecraft:block/arrow")
        self.assertEqual(self.face("t_rot_y", "east", (0, 0)), RED)
        # observer facing=south: y=180, лицо на south
        self.assertEqual(self.B["observer"]["faces"]["south"], "minecraft:block/arrow")
        # x=90: north уходит вниз; верх картинки смотрит на север, а у down верх - юг, значит 180
        e = self.B["t_rot_x"]
        self.assertEqual(e["faces"]["down"], "minecraft:block/arrow")
        self.assertEqual(self.face("t_rot_x", "down", (15, 15)), RED)
        self.assertEqual(self.face("t_rot_lock", "down", (0, 0)), RED)  # uvlock - без поворота картинки

    def test_rotation_map_is_permutation(self):
        for x in (0, 90, 180, 270):
            for y in (0, 90, 180, 270):
                m = T.rotation_map(x, y)
                self.assertEqual(sorted(m), sorted(T.DIRS))
                self.assertEqual(sorted(s for s, _ in m.values()), sorted(T.DIRS))
        m = T.rotation_map(90, 0)   # бревно axis=z: торцы на north/south
        self.assertEqual({m["north"][0], m["south"][0]}, {"up", "down"})
        self.assertEqual(m["east"], ("east", 90))  # кора на боку легла поперёк

    # ---- атлас и манифест
    def test_atlas_layout_and_spare(self):
        a = self.atlas("t_log")
        self.assertEqual(a.size, (4 * self.PX, 2 * self.PX))
        for (c, r), (sc, sr) in CM.SPARE.items():
            src = a.crop((sc * self.PX, sr * self.PX, (sc + 1) * self.PX, (sr + 1) * self.PX))
            dst = a.crop((c * self.PX, r * self.PX, (c + 1) * self.PX, (r + 1) * self.PX))
            self.assertEqual(dst.tobytes(), src.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes())
        lay = self.m["layout"]
        self.assertEqual(lay["size"], [4 * self.PX, 2 * self.PX])
        self.assertEqual(lay["cells"]["up"], [0, 1])
        n = load(os.path.join(self.out, self.m["normal_flat"]))
        self.assertEqual((n.size, n.getpixel((3, 3))), ((4 * self.PX, 2 * self.PX), (128, 128, 255)))
        with open(os.path.join(self.out, "manifest.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["summary"]["converted"], self.m["summary"]["converted"])

    def test_gutter(self):
        faces = {d: marked() for d in T.DIRS}
        a = T.build_atlas(faces, cell=40, gutter=4)
        self.assertEqual(a.size, (160, 80))
        self.assertEqual(a.getpixel((0, 0)), RED)            # угол рамки = угловой пиксель грани
        self.assertEqual(a.getpixel((4, 4)), RED)            # грань 32 px со сдвигом 4
        self.assertEqual(a.getpixel((4 + 2, 4)), WHITE)

    def test_preview_written(self):
        p = T.preview(self.B, self.out, ["grass_block", "oak_leaves", T.UNKNOWN], self.PX, 0,
                      os.path.join(self.out, "preview.png"))
        self.assertTrue(os.path.getsize(p) > 0)


class Render(unittest.TestCase):
    def test_view_shows_expected_faces(self):
        """Вид с юго-востока сверху: up сверху, south слева, east справа - проверка меша + UV, а не только атласа."""
        cell = 16
        colors = {"north": (255, 0, 0), "west": (0, 255, 0), "south": (0, 0, 255), "east": (255, 255, 0),
                  "up": (255, 0, 255), "down": (0, 255, 255)}
        a = Image.new("RGBA", (4 * cell, 2 * cell))
        for f, (c, r) in CM.CELLS.items():
            a.paste(Image.new("RGBA", (cell, cell), (*colors[f], 255)), (c * cell, r * cell))
        img = T.render_cube(a, (1, -1, 0.8), 200, cell)
        k = T.SHADE
        self.assertTrue(close(img.getpixel((100, 40))[:3], [int(v * k["up"]) for v in colors["up"]]))
        self.assertTrue(close(img.getpixel((60, 130))[:3], [int(v * k["south"]) for v in colors["south"]]))
        self.assertTrue(close(img.getpixel((140, 130))[:3], [int(v * k["east"]) for v in colors["east"]]))
        img = T.render_cube(a, (-1, 1, 0.8), 200, cell)  # с северо-запада (лицом на юго-восток): north слева
        self.assertTrue(close(img.getpixel((60, 130))[:3], [int(v * k["north"]) for v in colors["north"]]))
        self.assertTrue(close(img.getpixel((140, 130))[:3], [int(v * k["west"]) for v in colors["west"]]))


class Mesh(unittest.TestCase):
    def test_geometry(self):
        q = {x["face"]: x for x in CM.face_quads("atlas", 256, 0, 0)}
        self.assertEqual(set(q), set(CM.FACES))
        for f, x in q.items():
            pts = x["corners"]
            self.assertTrue(all(abs(c) == 0.5 for p in pts for c in p), f)
            tl, bl, br = pts[0], pts[1], pts[2]
            n = T._cross(T._sub(bl, tl), T._sub(br, tl))   # обход TL, BL, BR против часовой снаружи
            self.assertEqual(tuple(c / max(abs(v) for v in n) for c in n), x["normal"], f)
            for p in pts:
                self.assertAlmostEqual(T._dot(p, x["normal"]), 0.5)
        self.assertEqual(q["north"]["normal"], (0, 1, 0))   # север MC = +Y Ведьмака
        self.assertEqual(q["up"]["normal"], (0, 0, 1))
        self.assertTrue(all(p[1] == 0.5 for p in (q["up"]["corners"][0], q["up"]["corners"][3])))      # верх up - север
        self.assertTrue(all(p[1] == -0.5 for p in (q["down"]["corners"][0], q["down"]["corners"][3])))  # верх down - юг
        for f in ("north", "west", "south", "east"):
            self.assertEqual(q[f]["corners"][0][2], 0.5)    # верх боковой картинки - вверху

    def test_uv_cells_and_inset(self):
        q = {x["face"]: x for x in CM.face_quads("atlas", 256, 0, 0.5)}
        e = 0.5 / 256
        self.assertEqual(q["north"]["uv"][0], (e / 4, e / 2))
        self.assertEqual(q["up"]["uv"][2], ((1 - e) / 4, (2 - e) / 2))
        u = CM.face_quads("uniform", 256, 8, 0)
        self.assertTrue(all(x["uv"][0] == (0, 0) and x["uv"][2] == (1, 1) for x in u))
        g = {x["face"]: x for x in CM.face_quads("atlas", 256, 8, 0)}
        self.assertEqual(g["east"]["uv"][0], ((3 + 8 / 256) / 4, (8 / 256) / 2))
        with self.assertRaises(ValueError):
            CM.face_quads("atlas", 16, 8, 1)

    def test_obj(self):
        text = CM.obj_text(CM.face_quads(), "cube", "cube.mtl")
        v = [tuple(map(float, ln.split()[1:])) for ln in text.splitlines() if ln.startswith("v ")]
        vt = [tuple(map(float, ln.split()[1:])) for ln in text.splitlines() if ln.startswith("vt ")]
        vn = [ln for ln in text.splitlines() if ln.startswith("vn ")]
        f = [ln.split()[1:] for ln in text.splitlines() if ln.startswith("f ")]
        self.assertEqual((len(v), len(vt), len(vn), len(f)), (24, 24, 6, 12))
        self.assertEqual({c for p in v for c in p}, {-0.5, 0.5})
        self.assertTrue(all(0 <= c <= 1 for p in vt for c in p))
        for tri in f:   # нормаль треугольника = vn грани
            idx = [int(x.split("/")[0]) - 1 for x in tri]
            n = T._cross(T._sub(v[idx[1]], v[idx[0]]), T._sub(v[idx[2]], v[idx[0]]))
            vnn = tuple(map(float, vn[int(tri[0].split("/")[2]) - 1].split()[1:]))
            self.assertGreater(T._dot(n, vnn), 0)
        self.assertIn("map_Kd", CM.mtl_text("x.png"))

    def test_fbx(self):
        text = CM.fbx_text(CM.face_quads(), "cube")
        self.assertEqual(text.count("{"), text.count("}"))
        counts = dict(re.findall(r"(\w+): \*(\d+)", text))
        self.assertEqual({k: int(v) for k, v in counts.items()},
                         {"Vertices": 72, "PolygonVertexIndex": 36, "Normals": 108, "UV": 48, "UVIndex": 36,
                          "Materials": 1})
        pvi = [int(x) for x in re.search(r"PolygonVertexIndex: \*36 \{\s*a: ([^\n]+)", text).group(1).split(",")]
        self.assertEqual(sum(1 for i in pvi if i < 0), 12)
        self.assertIn('"UnitScaleFactor", "double", "Number", "",100', text)
        cm = CM.fbx_text(CM.face_quads(), "cube", units="cm")
        self.assertIn("-50", cm)
        self.assertIn('"UnitScaleFactor", "double", "Number", "",1\n', cm)


class OutDir(unittest.TestCase):
    def test_refuse(self):
        self.assertIsNotNone(CM.out_dir_problem(os.path.join(CM.PROJECT_ROOT, "tools", "out")))
        self.assertIsNotNone(CM.out_dir_problem(CM.PROJECT_ROOT))
        self.assertIsNotNone(CM.out_dir_problem(os.path.join(tempfile.gettempdir(), ".minecraft", "x")))
        self.assertIsNone(CM.out_dir_problem(os.path.join(tempfile.gettempdir(), "mcw_ok")))
        with self.assertRaises(SystemExit):
            T.main(["--out", os.path.join(CM.PROJECT_ROOT, "tex"), "--jar", "nope.jar"])
        with self.assertRaises(SystemExit):
            CM.main(["--out", os.path.join(CM.PROJECT_ROOT, "mesh")])
        self.assertFalse(os.path.exists(os.path.join(CM.PROJECT_ROOT, "tex")))
        self.assertFalse(os.path.exists(os.path.join(CM.PROJECT_ROOT, "mesh")))

    def test_meshes_written(self):
        d = tempfile.mkdtemp(prefix="mcw_mesh_test_")
        try:
            files = CM.write_meshes(d)
            self.assertEqual(sorted(os.path.basename(p) for p in files),
                             sorted(f"mcw_cube_{lay}.{ext}" for lay in ("atlas", "uniform")
                                    for ext in ("obj", "mtl", "fbx")))
        finally:
            shutil.rmtree(d, ignore_errors=True)


@unittest.skipUnless(os.path.isfile(T.JAR_DEFAULT), "no Minecraft 1.21.11 jar")
class RealJar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jar = T.Jar(T.JAR_DEFAULT)
        cls.tmp = tempfile.mkdtemp(prefix="mcw_tex_real_")
        cls.m = T.convert(cls.jar, list(T.MVP), cls.tmp, face_px=16, biome=T.load_biome(cls.jar, "plains"))
        cls.B = cls.m["blocks"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_mvp_all_converted(self):
        self.assertEqual(self.m["summary"]["skipped"], 0, self.m["skipped"])
        self.assertEqual(self.m["mc_version"], "1.21.11")

    def test_plains_colors(self):
        self.assertEqual(self.B["grass_block"]["tint"]["rgb"], "#91BD59")   # minecraft.wiki/w/Biome: plains
        self.assertEqual(self.B["oak_leaves"]["tint"]["rgb"], "#77AB2F")

    def test_mvp_faces(self):
        ct = self.B["crafting_table"]["faces"]
        self.assertEqual((ct["up"], ct["down"]), ("minecraft:block/crafting_table_top", "minecraft:block/oak_planks"))
        self.assertEqual({ct["north"], ct["west"]}, {"minecraft:block/crafting_table_front"})
        self.assertEqual({ct["south"], ct["east"]}, {"minecraft:block/crafting_table_side"})
        self.assertEqual(self.B["oak_log"]["faces"]["up"], "minecraft:block/oak_log_top")
        self.assertEqual(self.B["oak_leaves"]["alpha"], "cutout")
        self.assertEqual(self.B["glass"]["alpha"], "cutout")
        self.assertTrue(self.B["stone"]["uniform"])

    def test_tint_constants_match_item_tints(self):
        """Постоянные цвета TINT = числа в assets/minecraft/items/<блок>.json того же jar."""
        for block in ("spruce_leaves", "birch_leaves", "mangrove_leaves"):
            item = self.jar.json(f"assets/minecraft/items/{block}.json")
            self.assertEqual(T.hex_rgb(item["model"]["tints"][0]["value"]), T.hex_rgb(T.TINT[block]), block)
        for block in T.UNTINTED:
            item = self.jar.json(f"assets/minecraft/items/{block}.json")
            self.assertFalse(item["model"].get("tints"), block)


if __name__ == "__main__":
    unittest.main()
