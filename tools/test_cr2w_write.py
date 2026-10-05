"""Тесты записи CR2W (cr2w.CR2WDoc):  python -m unittest test_cr2w_write -v   (из папки tools).

Ванильные файлы читаются прямо из бандлов игры и из депо REDkit (r4data) в память, на диск ничего не пишется.
Нет игры или REDkit - соответствующие блоки пропускаются. Главная проверка - побайтовый round-trip
«прочитал -> записал -> то же самое»; плюс правки (путь импорта, вложенный CR2W, параметры материала) с проверкой
всех контрольных сумм и смещений.
"""
import os
import random
import struct
import unittest
import zlib

import cr2w
import w3bundle

GAME = r"C:\Program Files (x86)\Steam\steamapps\common\The Witcher 3"
BUNDLES = os.path.join(GAME, "content", "content0", "bundles")
R4DATA = r"C:\Program Files (x86)\Steam\steamapps\common\The Witcher 3 REDkit\r4data"

CRATE_ENT = r"gameplay\containers\_container_definitions\_mesh_entities\cont_crate.w2ent"
BOX_MESH = r"engine\meshes\editor\box.w2mesh"
FLOOR_MI = r"environment\textures_tileable\common_materials\novigrad_poor\floor_brick.w2mi"
PBR_STD = r"engine\materials\graphs\pbr_std.w2mg"
# имя с не-ASCII исходником: хэш в файле не пересчитывается из строки (см. CR2WDoc.name_hashes)
ODD_HASH_SCENE = r"gameplay\community\dialogues\skellige\shops\shop_larvik_shopkeeper_01.w2scene"


def check_integrity(t, data):
    """Все поля, которые пересчитывает to_bytes: crc заголовка, таблиц, экспортов, буферов, fileSize/bufferSize."""
    hdr = cr2w.HDR.unpack_from(data, 0)
    file_size, buffer_size, crc = hdr[5], hdr[6], hdr[7]
    head = bytearray(data[:0xA0])
    struct.pack_into("<I", head, 0x20, 0xDEADBEEF)
    t.assertEqual(zlib.crc32(head), crc, "header crc")
    tabs = [struct.unpack_from("<III", data, 40 + 12 * i) for i in range(10)]
    for i, (off, cnt, tcrc) in enumerate(tabs[:6]):
        n = cnt * cr2w.CR2WDoc.ROW[i]
        t.assertEqual(zlib.crc32(data[off:off + n]) if cnt else 0, tcrc, f"table {i} crc")
    end = None
    for c, f, p, sz, off, tmpl, ecrc in (struct.unpack_from("<HHIIIII", data, tabs[4][0] + 24 * k)
                                          for k in range(tabs[4][1])):
        t.assertEqual(zlib.crc32(data[off:off + sz]), ecrc, "export crc")
        t.assertTrue(end is None or off == end, "exports are contiguous")
        end = off + sz
    if end is not None:
        t.assertEqual(end, file_size)
    pos = file_size
    for fl, idx, off, disk, mem, bcrc in (struct.unpack_from("<IIIIII", data, tabs[5][0] + 24 * k)
                                          for k in range(tabs[5][1])):
        if off >= file_size:
            t.assertEqual(off, pos)
            t.assertEqual(zlib.crc32(data[off:off + disk]), bcrc, "buffer crc")
            pos += disk
    t.assertEqual(pos, buffer_size)


def bundle_items(name, exts, per_ext, seed=1):
    _, items = w3bundle.read_table(os.path.join(BUNDLES, name))
    rnd = random.Random(seed)
    out = []
    for ext in exts:
        pool = [it for it in items if it["name"] and it["name"].endswith("." + ext)]
        out += rnd.sample(pool, min(per_ext, len(pool)))
    return out


def bundle_file(name, depot_path):
    _, items = w3bundle.read_table(os.path.join(BUNDLES, name))
    it = next(it for it in items if it["name"] == depot_path)
    data, ok = w3bundle.extract(os.path.join(BUNDLES, name), it)
    assert ok
    return data


def r4(depot_path):
    with open(os.path.join(R4DATA, depot_path), "rb") as f:
        return f.read()


class NameHash(unittest.TestCase):
    def test_known(self):
        # значения из names-таблицы ванильного box.w2mesh 5.00c
        self.assertEqual(cr2w.name_hash(""), 0)
        self.assertEqual(cr2w.name_hash("CMesh"), 0xA97C7881)
        self.assertEqual(cr2w.name_hash("materials"), 0xFD2865CB)
        self.assertEqual(cr2w.name_hash("CMaterialInstance"), 0x5286B01C)


@unittest.skipUnless(os.path.isdir(BUNDLES), "нет установки игры")
class RoundTripCooked(unittest.TestCase):
    EXTS = ("w2ent", "w2mesh", "w2mi", "xbm", "w2mg", "w2p", "w2rig", "redcloth", "w2scene")

    def test_known_files(self):
        for bundle, path in (("bob.bundle", CRATE_ENT), ("bob.bundle", BOX_MESH),
                             ("bob.bundle", r"environment\decorations\containers\crate\crate.w2mesh"),
                             ("startup.bundle", r"engine\textures\editor\grey.xbm")):
            with self.subTest(path=path):
                data = bundle_file(bundle, path)
                self.assertEqual(cr2w.CR2WDoc(data).to_bytes(), data)
                check_integrity(self, data)

    def test_sample(self):
        n = 0
        for bundle in ("blob.bundle", "bob.bundle", "ep1.bundle"):
            for it in bundle_items(bundle, self.EXTS, 25):
                data, ok = w3bundle.extract(os.path.join(BUNDLES, bundle), it)
                with self.subTest(path=it["name"]):
                    self.assertEqual(cr2w.CR2WDoc(data).to_bytes(), data)
                    n += 1
        self.assertGreater(n, 500)


