"""Текстуры блоков Minecraft 1.21.11 -> атласы граней для кубов в Witcher 3. Pillow, остальное stdlib.

Только читает клиентский jar (%APPDATA%\\.minecraft\\versions\\1.21.11\\1.21.11.jar). Для каждого блока:
blockstate -> вариант по умолчанию -> модель (parent, ссылки #name) -> текстуры 6 граней -> первый кадр анимации,
uv и rotation грани, тинт по tintindex, наложение следующих полных элементов (оверлей травы) -> поворот варианта x/y ->
атлас 4x2 (раскладка и меш - cube_mesh.py), nearest-апскейл до --face-px. Плюс manifest.json: блок -> файл, альфа,
тинт, одинаковые грани; не-кубы пропущены с причиной.

Источники (декомпилированного кода Mojang здесь нет):
- климат и цвет воды: data/minecraft/worldgen/biome/<биом>.json в том же jar (plains: 0.8 / 0.4, water #3f76e4)
- точка в colormap: AdjTemp = clamp(T, 0, 1), AdjDownfall = clamp(D, 0, 1) * AdjTemp, x = (1 - AdjTemp) * 255,
  y = (1 - AdjDownfall) * 255 (minecraft.fandom.com/wiki/Color, minecraft.wiki/w/Biome "Plants"); T и D берём как
  float32. Для plains это (50, 173) -> grass #91BD59, foliage #77AB2F, как в таблице minecraft.wiki/w/Biome
- листва с постоянным цветом: minecraft.wiki/w/Leaves (spruce #619961, birch #80A755, mangrove #92C648; cherry, azalea,
  flowering azalea и pale oak не тонируются). Те же числа в jar: assets/minecraft/items/<блок>.json "tints"
  (spruce -10380959 = 0xFF619961, birch -8345771, mangrove -7158200); тест сверяет их с таблицей TINT.
- observer по умолчанию смотрит на юг (minecraft.wiki/w/Observer, Block states)

Запуск:
  python mc_textures.py [--jar JAR] [--out DIR] [--blocks a,b,c | --all] [--face-px 256] [--gutter-px 0]
                        [--biome plains] [--climate T D] [--normal] [--masks] [--no-preview]
По умолчанию - MVP-блоки и серый куб для неизвестных, выход в %LOCALAPPDATA%\\mcw\\tex. Выход - производные от
ассетов Mojang: внутрь проекта, папки игры и .minecraft писать отказывается (cube_mesh.refuse_out_dir).
"""
import argparse
import glob
import hashlib
import io
import json
import os
import struct
import sys
import time
import zipfile

from PIL import Image, ImageChops, ImageDraw, ImageFont

import cube_mesh as CM

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "spike"))
import w3paths  # noqa: E402  (MCW_MC_JAR -> лаунчер -> кэш Loom)

JAR_DEFAULT = w3paths.mc_jar() or os.path.join(os.environ.get("APPDATA", ""), ".minecraft", "versions", "1.21.11",
                                               "1.21.11.jar")
MVP = ("grass_block", "dirt", "stone", "cobblestone", "oak_log", "oak_planks", "sand", "crafting_table",
       "oak_leaves", "glass")
UNKNOWN = "_unknown"
DIRS = ("down", "up", "north", "south", "west", "east")

# Цвет для граней с tintindex. "grass"/"foliage"/"dry_foliage" - colormap в точке биома (или цвет-оверрайд биома),
# "water" - water_color биома, "#rrggbb" - постоянный. Источники - в docstring. Влияет только на кубы, остальные
# блоки списка (трава, лоза) пропускаются как не-кубы.
TINT = {
    "grass_block": "grass", "short_grass": "grass", "tall_grass": "grass", "fern": "grass", "large_fern": "grass",
    "potted_fern": "grass", "sugar_cane": "grass", "bush": "grass",
    "oak_leaves": "foliage", "jungle_leaves": "foliage", "acacia_leaves": "foliage", "dark_oak_leaves": "foliage",
    "vine": "foliage",
    "leaf_litter": "dry_foliage",
    "spruce_leaves": "#619961", "birch_leaves": "#80A755", "mangrove_leaves": "#92C648",
    "water": "water", "bubble_column": "water", "water_cauldron": "water",
}
# tintindex в модели есть (родитель block/leaves), но в игре цвет берётся из текстуры
UNTINTED = {"cherry_leaves", "azalea_leaves", "flowering_azalea_leaves", "pale_oak_leaves"}
# Жидкости: модель без элементов, рисуем кубом из этой текстуры (поверхность в MC ниже 1 м - здесь не учитываем)
FLUID_CUBES = {"water": ("minecraft:block/water_still", 0), "lava": ("minecraft:block/lava_still", None)}
# Вариант по умолчанию: в jar его нет (он в коде блока), выбираем по предпочтительным значениям свойств
PREFERRED = {"facing": "north", "axis": "y", "half": "bottom", "type": "bottom", "orientation": "north_up",
             "attachment": "floor", "face": "wall", "shape": "straight", "mode": "save", "instrument": "harp",
             "trial_spawner_state": "inactive", "vault_state": "inactive", "creaking_heart_state": "uprooted"}
