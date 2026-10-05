"""Тесты build_blocks.py:  python -m unittest test_build_blocks -v   (из папки tools).

wcc_lite тесты не запускают. Шаблоны берутся из депо REDkit (r4data) в память; без REDkit эти тесты пропускаются.
Проверяется, что сгенерированные w2mi/w2mesh/w2ent - валидные CR2W с нужными ссылками, UV граней легли в свои
клетки атласа, а остальное в шаблонах не тронуто; что обвязка wcc уводит его «Документы» в <work>\\userdir.
Если мод уже собран (`python build_blocks.py all`), BuiltMod прогоняет по нему verify.
"""
import contextlib
import io
import os
import re
import struct
import tempfile
import unittest
import zlib

import build_blocks as BB
import cr2w
import cube_mesh
from test_cr2w_write import BUNDLES, R4DATA, bundle_file, check_integrity

P = BB.paths("stone")


@unittest.skipUnless(os.path.isdir(R4DATA), "нет REDkit")
class Material(unittest.TestCase):
    def test_material(self):
        out = BB.make_material(P["xbm"])
        check_integrity(self, out)
        doc = cr2w.CR2WDoc(out)
        self.assertEqual([i[0] for i in doc.imports], [r"engine\materials\graphs\pbr_std.w2mg", P["xbm"]])
        x = doc.exports[0]
        self.assertEqual(doc.get_prop(x["data"], "baseMaterial"), ("handle:IMaterial", struct.pack("<i", -1)))
        self.assertEqual(doc.material_params(x["data"]), [("Diffuse", "handle:ITexture", struct.pack("<i", -2))])
        with self.assertRaises(KeyError):
            doc.get_prop(x["data"], "enableMask")

    def test_masked_material(self):
        out = BB.make_material(P["xbm"], masked=True)
        check_integrity(self, out)
        doc = cr2w.CR2WDoc(out)
        x = doc.exports[0]
        self.assertEqual(doc.get_prop(x["data"], "enableMask"), ("Bool", b"\x01"))
        self.assertEqual(doc.material_params(x["data"]), [("Diffuse", "handle:ITexture", struct.pack("<i", -2))])


