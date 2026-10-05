"""Модели из коробок Minecraft для Witcher 3 5.00c через REDkit без GUI: тело Стива со скином (катсцены, диалоги,
вид от третьего лица) и лошадь MC с седлом. Обвязка wcc_lite и шаги import..pack — из build_blocks.py.

Каждая коробка — своя сущность: w2ent (CStaticMeshComponent, без навмеша) с w2mesh из engine\\meshes\\editor\\box.w2mesh,
у которого переписаны позиции вершин (размер и пивот коробки), UV (box-UV Майнкрафта по текстуре 64x64) и рамка, а
коллизия снята (части тела висят на костях Геральта, лошадь — кукла: физики у них быть не должно). Материал — pbr_std с
альфа-тестом (второй слой скина, седло), текстура — PNG 64x64, увеличенный без сглаживания до 1024.

Оси меша (как у блоков, spike/coords.py): X — вправо от существа, Y — вперёд, Z — вверх. Модель MC (tools/mc_models.json):
+X — влево, +Y — вниз, -Z — вперёд, поэтому точка модели (mx, my, mz) -> меш (-mx, -mz, -my).

  Стив: части по костям Геральта, пивот — сустав. Ноги и руки — по две коробки (плечо/предплечье, бедро/голень): верх и
        низ текстуры конечности; поперёк — K м на пиксель скина, вдоль — длина кости Геральта (docs/NOTES.md, mcwx_bones).
  Лошадь: части по группам вращения (body, tail, head_parts, четыре ноги); коробка в координатах группы — от её пивота;
        масштаб 1,1 / 16 м на пиксель (корень модели взрослой лошади), земля — z = 0 при пивоте группы на высоте (24 -
        y_пивота) пикселей. Сколько и куда — в манифесте models.json рядом с модом (его читает mcw_models.ws).

Запуск (шаги с wcc — только с --allow-wcc):
  python build_models.py all --allow-wcc [--skin <png>] [--horse-coat chestnut] [--out <папка>]
  python build_models.py prepare|verify;  python build_models.py import|cook|caches|pack --allow-wcc
Рабочая папка — %LOCALAPPDATA%\\mcw\\redkit_models (своя: cook кукает весь workspace). Мод — modMCWModels.
"""
import argparse
import io
import json
import math
import os
import shutil
import struct
import sys
import uuid
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_blocks as bb  # noqa: E402
import cr2w  # noqa: E402
import mc_textures  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
WORK = os.path.join(bb.LOCAL, "mcw", "redkit_models")
MOD = "modMCWModels"
DEPOT = r"mcw\models"
# Свой скин — --skin <png> или файл assets_local\skins\geralt.png (в репозиторий не входит: у скинов с Planet
# Minecraft нет лицензии на распространение, assets_local\skins\README.md); без них — Стив из jar Minecraft.
SKIN = os.path.join(PROJECT, "assets_local", "skins", "geralt.png")
MODELS_JSON = os.path.join(HERE, "mc_models.json")
UPSCALE = 16                       # 64 -> 1024: пиксели MC остаются чёткими
TEX = 64
INSET = 0.02                       # отступ UV внутрь грани, пикселей MC (против соседних пикселей на мипах)

K = 0.05                           # Стив: метров на пиксель скина поперёк
HORSE_S = 1.1 / 16.0               # лошадь: метров на пиксель модели (корень взрослой лошади 1.1)

