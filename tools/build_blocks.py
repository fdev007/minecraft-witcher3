"""Конвейер блоков Minecraft для Witcher 3 5.00c через REDkit без GUI: исходники -> wcc_lite -> мод modMCWBlocks.

Шаги (по отдельности или `all`):
  prepare  депо мода (project\\workspace\\) из шаблонов депо REDkit (r4data) + PNG-атласы:
             mcw\\blocks\\<b>\\<b>.w2mi   из ...\\floor_brick.w2mi: pbr_std, Diffuse = <b>.xbm (Normal по умолчанию плоский)
             mcw\\blocks\\<b>\\<b>.w2mesh из engine\\meshes\\editor\\box.w2mesh: куб 1 м, pivot в центре, UV граней
                                         переложены в раскладку атласа 4x2 (cube_mesh.face_quads), локальный
                                         материал -> base = <b>.w2mi, коллизия box 0.5 со своим physmat;
                                         у cutout (листва, стекло) enableMask и в w2mi, и в локальном материале
             mcw\\blocks\\<b>.w2ent      из sq204 box_05_05_1m.w2ent: один CStaticMeshComponent, isStreamed=false,
                                         видимый с тенью, transform = identity, bbox +-0.5
           PNG кладётся в src\\<b>.png (вход для import).
  import   wcc_lite import -depot=local -file=src\\<b>.png -out=<абсолютный путь в workspace>\\<b>.xbm (WorldDiffuse)
  cook     wcc_lite cook -platform=pc -mod=project\\workspace\\ -outdir=cooked\\  (+ cook.db)
  caches   wcc_lite buildcache textures|physics -db=cooked\\cook.db -> out\\<mod>\\content\\texture.cache, collision.cache
  pack     wcc_lite pack -> out\\<mod>\\content\\blob0.bundle + buffers0.bundle; metadatastore -> metadata.store
  verify   своими инструментами: бандлы читаются (w3bundle, crc), CR2W валиден (cr2w), все импорты (и во вложенных
           CR2W) ведут в мод или в бандлы игры, в texture.cache есть все xbm (opaque - DXT1, cutout - DXT5), в
           collision.cache - все меши; у cutout enableMask в w2mi и в материале меша

Рабочая папка <work> = %LOCALAPPDATA%\\mcw\\redkit_work:
  src\\ (PNG), project\\workspace\\ (депо мода = workspace REDkit), uncook\\ (пустое uncook-депо), cooked\\, out\\<mod>\\,
  logs\\ (лог и stdout каждого вызова wcc), wccroot\\ (cwd для wcc), userdir\\ (его «Документы»).
Как wcc_lite видит наш депо (docs/REDKIT.md): `-workspaceDir <work>\\project\\ -uncookDir <work>\\uncook\\` ЧЕРЕЗ ПРОБЕЛ
(с `=` ключи молча игнорируются), файлы мода - в project\\workspace\\. cwd = wccroot\\bin\\x64_RedKit: wcc читает
`..\\gameconf.cfg` от cwd и пишет туда `..\\wcc.log`; в нашей копии gameconf `title` ведёт в <work>\\userdir, иначе wcc
пишет в Documents\\The Witcher 3 (editoruser.settings, profile.settings). Каждый вызов сверяет снимки «Документов»
(верхний уровень + каждый файл Documents\\The Witcher 3: размер, mtime, sha256) и верхних уровней REDkit до и после
и печатает WARNING; изменились «Документы» - конвейер стоп. Исключение одно: scriptslog.txt, если witcher3.exe
запущен при обоих снимках (его пишет игра). Сам wcc при старте пишет r4data\\x64.release.redscripts
(скрипты), слушает TCP 37000 и пробует 37010; без -agreetoterms открывает окно EULA, поэтому флаг всегда.

Запуск (шаги с wcc_lite - только с явным --allow-wcc; prepare и verify wcc не зовут):
  python build_blocks.py all --allow-wcc [--blocks stone,dirt] [--test-png] [--compression LZ4HC|ZLIB|LZ4]
                                         [--out <папка>]   (мод -> <out>\\<mod>\\content вместо <work>\\out)
  python build_blocks.py prepare|verify ...;   python build_blocks.py import|cook|caches|pack --allow-wcc ...
  python build_blocks.py wcc --allow-wcc -- <командлет> <ключи>      (любой командлет в той же обвязке)
В папку игры, в REDkit и в проект сам не пишет: всё производное от игры живёт в <work>.
"""
import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import uuid
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collcache  # noqa: E402
import cr2w  # noqa: E402
import cube_mesh  # noqa: E402
import w3bundle  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "spike"))
import w3paths  # noqa: E402  (spike\w3paths.py: где стоят игра, REDkit, Minecraft)

REDKIT = w3paths.redkit_dir() or r"C:\Program Files (x86)\Steam\steamapps\common\The Witcher 3 REDkit"
GAME = w3paths.game_dir() or r"C:\Program Files (x86)\Steam\steamapps\common\The Witcher 3"
LOCAL = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
WORK = os.path.join(LOCAL, "mcw", "redkit_work")
TEX = os.path.join(LOCAL, "mcw", "tex")
MOD = "modMCWBlocks"
DEPOT_DIR = r"mcw\blocks"

