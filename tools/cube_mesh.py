"""Меш куба 1x1x1 м для блоков Minecraft в Witcher 3 и раскладка атласа граней. Только stdlib.

Раскладка (общая для всех блоков, её же использует mc_textures.py): атлас 4x2 клетки по cell_px (по умолчанию
256 -> 1024x512, степени двойки). Клетки выровнены по cell_px, поэтому box-мипы не смешивают грани вплоть до мипа,
где клетка становится 1 px.

    +-------+-------+-------+-------+
    | north | west  | south | east  |   ряд 0: боковые грани в порядке обхода, стыки бесшовные
    +-------+-------+-------+-------+          (правый край каждой переходит в левый край следующей)
    |  up   | down  | (S)   | (E)   |   ряд 1: верх, низ и две запасные клетки - зеркальные по вертикали
    +-------+-------+-------+-------+          копии south и east, чтобы мипы не тянули чужой цвет в их нижний край

Грани названы по-майнкрафтовски. Картинка каждой грани ориентирована так, как её видно снаружи блока в Minecraft
(для текстур, которые модель кладёт с uv [0,0,16,16] без поворота, это сама PNG без изменений):
боковые - верх картинки вверху, up - верх картинки к северу, down - верх картинки к югу; право у up и down - восток.

Оси. Minecraft: X восток, Y вверх, Z юг. Ведьмак: метры, X восток, Y север, Z вверх (spike/coords.py).
Точка блока MC (0..1) -> меш Ведьмака с pivot в центре (как у cont_crate, spike/templates.json "pivot": "center"):
    (x - 0.5, 0.5 - z, y - 0.5)
Грань north (MC -Z) смотрит в +Y Ведьмака, south в -Y, east в +X, west в -X, up в +Z, down в -Z.
Это поворот без отражения, поэтому текстуры не зеркалятся. Обход треугольников против часовой снаружи.

UV в файлах: OBJ и FBX считают V снизу вверх, поэтому v = 1 - v_картинки. Отступ inset_px (по умолчанию 0.5 px
клетки) убирает билинейное смешивание с соседней клеткой на мипе 0; gutter_px - рамка в клетке атласа, если атлас
собран с mc_textures.py --gutter-px (тогда и меш с тем же числом).

Запуск:
  python cube_mesh.py [--out DIR] [--cell-px 256] [--gutter-px 0] [--inset-px 0.5] [--fbx-units m|cm]
Пишет mcw_cube_atlas.{obj,mtl,fbx} (раскладка 4x2) и mcw_cube_uniform.{obj,mtl,fbx} (все грани на всю текстуру:
для блоков, у которых 6 граней одинаковые, manifest "uniform": true). По умолчанию в %LOCALAPPDATA%\\mcw\\tex\\mesh;
внутрь проекта, папки игры и .minecraft писать отказывается.
"""
import argparse
import os
import sys

COLS, ROWS = 4, 2
CELLS = {"north": (0, 0), "west": (1, 0), "south": (2, 0), "east": (3, 0), "up": (0, 1), "down": (1, 1)}
SPARE = {(2, 1): (2, 0), (3, 1): (3, 0)}  # запасная клетка -> клетка, чью копию (FLIP_TOP_BOTTOM) в неё кладём
FACES = tuple(CELLS)

# Углы грани в блоке MC (0..1) в порядке TL, BL, BR, TR картинки, если смотреть на грань снаружи.
# Соответствует uv по умолчанию у Minecraft: down u=x v=1-z, up u=x v=z, north u=1-x, south u=x, west u=z,
# east u=1-z, у боковых v=1-y.
MC_CORNERS = {
    "north": ((1, 1, 0), (1, 0, 0), (0, 0, 0), (0, 1, 0)),
    "west": ((0, 1, 0), (0, 0, 0), (0, 0, 1), (0, 1, 1)),
    "south": ((0, 1, 1), (0, 0, 1), (1, 0, 1), (1, 1, 1)),
    "east": ((1, 1, 1), (1, 0, 1), (1, 0, 0), (1, 1, 0)),
    "up": ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)),
    "down": ((0, 0, 1), (0, 0, 0), (1, 0, 0), (1, 0, 1)),
}
MC_NORMAL = {"north": (0, 0, -1), "west": (-1, 0, 0), "south": (0, 0, 1), "east": (1, 0, 0),
             "up": (0, 1, 0), "down": (0, -1, 0)}