# Стив: имя -> кость Геральта, сустав-цель (направление «вдоль»), текстура (U, V), (w, h, d) box-UV, ряды боковых граней
# [r0, r1), коробка меша (sx, sy, z0, z1) в м от сустава, inflate (px скина). Длины — по костям Геральта (docs/NOTES.md):
# плечо 0,28 + запас, предплечье с кистью 0,39, бедро 0,50, голень до подошвы 0,52, торс от шеи до таза 0,60.
_ARM_UP, _ARM_LO, _LEG_UP, _LEG_LO = 0.30, 0.38, 0.49, 0.53
STEVE_BASE = {
    "head":   ("head", "neck", (0, 0), (8, 8, 8), (0, 8), (8 * K, 8 * K, -0.10, -0.10 + 8 * K)),
    "body":   ("torso3", "pelvis", (16, 16), (8, 12, 4), (0, 12), (8 * K, 4 * K, -0.60, 0.0)),
    "r_arm1": ("r_bicep", "r_forearm", (40, 16), (4, 12, 4), (0, 6), (4 * K, 4 * K, -_ARM_UP, 0.0)),
    "r_arm2": ("r_forearm", "r_hand", (40, 16), (4, 12, 4), (6, 12), (4 * K, 4 * K, -_ARM_LO, 0.0)),
    "l_arm1": ("l_bicep", "l_forearm", (32, 48), (4, 12, 4), (0, 6), (4 * K, 4 * K, -_ARM_UP, 0.0)),
    "l_arm2": ("l_forearm", "l_hand", (32, 48), (4, 12, 4), (6, 12), (4 * K, 4 * K, -_ARM_LO, 0.0)),
    "r_leg1": ("r_thigh", "r_shin", (0, 16), (4, 12, 4), (0, 6), (4 * K, 4 * K, -_LEG_UP, 0.0)),
    "r_leg2": ("r_shin", "r_foot", (0, 16), (4, 12, 4), (6, 12), (4 * K, 4 * K, -_LEG_LO, 0.0)),
    "l_leg1": ("l_thigh", "l_shin", (16, 48), (4, 12, 4), (0, 6), (4 * K, 4 * K, -_LEG_UP, 0.0)),
    "l_leg2": ("l_shin", "l_foot", (16, 48), (4, 12, 4), (6, 12), (4 * K, 4 * K, -_LEG_LO, 0.0)),
}
# второй слой: та же коробка, своя текстура, inflate (px): шапка 0.5, остальное 0.25
STEVE_OUTER = {"head": ("hat", (32, 0), 0.5), "body": ("jacket", (16, 32), 0.25),
               "r_arm1": ("r_sleeve1", (40, 32), 0.25), "r_arm2": ("r_sleeve2", (40, 32), 0.25),
               "l_arm1": ("l_sleeve1", (48, 48), 0.25), "l_arm2": ("l_sleeve2", (48, 48), 0.25),
               "r_leg1": ("r_pants1", (0, 32), 0.25), "r_leg2": ("r_pants2", (0, 32), 0.25),
               "l_leg1": ("l_pants1", (0, 48), 0.25), "l_leg2": ("l_pants2", (0, 48), 0.25)}

# лошадь: группа вращения каждой части (по родителям mc_models.json, без поводьев: у них нулевая ширина)
HORSE_GROUP = {"body": "body", "tail": "tail", "head_parts": "head_parts", "head": "head_parts",
               "left_ear": "head_parts", "right_ear": "head_parts", "mane": "head_parts", "upper_mouth": "head_parts",
               "left_hind_leg": "left_hind_leg", "right_hind_leg": "right_hind_leg",
               "left_front_leg": "left_front_leg", "right_front_leg": "right_front_leg",
               "saddle": "body", "head_saddle": "head_parts", "mouth_saddle_wrap": "head_parts",
               "left_saddle_mouth": "head_parts", "right_saddle_mouth": "head_parts"}
SADDLE_PARTS = ("saddle", "head_saddle", "mouth_saddle_wrap", "left_saddle_mouth", "right_saddle_mouth")


# ---- box-UV Майнкрафта ------------------------------------------------------------------------------------------
def box_uv(face, corner, uv, whd, rows=None, mirror=False):
    """UV в пикселях текстуры (V вниз) для угла грани коробки. face — нормаль грани меша ('+x', '-x', '+y', '-y', '+z',
    '-z'); corner — (x_max, y_max, z_max) угла в осях меша. Раскладка — _note в mc_models.json (углы граней в осях модели:
    x модели = -X меша, y = -Z, z = -Y). rows — ряды боковых граней [r0, r1) (половина конечности), иначе [0, h).
    mirror — как у ModelPart MC: текстура каждой грани зеркальна, боковые прямоугольники меняются гранями."""
    U, V = uv
    w, h, d = whd
    r0, r1 = rows or (0, h)
    mx1 = not corner[0]            # x модели максимален там, где X меша минимален
    my1 = not corner[2]            # y модели (вниз) максимален внизу
    mz1 = not corner[1]            # z модели (назад) максимален сзади
    if mirror:
        mx1 = not mx1
        if face in ("+x", "-x"):
            face = "-x" if face == "+x" else "+x"
            mz1 = not mz1
    vs = V + d + (r1 if my1 else r0)
    if face == "+z":                                   # верх модели (-Y)
        return U + d + (w if mx1 else 0), V + (0 if mz1 else d)
    if face == "-z":                                   # низ модели (+Y)
        return U + d + w + (w if mx1 else 0), V + (0 if mz1 else d)
    if face == "+x":                                   # бок -X модели (правый бок существа)
        return U + (0 if mz1 else d), vs
    if face == "-x":                                   # бок +X модели (левый бок)
        return U + d + w + (d if mz1 else 0), vs
    if face == "+y":                                   # перёд (-Z модели)
        return U + d + (w if mx1 else 0), vs
    if face == "-y":                                   # зад (+Z модели)
        return U + 2 * d + w + (0 if mx1 else w), vs
    raise ValueError(face)