@unittest.skipUnless(os.path.isdir(R4DATA), "нет REDkit")
class Mesh(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.quads = cube_mesh.face_quads("atlas", 256, 0, 0.5)
        cls.out = BB.make_mesh(P["mi"], "stone_solid", cls.quads, auto_hide=90.0)
        cls.doc = cr2w.CR2WDoc(cls.out)
        cls.src = cr2w.CR2WDoc(BB.r4(BB.SRC_MESH))

    def test_integrity_and_refs(self):
        check_integrity(self, self.out)
        self.assertEqual([i[0] for i in self.doc.imports], [P["mi"]])
        self.assertEqual(self.doc.names[self.doc.imports[0][1]], "CMaterialInstance")
        mesh, mat, _, shape = self.doc.exports
        self.assertEqual(self.doc.get_prop(mat["data"], "baseMaterial")[1], struct.pack("<i", -1))
        self.assertEqual(self.doc.material_params(mat["data"]), [])
        with self.assertRaises(KeyError):
            self.doc.get_prop(mat["data"], "enableMask")
        _, v = self.doc.get_prop(shape["data"], "physicalMaterialName")
        self.assertEqual(self.doc.names[struct.unpack("<H", v)[0]], "stone_solid")
        self.assertEqual(self.doc.get_prop(mesh["data"], "autoHideDistance")[1], struct.pack("<f", 90.0))

    def buf(self, doc, prop):
        return BB._buffer(doc, doc.exports[0]["data"], prop)["data"]

    def test_only_uvs_changed_in_buffers(self):
        first_vertex = self.buf(self.src, "rawVertices")[:BB.VSTRIDE]
        for prop in ("rawVertices", "chunksBuffer"):
            new, old = self.buf(self.doc, prop), self.buf(self.src, prop)
            self.assertEqual(len(new), len(old))
            diff = [i for i in range(len(new)) if new[i] != old[i]]
            self.assertTrue(diff)
            base = old.find(first_vertex)  # в chunksBuffer вершины идут после заголовка чанка
            self.assertTrue(all(i >= base and BB.V_UV <= (i - base) % BB.VSTRIDE < BB.V_UV + 8 for i in diff), prop)
        self.assertEqual(self.buf(self.doc, "rawIndices"), self.buf(self.src, "rawIndices"))

    def test_faces_land_in_their_cells(self):
        raw = BB._buffer(self.doc, self.doc.exports[0]["data"], "rawVertices")["data"]
        for k in range(len(raw) // BB.VSTRIDE):
            rec = raw[k * BB.VSTRIDE:(k + 1) * BB.VSTRIDE]
            nrm = tuple(round(c) for c in struct.unpack_from("<3f", rec, BB.V_NRM))
            u, v = struct.unpack_from("<2f", rec, BB.V_UV)
            face = next(q["face"] for q in self.quads if tuple(round(c) for c in q["normal"]) == nrm)
            col, row = cube_mesh.CELLS[face]
            self.assertEqual((int(u * cube_mesh.COLS), int(v * cube_mesh.ROWS)), (col, row), (k, face, u, v))

    def test_grid_of_faces_matches_witcher_axes(self):
        # north (MC -Z) смотрит в +Y Ведьмака, up - в +Z
        normals = {q["face"]: tuple(round(c) for c in q["normal"]) for q in self.quads}
        self.assertEqual(normals["north"], (0, 1, 0))
        self.assertEqual(normals["up"], (0, 0, 1))

    def test_masked_mesh_sets_enable_mask_on_local_material(self):
        # IsMasked() чанка читает enableMask своего (локального) экземпляра, не базового w2mi
        # auto_hide как в setUpClass: без правки CMesh остаётся исходный crc шаблона, а он не сходится с данными
        out = BB.make_mesh(P["mi"], "stone_solid", self.quads, auto_hide=90.0, masked=True)
        check_integrity(self, out)
        doc = cr2w.CR2WDoc(out)
        mat = doc.exports[1]
        self.assertEqual(doc.get_prop(mat["data"], "enableMask"), ("Bool", b"\x01"))
        # порядок как у ванильных локальных экземпляров: baseMaterial, enableMask; параметров нет
        self.assertEqual([r[0] for r in doc.prop_records(mat["data"])[0]], ["baseMaterial", "enableMask"])
        self.assertEqual(doc.material_params(mat["data"]), [])
        # остальное как у непрозрачного меша (у physmat другой индекс имени: enableMask добавлен в names раньше)
        for k in (0, 2):
            self.assertEqual(doc.exports[k]["data"], self.doc.exports[k]["data"], k)
        self.assertEqual([b["data"] for b in doc.buffers], [b["data"] for b in self.doc.buffers])
        _, v = doc.get_prop(doc.exports[3]["data"], "physicalMaterialName")
        self.assertEqual(doc.names[struct.unpack("<H", v)[0]], "stone_solid")


@unittest.skipUnless(os.path.isdir(R4DATA) and os.path.isdir(BUNDLES), "нет REDkit или игры")
class MaskedVerify(unittest.TestCase):
    """verify для cutout-блока: enableMask у w2mi и у материала меша, диффуз TCM_DXTAlpha."""

    @classmethod
    def setUpClass(cls):
        cls.p = BB.paths("glass")
        cls.quads = cube_mesh.face_quads("atlas", 256, 0, 0.5)
        cls.grey = bundle_file("startup.bundle", r"engine\textures\editor\grey.xbm")  # TCM_DXTNoAlpha

    def files(self, mi_masked=True, mesh_masked=True, comp="TCM_DXTAlpha"):
        xbm = cr2w.CR2WDoc(self.grey)
        x = xbm.exports[0]
        x["data"] = xbm.set_prop(x["data"], "compression", struct.pack("<H", xbm.name_index(comp)))
        files = {self.p["mi"]: BB.make_material(self.p["xbm"], mi_masked),
                 self.p["mesh"]: BB.make_mesh(self.p["mi"], "wood_solid", self.quads, masked=mesh_masked),
                 self.p["xbm"]: xbm.to_bytes()}
        return files, dict(self.p, masked=True)

    def test_ok(self):
        self.assertEqual(BB.masked_problems(*self.files()), [])

    def test_mask_only_on_w2mi_is_reported(self):
        # так была собрана первая версия: в игре стекло и листва без дыр
        problems = BB.masked_problems(*self.files(mesh_masked=False))
        self.assertEqual(len(problems), 1)
        self.assertIn(self.p["mesh"], problems[0])

    def test_mask_missing_on_w2mi_is_reported(self):
        problems = BB.masked_problems(*self.files(mi_masked=False))
        self.assertEqual(len(problems), 1)
        self.assertIn(self.p["mi"], problems[0])

    def test_texture_without_alpha_is_reported(self):
        problems = BB.masked_problems(*self.files(comp="TCM_DXTNoAlpha"))
        self.assertEqual(len(problems), 1)
        self.assertIn("TCM_DXTNoAlpha", problems[0])


@unittest.skipUnless(os.path.isdir(R4DATA), "нет REDkit")
class Entity(unittest.TestCase):
    def test_entity(self):
        out = BB.make_entity(P["mesh"], "stone")
        check_integrity(self, out)
        doc = cr2w.CR2WDoc(out)
        flat = doc.get_nested(doc.exports[0]["data"], "flatCompiledData")
        check_integrity(self, flat.to_bytes())
        for d, comp in ((doc, doc.exports[2]), (flat, flat.exports[1])):
            self.assertEqual([i[0] for i in d.imports], [P["mesh"]])
            c = cr2w.CR2W(d.to_bytes())
            props = dict((k, v) for k, t, v in c.export_props(d.exports.index(comp)))
            self.assertEqual(props["drawableFlags"], "{DF_IsVisible|DF_CastShadows}")
            self.assertEqual(props["transform"], "identity")
            self.assertEqual(props["isStreamed"], 0)
            self.assertEqual(props["name"], "'mcw_stone'")
            box = props["boundingBox"]
            self.assertEqual([box["Min"][a] for a in "XYZ"], [-0.5] * 3)
            self.assertEqual([box["Max"][a] for a in "XYZ"], [0.5] * 3)
            self.assertEqual(props["mesh"], f"-> import:{P['mesh']}")
        self.assertEqual(guid(doc), flat.get_prop(flat.exports[1]["data"], "guid")[1])  # одна GUID в обеих копиях
        self.assertEqual(guid(doc), guid(cr2w.CR2WDoc(BB.make_entity(P["mesh"], "stone"))))  # стабильна
        self.assertNotEqual(guid(doc), guid(cr2w.CR2WDoc(BB.make_entity(BB.paths("dirt")["mesh"], "dirt"))))


def guid(doc):
    return doc.get_prop(doc.exports[2]["data"], "guid")[1]


@unittest.skipUnless(os.path.isdir(BB.REDKIT), "нет REDkit")
class WccRoot(unittest.TestCase):
    def test_title_points_to_userdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, cwd, userdir = BB.ensure_wccroot(tmp)
            self.assertEqual(os.path.dirname(os.path.dirname(cwd)), root)
            with open(os.path.join(root, "bin", "gameconf.cfg"), "rb") as f:
                conf = f.read().decode("mbcs")
            title = re.search(r'title "([^"]+)"', conf).group(1)
            # wcc_lite: пользовательская папка = Documents\<title>
            got = os.path.normcase(os.path.normpath(os.path.join(BB.documents_dir(), title)))
            self.assertEqual(got, os.path.normcase(os.path.normpath(userdir)))
            self.assertNotIn("The Witcher 3", conf.split("data")[0])
            for d in ("uncook", os.path.join("project", "workspace")):
                self.assertTrue(os.path.isdir(os.path.join(tmp, d)))

    def test_workspace_args_are_space_separated(self):
        args = BB.workspace_args(r"C:\w")
        self.assertEqual(args, ["-workspaceDir", "C:\\w\\project\\", "-uncookDir", "C:\\w\\uncook\\"])


class DocumentsGuard(unittest.TestCase):
    def test_hash_catches_same_size_same_mtime(self):
        with tempfile.TemporaryDirectory() as docs:
            w3 = os.path.join(docs, "The Witcher 3")
            os.makedirs(os.path.join(w3, "gamesaves"))
            p = os.path.join(w3, "profile.settings")
            with open(p, "w") as f:
                f.write("wasConsentPopupShown=true\n")
            with open(os.path.join(w3, "gamesaves", "x.sav"), "w") as f:
                f.write("save")
            before = BB.docs_snapshot(docs)
            self.assertNotIn(os.path.join(w3, "gamesaves", "x.sav"), before)
            st = os.stat(p)
            with open(p, "w") as f:
                f.write("wasConsentPopupShown=fals\n")  # тот же размер
            os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))  # и тот же mtime
            self.assertEqual(BB.snapshot_diff(before, BB.docs_snapshot(docs)), [p])
            os.makedirs(os.path.join(docs, "New Folder"))
            self.assertIn(os.path.join(docs, "New Folder"), BB.snapshot_diff(before, BB.docs_snapshot(docs)))

    def test_game_log_ignored_only_while_game_runs(self):
        with tempfile.TemporaryDirectory() as docs:
            w3 = os.path.join(docs, "The Witcher 3")
            os.makedirs(w3)
            log, prof = os.path.join(w3, "scriptslog.txt"), os.path.join(w3, "profile.settings")
            for p in (log, prof):
                with open(p, "w") as f:
                    f.write("x\n")
            before = BB.docs_snapshot(docs)
            with open(log, "a") as f:
                f.write("exec line\n")
            after = BB.docs_snapshot(docs)
            self.assertEqual(BB.docs_changes(before, after, True, True, docs), ([], True))
            for running in ((False, False), (True, False), (False, True)):  # игры нет хотя бы при одном снимке
                self.assertEqual(BB.docs_changes(before, after, *running, docs), ([log], True), running)
            with open(prof, "a") as f:
                f.write("y\n")
            self.assertEqual(BB.docs_changes(before, BB.docs_snapshot(docs), True, True, docs), ([prof], True))

    def test_game_running_is_bool(self):
        self.assertIsInstance(BB.game_running(), bool)

    def test_wcc_needs_explicit_allow(self):
        a = type("A", (), {"work": tempfile.gettempdir(), "allow_wcc": False})()
        with self.assertRaises(SystemExit):
            BB.wcc(a, "help", ["help"])