CELL_UV = ((0, 0), (0, 1), (1, 1), (1, 0))  # TL, BL, BR, TR в клетке, V вниз


def mc_to_w(p):
    """Точка блока MC (0..1) -> координаты меша Ведьмака (м), pivot в центре."""
    x, y, z = p
    return (x - 0.5, 0.5 - z, y - 0.5)


def mc_dir_to_w(v):
    x, y, z = v
    return (x, -z, y)


def layout_info(cell_px=256, gutter_px=0):
    return {
        "name": "atlas4x2",
        "cols": COLS, "rows": ROWS, "cell_px": cell_px, "gutter_px": gutter_px,
        "size": [COLS * cell_px, ROWS * cell_px],
        "cells": {f: list(c) for f, c in CELLS.items()},
        "spare": {f"{c},{r}": f"copy of cell {sc},{sr} flipped vertically" for (c, r), (sc, sr) in SPARE.items()},
        "face_orientation": "as seen from outside in Minecraft; up: image top = north; down: image top = south",
        "axes": "Witcher: X east, Y north, Z up, metres; MC north face -> +Y, up -> +Z",
        "mesh": "tools/cube_mesh.py mcw_cube_atlas.obj/.fbx",
    }


def face_quads(layout="atlas", cell_px=256, gutter_px=0, inset_px=0.5):
    """-> [dict(face, normal, corners, uv)]: corners - 4 точки меша Ведьмака (TL, BL, BR, TR), uv - доли текстуры,
    V вниз (как в картинке). layout: "atlas" (4x2) или "uniform" (каждая грань на всю текстуру)."""
    if layout == "atlas":
        cols, rows, cells = COLS, ROWS, CELLS
    elif layout == "uniform":
        cols, rows, cells = 1, 1, {f: (0, 0) for f in FACES}
        gutter_px = 0
    else:
        raise ValueError(layout)
    e = (gutter_px + inset_px) / cell_px
    if not 0 <= e < 0.5:
        raise ValueError(f"inset+gutter {gutter_px + inset_px} px is too big for cell {cell_px}")
    out = []
    for f in FACES:
        col, row = cells[f]
        uv = [((col + (e if fu == 0 else 1 - e)) / cols, (row + (e if fv == 0 else 1 - e)) / rows)
              for fu, fv in CELL_UV]
        out.append(dict(face=f, normal=mc_dir_to_w(MC_NORMAL[f]), corners=[mc_to_w(p) for p in MC_CORNERS[f]],
                        uv=uv))
    return out


TRIS = ((0, 1, 2), (0, 2, 3))  # квад TL, BL, BR, TR -> два треугольника, против часовой снаружи


def _f(x):
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def obj_text(quads, name, mtl_name, material="mcw_block"):
    lines = ["# Minecraft block cube for Witcher 3 (mcw), generated by tools/cube_mesh.py",
             "# metres, Z up (Witcher axes), pivot at centre; vt v is bottom-up (OBJ), image v = 1 - vt.v",
             f"mtllib {mtl_name}", f"o {name}"]
    for q in quads:
        lines += [f"v {_f(x)} {_f(y)} {_f(z)}" for x, y, z in q["corners"]]
    for q in quads:
        lines += [f"vt {_f(u)} {_f(1 - v)}" for u, v in q["uv"]]
    lines += [f"vn {_f(x)} {_f(y)} {_f(z)}" for x, y, z in (q["normal"] for q in quads)]
    lines += [f"usemtl {material}", "s off"]
    for i, q in enumerate(quads):
        for t in TRIS:
            lines.append("f " + " ".join(f"{4 * i + k + 1}/{4 * i + k + 1}/{i + 1}" for k in t))
    return "\n".join(lines) + "\n"


def mtl_text(texture, material="mcw_block"):
    return (f"# texture: any atlas from mc_textures.py (atlas/<block>.png, face/<block>.png for uniform)\n"
            f"newmtl {material}\n"
            f"Ka 0 0 0\nKd 1 1 1\nKs 0 0 0\nd 1\nillum 1\nmap_Kd {texture}\n")


def _arr(vals):
    return ",".join(_f(v) if isinstance(v, float) else str(v) for v in vals)