def _face_of(nrm):
    i = max(range(3), key=lambda k: abs(nrm[k]))
    return ("+" if nrm[i] > 0 else "-") + "xyz"[i]


# ---- меш ---------------------------------------------------------------------------------------------------------
def make_box_mesh(mi_path, ext, uv, whd, rows=None, mirror=False, masked=True, auto_hide=100.0):
    """w2mesh коробки ext = (x0, x1, y0, y1, z0, z1) м в осях меша, box-UV (uv, whd, rows, mirror), без коллизии."""
    doc = cr2w.CR2WDoc(bb.r4(bb.SRC_MESH))
    cls = [doc.names[x["cls"]] for x in doc.exports]
    assert cls == ["CMesh", "CMaterialInstance", "CCollisionMesh", "CCollisionShapeBox"], cls
    mesh, mat, _, _ = doc.exports
    doc.imports = [[mi_path, doc.name_index("CMaterialInstance"), 0]]
    mat["data"] = doc.set_prop(mat["data"], "baseMaterial", struct.pack("<i", -1))
    if masked:
        mat["data"] = doc.set_prop(mat["data"], "enableMask", b"\x01", "Bool")
    mat["data"] = doc.set_material_params(mat["data"], [])
    # без коллизии: handle 0 = NULL (экспорты CCollisionMesh/Shape остаются сиротами, кук их пропустит)
    mesh["data"] = doc.set_prop(mesh["data"], "collisionMesh", struct.pack("<i", 0))
    mesh["data"] = doc.set_prop(mesh["data"], "isOccluder", b"\x00")
    mesh["data"] = doc.set_prop(mesh["data"], "autoHideDistance", struct.pack("<f", auto_hide))
    x0, x1, y0, y1, z0, z1 = ext
    _, box = doc.get_prop(mesh["data"], "boundingBox")
    box = bb._patch_floats(doc, box, {("Min", "X"): x0, ("Min", "Y"): y0, ("Min", "Z"): z0,
                                      ("Max", "X"): x1, ("Max", "Y"): y1, ("Max", "Z"): z1})
    mesh["data"] = doc.set_prop(mesh["data"], "boundingBox", box)
    r = 0.5 * math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2 + (z1 - z0) ** 2)
    mesh["data"] = doc.set_prop(mesh["data"], "generalizedMeshRadius", struct.pack("<f", r))
    raw = bb._buffer(doc, mesh["data"], "rawVertices")
    chunks = bb._buffer(doc, mesh["data"], "chunksBuffer")
    vb, cb = bytearray(raw["data"]), bytearray(chunks["data"])
    n = len(vb) // bb.VSTRIDE
    assert n * bb.VSTRIDE == len(vb) and n == 24, n
    for k in range(n):
        rec = bytes(vb[k * bb.VSTRIDE:(k + 1) * bb.VSTRIDE])
        pos = struct.unpack_from("<3f", rec, bb.V_POS)
        nrm = struct.unpack_from("<3f", rec, bb.V_NRM)
        corner = tuple(c > 0 for c in pos)
        p = (x1 if corner[0] else x0, y1 if corner[1] else y0, z1 if corner[2] else z0)
        face = _face_of(nrm)
        u, v = box_uv(face, corner, uv, whd, rows, mirror)
        u, v = inset_uv(u, v, face, corner, uv, whd, rows, mirror)
        new = bytearray(rec)
        struct.pack_into("<3f", new, bb.V_POS, *p)
        struct.pack_into("<2f", new, bb.V_UV, u / TEX, v / TEX)
        hits = [i for i in range(len(cb) - bb.VSTRIDE + 1) if bytes(cb[i:i + bb.VSTRIDE]) == rec]
        assert len(hits) == 1, f"vertex {k}: {len(hits)} copies in chunksBuffer"
        vb[k * bb.VSTRIDE:(k + 1) * bb.VSTRIDE] = new
        cb[hits[0]:hits[0] + bb.VSTRIDE] = new
    raw["data"], chunks["data"] = bytes(vb), bytes(cb)
    return doc.to_bytes()