SRC_MI = r"environment\textures_tileable\common_materials\novigrad_poor\floor_brick.w2mi"
SRC_MESH = r"engine\meshes\editor\box.w2mesh"
SRC_ENT = r"quests\sidequests\skellige\quest_files\sq204_forest_spirit\entities\box_05_05_1m.w2ent"

# как MVP в mc_textures.py + серый куб; oak_leaves и glass - cutout (masked-материал)
MVP = ("grass_block", "dirt", "stone", "cobblestone", "oak_log", "oak_planks", "sand", "crafting_table",
       "oak_leaves", "glass", "_unknown")
# physmat коллизии; stone_solid и wood_solid подтверждены в ванильном collision.cache, остальное [проверить]
PHYSMAT = {"stone": "stone_solid", "cobblestone": "stone_solid", "oak_log": "wood_solid", "oak_planks": "wood_solid",
           "crafting_table": "wood_solid", "grass_block": "wood_solid", "dirt": "wood_solid", "sand": "wood_solid"}
DEFAULT_PHYSMAT = "wood_solid"

VSTRIDE, V_POS, V_NRM, V_UV = 152, 0, 32, 48  # сырая вершина uncooked w2mesh (MVT_StaticMesh), см. docs/REDKIT.md


def r4(depot_path):
    with open(os.path.join(REDKIT, "r4data", depot_path), "rb") as f:
        return f.read()


def paths(block):
    b = block.lower()
    d = f"{DEPOT_DIR}\\{b}"
    return dict(xbm=f"{d}\\{b}.xbm", mi=f"{d}\\{b}.w2mi", mesh=f"{d}\\{b}.w2mesh", ent=f"{DEPOT_DIR}\\{b}.w2ent")


# ---- CR2W из шаблонов ------------------------------------------------------------------------------------------
def make_material(xbm_path, masked=False):
    """w2mi: pbr_std, единственный параметр Diffuse -> xbm (Normal и прочее - значения графа по умолчанию).
    masked: enableMask=true - альфа-тест по альфе диффуза (вход Mask графа pbr_std = Alpha сэмплера Diffuse), так в
    ванили сделаны паутина и корни на pbr_std (21 w2mi в r4data, 61 cooked в бандлах); для листвы и стекла Minecraft
    (cutout), текстура тогда WorldDiffuseWithAlpha (DXT5). Одного этого мало: см. make_mesh(masked)."""
    doc = cr2w.CR2WDoc(r4(SRC_MI))
    x = doc.exports[0]
    assert doc.imports[0][0].endswith("pbr_std.w2mg")
    doc.imports = [doc.imports[0], [xbm_path, doc.name_index("CBitmapTexture"), 0]]
    if masked:
        x["data"] = doc.set_prop(x["data"], "enableMask", b"\x01", "Bool")
    x["data"] = doc.set_material_params(x["data"], [("Diffuse", "handle:ITexture", struct.pack("<i", -2))])
    return doc.to_bytes()


def _buffer(doc, export_data, prop):
    """Внутренний буфер, на который указывает DeferredDataBuffer-свойство (u16 index)."""
    _, v = doc.get_prop(export_data, prop)
    idx = struct.unpack_from("<H", v)[0]
    return next(b for b in doc.buffers if b["index"] == idx)


def face_uv_for(pos, nrm, quads):
    """UV (V вниз, доли текстуры) для вершины куба: грань по нормали, угол - ближайший к позиции."""
    key = tuple(round(c) for c in nrm)
    q = next(q for q in quads if tuple(round(c) for c in q["normal"]) == key)
    k = min(range(4), key=lambda i: sum((a - b) ** 2 for a, b in zip(q["corners"][i], pos)))
    return q["uv"][k]