# Исключения из PREFERRED; значения по умолчанию сверены с таблицами Block states на minecraft.wiki
STATE_DEFAULTS = {"observer": {"facing": "south"}}
SHADE = {"up": 1.0, "down": 0.5, "north": 0.8, "south": 0.8, "west": 0.6, "east": 0.6}  # только превью


class Skip(Exception):
    """Блок не кубический или не читается: (категория, подробность)."""

    def __init__(self, kind, detail=""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind


# ---- jar ------------------------------------------------------------------------------------------------------
def split_ns(ref):
    ref = ref.lstrip("#")
    return ref.split(":", 1) if ":" in ref else ("minecraft", ref)


def norm_ref(ref):
    ns, p = split_ns(ref)
    return f"{ns}:{p}"


class Jar:
    def __init__(self, path):
        self.path = path
        self.z = zipfile.ZipFile(path)
        self.names = set(self.z.namelist())
        self._json, self._tex = {}, {}

    def json(self, name):
        if name not in self._json:
            self._json[name] = json.loads(self.z.read(name)) if name in self.names else None
        return self._json[name]

    def texture(self, ref):
        """ref "ns:path" -> (RGBA первого кадра, анимирована ли). Кэш: одна текстура на много блоков."""
        if ref not in self._tex:
            ns, p = split_ns(ref)
            name = f"assets/{ns}/textures/{p}.png"
            if name not in self.names:
                raise Skip("missing texture", ref)
            img = Image.open(io.BytesIO(self.z.read(name)))
            img.load()
            img = img.convert("RGBA")  # P/L/LA с tRNS тоже дают правильную альфу
            meta = self.json(name + ".mcmeta") or {}
            anim = meta.get("animation")
            if anim is not None:
                img = first_frame(img, anim)
            self._tex[ref] = (img, anim is not None)
        return self._tex[ref]


def first_frame(img, anim):
    """Кадр frames[0] (или 0) анимационной полосы. Размер кадра: width/height из mcmeta, иначе квадрат по короткой
    стороне; кадры идут по строкам слева направо."""
    w, h = img.size
    fw, fh = anim.get("width"), anim.get("height")
    if fw is None and fh is None:
        fw = fh = min(w, h)
    fw, fh = fw or w, fh or h
    frames = anim.get("frames") or [0]
    f0 = frames[0]
    idx = int(f0["index"] if isinstance(f0, dict) else f0)
    per_row = max(1, w // fw)
    x, y = idx % per_row * fw, idx // per_row * fh
    return img.crop((x, y, x + fw, y + fh))


# ---- биом и тинт ----------------------------------------------------------------------------------------------
def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def colormap_xy(temperature, downfall):
    t = min(max(f32(temperature), 0.0), 1.0)
    d = min(max(f32(downfall), 0.0), 1.0) * t
    return int((1.0 - t) * 255), int((1.0 - d) * 255)


def hex_rgb(v):
    if isinstance(v, int):
        v &= 0xFFFFFF
        return (v >> 16, v >> 8 & 255, v & 255)
    v = v.lstrip("#")
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))


def rgb_hex(rgb):
    return "#%02X%02X%02X" % tuple(rgb)


def load_biome(jar, name="plains", climate=None):
    d = jar.json(f"data/minecraft/worldgen/biome/{name}.json")
    if d is None:
        if climate is None:
            sys.exit(f"biome {name} not found in the jar; pass --climate T D")
        d = {}
    eff = d.get("effects", {})
    t, dn = climate if climate else (d["temperature"], d["downfall"])
    b = dict(name=name, temperature=t, downfall=dn, colormap_xy=list(colormap_xy(t, dn)),
             water_color=rgb_hex(hex_rgb(eff.get("water_color", "#3f76e4"))))
    for k in ("grass_color", "foliage_color", "dry_foliage_color"):
        if k in eff:
            b[k] = rgb_hex(hex_rgb(eff[k]))
    if "grass_color_modifier" in eff:
        b["grass_color_modifier"] = eff["grass_color_modifier"]
    return b