def inset_uv(u, v, face, corner, uv, whd, rows, mirror):
    """UV угла сдвинуть на INSET к центру прямоугольника грани (центр — среднее UV всех четырёх углов грани)."""
    axis = "xyz".index(face[1])
    us, vs = [], []
    for a in (False, True):
        for b in (False, True):
            c = [a, b]
            c.insert(axis, face[0] == "+")
            cu, cv = box_uv(face, tuple(c), uv, whd, rows, mirror)
            us.append(cu)
            vs.append(cv)
    cu, cv = sum(us) / 4, sum(vs) / 4
    du = INSET if u < cu else -INSET if u > cu else 0.0
    dv = INSET if v < cv else -INSET if v > cv else 0.0
    return u + du, v + dv


def make_part_entity(mesh_path, name, ext):
    """w2ent из шаблона блоков: компонент правится и в entityObject, и во flatCompiledData; без навмеша (часть тела
    и лошадь двигаются — препятствием для NPC быть не должны), рамка — по коробке."""
    doc = cr2w.CR2WDoc(bb.r4(bb.SRC_ENT))
    tpl, _, comp = doc.exports
    guid = uuid.uuid5(uuid.NAMESPACE_URL, "mcw/models/" + name).bytes

    def patch(d, x):
        bb._patch_component(d, x, mesh_path, "mcw_" + name, guid)
        dd = x["data"]
        _, box = d.get_prop(dd, "boundingBox")
        box = bb._patch_floats(d, box, {("Min", "X"): ext[0], ("Max", "X"): ext[1], ("Min", "Y"): ext[2],
                                        ("Max", "Y"): ext[3], ("Min", "Z"): ext[4], ("Max", "Z"): ext[5]})
        dd = d.set_prop(dd, "boundingBox", box)
        dd = d.set_prop(dd, "pathLibCollisionType", struct.pack("<H", d.name_index("PLC_Disabled")))
        x["data"] = dd

    assert doc.names[comp["cls"]] == "CStaticMeshComponent"
    patch(doc, comp)
    flat = doc.get_nested(tpl["data"], "flatCompiledData")
    fcomp = flat.exports[1]
    assert flat.names[fcomp["cls"]] == "CStaticMeshComponent"
    patch(flat, fcomp)
    tpl["data"] = doc.set_nested(tpl["data"], "flatCompiledData", flat)
    return doc.to_bytes()


# ---- спецификации частей ------------------------------------------------------------------------------------------
def steve_parts():
    """[(имя, кость, сустав-цель, ext, uv, whd, rows)] — 10 частей и 10 второго слоя."""
    out = []
    for name, (bone, to, uv, whd, rows, (sx, sy, z0, z1)) in STEVE_BASE.items():
        ext = (-sx / 2, sx / 2, -sy / 2, sy / 2, z0, z1)
        out.append(dict(name=name, bone=bone, to=to, ext=ext, uv=uv, whd=whd, rows=rows, tex="skin"))
        oname, ouv, inf = STEVE_OUTER[name]
        e = inf * K
        oext = (ext[0] - e, ext[1] + e, ext[2] - e, ext[3] + e, ext[4] - e, ext[5] + e)
        out.append(dict(name=oname, bone=bone, to=to, ext=oext, uv=ouv, whd=whd, rows=rows, tex="skin"))
    return out


def horse_parts(models):
    """[(имя, группа, ext, uv, whd, mirror, tex)] + {группа: пивот (м меша от ног лошади, ноги — z = 0)}.
    Пивот группы в модели p (px) -> после корня 1.1p + (0, -2.4016, 0) -> меш ((-x), (-z), (24 - y)) / 16."""
    h = models["horse"]
    parts = dict(h["parts"])
    saddle = h["saddle_layer"]["parts"]
    groups = {}
    for g in ("body", "tail", "head_parts", "left_hind_leg", "right_hind_leg", "left_front_leg", "right_front_leg"):
        p = parts[g]["pivot"]
        if parts[g]["parent"]:                      # tail — от пивота body
            pp = parts[parts[g]["parent"]]["pivot"]
            p = [a + b for a, b in zip(p, pp)]
        x, y, z = (1.1 * c for c in p)
        y -= 2.4016
        groups[g] = (-x / 16, -z / 16, (24 - y) / 16, parts[g]["rot"][0])
    out = []
    for name, grp in HORSE_GROUP.items():
        src = saddle.get(name) if name in SADDLE_PARTS else parts[name]
        ox, oy, oz = src["origin"]
        w, hh, d = src["size"]
        inf = src["inflate"]
        if src["parent"] and src["parent"] not in groups and src["parent"] != grp:
            pass                                    # уши: родитель head с нулевым пивотом — тот же кадр, что head_parts
        lo = (ox - inf, oy - inf, oz - inf)
        hi = (ox + w + inf, oy + hh + inf, oz + d + inf)
        s = HORSE_S
        ext = (-hi[0] * s, -lo[0] * s, -hi[2] * s, -lo[2] * s, -hi[1] * s, -lo[1] * s)
        out.append(dict(name=name, group=grp, ext=ext, uv=tuple(src["uv"]), whd=(w, hh, d), rows=None,
                        mirror=bool(src.get("mirror")), tex="saddle" if name in SADDLE_PARTS else "horse"))
    return out, groups