def fbx_text(quads, name, units="m", material="mcw_block"):
    """FBX 7.4 ASCII: одна модель, одна геометрия (треугольники), нормали по вершинам полигона, UV, один материал.
    Оси как у 3ds Max (Z вверх, правая тройка) = оси Ведьмака. units="m": координаты в метрах, UnitScaleFactor 100;
    "cm": координаты x100, UnitScaleFactor 1."""
    k = 100.0 if units == "cm" else 1.0
    usf = 1 if units == "cm" else 100
    verts = [c * k for q in quads for p in q["corners"] for c in p]
    pvi, normals, uvidx = [], [], []
    for i, q in enumerate(quads):
        for t in TRIS:
            idx = [4 * i + j for j in t]
            pvi += idx[:2] + [~idx[2]]
            uvidx += idx
            normals += [float(c) for c in q["normal"]] * 3
    uvs = [c for q in quads for u, v in q["uv"] for c in (u, 1.0 - v)]
    g, m, mat = 2000000001, 3000000001, 4000000001
    axes = [("UpAxis", 2), ("UpAxisSign", 1), ("FrontAxis", 1), ("FrontAxisSign", -1), ("CoordAxis", 0),
            ("CoordAxisSign", 1), ("OriginalUpAxis", 2), ("OriginalUpAxisSign", 1)]
    gs = "\n".join(f'\t\tP: "{n}", "int", "Integer", "",{v}' for n, v in axes)
    return f"""; FBX 7.4.0 project file
; Minecraft block cube for Witcher 3 (mcw), generated by tools/cube_mesh.py
; ----------------------------------------------------

FBXHeaderExtension:  {{
\tFBXHeaderVersion: 1003
\tFBXVersion: 7400
\tCreator: "mcw tools/cube_mesh.py"
}}
GlobalSettings:  {{
\tVersion: 1000
\tProperties70:  {{
{gs}
\t\tP: "UnitScaleFactor", "double", "Number", "",{usf}
\t\tP: "OriginalUnitScaleFactor", "double", "Number", "",{usf}
\t}}
}}
Documents:  {{
\tCount: 1
\tDocument: 1000000001, "", "Scene" {{
\t\tRootNode: 0
\t}}
}}
References:  {{
}}
Definitions:  {{
\tVersion: 100
\tCount: 4
\tObjectType: "GlobalSettings" {{
\t\tCount: 1
\t}}
\tObjectType: "Model" {{
\t\tCount: 1
\t}}
\tObjectType: "Geometry" {{
\t\tCount: 1
\t}}
\tObjectType: "Material" {{
\t\tCount: 1
\t}}
}}
Objects:  {{
\tGeometry: {g}, "Geometry::{name}", "Mesh" {{
\t\tVertices: *{len(verts)} {{
\t\t\ta: {_arr(verts)}
\t\t}}
\t\tPolygonVertexIndex: *{len(pvi)} {{
\t\t\ta: {_arr(pvi)}
\t\t}}
\t\tGeometryVersion: 124
\t\tLayerElementNormal: 0 {{
\t\t\tVersion: 101
\t\t\tName: ""
\t\t\tMappingInformationType: "ByPolygonVertex"
\t\t\tReferenceInformationType: "Direct"
\t\t\tNormals: *{len(normals)} {{
\t\t\t\ta: {_arr(normals)}
\t\t\t}}
\t\t}}
\t\tLayerElementUV: 0 {{
\t\t\tVersion: 101
\t\t\tName: "UVMap"
\t\t\tMappingInformationType: "ByPolygonVertex"
\t\t\tReferenceInformationType: "IndexToDirect"
\t\t\tUV: *{len(uvs)} {{
\t\t\t\ta: {_arr(uvs)}
\t\t\t}}
\t\t\tUVIndex: *{len(uvidx)} {{
\t\t\t\ta: {_arr(uvidx)}
\t\t\t}}
\t\t}}
\t\tLayerElementMaterial: 0 {{
\t\t\tVersion: 101
\t\t\tName: ""
\t\t\tMappingInformationType: "AllSame"
\t\t\tReferenceInformationType: "IndexToDirect"
\t\t\tMaterials: *1 {{
\t\t\t\ta: 0
\t\t\t}}
\t\t}}
\t\tLayer: 0 {{
\t\t\tVersion: 100
\t\t\tLayerElement:  {{
\t\t\t\tType: "LayerElementNormal"
\t\t\t\tTypedIndex: 0
\t\t\t}}
\t\t\tLayerElement:  {{
\t\t\t\tType: "LayerElementUV"
\t\t\t\tTypedIndex: 0
\t\t\t}}
\t\t\tLayerElement:  {{
\t\t\t\tType: "LayerElementMaterial"
\t\t\t\tTypedIndex: 0
\t\t\t}}
\t\t}}
\t}}
\tModel: {m}, "Model::{name}", "Mesh" {{
\t\tVersion: 232
\t\tProperties70:  {{
\t\t\tP: "Lcl Translation", "Lcl Translation", "", "A",0,0,0
\t\t\tP: "Lcl Rotation", "Lcl Rotation", "", "A",0,0,0
\t\t\tP: "Lcl Scaling", "Lcl Scaling", "", "A",1,1,1
\t\t}}
\t\tShading: T
\t\tCulling: "CullingOff"
\t}}
\tMaterial: {mat}, "Material::{material}", "" {{
\t\tVersion: 102
\t\tShadingModel: "phong"
\t\tMultiLayer: 0
\t\tProperties70:  {{
\t\t\tP: "DiffuseColor", "Color", "", "A",1,1,1
\t\t}}
\t}}
}}
Connections:  {{
\tC: "OO",{m},0
\tC: "OO",{g},{m}
\tC: "OO",{mat},{m}
}}
"""