class Tints:
    def __init__(self, jar, biome):
        self.jar, self.biome = jar, biome
        self.maps = {}

    def colormap(self, kind):
        """grass | foliage | dry_foliage -> (rgb, откуда)."""
        if kind + "_color" in self.biome:
            return hex_rgb(self.biome[kind + "_color"]), f"biome {self.biome['name']} {kind}_color"
        if kind not in self.maps:
            name = f"assets/minecraft/textures/colormap/{kind}.png"
            self.maps[kind] = Image.open(io.BytesIO(self.jar.z.read(name))).convert("RGB")
        x, y = self.biome["colormap_xy"]
        return self.maps[kind].getpixel((x, y)), f"colormap/{kind}.png ({x},{y})"

    def for_block(self, block):
        """-> (rgb | None, описание, предупреждение | None)."""
        src = TINT.get(block)
        if src is None and block in UNTINTED:
            return None, "none (vanilla: colour from texture)", None
        if src is None:
            item = self.jar.json(f"assets/minecraft/items/{block}.json") or {}
            tints = (item.get("model") or {}).get("tints") or []
            if tints and tints[0].get("type") == "minecraft:constant":
                return hex_rgb(tints[0]["value"]), "items json constant", f"{block}: tint taken from items/{block}.json"
            return None, "none", f"{block}: tintindex without known tint source, left untinted"
        if src.startswith("#"):
            return hex_rgb(src), "constant", None
        if src == "water":
            return hex_rgb(self.biome["water_color"]), f"biome {self.biome['name']} water_color", None
        rgb, how = self.colormap(src)
        warn = None
        if src == "grass" and "grass_color_modifier" in self.biome:
            warn = f"grass_color_modifier {self.biome['grass_color_modifier']} not applied"
        return rgb, how, warn


# ---- модель ---------------------------------------------------------------------------------------------------
def model_path(ref):
    ns, p = split_ns(ref)
    return f"assets/{ns}/models/{p}.json"


def resolve_model(jar, ref):
    """-> (цепочка моделей, textures с учётом наследования, elements | None, builtin | None)."""
    chain, textures, elements, builtin = [], {}, None, None
    cur = ref
    while cur:
        if split_ns(cur)[1].startswith("builtin/"):
            builtin = norm_ref(cur)
            break
        if len(chain) > 32:
            raise Skip("invalid", f"parent loop at {cur}")
        data = jar.json(model_path(cur))
        if data is None:
            raise Skip("missing model", norm_ref(cur))
        chain.append(norm_ref(cur))
        for k, v in (data.get("textures") or {}).items():
            textures.setdefault(k, v)
        if elements is None and "elements" in data:
            elements = data["elements"]
        cur = data.get("parent")
    return chain, textures, elements, builtin


def resolve_tex(textures, ref):
    for _ in range(16):
        if not ref.startswith("#"):
            return norm_ref(ref)
        ref = textures.get(ref[1:])
        if ref is None:
            return None
    return None


def _full(el):
    return [float(c) for c in el.get("from", ())] == [0, 0, 0] and [float(c) for c in el.get("to", ())] == [16] * 3


def cube_layers(elements, textures):
    """Полные элементы 0..16 без поворота -> {грань: [слой]} в порядке отрисовки. Внутренние элементы (ядро слизи,
    мёда, маяка) отбрасываем с предупреждением, всё остальное - не куб."""
    if not elements:
        raise Skip("no elements", "entity, fluid or item-only model")
    layers, inner, first_partial = {d: [] for d in DIRS}, 0, None
    for i, el in enumerate(elements):
        if _full(el):
            rot = el.get("rotation") or {}
            if float(rot.get("angle", 0)) != 0:
                raise Skip("element rotation", f"element {i} rotated {rot.get('angle')} deg")
            for d, face in (el.get("faces") or {}).items():
                tex = resolve_tex(textures, face.get("texture", ""))
                if tex is None:
                    raise Skip("missing texture", f"element {i} {d} {face.get('texture')}")
                layers[d].append(dict(tex=tex, uv=face.get("uv"), rotation=int(face.get("rotation", 0)),
                                      tintindex=face.get("tintindex")))
        elif all(0 <= float(c) <= 16 for c in list(el.get("from", ())) + list(el.get("to", ()))):
            inner += 1
            first_partial = first_partial or f"element {i} from {el.get('from')} to {el.get('to')}"
        else:
            raise Skip("not a full cube", f"element {i} from {el.get('from')} to {el.get('to')} sticks out")
    if not any(layers.values()):
        raise Skip("not a full cube", f"no element spans 0..16 ({first_partial})")
    missing = [d for d in DIRS if not layers[d]]
    if missing:
        raise Skip("missing face", ",".join(missing))
    return layers, inner


# ---- вариант blockstate ---------------------------------------------------------------------------------------
def parse_state(key):
    return dict(kv.split("=", 1) for kv in key.split(",") if "=" in kv)