# ---- текстуры -----------------------------------------------------------------------------------------------------
def upscale_png(src_bytes, factor=UPSCALE):
    from PIL import Image                           # Pillow — как у mc_textures.py (requirements.txt)
    im = Image.open(io.BytesIO(src_bytes)).convert("RGBA")
    if im.size != (TEX, TEX):
        sys.exit(f"текстура {im.size}, нужна {TEX}x{TEX}")
    im = im.resize((TEX * factor, TEX * factor), Image.NEAREST)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def jar_png(jar, path):
    with zipfile.ZipFile(jar) as z:
        return z.read(path)


# ---- шаги ---------------------------------------------------------------------------------------------------------
def tex_paths(t):
    return dict(xbm=f"{DEPOT}\\tex\\{t}.xbm", mi=f"{DEPOT}\\tex\\{t}.w2mi")


def step_prepare(a):
    with open(MODELS_JSON, encoding="utf-8") as f:
        models = json.load(f)
    ws = bb.workspace_dir(a.work)
    if os.path.isdir(os.path.join(ws, DEPOT)):
        shutil.rmtree(os.path.join(ws, DEPOT))
    jar = a.jar
    skin = a.skin or (SKIN if os.path.isfile(SKIN) else None)
    print(f"prepare: jar {jar}, скин {skin or 'Стив из jar (' + models['assets']['steve'] + ')'}")
    sources = {"skin": open(skin, "rb").read() if skin else jar_png(jar, models["assets"]["steve"]),
               "horse": jar_png(jar, f"assets/minecraft/textures/entity/horse/horse_{a.horse_coat}.png"),
               "saddle": jar_png(jar, models["assets"]["saddle_texture"])}
    textures = {}
    for t, data in sources.items():
        png = os.path.join(a.work, "src", f"{t}.png")
        bb.write(png, upscale_png(data))
        p = tex_paths(t)
        bb.write(bb.depot_file(a.work, p["mi"]), bb.make_material(p["xbm"], masked=True))
        textures[t] = dict(p, png=png, texgroup="WorldDiffuseWithAlpha")
    manifest = {"textures": textures, "steve": [], "horse": [], "horse_groups": {}}
    for part in steve_parts():
        mesh = f"{DEPOT}\\steve\\{part['name']}.w2mesh"
        ent = f"{DEPOT}\\steve\\{part['name']}.w2ent"
        e = part["ext"]
        bb.write(bb.depot_file(a.work, mesh), make_box_mesh(tex_paths(part["tex"])["mi"], e, part["uv"], part["whd"],
                                                            part["rows"]))
        bb.write(bb.depot_file(a.work, ent), make_part_entity(mesh, "steve_" + part["name"], e))
        manifest["steve"].append(dict(part, ent=ent, mesh=mesh))
    hparts, groups = horse_parts(models)
    for part in hparts:
        mesh = f"{DEPOT}\\horse\\{part['name']}.w2mesh"
        ent = f"{DEPOT}\\horse\\{part['name']}.w2ent"
        e = part["ext"]
        bb.write(bb.depot_file(a.work, mesh), make_box_mesh(tex_paths(part["tex"])["mi"], e, part["uv"], part["whd"],
                                                            None, part["mirror"]))
        bb.write(bb.depot_file(a.work, ent), make_part_entity(mesh, "horse_" + part["name"], e))
        manifest["horse"].append(dict(part, ent=ent, mesh=mesh))
    manifest["horse_groups"] = {g: list(v) for g, v in groups.items()}
    bb.write(os.path.join(a.work, "models.json"), json.dumps(manifest, indent=1).encode())
    print(f"prepare: {len(manifest['steve'])} частей Стива, {len(manifest['horse'])} частей лошади ({a.horse_coat}), "
          f"{len(textures)} текстуры")