def make_mesh(mi_path, physmat, quads, auto_hide=None, masked=False):
    """w2mesh из box.w2mesh: UV из quads, материал -> base mi_path, physmat коллизии.
    masked: enableMask=true и у локального материала. Чанк рисуется с материалом меша, а
    CMaterialInstance::IsMasked() (editor.exe) = граф isMasked && (!canOverrideMasked || свой m_enableMask):
    флаг базового w2mi не наследуется, а у pbr_std canOverrideMasked=1. Ванильные меши так и делают: 97 из 138
    локальных экземпляров с base = masked w2mi повторяют enableMask (остальные 502 ссылки - сразу на w2mi)."""
    doc = cr2w.CR2WDoc(r4(SRC_MESH))
    cls = [doc.names[x["cls"]] for x in doc.exports]
    assert cls == ["CMesh", "CMaterialInstance", "CCollisionMesh", "CCollisionShapeBox"], cls
    mesh, mat, _, shape = doc.exports
    # материал: локальный экземпляр с base = наш w2mi, без своих параметров
    doc.imports = [[mi_path, doc.name_index("CMaterialInstance"), 0]]
    mat["data"] = doc.set_prop(mat["data"], "baseMaterial", struct.pack("<i", -1))
    if masked:
        mat["data"] = doc.set_prop(mat["data"], "enableMask", b"\x01", "Bool")
    mat["data"] = doc.set_material_params(mat["data"], [])
    shape["data"] = doc.set_prop(shape["data"], "physicalMaterialName", struct.pack("<H", doc.name_index(physmat)))
    if auto_hide:
        mesh["data"] = doc.set_prop(mesh["data"], "autoHideDistance", struct.pack("<f", auto_hide))
    # UV: rawVertices и копия вершин в chunksBuffer
    raw = _buffer(doc, mesh["data"], "rawVertices")
    chunks = _buffer(doc, mesh["data"], "chunksBuffer")
    vb, cb = bytearray(raw["data"]), bytearray(chunks["data"])
    n = len(vb) // VSTRIDE
    assert n * VSTRIDE == len(vb) and n == 24, n
    for k in range(n):
        rec = bytes(vb[k * VSTRIDE:(k + 1) * VSTRIDE])
        pos = struct.unpack_from("<3f", rec, V_POS)
        nrm = struct.unpack_from("<3f", rec, V_NRM)
        new = rec[:V_UV] + struct.pack("<2f", *face_uv_for(pos, nrm, quads)) + rec[V_UV + 8:]
        hits = [i for i in range(len(chunks["data"]) - VSTRIDE + 1) if chunks["data"][i:i + VSTRIDE] == rec]
        assert len(hits) == 1, f"vertex {k}: {len(hits)} copies in chunksBuffer"
        vb[k * VSTRIDE:(k + 1) * VSTRIDE] = new
        cb[hits[0]:hits[0] + VSTRIDE] = new
    raw["data"], chunks["data"] = bytes(vb), bytes(cb)
    return doc.to_bytes()


def w3string(s):
    b = s.encode("ascii")
    assert len(b) < 64
    return bytes([0x80 | len(b)]) + b


def _patch_floats(doc, value, path_vals):
    """Значение-структура (Box/Vector): заменить Float-поля по путям ('Min','X') -> float, на месте."""
    out = bytearray(value)
    for path, val in path_vals.items():
        base, v = 0, bytes(value)
        for i, key in enumerate(path):
            rec = next(r for r in doc.prop_records(v)[0] if r[0] == key)
            if i == len(path) - 1:
                assert rec[1] == "Float"
                out[base + rec[3]:base + rec[4]] = struct.pack("<f", val)
            else:
                base += rec[3]
                v = v[rec[3]:rec[4]]
    return bytes(out)


def _patch_component(doc, x, mesh_path, name, guid):
    doc.set_import_path(SRC_MESH, mesh_path)
    d = x["data"]
    d = doc.set_prop(d, "drawableFlags", struct.pack("<HHH", doc.name_index("DF_IsVisible"),
                                                     doc.name_index("DF_CastShadows"), 0))
    _, box = doc.get_prop(d, "boundingBox")
    box = _patch_floats(doc, box, {(m, a): (-0.5 if m == "Min" else 0.5) for m in ("Min", "Max") for a in "XYZ"})
    d = doc.set_prop(d, "boundingBox", box)
    d = doc.set_prop(d, "transform", b"\x00")  # EngineTransform без pos/rot/scale = identity
    d = doc.set_prop(d, "isStreamed", b"\x00")
    d = doc.set_prop(d, "name", w3string(name))
    d = doc.set_prop(d, "guid", guid)
    x["data"] = d


def make_entity(mesh_path, block):
    """w2ent из box_05_05_1m: компонент правится и в entityObject, и во flatCompiledData."""
    doc = cr2w.CR2WDoc(r4(SRC_ENT))
    tpl, _, comp = doc.exports
    assert doc.names[comp["cls"]] == "CStaticMeshComponent"
    guid = uuid.uuid5(uuid.NAMESPACE_URL, "mcw/blocks/" + block).bytes  # стабильный между сборками
    name = f"mcw_{block}"
    _patch_component(doc, comp, mesh_path, name, guid)
    flat = doc.get_nested(tpl["data"], "flatCompiledData")
    fcomp = flat.exports[1]
    assert flat.names[fcomp["cls"]] == "CStaticMeshComponent"
    _patch_component(flat, fcomp, mesh_path, name, guid)
    tpl["data"] = doc.set_nested(tpl["data"], "flatCompiledData", flat)
    return doc.to_bytes()


# ---- тестовая картинка (stdlib PNG) ----------------------------------------------------------------------------
TEST_COLORS = {"north": (200, 40, 40), "west": (40, 160, 40), "south": (40, 60, 200), "east": (220, 200, 40),
               "up": (240, 240, 240), "down": (30, 30, 30)}