def pick_variant(variants, block=""):
    """Ключ варианта по умолчанию. В jar нет default state, поэтому оцениваем: совпадение с PREFERRED (+2),
    false/0/none у прочих свойств (+1); при равенстве - первый в файле."""
    if "" in variants:
        return ""
    want = dict(PREFERRED, **STATE_DEFAULTS.get(block, {}))

    def score(key):
        s = 0
        for k, v in parse_state(key).items():
            if k in want:
                s += 2 if v == want[k] else 0
            elif v in ("false", "0", "none"):
                s += 1
        return s
    return max(variants, key=score)


# ---- грани ----------------------------------------------------------------------------------------------------
def rotate_cw(img, deg):
    k = (deg // 90) % 4
    return img if k == 0 else img.transpose((None, Image.Transpose.ROTATE_270, Image.Transpose.ROTATE_180,
                                             Image.Transpose.ROTATE_90)[k])


def layer_image(tex, uv, rotation):
    """Кусок текстуры по uv грани (в 1/16 текстуры; u0 > u1 - отражение), затем rotation по часовой."""
    w, h = tex.size
    u0, v0, u1, v1 = [float(c) for c in (uv or (0, 0, 16, 16))]
    box = (round(min(u0, u1) * w / 16), round(min(v0, v1) * h / 16), round(max(u0, u1) * w / 16),
           round(max(v0, v1) * h / 16))
    if box[2] <= box[0] or box[3] <= box[1]:
        raise Skip("invalid", f"empty uv {uv}")
    img = tex.crop(box)
    if u0 > u1:
        img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if v0 > v1:
        img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    return rotate_cw(img, rotation)


def tint_image(img, rgb):
    r, g, b, a = img.split()
    t = ImageChops.multiply(Image.merge("RGB", (r, g, b)), Image.new("RGB", img.size, tuple(rgb)))
    return Image.merge("RGBA", (*t.split(), a))


def compose_face(jar, layers, tint_rgb):
    """Слои одной грани -> RGBA в родном размере (максимум по слоям), анимирована ли, был ли тинт."""
    imgs, animated, tinted = [], [], False
    for ly in layers:
        tex, anim = jar.texture(ly["tex"])
        if anim:
            animated.append(ly["tex"])
        img = layer_image(tex, ly["uv"], ly["rotation"])
        if ly["tintindex"] is not None and tint_rgb is not None:
            img = tint_image(img, tint_rgb)
            tinted = True
        imgs.append(img)
    n = max(max(i.size) for i in imgs)
    out = None
    for img in imgs:
        if img.size != (n, n):
            img = img.resize((n, n), Image.Resampling.NEAREST)
        out = img if out is None else Image.alpha_composite(out, img)
    return out, animated, tinted


def _vrot(v, x, y):
    """Поворот варианта blockstate: сначала x, потом y, шаги по 90 (y=90 переводит north в east, x=90 - up в north)."""
    a, b, c = v
    for _ in range(x // 90 % 4):
        a, b, c = a, c, -b
    for _ in range(y // 90 % 4):
        a, b, c = -c, b, a
    return (a, b, c)


def _sub(p, q):
    return tuple(i - j for i, j in zip(p, q))


# Рамка грани: (право, низ) картинки в осях MC, из углов cube_mesh (TR - TL, BL - TL)
FRAME = {f: (_sub(c[3], c[0]), _sub(c[1], c[0])) for f, c in CM.MC_CORNERS.items()}
VEC_DIR = {v: f for f, v in CM.MC_NORMAL.items()}


def rotation_map(x, y, uvlock=False):
    """Поворот варианта -> {грань блока: (грань модели, поворот картинки по часовой)}. uvlock: текстура не крутится
    вместе с гранью (точно для uv на всю грань)."""
    out = {}
    for s in DIRS:
        t = VEC_DIR[_vrot(CM.MC_NORMAL[s], x, y)]
        r = _vrot(FRAME[s][0], x, y)
        right, down = FRAME[t]
        k = 0
        if not uvlock:
            k = {right: 0, down: 90, tuple(-c for c in right): 180, tuple(-c for c in down): 270}[r]
        out[t] = (s, k)
    return out


def rotate_block(faces, x, y, uvlock=False):
    """Грани модели -> грани блока после поворота варианта."""
    return {t: rotate_cw(faces[s], k) for t, (s, k) in rotation_map(x, y, uvlock).items()}


def bleed_rgb(img):
    """RGB полностью прозрачных пикселей := среднее непрозрачных соседей (проходами), альфа не меняется. Иначе
    мипы и билинейка тянут в край листвы и стекла случайный цвет из-под прозрачности."""
    w, h = img.size
    raw = img.tobytes()
    px = [list(raw[i:i + 4]) for i in range(0, len(raw), 4)]
    known = [p[3] > 0 for p in px]
    if all(known) or not any(known):
        return img
    while True:
        upd = []
        for i, ok in enumerate(known):
            if ok:
                continue
            x, y = i % w, i // w
            acc, n = [0, 0, 0], 0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    xx, yy = x + dx, y + dy
                    if (dx or dy) and 0 <= xx < w and 0 <= yy < h and known[yy * w + xx]:
                        q = px[yy * w + xx]
                        acc = [acc[0] + q[0], acc[1] + q[1], acc[2] + q[2]]
                        n += 1
            if n:
                upd.append((i, [round(c / n) for c in acc]))
        if not upd:
            break
        for i, rgb in upd:
            px[i][:3] = rgb
            known[i] = True
    return Image.frombytes("RGBA", img.size, bytes(c for p in px for c in p))


def alpha_kind(faces):
    vals = set()
    for img in faces.values():
        vals |= {i for i, c in enumerate(img.getchannel("A").histogram()) if c}
    if vals == {255}:
        return "opaque"
    return "cutout" if vals <= {0, 255} else "translucent"


def img_hash(img):
    return hashlib.sha1(f"{img.mode}{img.size}".encode() + img.tobytes()).hexdigest()


def same_faces(faces):
    groups = {}
    for f in CM.FACES:
        groups.setdefault(img_hash(faces[f]), []).append(f)
    return list(groups.values())


def unknown_faces():
    """Серый куб для неизвестных блоков (своя картинка, не Mojang): 16x16, серый с тёмной рамкой."""
    img = Image.new("RGBA", (16, 16), (128, 128, 128, 255))
    ImageDraw.Draw(img).rectangle((0, 0, 15, 15), outline=(96, 96, 96, 255))
    return {d: img for d in DIRS}


# ---- атлас ----------------------------------------------------------------------------------------------------
def pad_edges(tile, g):
    """Рамка g px повтором крайних пикселей (против протекания соседних клеток в мипах)."""
    w, h = tile.size
    out = Image.new(tile.mode, (w + 2 * g, h + 2 * g))
    out.paste(tile, (g, g))
    out.paste(tile.crop((0, 0, w, 1)).resize((w, g)), (g, 0))
    out.paste(tile.crop((0, h - 1, w, h)).resize((w, g)), (g, h + g))
    out.paste(tile.crop((0, 0, 1, h)).resize((g, h)), (0, g))
    out.paste(tile.crop((w - 1, 0, w, h)).resize((g, h)), (w + g, g))
    for (sx, sy), (dx, dy) in (((0, 0), (0, 0)), ((w - 1, 0), (w + g, 0)), ((0, h - 1), (0, h + g)),
                               ((w - 1, h - 1), (w + g, h + g))):
        out.paste(Image.new(tile.mode, (g, g), tile.getpixel((sx, sy))), (dx, dy))
    return out


def build_atlas(faces, cell=256, gutter=0):
    atlas = Image.new("RGBA", (CM.COLS * cell, CM.ROWS * cell), (0, 0, 0, 0))
    inner = cell - 2 * gutter
    for f, (c, r) in CM.CELLS.items():
        tile = faces[f].resize((inner, inner), Image.Resampling.NEAREST)
        atlas.paste(pad_edges(tile, gutter) if gutter else tile, (c * cell, r * cell))
    for (c, r), (sc, sr) in CM.SPARE.items():
        src = atlas.crop((sc * cell, sr * cell, (sc + 1) * cell, (sr + 1) * cell))
        atlas.paste(src.transpose(Image.Transpose.FLIP_TOP_BOTTOM), (c * cell, r * cell))
    return atlas


# ---- превью: куб рисуется по UV меша из cube_mesh, так что проверяет связку меш + атлас ------------------------
VIEWS = (("SE", (1, -1, 0.8)), ("NW", (-1, 1, 0.8)), ("SE below", (1, -1, -0.8)))


def _norm(v):
    n = sum(c * c for c in v) ** 0.5
    return tuple(c / n for c in v)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return sum(i * j for i, j in zip(a, b))


def _affine(src, dst):
    """Коэффициенты (a..f) для Image.transform: точки экрана src[0..2] -> точки текстуры dst[0..2]."""
    (x0, y0), (x1, y1), (x2, y2) = src
    det = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    res = []
    for k in (0, 1):
        t0, t1, t2 = dst[0][k], dst[1][k], dst[2][k]
        a = ((t1 - t0) * (y2 - y0) - (t2 - t0) * (y1 - y0)) / det
        b = ((t2 - t0) * (x1 - x0) - (t1 - t0) * (x2 - x0)) / det
        res += [a, b, t0 - a * x0 - b * y0]
    return res


def render_cube(atlas, cam, size=192, cell=256, gutter=0):
    """Ортографический вид куба с направления cam (оси Ведьмака, Z вверх) по UV из cube_mesh.face_quads."""
    atlas = atlas.convert("RGBA")
    W, H = atlas.size
    c = _norm(cam)
    right = _norm(_cross((-c[0], -c[1], -c[2]), (0, 0, 1)))
    up = _cross(right, (-c[0], -c[1], -c[2]))
    s = size / 1.9
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    for q in CM.face_quads("atlas", cell, gutter, inset_px=0):
        if _dot(q["normal"], c) <= 0:
            continue
        pts = [(size / 2 + s * _dot(p, right), size / 2 - s * _dot(p, up)) for p in q["corners"]]
        uvs = [(u * W, v * H) for u, v in q["uv"]]
        coef = _affine((pts[0], pts[1], pts[3]), (uvs[0], uvs[1], uvs[3]))
        tex = atlas.transform((size, size), Image.Transform.AFFINE, coef, resample=Image.Resampling.NEAREST)
        k = SHADE[q["face"]]
        r, g, b, a = tex.split()
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).polygon(pts, fill=255)
        rgb = [ch.point(lambda v, k=k: int(v * k)) for ch in (r, g, b)]
        out.alpha_composite(Image.merge("RGBA", rgb + [ImageChops.multiply(mask, a)]))
    return out


def _font(size):
    try:
        return ImageFont.load_default(size)
    except (TypeError, OSError):
        return ImageFont.load_default()


def preview(entries, out_dir, names, cell, gutter, path):
    """Коллаж: подпись, атлас с метками клеток, три вида куба. Фон - шахматка, чтобы видеть альфу."""
    names = [n for n in names if n in entries][:24]
    if not names:
        return None
    tw, th, v = 384, 192, 192
    row_h, col_w = th + 34, tw + 3 * v + 24
    cols = 2 if len(names) > 6 else 1
    rows = (len(names) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * col_w, rows * row_h), (40, 40, 40))
    checker = Image.new("RGB", (16, 16), (90, 90, 90))
    ImageDraw.Draw(checker).rectangle((0, 0, 7, 7), fill=(130, 130, 130))
    ImageDraw.Draw(checker).rectangle((8, 8, 15, 15), fill=(130, 130, 130))
    font, small = _font(16), _font(12)
    for i, n in enumerate(names):
        e = entries[n]
        x0, y0 = i // rows * col_w, i % rows * row_h
        d = ImageDraw.Draw(sheet)
        d.text((x0 + 6, y0 + 4), f"{n}  [{e['alpha']}]  tint {e['tint']['rgb'] if e['tint'] else '-'}",
               fill=(255, 255, 255), font=font)
        with Image.open(os.path.join(out_dir, e["file"])) as im:
            atlas = im.convert("RGBA")
        bg = Image.new("RGBA", (tw, th))
        for yy in range(0, th, 16):
            for xx in range(0, tw, 16):
                bg.paste(checker, (xx, yy))
        bg.alpha_composite(atlas.resize((tw, th), Image.Resampling.NEAREST))
        dd = ImageDraw.Draw(bg)
        cw = tw // CM.COLS
        for f, (c, r) in CM.CELLS.items():
            dd.text((c * cw + 3, r * cw + 2), f, fill=(255, 255, 0), font=small, stroke_width=2, stroke_fill=(0, 0, 0))
        sheet.paste(bg.convert("RGB"), (x0 + 6, y0 + 28))
        for j, (label, cam) in enumerate(VIEWS):
            img = render_cube(atlas, cam, v, cell, gutter)
            px = x0 + 12 + tw + j * v
            sheet.paste(img.convert("RGB"), (px, y0 + 28), img)
            d.text((px + 4, y0 + 30), label, fill=(200, 200, 200), font=small)
    sheet.save(path)
    return path


# ---- конвертация ----------------------------------------------------------------------------------------------
def block_faces(jar, tints, name):
    """-> (грани {dir: RGBA родного размера}, сведения для манифеста). Skip, если блок не куб."""
    bs = jar.json(f"assets/minecraft/blockstates/{name}.json")
    if bs is None:
        raise Skip("no blockstate", name)
    if "variants" not in bs:
        raise Skip("multipart", "blockstate has only multipart parts")
    variants = bs["variants"]
    key = pick_variant(variants, name)
    v = variants[key]
    nrand = len(v) if isinstance(v, list) else 1
    v = v[0] if isinstance(v, list) else v
    x, y, uvlock = int(v.get("x", 0)), int(v.get("y", 0)), bool(v.get("uvlock", False))
    chain, textures, elements, builtin = resolve_model(jar, v["model"])
    info = dict(variant=key, variants=len(variants), random_models=nrand, model=norm_ref(v["model"]), parents=chain[1:],
                rotation=[x, y], warnings=[])
    if builtin:
        info["parents"].append(builtin)
    if not elements and name in FLUID_CUBES:
        tex, ti = FLUID_CUBES[name]
        layers, inner = {d: [dict(tex=tex, uv=None, rotation=0, tintindex=ti)] for d in DIRS}, 0
        info["fluid"] = True
        info["warnings"].append("fluid drawn as a full 1 m cube")
    else:
        layers, inner = cube_layers(elements, textures)
    if inner:
        info["warnings"].append(f"dropped {inner} inner element(s)")
    tint_rgb, tint_how = None, None
    if any(ly["tintindex"] is not None for ls in layers.values() for ly in ls):
        tint_rgb, tint_how, warn = tints.for_block(name)
        if warn:
            info["warnings"].append(warn)
    faces, animated, tinted_faces, srcs = {}, set(), [], {}
    for d, ls in layers.items():
        faces[d], anim, tinted = compose_face(jar, ls, tint_rgb)
        animated.update(anim)
        srcs[d] = " + ".join(ly["tex"] for ly in ls)
        if tinted:
            tinted_faces.append(d)
    # после поворота варианта грань d блока берёт картинку другой грани модели
    moved = {t: s for t, (s, _k) in rotation_map(x, y, uvlock).items()}
    faces = rotate_block(faces, x, y, uvlock)
    info["faces"] = {d: srcs[moved[d]] for d in DIRS}
    tinted_after = sorted({t for t in DIRS if moved[t] in tinted_faces}, key=DIRS.index)
    info["tint"] = dict(rgb=rgb_hex(tint_rgb), source=tint_how, faces=tinted_after) if tinted_faces else None
    if tint_rgb is None and tint_how:
        info["tint_note"] = tint_how
    info["animated"] = sorted(animated)
    return faces, info


def convert(jar, names, out, face_px=256, gutter=0, biome=None, normal=False, masks=False):
    """Главная работа: пишет atlas/, face/, mask/, manifest.json в out. -> манифест."""
    t0 = time.perf_counter()
    tints = Tints(jar, biome)
    for sub in ("atlas", "face") + (("mask",) if masks else ()):
        os.makedirs(os.path.join(out, sub), exist_ok=True)
    blocks, skipped, faces_of, groups_by_key = {}, {}, {}, {}
    for name in [UNKNOWN] + list(names):
        try:
            if name == UNKNOWN:
                faces = unknown_faces()
                info = dict(generated=True, faces={d: "generated grey" for d in DIRS}, tint=None, animated=[],
                            warnings=[])
            else:
                faces, info = block_faces(jar, tints, name)
        except Skip as e:
            skipped[name] = str(e)
            continue
        except (KeyError, ValueError, OSError) as e:
            skipped[name] = f"invalid: {type(e).__name__} {e}"
            continue
        kind = alpha_kind(faces)
        if kind != "opaque":
            faces = {d: bleed_rgb(img) for d, img in faces.items()}
        groups = same_faces(faces)
        key = hashlib.sha1((kind + "".join(img_hash(faces[f]) for f in CM.FACES)).encode()).hexdigest()
        blocks[name] = dict(file=None, face_file=None, mask_file=None, same_as=None, alpha=kind,
                            uniform=len(groups) == 1, unique_faces=len(groups), same_faces=groups, **info)
        faces_of[name] = faces
        groups_by_key.setdefault(key, []).append(name)
    # одинаковые атласы (infested_stone = stone, waxed_* = *) пишем один раз под самым коротким именем
    for members in groups_by_key.values():
        name = min(members, key=lambda n: (len(n), n))
        entry, faces, kind = blocks[name], faces_of[name], blocks[name]["alpha"]
        atlas = build_atlas(faces, face_px, gutter)
        entry["file"] = f"atlas/{name}.png"
        (atlas if kind != "opaque" else atlas.convert("RGB")).save(os.path.join(out, entry["file"]))
        if entry["uniform"]:
            entry["face_file"] = f"face/{name}.png"
            face = faces["up"].resize((face_px, face_px), Image.Resampling.NEAREST)
            (face if kind != "opaque" else face.convert("RGB")).save(os.path.join(out, entry["face_file"]))
        if masks and kind != "opaque":
            entry["mask_file"] = f"mask/{name}.png"
            atlas.getchannel("A").save(os.path.join(out, entry["mask_file"]))
        for other in members:
            if other != name:
                blocks[other].update(file=entry["file"], face_file=entry["face_file"], mask_file=entry["mask_file"],
                                     same_as=name)
    normal_file = None
    if normal:
        normal_file = "normal_flat.png"
        Image.new("RGB", (CM.COLS * face_px, CM.ROWS * face_px), (128, 128, 255)).save(os.path.join(out, normal_file))
    reasons = {}
    for r in skipped.values():
        k = r.split(":", 1)[0]
        reasons[k] = reasons.get(k, 0) + 1
    alpha = {}
    for e in blocks.values():
        alpha[e["alpha"]] = alpha.get(e["alpha"], 0) + 1
    real = [n for n in blocks if n != UNKNOWN]
    manifest = {
        "_about": "Minecraft block textures as cube face atlases for Witcher 3 (mcw). Derived from the user's own "
                  "Minecraft jar: keep local, do not commit or ship. Layout and UV: tools/cube_mesh.py.",
        "generator": "tools/mc_textures.py",
        "jar": jar.path,
        "mc_version": (jar.json("version.json") or {}).get("id"),
        "biome": biome,
        "params": dict(face_px=face_px, gutter_px=gutter, upscale="nearest"),
        "layout": CM.layout_info(face_px, gutter),
        "normal_flat": normal_file,
        "unknown": UNKNOWN,
        "summary": dict(requested=len(names), converted=len(real), skipped=len(skipped), skip_reasons=reasons,
                        alpha=alpha, uniform=sum(1 for n in real if blocks[n]["uniform"]),
                        files=len(groups_by_key), tinted=sum(1 for n in real if blocks[n]["tint"]),
                        animated=sum(1 for n in real if blocks[n]["animated"]),
                        with_warnings=sum(1 for n in real if blocks[n]["warnings"]),
                        seconds=round(time.perf_counter() - t0, 2)),
        "blocks": blocks,
        "skipped": dict(sorted(skipped.items())),
    }
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=1, ensure_ascii=False)
    return manifest