def step_import(a):
    with open(os.path.join(a.work, "models.json"), encoding="utf-8") as f:
        m = json.load(f)
    for t, p in m["textures"].items():
        out = bb.depot_file(a.work, p["xbm"])
        if os.path.exists(out):
            os.remove(out)
        bb.wcc(a, f"import_{t}", ["import", "-depot=local", f"-file={p['png']}", f"-out={out}",
                                  f"-texturegroup={p['texgroup']}"])
        if not os.path.isfile(out):
            sys.exit(f"import {t}: нет {out}")


def step_verify(a):
    """Бандлы читаются (crc), все наши w2ent/w2mesh/w2mi/xbm в бандлах, импорты CR2W ведут в мод или в игру, xbm — в
    texture.cache. -> True, если есть проблемы."""
    import w3bundle
    content = bb.out_content(a.work, a.out)
    names, files, bad = set(), {}, []
    for fn in sorted(os.listdir(content)):
        if fn.endswith(".bundle"):
            _, items = w3bundle.read_table(os.path.join(content, fn))
            for it in items:
                data, ok = w3bundle.extract(os.path.join(content, fn), it)
                if not ok:
                    bad.append(f"{fn}: crc {it['name']}")
                names.add(it["name"].lower())
                files[it["name"].lower()] = data
    vanilla = bb.vanilla_index()
    for name, data in sorted(files.items()):
        if data.startswith(b"CR2W"):
            for ref, _ in bb.iter_cr2w_imports(data):
                if ref.lower() not in files and ref.lower() not in vanilla:
                    bad.append(f"{name}: импорт {ref} нигде не найден")
    with open(os.path.join(a.work, "models.json"), encoding="utf-8") as f:
        m = json.load(f)
    want = [p[k] for p in m["steve"] + m["horse"] for k in ("ent", "mesh")]
    want += [p["mi"] for p in m["textures"].values()]
    missing = [w for w in want if w.lower() not in names]
    _, _, cached = bb.texture_cache_names(os.path.join(content, "texture.cache"))
    cached = {c.lower() for c in cached}
    nocache = [p["xbm"] for p in m["textures"].values() if p["xbm"].lower() not in cached]
    for w in missing:
        print("нет в бандлах:", w)
    for w in nocache:
        print("нет в texture.cache:", w)
    for w in bad:
        print(w)
    ok = not missing and not nocache and not bad
    print("verify:", "OK" if ok else "FAIL", f"({len(want)} файлов, {len(names)} в бандлах)")
    return not ok


STEPS = {"prepare": step_prepare, "import": step_import, "cook": bb.step_cook, "caches": bb.step_caches,
         "pack": bb.step_pack, "verify": step_verify}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Minecraft box models (Steve skin, horse) -> Witcher 3 mod via wcc_lite")
    ap.add_argument("step", choices=list(STEPS) + ["all"])
    ap.add_argument("--work", default=WORK)
    ap.add_argument("--out", default=None, help="куда класть мод: <out>\\modMCWModels\\content (по умолчанию <work>\\out)")
    ap.add_argument("--skin", default=None, help="PNG 64x64 (wide); по умолчанию assets_local\\skins\\geralt.png, "
                    "если он есть, иначе Стив из jar")
    ap.add_argument("--jar", default=mc_textures.JAR_DEFAULT, help="клиентский jar Minecraft 1.21.11")
    ap.add_argument("--horse-coat", default="chestnut",
                    choices=("white", "creamy", "chestnut", "brown", "black", "gray", "darkbrown"))
    ap.add_argument("--platform", default="pc")
    ap.add_argument("--compression", default="LZ4HC", choices=("LZ4HC", "LZ4", "ZLIB"))
    ap.add_argument("--keep-going", action="store_true")
    ap.add_argument("--allow-wcc", action="store_true")
    a = ap.parse_args(argv)
    bb.MOD = MOD                                    # out_content, pack и verify — в modMCWModels
    bb.refuse_work_dir(a.work)
    if a.out:
        bb.refuse_work_dir(a.out)
    steps = list(STEPS) if a.step == "all" else [a.step]
    for s in steps:
        r = STEPS[s](a)
        if s == "verify" and r:
            sys.exit(1)


if __name__ == "__main__":
    main()