def png_bytes(w, h, pixel):
    rows = b"".join(b"\0" + bytes(c for x in range(w) for c in pixel(x, y)) for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def test_atlas_png(cell=64):
    """Атлас 4x2 в раскладке cube_mesh: у каждой грани свой цвет и тёмный квадрат в левом верхнем углу картинки
    (по нему в игре видно ориентацию грани и направление V)."""
    cells = {v: k for k, v in cube_mesh.CELLS.items()}
    cells.update({k: cells[v] for k, v in cube_mesh.SPARE.items()})

    def px(x, y):
        face = cells[(x // cell, y // cell)]
        u, v = x % cell, y % cell
        if (x // cell, y // cell) in cube_mesh.SPARE:
            v = cell - 1 - v
        c = TEST_COLORS[face]
        return tuple(c_ // 3 for c_ in c) if u < cell // 4 and v < cell // 4 else c
    return png_bytes(cube_mesh.COLS * cell, cube_mesh.ROWS * cell, px)


# ---- рабочая папка ---------------------------------------------------------------------------------------------
def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def project_dir(work):
    return os.path.join(work, "project")


def workspace_dir(work):
    return os.path.join(project_dir(work), "workspace")


def depot_file(work, depot_path):
    return os.path.join(workspace_dir(work), depot_path)


def out_content(work, out_root=None):
    """<out_root или <work>\\out>\\<mod>\\content."""
    return os.path.join(out_root or os.path.join(work, "out"), MOD, "content")


def content_of(a):
    return out_content(a.work, getattr(a, "out", None))


def load_manifest(work):
    with open(os.path.join(work, "blocks.json"), encoding="utf-8") as f:
        return json.load(f)


# ---- обвязка wcc_lite -------------------------------------------------------------------------------------------
def documents_dir():
    """Настоящая папка «Документы» (может быть перенаправлена), как её видит движок (SHGetFolderPath CSIDL_PERSONAL)."""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:
            return buf.value
    except (AttributeError, OSError):
        pass
    return os.path.join(os.path.expanduser("~"), "Documents")


def ensure_wccroot(work):
    """<work>\\wccroot\\bin\\{gameconf.cfg, redscripts.ini} + cwd. В копии gameconf title = путь от «Документов» до
    <work>\\userdir: wcc_lite строит пользовательскую папку как Documents\\<title> и иначе пишет в
    Documents\\The Witcher 3 (editoruser.settings, profile.settings)."""
    root = os.path.join(work, "wccroot")
    cwd = os.path.join(root, "bin", "x64_RedKit")
    userdir = os.path.join(work, "userdir")
    for d in (cwd, userdir, os.path.join(work, "uncook"), workspace_dir(work)):
        os.makedirs(d, exist_ok=True)
    title = os.path.relpath(userdir, documents_dir())  # ValueError, если разные диски: тогда только вручную
    with open(os.path.join(REDKIT, "bin", "gameconf.cfg"), "rb") as f:
        conf = f.read()
    new = conf.replace(b'title "The Witcher 3"', b'title "' + title.encode("mbcs") + b'"')
    if new == conf:
        sys.exit("gameconf.cfg REDkit изменился: не нашёл title \"The Witcher 3\"")
    write(os.path.join(root, "bin", "gameconf.cfg"), new)
    shutil.copyfile(os.path.join(REDKIT, "bin", "redscripts.ini"), os.path.join(root, "bin", "redscripts.ini"))
    return root, cwd, userdir


# пишутся при каждом старте wcc (скрипты) и при первом без workspace (string db); см. docs/REDKIT.md
REDKIT_KNOWN_WRITES = {r"r4data\x64.release.redscripts", r"bin\LocalEditorStringDataBaseW3_UTF8_mod.db"}


def redkit_snapshot():
    """Содержимое <REDkit>, bin, bin\\x64_RedKit и r4data без рекурсии (весь r4data - 232 тыс. файлов): новая папка
    вроде bin\\mcw видна как новая запись. -> {путь: (size, mtime)}."""
    snap = {}
    for sub in ("", "bin", os.path.join("bin", "x64_RedKit"), "r4data"):
        base = os.path.join(REDKIT, sub)
        for n in os.listdir(base):
            p = os.path.join(base, n)
            st = os.stat(p)
            snap[os.path.join(sub, n)] = (st.st_size, 0 if os.path.isdir(p) else st.st_mtime_ns)
    return snap


def _sha256(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:  # scriptslog.txt держит запущенная игра
        return None


def docs_snapshot(root=None):
    """Снимок «Документов»: верхний уровень (имя -> mtime папки/файла) + каждый файл Documents\\The Witcher 3
    (кроме сейвов) -> (size, mtime_ns, sha256). Сравнение до/после вызова wcc ловит и запись без смены mtime."""
    root = root or documents_dir()
    snap = {}
    for n in os.listdir(root):
        snap[os.path.join(root, n)] = (0, os.stat(os.path.join(root, n)).st_mtime_ns, None)
    for dp, dns, fns in os.walk(os.path.join(root, "The Witcher 3")):
        dns[:] = [d for d in dns if d.lower() != "gamesaves"]
        for fn in fns:
            fp = os.path.join(dp, fn)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            snap[fp] = (st.st_size, st.st_mtime_ns, _sha256(fp))
    return snap


def snapshot_diff(before, after):
    """Пути, которые появились, пропали или изменились (размер, mtime или хеш)."""
    return sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))


GAME_EXE = "witcher3.exe"
# лог скриптов: его пишет запущенная игра (каждая строка exec в консоли), wcc пишет свой в <work>\userdir
GAME_LOG = os.path.join("The Witcher 3", "scriptslog.txt")


def game_running():
    """Запущен ли witcher3.exe (tasklist). Если проверить нельзя - False, т.е. scriptslog.txt сравнивается."""
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {GAME_EXE}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return f'"{GAME_EXE}"' in out.lower()


def docs_changes(before, after, game_before, game_after, root=None):
    """-> (изменённые пути, изменился ли scriptslog.txt). scriptslog.txt не считается изменением, только если игра
    шла и при снимке до вызова wcc, и при снимке после; всё остальное в «Документах» сравнивается всегда."""
    game_log = os.path.normcase(os.path.join(root or documents_dir(), GAME_LOG))
    changed = snapshot_diff(before, after)
    log_changed = any(os.path.normcase(p) == game_log for p in changed)
    if game_before and game_after:
        changed = [p for p in changed if os.path.normcase(p) != game_log]
    return changed, log_changed


def workspace_args(work):
    # только через пробел: с "=" wcc их не видит и пишет новые файлы от <REDkit>\bin\
    return ["-workspaceDir", project_dir(work) + os.sep, "-uncookDir", os.path.join(work, "uncook") + os.sep]


def wcc(a, label, args, workspace=False, timeout=3600):
    """wcc_lite из изолированной cwd; лог -> logs\\<label>.log; сообщает, если он тронул Documents или REDkit.
    Без --allow-wcc не запускает ничего: решение о запусках wcc (он умеет писать в Documents) принимает пользователь."""
    if not getattr(a, "allow_wcc", False):
        sys.exit(f"wcc {label}: запуск wcc_lite выключен, нужен явный --allow-wcc (см. docs/REDKIT.md, «Побочные эффекты»)")
    root, cwd, userdir = ensure_wccroot(a.work)
    exe = os.path.join(REDKIT, "bin", "x64_RedKit", "wcc_lite.exe")
    cmd = [exe] + args + (workspace_args(a.work) if workspace else []) + ["-agreetoterms"]
    logs = os.path.join(a.work, "logs")
    os.makedirs(logs, exist_ok=True)
    wlog = os.path.join(root, "bin", "wcc.log")
    if os.path.exists(wlog):
        os.remove(wlog)
    game_before = game_running()
    before, rk_before = docs_snapshot(), redkit_snapshot()
    t0 = time.time()
    print(f"wcc {label}: {' '.join(cmd[1:])}")
    with open(os.path.join(logs, f"{label}.out"), "wb") as out:
        r = subprocess.run(cmd, cwd=cwd, stdout=out, stderr=subprocess.STDOUT, timeout=timeout)
    log = os.path.join(logs, f"{label}.log")
    if os.path.exists(wlog):
        shutil.move(wlog, log)
        with open(log, encoding="utf-8", errors="replace") as f:
            save_dir = next((ln for ln in f if "Save directory set to" in ln), "")
        if save_dir and os.path.normcase(os.path.basename(userdir)) not in os.path.normcase(save_dir):
            print(f"  WARNING: пользовательская папка wcc не в {userdir}: {save_dir.strip()}")
    after, rk_after = docs_snapshot(), redkit_snapshot()
    game_after = game_running()
    touched, log_changed = docs_changes(before, after, game_before, game_after)
    rk_new = sorted(p for p in rk_after if rk_before.get(p) != rk_after[p] and p not in REDKIT_KNOWN_WRITES)
    print(f"  exit={r.returncode} {time.time() - t0:.1f}s log={log}")
    if log_changed and game_before and game_after:
        print(f"  note: {GAME_LOG} изменился, его пишет запущенная игра ({GAME_EXE}) - не сравнивается")
    if touched:
        for p in touched:
            print(f"  WARNING: wcc_lite changed {p}")
        sys.exit(f"wcc {label}: изменились «Документы» ({len(touched)}), конвейер остановлен")
    for p in rk_new:
        print(f"  WARNING: wcc_lite wrote into REDkit: {p}")
    if r.returncode != 0 and not a.keep_going:
        sys.exit(f"wcc {label} failed ({r.returncode}), см. {log} и .out")
    return r.returncode


# ---- шаги ------------------------------------------------------------------------------------------------------
def tex_alpha(tex_dir):
    """{блок: opaque|cutout|translucent} из manifest.json mc_textures.py (нет манифеста - пусто)."""
    try:
        with open(os.path.join(tex_dir, "manifest.json"), encoding="utf-8") as f:
            return {b: v.get("alpha", "opaque") for b, v in json.load(f).get("blocks", {}).items()}
    except (OSError, ValueError):
        return {}


def step_prepare(a):
    quads = cube_mesh.face_quads("atlas", a.cell_px, 0, a.inset_px)
    ws = workspace_dir(a.work)
    if os.path.isdir(os.path.join(ws, DEPOT_DIR)):
        shutil.rmtree(os.path.join(ws, DEPOT_DIR))
    alpha = {} if a.test_png else tex_alpha(a.tex)
    manifest = {}
    for b in a.blocks:
        p = paths(b)
        src_png = os.path.join(a.work, "src", f"{b}.png")
        if a.test_png:
            write(src_png, test_atlas_png())
        else:
            src = os.path.join(a.tex, "atlas", f"{b}.png")
            if not os.path.isfile(src):
                sys.exit(f"нет атласа {src} (mc_textures.py) - или запускай с --test-png")
            os.makedirs(os.path.dirname(src_png), exist_ok=True)
            shutil.copyfile(src, src_png)
        physmat = PHYSMAT.get(b, DEFAULT_PHYSMAT)
        masked = alpha.get(b, "opaque") != "opaque"
        if alpha.get(b) == "translucent":
            print(f"  {b}: translucent -> пока альфа-тест (enableMask), полупрозрачности на pbr_std нет [проверить]")
        write(depot_file(a.work, p["mi"]), make_material(p["xbm"], masked))
        write(depot_file(a.work, p["mesh"]), make_mesh(p["mi"], physmat, quads, a.auto_hide, masked))
        write(depot_file(a.work, p["ent"]), make_entity(p["mesh"], b))
        manifest[b] = dict(p, png=src_png, physmat=physmat, masked=masked,
                           texgroup="WorldDiffuseWithAlpha" if masked else "WorldDiffuse")
        print(f"prepare {b}: {p['ent']}" + (" (masked)" if masked else ""))
    write(os.path.join(a.work, "blocks.json"), json.dumps(manifest, indent=1).encode())


def step_import(a):
    # -depot=<abs> падает ("Remap is not supported for SplitEditorDepot!"); при -depot=local относительный -out
    # пишется от <REDkit>\bin\ (!), поэтому -out только абсолютный - прямо в наш workspace.
    for b, m in load_manifest(a.work).items():
        out = depot_file(a.work, m["xbm"])
        if os.path.exists(out):
            os.remove(out)
        wcc(a, f"import_{b}", ["import", "-depot=local", f"-file={m['png']}", f"-out={out}",
                               f"-texturegroup={m.get('texgroup', 'WorldDiffuse')}"])
        if not os.path.isfile(out):
            sys.exit(f"import {b}: нет {out}")


def step_cook(a):
    cooked = os.path.join(a.work, "cooked")
    shutil.rmtree(cooked, ignore_errors=True)
    wcc(a, "cook", ["cook", f"-platform={a.platform}", f"-mod={workspace_dir(a.work)}{os.sep}",
                    f"-outdir={cooked}{os.sep}"], workspace=True)


def step_caches(a):
    db = os.path.join(a.work, "cooked", "cook.db")
    content = content_of(a)
    os.makedirs(content, exist_ok=True)
    for kind, fn in (("textures", "texture.cache"), ("physics", "collision.cache")):
        out = os.path.join(content, fn)
        if os.path.exists(out):
            os.remove(out)
        wcc(a, f"buildcache_{kind}", ["buildcache", kind, f"-platform={a.platform}", f"-db={db}", f"-out={out}"],
            workspace=True)


def step_pack(a):
    content = content_of(a)
    os.makedirs(content, exist_ok=True)
    for fn in os.listdir(content):
        if fn.endswith(".bundle") or fn == "metadata.store":
            os.remove(os.path.join(content, fn))
    wcc(a, "pack", ["pack", f"-dir={os.path.join(a.work, 'cooked')}{os.sep}", f"-outdir={content}{os.sep}",
                    f"-compression={a.compression}"])
    wcc(a, "metadatastore", ["metadatastore", f"-path={content}{os.sep}"])


# ---- проверка ----------------------------------------------------------------------------------------------------
def vanilla_index():
    idx = set()
    bdir = os.path.join(GAME, "content", "content0", "bundles")
    for bn in os.listdir(bdir):
        if bn.endswith(".bundle"):
            idx.update(it["name"].lower() for it in w3bundle.read_table(os.path.join(bdir, bn))[1] if it["name"])
    return idx


def iter_cr2w_imports(data):
    """Все пути импортов файла, включая вложенные CR2W (flatCompiledData и т.п.)."""
    for off in cr2w.find_all(data, b"CR2W"):
        try:
            c = cr2w.CR2W(data, off)
        except Exception:  # noqa: BLE001 - случайное совпадение магии внутри данных
            continue
        for p, cls, _ in c.imports:
            yield p, cls


TC_FOOTER = struct.Struct("<QIIII4sI")  # u64 crc, u32 pages, u32 entries, u32 strings_size, u32 mips, 'HCXT', u32 ver
TC_ENTRY = struct.Struct("<IiIIIIHHHHiiqBBBB")  # 52 байта, поля - в texture_cache_entries
# формат записи кэша (первый из двух байтов типа): сверено на нашем кэше и на content0\texture.cache 5.00c -
# TCM_DXTNoAlpha (WorldDiffuse) -> 7, TCM_DXTAlpha (WorldDiffuseWithAlpha, NormalmapGloss) -> 8; второй байт 4
TC_FORMATS = {7: "DXT1", 8: "DXT5"}


def _texture_cache_tail(path):
    """Хвост файла: [u32 x mips][имена ssize байт][записи 52 x n][футер 32]. Читает только его (кэш игры - 31 ГБ)."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(size - TC_FOOTER.size)
        _, _, n, ssize, mips, magic, ver = TC_FOOTER.unpack(f.read(TC_FOOTER.size))
        if magic != b"HCXT":
            raise ValueError(f"{path}: нет HCXT")
        f.seek(size - TC_FOOTER.size - n * TC_ENTRY.size - ssize)
        tail = f.read(ssize + n * TC_ENTRY.size)
    return ver, n, tail[:ssize], tail[ssize:]


def texture_cache_names(path):
    """texture.cache v7 (HCXT) -> (version, entries, [имена])."""
    ver, n, names, _ = _texture_cache_tail(path)
    return ver, n, [s.decode("latin-1") for s in names.split(b"\0") if s.lower().endswith(b".xbm")]


def texture_cache_entries(path):
    """-> {имя (нижний регистр): dict(width, height, mips, size, fmt, fmt2)}; имя - по смещению в блоке имён."""
    _, n, names, table = _texture_cache_tail(path)
    out = {}
    for k in range(n):
        (_, soff, _, _, usize, _, w, h, mips, _, _, _, _, fmt, fmt2, _, _) = TC_ENTRY.unpack_from(table, k * TC_ENTRY.size)
        name = names[soff:names.index(b"\0", soff)].decode("latin-1").lower()
        out[name] = dict(width=w, height=h, mips=mips, size=usize, fmt=TC_FORMATS.get(fmt, fmt), fmt2=fmt2)
    return out


def masked_problems(files, m):
    """cutout-блок (m['masked']): альфа-тест включается, только если enableMask стоит у w2mi и у локального
    материала меша (make_mesh), а диффуз сжат с альфой (TCM_DXTAlpha). files: {депо-путь: cooked-байты}."""
    out = []
    for key, cls in (("mi", "CMaterialInstance"), ("mesh", "CMaterialInstance")):
        doc = cr2w.CR2WDoc(files[m[key].lower()])
        mats = [x for x in doc.exports if doc.names[x["cls"]] == cls]
        if not mats:
            out.append(f"{m[key]}: нет {cls}")
        for x in mats:
            try:
                ok = doc.get_prop(x["data"], "enableMask")[1] == b"\x01"
            except KeyError:
                ok = False
            if not ok:
                out.append(f"{m[key]}: у {cls} нет enableMask=true - альфа-теста не будет")
    doc = cr2w.CR2WDoc(files[m["xbm"].lower()])
    _, v = doc.get_prop(doc.exports[0]["data"], "compression")
    comp = doc.names[struct.unpack("<H", v)[0]]
    if comp != "TCM_DXTAlpha":
        out.append(f"{m['xbm']}: compression {comp}, нужна TCM_DXTAlpha (альфа)")
    return out


def step_verify(a):
    """-> список проблем; печатает сводку."""
    content = content_of(a)
    print(f"verify {content}")
    manifest = load_manifest(a.work)
    problems = []
    bundles = sorted(f for f in os.listdir(content) if f.endswith(".bundle")) if os.path.isdir(content) else []
    if not bundles:
        return [f"нет бандлов в {content}"]
    mod_files = {}
    for bn in bundles:
        hdr, items = w3bundle.read_table(os.path.join(content, bn))
        comps = sorted({w3bundle.COMP.get(it["comp"], it["comp"]) for it in items})
        print(f"{bn}: bundle v{hdr['version']}, {len(items)} entries, compression {comps}")
        for it in items:
            data, ok = w3bundle.extract(os.path.join(content, bn), it)
            if not ok:
                problems.append(f"{bn}: crc mismatch {it['name']}")
            mod_files[it["name"].lower()] = data
    for b, m in manifest.items():
        for key in ("ent", "mesh", "mi", "xbm"):
            if m[key].lower() not in mod_files:
                problems.append(f"{b}: в бандлах нет {m[key]}")
        if m["mesh"].lower() + ".1.buffer" not in mod_files:
            problems.append(f"{b}: нет {m['mesh']}.1.buffer")
        if m.get("masked") and all(m[k].lower() in mod_files for k in ("mesh", "mi", "xbm")):
            mp = masked_problems(mod_files, m)
            print(f"  {b}: masked -> {'OK' if not mp else mp}")
            problems += mp
    vanilla = vanilla_index()
    for name, data in sorted(mod_files.items()):
        if not data.startswith(b"CR2W"):
            continue
        try:
            c = cr2w.CR2W(data)
        except Exception as e:  # noqa: BLE001
            problems.append(f"{name}: CR2W не читается ({e})")
            continue
        refs = sorted({p for p, _ in iter_cr2w_imports(data)})
        bad = [p for p in refs if p.lower() not in mod_files and p.lower() not in vanilla]
        print(f"  {name}: CR2W v{c.version}, {len(c.exports)} exports -> {refs}")
        problems += [f"{name}: ссылка {p} не ведёт ни в мод, ни в бандлы игры" for p in bad]
    meta = os.path.join(content, "metadata.store")
    if os.path.isfile(meta):
        with open(meta, "rb") as f:
            head = f.read(8)
        print(f"metadata.store: {os.path.getsize(meta)} B, magic {head[:4]!r} v{struct.unpack_from('<I', head, 4)[0]}")
        if head[:4] != b"\x03VTM":
            problems.append("metadata.store: не VTM")
    else:
        problems.append("нет metadata.store")
    tc = os.path.join(content, "texture.cache")
    if os.path.isfile(tc):
        ver, n, names = texture_cache_names(tc)
        print(f"texture.cache: HCXT v{ver}, {n} entries: {names}")
        entries = texture_cache_entries(tc)
        for b, m in manifest.items():
            e = entries.get(m["xbm"].lower())
            if not e:
                problems.append(f"{b}: нет {m['xbm']} в texture.cache")
                continue
            want = "DXT5" if m.get("masked") else "DXT1"
            print(f"  {m['xbm']}: {e['width']}x{e['height']} mips {e['mips']} {e['fmt']}")
            if e["fmt"] != want:
                problems.append(f"{b}: в texture.cache {e['fmt']}, ожидался {want}")
    else:
        problems.append("нет texture.cache")
    cc = os.path.join(content, "collision.cache")
    if os.path.isfile(cc):
        cache = collcache.CollisionCache(cc)
        print(f"collision.cache: CC3W v{cache.version}, {len(cache.entries)} entries")
        for b, m in manifest.items():
            s = cache.summary(m["mesh"])
            if not s:
                problems.append(f"{b}: нет {m['mesh']} в collision.cache")
            else:
                print(f"  {m['mesh']}: {s}")
                if s["physmat"] != m["physmat"] or s["tail3f"] != (0.5, 0.5, 0.5):
                    problems.append(f"{b}: коллизия {s} вместо box 0.5 {m['physmat']}")
    else:
        problems.append("нет collision.cache")
    for p in problems:
        print("PROBLEM:", p)
    print("verify:", "OK" if not problems else f"{len(problems)} problems")
    return problems


STEPS = {"prepare": step_prepare, "import": step_import, "cook": step_cook, "caches": step_caches,
         "pack": step_pack, "verify": step_verify}


def refuse_work_dir(work):
    w3bundle.refuse_inside_game(work)
    if w3bundle._inside(work, REDKIT) or w3bundle._inside(work, w3bundle.PROJECT_ROOT):
        sys.exit(f"refuse: рабочая папка {work} внутри REDkit или проекта")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Minecraft blocks -> Witcher 3 mod via REDkit wcc_lite (no GUI)")
    ap.add_argument("step", choices=list(STEPS) + ["all", "wcc"], help="wcc: любой командлет, аргументы после --")
    ap.add_argument("wcc_args", nargs="*", help="для step=wcc: python build_blocks.py wcc -- help import")
    ap.add_argument("--blocks", default=",".join(MVP), help="через запятую")
    ap.add_argument("--work", default=WORK)
    ap.add_argument("--out", default=None,
                    help="куда класть мод (caches, pack, verify): <out>\\<mod>\\content; по умолчанию <work>\\out")
    ap.add_argument("--tex", default=TEX, help="выход mc_textures.py (atlas\\<b>.png)")
    ap.add_argument("--test-png", action="store_true", help="вместо атласов из --tex - тестовые цветные грани")
    ap.add_argument("--platform", default="pc")
    ap.add_argument("--compression", default="LZ4HC", choices=("LZ4HC", "LZ4", "ZLIB"),
                    help="сжатие бандлов (у wcc pack по умолчанию LZ4HC; в ванили 5.00c только zlib и none)")
    ap.add_argument("--cell-px", type=int, default=256, help="клетка атласа (для отступа UV)")
    ap.add_argument("--inset-px", type=float, default=0.5)
    ap.add_argument("--auto-hide", type=float, default=None, help="autoHideDistance меша, м (в box.w2mesh 60)")
    ap.add_argument("--workspace", action="store_true", help="для step=wcc: добавить -workspaceDir/-uncookDir")
    ap.add_argument("--keep-going", action="store_true")
    ap.add_argument("--allow-wcc", action="store_true", help="разрешить запуск wcc_lite (шаги import..pack и wcc)")
    a = ap.parse_args(argv)
    a.blocks = [b for b in a.blocks.split(",") if b]
    refuse_work_dir(a.work)
    if a.out:
        refuse_work_dir(a.out)
    if a.step == "wcc":
        a.keep_going = True
        label = "raw_" + "".join(c if c.isalnum() else "_" for c in "_".join(a.wcc_args[:2]))[:40]
        return wcc(a, label, a.wcc_args, workspace=a.workspace)
    steps = list(STEPS) if a.step == "all" else [a.step]
    for s in steps:
        r = STEPS[s](a)
        if s == "verify" and r:
            sys.exit(1)


if __name__ == "__main__":
    main()