# ---- куда можно писать --------------------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # minecraft-witcher3
DEFAULT_OUT = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "mcw", "tex")


def _inside(path, root):
    p, r = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(root))
    try:
        return os.path.commonpath([p, r]) == r
    except ValueError:  # разные диски
        return False


def out_dir_problem(out):
    """None, если в out можно писать; иначе причина. Нельзя: проект, папка Ведьмака, любая .minecraft."""
    from w3bundle import game_root_of
    if _inside(out, PROJECT_ROOT):
        return (f"{out} is inside the project {PROJECT_ROOT}: textures derived from Mojang assets "
                "and generated files stay outside (default %LOCALAPPDATA%\\mcw\\tex)")
    root = game_root_of(out)
    if root:
        return f"{out} is inside the game folder {root}"
    parts = os.path.normcase(os.path.realpath(out)).split(os.sep)
    if ".minecraft" in parts:
        return f"{out} is inside a .minecraft folder (the jar is read-only input)"
    return None


def refuse_out_dir(out):
    why = out_dir_problem(out)
    if why:
        sys.exit("refuse: " + why)


def write_meshes(out, cell_px=256, gutter_px=0, inset_px=0.5, units="m"):
    refuse_out_dir(out)
    os.makedirs(out, exist_ok=True)
    written = []
    for layout in ("atlas", "uniform"):
        quads = face_quads(layout, cell_px, gutter_px, inset_px)
        base = f"mcw_cube_{layout}"
        tex = "../atlas/grass_block.png" if layout == "atlas" else "../face/stone.png"  # для просмотрщика OBJ
        for ext, text in (("obj", obj_text(quads, base, base + ".mtl")), ("mtl", mtl_text(tex)),
                          ("fbx", fbx_text(quads, base, units))):
            p = os.path.join(out, f"{base}.{ext}")
            with open(p, "w", encoding="utf-8", newline="\n") as fo:
                fo.write(text)
            written.append(p)
    return written


def main(argv=None):
    ap = argparse.ArgumentParser(description="Cube mesh (OBJ + FBX ASCII) for Minecraft blocks in Witcher 3")
    ap.add_argument("--out", default=os.path.join(DEFAULT_OUT, "mesh"))
    ap.add_argument("--cell-px", type=int, default=256)
    ap.add_argument("--gutter-px", type=int, default=0)
    ap.add_argument("--inset-px", type=float, default=0.5)
    ap.add_argument("--fbx-units", choices=("m", "cm"), default="m")
    a = ap.parse_args(argv)
    for p in write_meshes(a.out, a.cell_px, a.gutter_px, a.inset_px, a.fbx_units):
        print(p)


if __name__ == "__main__":
    main()