@unittest.skipUnless(os.path.isdir(R4DATA), "нет REDkit")
class RoundTripRedkit(unittest.TestCase):
    def test_known_files(self):
        for path in (CRATE_ENT, BOX_MESH, FLOOR_MI, PBR_STD, ODD_HASH_SCENE):
            with self.subTest(path=path):
                data = r4(path)
                self.assertEqual(cr2w.CR2WDoc(data).to_bytes(), data)

    def test_dirs(self):
        n = old = 0
        for sub in (r"engine\materials", r"engine\meshes", r"gameplay\containers",
                    r"environment\textures_tileable\common_materials"):
            for root, _, files in os.walk(os.path.join(R4DATA, sub)):
                for fn in files:
                    if not fn.endswith((".w2ent", ".w2mesh", ".w2mi", ".w2mg", ".xbm")):
                        continue
                    with open(os.path.join(root, fn), "rb") as f:
                        data = f.read()
                    try:
                        doc = cr2w.CR2WDoc(data)
                    except ValueError:  # v<=154: старая раскладка, писатель её не трогает
                        old += 1
                        continue
                    with self.subTest(path=os.path.join(root, fn)):
                        self.assertEqual(doc.to_bytes(), data)
                        n += 1
        self.assertGreater(n, 300)

    def test_old_layout_refused(self):
        with self.assertRaises(ValueError):
            cr2w.CR2WDoc(r4(r"engine\textures\editor\grey.xbm"))  # v152


@unittest.skipUnless(os.path.isdir(R4DATA), "нет REDkit")
class Edits(unittest.TestCase):
    def test_import_path(self):
        doc = cr2w.CR2WDoc(r4(FLOOR_MI))
        old = doc.exports[0]["data"]
        doc.set_import_path(r"environment\textures_tileable\floor\floor_red_brick.xbm", r"mcw\blocks\stone.xbm")
        out = doc.to_bytes()
        check_integrity(self, out)
        c = cr2w.CR2W(out)
        self.assertEqual([i[0] for i in c.imports],
                         [PBR_STD, r"mcw\blocks\stone.xbm",
                          r"environment\textures_tileable\floor\floor_red_brick_n.xbm"])
        self.assertEqual(cr2w.CR2WDoc(out).exports[0]["data"], old)  # данные экспорта не тронуты

    def test_material_params(self):
        doc = cr2w.CR2WDoc(r4(FLOOR_MI))
        x = doc.exports[0]
        params = doc.material_params(x["data"])
        self.assertEqual([(n, t) for n, t, _ in params], [("Diffuse", "handle:ITexture"), ("Normal", "handle:ITexture")])
        self.assertEqual(doc.set_material_params(x["data"], params), x["data"])
        # оставить только Diffuse и добавить скаляр
        new = [params[0], ("RSpecScale", "Float", struct.pack("<f", 0.1))]
        x["data"] = doc.set_material_params(x["data"], new)
        out = doc.to_bytes()
        check_integrity(self, out)
        d2 = cr2w.CR2WDoc(out)
        self.assertEqual([(n, t, v) for n, t, v in d2.material_params(d2.exports[0]["data"])], new)

    def test_nested_flat_compiled_data(self):
        new_mesh = r"mcw\blocks\mcw_cube.w2mesh"
        doc = cr2w.CR2WDoc(r4(CRATE_ENT))
        tpl = doc.exports[0]
        flat = doc.get_nested(tpl["data"], "flatCompiledData")
        self.assertEqual(flat.to_bytes(), doc.get_prop(tpl["data"], "flatCompiledData")[1][4:])
        # компонент лежит ещё глубже: flatCompiledData -> CEntity.streamingDataBuffer -> CStaticMeshComponent
        ent = flat.exports[0]
        comp = flat.get_nested(ent["data"], "streamingDataBuffer")
        comp.set_import_path(r"environment\decorations\containers\crate\crate.w2mesh", new_mesh)
        ent["data"] = flat.set_nested(ent["data"], "streamingDataBuffer", comp)
        tpl["data"] = doc.set_nested(tpl["data"], "flatCompiledData", flat)
        out = doc.to_bytes()
        check_integrity(self, out)
        d2 = cr2w.CR2WDoc(out)
        f2 = d2.get_nested(d2.exports[0]["data"], "flatCompiledData")
        check_integrity(self, f2.to_bytes())
        c2 = f2.get_nested(f2.exports[0]["data"], "streamingDataBuffer")
        self.assertEqual([i[0] for i in c2.imports], [new_mesh])
        # остальное содержимое на месте
        self.assertEqual([x["data"] for x in c2.exports], [x["data"] for x in comp.exports])

    def test_set_prop_append_and_replace(self):
        doc = cr2w.CR2WDoc(r4(CRATE_ENT))
        ent = doc.exports[1]
        self.assertEqual(doc.get_prop(ent["data"], "streamingDistance"), ("Uint8", b"\x0a"))
        ent["data"] = doc.set_prop(ent["data"], "streamingDistance", b"\x20")
        ent["data"] = doc.set_prop(ent["data"], "mcwTestFlag", b"\x01", "Bool")
        out = doc.to_bytes()
        check_integrity(self, out)
        d2 = cr2w.CR2WDoc(out)
        self.assertEqual(d2.get_prop(d2.exports[1]["data"], "streamingDistance"), ("Uint8", b"\x20"))
        self.assertEqual(d2.get_prop(d2.exports[1]["data"], "mcwTestFlag"), ("Bool", b"\x01"))
        self.assertIn("mcwTestFlag", d2.names)


if __name__ == "__main__":
    unittest.main()