def all_block_names(jar):
    pre = "assets/minecraft/blockstates/"
    return sorted(n[len(pre):-5] for n in jar.names if n.startswith(pre) and n.endswith(".json"))


def find_jar(path):
    if os.path.isfile(path):
        return path
    base = os.path.join(os.environ.get("APPDATA", ""), ".minecraft", "versions")
    found = sorted(glob.glob(os.path.join(base, "*1.21.11*", "*.jar")))
    hint = ("found: " + ", ".join(found)) if found else f"no 1.21.11 jar under {base}"
    sys.exit(f"jar not found: {path}; {hint}. Pass --jar")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Minecraft block textures -> cube face atlases for Witcher 3")
    ap.add_argument("--jar", default=JAR_DEFAULT)
    ap.add_argument("--out", default=CM.DEFAULT_OUT)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--blocks", help="comma-separated block ids (default: MVP list)")
    g.add_argument("--all", action="store_true", help="every blockstate in the jar; non-cubes are skipped")
    ap.add_argument("--face-px", type=int, default=256, help="atlas cell size (nearest upscale)")
    ap.add_argument("--gutter-px", type=int, default=0, help="edge-extended border inside each cell")
    ap.add_argument("--biome", default="plains")
    ap.add_argument("--climate", type=float, nargs=2, metavar=("T", "D"), help="override temperature, downfall")
    ap.add_argument("--normal", action="store_true", help="write normal_flat.png (128,128,255)")
    ap.add_argument("--masks", action="store_true", help="write mask/<block>.png (alpha) for non-opaque blocks")
    ap.add_argument("--no-preview", action="store_true")
    a = ap.parse_args(argv)
    CM.refuse_out_dir(a.out)
    if a.face_px < 16 or a.face_px % 16 or not 0 <= 2 * a.gutter_px < a.face_px:
        sys.exit("--face-px must be a multiple of 16 and larger than 2*gutter")
    jar = Jar(find_jar(a.jar))
    names = all_block_names(jar) if a.all else [split_ns(n.strip())[1] for n in (a.blocks or ",".join(MVP)).split(",")
                                                 if n.strip()]
    biome = load_biome(jar, a.biome, tuple(a.climate) if a.climate else None)
    os.makedirs(a.out, exist_ok=True)
    m = convert(jar, names, a.out, a.face_px, a.gutter_px, biome, a.normal, a.masks)
    s = m["summary"]
    print(f"jar {jar.path} ({m['mc_version']}), biome {biome['name']} T={biome['temperature']} D={biome['downfall']} "
          f"colormap {biome['colormap_xy']}")
    print(f"requested {s['requested']}: converted {s['converted']} (+{UNKNOWN}), skipped {s['skipped']}, "
          f"files {s['files']}, {s['seconds']} s")
    print("skip reasons:", s["skip_reasons"])
    print("alpha:", s["alpha"], "uniform:", s["uniform"], "tinted:", s["tinted"], "animated:", s["animated"],
          "with warnings:", s["with_warnings"])
    for n in names:
        if n in m["skipped"] and not a.all:
            print(f"  skip {n}: {m['skipped'][n]}")
    if not a.no_preview:
        pv = [n for n in MVP if n in m["blocks"]] if a.all else list(names)
        p = preview(m["blocks"], a.out, pv + [UNKNOWN], a.face_px, a.gutter_px, os.path.join(a.out, "preview.png"))
        if p:
            print("preview", p)
    print("manifest", os.path.join(a.out, "manifest.json"))


if __name__ == "__main__":
    main()