class TextureCache(unittest.TestCase):
    def test_footer_and_names(self):
        names = b"mcw\\blocks\\a\\a.xbm\0mcw\\blocks\\b\\b.xbm\0"
        body = b"\0" * 4096 + names + b"\0" * (2 * 52)
        footer = struct.pack("<QIIII4sI", 0x1234, 2, 2, len(names), 6, b"HCXT", 7)
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "texture.cache")
            with open(p, "wb") as f:
                f.write(body + footer)
            self.assertEqual(BB.texture_cache_names(p), (7, 2, [r"mcw\blocks\a\a.xbm", r"mcw\blocks\b\b.xbm"]))

    def test_entries(self):
        # раскладка хвоста: [u32 x mips][имена][записи 52 байта][футер]
        names = b"mcw\\blocks\\a\\a.xbm\0mcw\\blocks\\B\\b.xbm\0"
        rows = b"".join(BB.TC_ENTRY.pack(0, soff, 0, 0, size, 16, 1024, 512, 11, 1, 0, 65541, 0, fmt, 4, 0, 0)
                        for soff, size, fmt in ((0, 349544, 7), (names.index(b"mcw", 1), 699088, 8)))
        mips = struct.pack("<3I", 1, 2, 3)
        footer = BB.TC_FOOTER.pack(0, 2, 2, len(names), 3, b"HCXT", 7)
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "texture.cache")
            with open(p, "wb") as f:
                f.write(b"\0" * 64 + mips + names + rows + footer)
            e = BB.texture_cache_entries(p)
        self.assertEqual(sorted(e), [r"mcw\blocks\a\a.xbm", r"mcw\blocks\b\b.xbm"])
        self.assertEqual(e[r"mcw\blocks\a\a.xbm"], dict(width=1024, height=512, mips=11, size=349544, fmt="DXT1", fmt2=4))
        self.assertEqual(e[r"mcw\blocks\b\b.xbm"]["fmt"], "DXT5")


def newest_build_root():
    """Самая свежая сборка в <work> (out\\ или out_*\\ из --out): verify сверяет её с blocks.json последнего prepare,
    более старые сборки ему не соответствуют (например, out\\ до правки enableMask)."""
    roots = [os.path.join(BB.WORK, d) for d in (os.listdir(BB.WORK) if os.path.isdir(BB.WORK) else [])
             if d == "out" or d.startswith("out_")]
    built = [(os.path.getmtime(os.path.join(BB.out_content(BB.WORK, r), "metadata.store")), r) for r in roots
             if os.path.isfile(os.path.join(BB.out_content(BB.WORK, r), "metadata.store"))]
    return max(built)[1] if built else None


@unittest.skipUnless(newest_build_root() and os.path.isdir(BB.GAME),
                     "мод ещё не собран (python build_blocks.py all) или нет игры")
class BuiltMod(unittest.TestCase):
    def test_verify_clean(self):
        a = type("A", (), {"work": BB.WORK, "out": newest_build_root()})()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(BB.step_verify(a), [])


class TestPng(unittest.TestCase):
    def test_png_is_valid(self):
        b = BB.test_atlas_png(cell=8)
        self.assertEqual(b[:8], b"\x89PNG\r\n\x1a\n")
        w, h = struct.unpack(">II", b[16:24])
        self.assertEqual((w, h), (32, 16))
        # IDAT распаковывается в h строк по (1 + 3w) байт
        i = b.index(b"IDAT")
        n = struct.unpack(">I", b[i - 4:i])[0]
        self.assertEqual(len(zlib.decompress(b[i + 4:i + 4 + n])), h * (1 + 3 * w))


if __name__ == "__main__":
    unittest.main()
