"""Инспектор и писатель CR2W (ресурсы Witcher 3: .w2ent, .w2mesh, .w2mi, .xbm ...). Только stdlib. CR2W — чтение,
CR2WDoc — правка и пересборка (раздел «Запись» ниже).

Раскладка по WolvenKit-7 (WolvenKit.CR2W/CR2W/CR2WHeaderStructs.cs, CR2WFile.cs, CR2W*.cs):
  "CR2W" u32 version u32 flags u64 timestamp u32 build u32 fileSize u32 bufferSize u32 crc u32 numChunks
  10 x {u32 offset, u32 itemCount, u32 crc}  таблицы: 0 strings(байты) 1 names{u32 strOff,u32 hash}
  2 imports{u32 strOff, u16 className, u16 flags} 3 properties{u16,u16,u16,u16,u64}
  4 exports{u16 className,u16 objFlags,u32 parentID,u32 dataSize,u32 dataOffset,u32 template,u32 crc}
  5 buffers{u32 flags,u32 index,u32 offset,u32 diskSize,u32 memSize,u32 crc} 6 embedded{...}
Данные экспорта: u8 0, затем {u16 nameIdx, u16 typeIdx, u32 size (вместе с самим полем), value} ... до nameIdx == 0.

Версии: в 5.0 файлы верхнего уровня v164, вложенные (flatCompiledData, streamingDataBuffer) v156..v164; заголовок
у них тот же. WolvenKit-7 на v>163 падает, этот разбор — нет. Разбор значений свойств best-effort: проверены
drawableFlags, isStreamed, transform, boundingBox, width/height у xbm; остальное сверять по сырым байтам.

Запуск: python cr2w.py <file> [<file> ...]   (ищет и вложенные CR2W, например в flatCompiledData)

Запись (класс CR2WDoc, раскладка с таблицами — v155..v164; v<=154 в депо REDkit лежат в старом формате, их не
трогаем). Правила сверены на ванили 5.00c и депо REDkit (test_cr2w_write.py, побайтовый round-trip):
  - строки: b"\\0" + уникальные строки (имена и пути импортов), каждая с NUL; в ванили порядок не всегда «имена,
    потом импорты», поэтому исходный порядок сохраняется, новые строки идут в конец;
  - имя: {u32 смещение строки, u32 FNV-1a32 по байтам строки ВМЕСТЕ с завершающим NUL}, у пустого имени 0; у имён
    с не-ASCII исходником (реплики в w2scene) хэш не пересчитывается, поэтому исходные хэши хранятся;
  - crc таблицы = crc32 её байтов (у строк count = длина в байтах), у пустой таблицы (0, 0, 0);
  - crc заголовка = crc32 первых 0xA0 байт (заголовок + 10 таблиц), где поле crc = 0xDEADBEEF;
  - crc экспорта = crc32 его данных; данные экспортов подряд после таблиц, fileSize = их конец;
  - буферы внутри файла идут сразу за экспортами, bufferSize = их конец; у cooked-файлов буферы внешние
    (`*.N.buffer`, offset 0, bufferSize == fileSize); хвост после bufferSize (миниатюра редактора) переносится как есть.
"""
import struct
import sys
import zlib

HDR = struct.Struct("<4sIIQIIIII")


class CR2W:
    def __init__(self, data, base=0):
        self.d = data
        self.base = base
        magic, self.version, self.flags, ts, build, self.file_size, self.buffer_size, crc, nchunks = \
            HDR.unpack_from(data, base)
        if magic != b"CR2W":
            raise ValueError("not CR2W")
        tb = base + HDR.size
        self.tables = [struct.unpack_from("<III", data, tb + 12 * i) for i in range(10)]
        so, ssize, _ = self.tables[0]
        self.strings = data[base + so: base + so + ssize]
        self.names = [self.str_at(v) for v, h in self._tab(1, "<II")]
        self.imports = [(self.str_at(p), self.name(c), fl) for p, c, fl in self._tab(2, "<IHH")]
        self.exports = [dict(cls=self.name(c), flags=f, parent=p, size=s, off=o, tmpl=t)
                        for c, f, p, s, o, t, crc in self._tab(4, "<HHIIIII")]
        self.buffers = list(self._tab(5, "<IIIIII"))
        self.embedded = list(self._tab(6, "<IIQII"))

    def _tab(self, i, fmt):
        off, cnt, _ = self.tables[i]
        st = struct.Struct(fmt)
        for k in range(cnt):
            yield st.unpack_from(self.d, self.base + off + k * st.size)

    def str_at(self, o):
        e = self.strings.find(b"\0", o)
        return self.strings[o:e].decode("latin-1")

    def name(self, i):
        return self.names[i] if 0 <= i < len(self.names) else f"#{i}"

    # ---- разбор свойств (best effort) ----
    def props(self, start, end, depth=0):
        d = self.d
        p = start
        out = []
        if p < end and d[p] == 0:
            p += 1
        while p + 8 <= end:
            ni, ti, sz = struct.unpack_from("<HHI", d, p)
            if ni == 0:
                break
            if sz < 4 or p + 4 + sz > end + 4:
                out.append(("<bad>", "", None))
                break
            vstart, vend = p + 8, p + 4 + sz
            name, typ = self.name(ni), self.name(ti)
            out.append((name, typ, self.value(typ, vstart, vend, depth)))
            p = vend
        return out

    def value(self, typ, s, e, depth):
        d = self.d
        n = e - s
        try:
            if typ == "Float" and n == 4:
                return round(struct.unpack_from("<f", d, s)[0], 4)
            if typ in ("Uint32", "Int32") and n == 4:
                return struct.unpack_from("<i" if typ == "Int32" else "<I", d, s)[0]
            if typ in ("Bool", "Uint8", "Int8") and n == 1:
                return d[s]
            if typ in ("Uint16", "Int16") and n == 2:
                return struct.unpack_from("<H", d, s)[0]
            if typ == "CName" and n == 2:
                return self.name(struct.unpack_from("<H", d, s)[0])
            if typ.startswith(("handle:", "ptr:")) and n == 4:
                v = struct.unpack_from("<i", d, s)[0]
                if v > 0 and v - 1 < len(self.exports):
                    return f"-> chunk{v - 1}:{self.exports[v - 1]['cls']}"
                if v < 0 and -v - 1 < len(self.imports):
                    return f"-> import:{self.imports[-v - 1][0]}"
                return v
            if typ in ("EDrawableFlags", "EPathLibCollision", "CPhysicalCollision") or (typ.startswith("E") and n % 2 == 0 and d[e-2:e] == b"\0\0" and n > 2):
                if typ == "CPhysicalCollision":
                    return d[s:e].hex()
                names = []
                q = s
                while q + 2 <= e:
                    v = struct.unpack_from("<H", d, q)[0]
                    q += 2
                    if v == 0:
                        break
                    names.append(self.name(v))
                return "{" + "|".join(names) + "}" if n > 2 or typ == "EDrawableFlags" else self.name(struct.unpack_from("<H", d, s)[0])
            if typ.startswith("soft:") and n == 2:
                v = struct.unpack_from("<H", d, s)[0]
                return f"soft:{self.imports[v - 1][0]}" if 0 < v <= len(self.imports) else v
            if typ.startswith("array:"):
                cnt = struct.unpack_from("<I", d, s)[0]
                inner = typ.split(",", 2)[2] if typ.count(",") >= 2 else typ[6:]
                if inner == "Uint8":
                    return f"<{cnt} bytes>"
                if inner.startswith(("handle:", "ptr:")) and n == 4 + 4 * cnt:
                    return [self.value(inner, s + 4 + 4 * k, s + 8 + 4 * k, depth) for k in range(cnt)]
                if inner == "CName" and n == 4 + 2 * cnt:
                    return [self.name(struct.unpack_from("<H", d, s + 4 + 2 * k)[0]) for k in range(cnt)]
                if inner.startswith("soft:") and n == 4 + 2 * cnt:
                    return [self.value(inner, s + 4 + 2 * k, s + 6 + 2 * k, depth) for k in range(cnt)]
                return f"<array {inner} x{cnt}, {n} bytes>"
            if typ == "String":
                raw = d[s:e]
                return "'" + "".join(chr(c) for c in raw[1:] if 32 <= c < 127) + "'"
            if typ == "EngineTransform":
                fl = d[s]
                q = s + 1
                r = {}
                for bit, key in ((1, "pos"), (2, "rot"), (4, "scale")):
                    if fl & bit:
                        r[key] = tuple(round(v, 4) for v in struct.unpack_from("<3f", d, q))
                        q += 12
                return r or "identity"
            if depth < 4 and n >= 9 and typ[:1] in "CSBVEM" or typ in ("Box", "Vector", "EulerAngles", "Matrix"):
                sub = self.props(s, e, depth + 1)
                if sub and sub[-1][0] != "<bad>":
                    return {k: v for k, t, v in sub}
            if n <= 4:
                return d[s:e].hex()
            return f"<{n} bytes>"
        except Exception as ex:  # noqa
            return f"<err {ex}>"

    def export_props(self, i):
        x = self.exports[i]
        s = self.base + x["off"]
        return self.props(s, s + x["size"])


def name_hash(s):
    """Хэш имени в таблице names: FNV-1a32 по байтам строки вместе с NUL; у пустого имени 0."""
    if not s:
        return 0
    h = 0x811C9DC5
    for c in s.encode("latin-1") + b"\0":
        h = ((h ^ c) * 0x01000193) & 0xFFFFFFFF
    return h


class CR2WDoc:
    """Изменяемая модель CR2W (раскладка с таблицами, v155..v164) с пересборкой в байты.

    Хранит имена, импорты, сырые данные экспортов и внутренних буферов. Ссылки внутри данных — индексы имён
    (u16) и импортов (handle = -(i+1)), поэтому смена пути импорта или добавление имени в конец списка
    не трогает данные экспортов. Свойства правятся на уровне записей {u16 name, u16 type, u32 size, value}.
    """

    ROW = (1, 8, 8, 16, 24, 24, 24)  # байт на строку таблиц 0..6 (у строк count = длина в байтах)

    def __init__(self, data):
        if data[:4] != b"CR2W":
            raise ValueError("not CR2W")
        _, self.version, self.flags, self.timestamp, self.build, file_size, buffer_size, _, nchunks = \
            HDR.unpack_from(data, 0)
        tabs = [struct.unpack_from("<III", data, HDR.size + 12 * i) for i in range(10)]
        if nchunks != 6 or tabs[0][0] != 0xA0 or any(t[1] for t in tabs[7:]):
            raise ValueError(f"CR2W v{self.version}: раскладка без таблиц (старый формат) не поддерживается")
        if tabs[6][1]:
            raise NotImplementedError("embedded-таблица не поддерживается")

        def rows(i, fmt):
            off, cnt, _ = tabs[i]
            st = struct.Struct(fmt)
            return [st.unpack_from(data, off + k * st.size) for k in range(cnt)]

        so, ssize, _ = tabs[0]
        strings = data[so:so + ssize]

        def s(o):
            return strings[o:strings.index(b"\0", o)].decode("latin-1")

        self.str_order = [x.decode("latin-1") for x in strings.split(b"\0")[1:-1]]  # порядок строк в исходнике
        self.names = [s(o) for o, _ in rows(1, "<II")]
        # исходные хэши: у редких имён с не-ASCII текстом (реплики в w2scene) хэш считали по исходному тексту,
        # а в строки легла ASCII-версия; для таких имён пересчёт не совпадает, поэтому старые храним
        self.name_hashes = {s(o): h for o, h in rows(1, "<II")}
        self.imports = [[s(o), c, fl] for o, c, fl in rows(2, "<IHH")]
        self.properties = (tabs[3][1], data[tabs[3][0]:tabs[3][0] + 16 * tabs[3][1]])
        self.exports = []
        for c, f, p, sz, off, t, crc in rows(4, "<HHIIIII"):
            d = data[off:off + sz]
            self.exports.append(dict(cls=c, flags=f, parent=p, template=t, data=d, crc=crc, src=d))
        self.buffers = []
        for fl, idx, off, disk, mem, crc in rows(5, "<IIIIII"):
            inside = disk > 0 and off >= file_size and off + disk <= buffer_size
            d = data[off:off + disk] if inside else None
            self.buffers.append(dict(flags=fl, index=idx, offset=off, disk=disk, mem=mem, crc=crc, data=d, src=d))
        self.tail = data[max(file_size, buffer_size):]

    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            return cls(f.read())

    # ---- имена и импорты ----
    def name_index(self, s, add=True):
        if s in self.names:
            return self.names.index(s)
        if not add:
            raise KeyError(s)
        self.names.append(s)
        return len(self.names) - 1

    def import_index(self, path):
        for i, (p, _, _) in enumerate(self.imports):
            if p.lower() == path.lower():
                return i
        raise KeyError(path)

    def add_import(self, path, cls_name, flags=0):
        """-> handle (-(i+1)); существующий путь не дублируется."""
        try:
            return -(self.import_index(path) + 1)
        except KeyError:
            self.imports.append([path, self.name_index(cls_name), flags])
            return -len(self.imports)

    def set_import_path(self, old, new):
        self.imports[self.import_index(old)][0] = new

    # ---- свойства ----
    def prop_records(self, data):
        """[(name, type, rec_start, value_start, value_end)] записей верхнего уровня; последняя позиция — терминатор."""
        p = 1 if data[:1] == b"\0" else 0
        out = []
        while p + 2 <= len(data):
            ni = struct.unpack_from("<H", data, p)[0]
            if ni == 0:
                break
            ti, sz = struct.unpack_from("<HI", data, p + 2)
            out.append((self.names[ni], self.names[ti], p, p + 8, p + 4 + sz))
            p += 4 + sz
        return out, p

    def get_prop(self, data, name):
        for n, t, _, vs, ve in self.prop_records(data)[0]:
            if n == name:
                return t, data[vs:ve]
        raise KeyError(name)

    def set_prop(self, data, name, value, type_name=None):
        """-> новые данные: значение заменено (или запись добавлена перед терминатором с type_name)."""
        recs, end = self.prop_records(data)
        for n, t, rs, vs, ve in recs:
            if n == name:
                rec = struct.pack("<HHI", self.name_index(n), self.name_index(t), 4 + len(value)) + value
                return data[:rs] + rec + data[ve:]
        if type_name is None:
            raise KeyError(name)
        rec = struct.pack("<HHI", self.name_index(name), self.name_index(type_name), 4 + len(value)) + value
        return data[:end] + rec + data[end:]

    def get_nested(self, data, name):
        """CR2W внутри свойства array:...,Uint8 (flatCompiledData, streamingDataBuffer) -> CR2WDoc."""
        _, v = self.get_prop(data, name)
        return CR2WDoc(v[4:4 + struct.unpack_from("<I", v)[0]])

    def set_nested(self, data, name, doc):
        b = doc.to_bytes()
        return self.set_prop(data, name, struct.pack("<I", len(b)) + b)

    def material_params(self, data):
        """CMaterialInstance: [(name, type, value)] из хвоста после свойств ({u32 count, {u32 size, u16, u16, v}})."""
        _, end = self.prop_records(data)
        p = end + 2
        cnt = struct.unpack_from("<I", data, p)[0]
        p += 4
        out = []
        for _ in range(cnt):
            sz, ni, ti = struct.unpack_from("<IHH", data, p)
            out.append((self.names[ni], self.names[ti], data[p + 8:p + sz]))
            p += sz
        return out

    def set_material_params(self, data, params):
        _, end = self.prop_records(data)
        body = b"".join(struct.pack("<IHH", 8 + len(v), self.name_index(n), self.name_index(t)) + v
                        for n, t, v in params)
        return data[:end + 2] + struct.pack("<I", len(params)) + body

    # ---- сборка ----
    def to_bytes(self):
        strings = bytearray(b"\0")
        soff = {"": 0}
        used = self.names + [p for p, _, _ in self.imports]
        need = set(used)
        for s in [x for x in self.str_order if x in need] + used:  # исходный порядок строк, новые - в конец
            if s not in soff:
                soff[s] = len(strings)
                strings += s.encode("latin-1") + b"\0"
        names = b"".join(struct.pack("<II", soff[n], self.name_hashes.get(n, name_hash(n))) for n in self.names)
        imports = b"".join(struct.pack("<IHH", soff[p], c, fl) for p, c, fl in self.imports)
        pcount, props = self.properties
        tables = [bytes(strings), names, imports, props]
        sizes = [len(t) for t in tables] + [24 * len(self.exports), 24 * len(self.buffers)]
        data_start = 0xA0 + sum(sizes)
        exp_rows, pos = [], data_start
        for x in self.exports:
            crc = x["crc"] if x["data"] == x["src"] else zlib.crc32(x["data"])
            exp_rows.append(struct.pack("<HHIIIII", x["cls"], x["flags"], x["parent"], len(x["data"]), pos,
                                        x["template"], crc))
            pos += len(x["data"])
        file_size = pos
        buf_rows, buf_data = [], []
        for b in self.buffers:
            if b["data"] is None:
                buf_rows.append(struct.pack("<IIIIII", b["flags"], b["index"], b["offset"], b["disk"], b["mem"], b["crc"]))
                continue
            changed = b["data"] != b["src"]
            crc = zlib.crc32(b["data"]) if changed else b["crc"]
            mem = len(b["data"]) if changed else b["mem"]
            buf_rows.append(struct.pack("<IIIIII", b["flags"], b["index"], pos, len(b["data"]), mem, crc))
            buf_data.append(b["data"])
            pos += len(b["data"])
        tables += [b"".join(exp_rows), b"".join(buf_rows)]
        heads, off = [], 0xA0
        for i, t in enumerate(tables):
            cnt = (len(t) // self.ROW[i]) if i != 3 else pcount
            heads.append((off, cnt, zlib.crc32(t)) if cnt else (0, 0, 0))
            off += len(t)
        heads += [(0, 0, 0)] * (10 - len(heads))
        head = bytearray(HDR.pack(b"CR2W", self.version, self.flags, self.timestamp, self.build, file_size, pos,
                                  0xDEADBEEF, 6) + b"".join(struct.pack("<III", *h) for h in heads))
        struct.pack_into("<I", head, 0x20, zlib.crc32(head))
        return bytes(head) + b"".join(tables) + b"".join(x["data"] for x in self.exports) + b"".join(buf_data) + self.tail


def find_all(data, needle):
    i = data.find(needle)
    while i != -1:
        yield i
        i = data.find(needle, i + 1)


KEY_CLASSES = ("CEntityTemplate", "CEntity", "CGameplayEntity", "CMeshComponent", "CStaticMeshComponent",
               "CRigidMeshComponent", "CRigidMeshComponentCooked", "CMesh", "CCollisionMesh",
               "CCollisionShapeBox", "CCollisionShapeConvex", "CCollisionShapeTriMesh", "CMaterialInstance",
               "CEntityAppearance", "CAppearanceComponent", "CPhysicsDestructionComponent",
               "CDestructionSystemComponent", "CInteractionComponent", "CBoundedComponent")

SHOW_PROPS = {"CMesh": ("boundingBox", "collisionMesh", "materials", "useExtraStreams", "isStatic",
                        "entityProxy", "autoHideDistance", "isTwoSided", "mergeInGlobalShadowMesh"),
              "CCollisionShapeBox": ("halfExtends", "pose", "physicalMaterials"),
              "CCollisionShapeConvex": ("physicalMaterials",),
              "CCollisionShapeTriMesh": ("physicalMaterials",),
              "CMaterialInstance": ("baseMaterial", "enableMask", "InstanceParameters"),
              "CStaticMeshComponent": ("mesh", "name", "transform", "pathLibCollisionType", "fadeOnCameraCollision",
                                       "physicalCollisionType", "drawableFlags", "boundingBox", "forceAutoHideDistance"),
              "CMeshComponent": ("mesh", "name", "transform", "drawableFlags", "boundingBox"),
              "CRigidMeshComponent": ("mesh", "name", "transform", "motionType", "physicalCollisionType", "linearDamping"),
              "CEntityTemplate": ("appearances", "includes", "entityClass", "coloringEntries",
                                  "usedAppearances", "flatCompiledData", "cookedEffects"),
              "CEntity": ("components", "name", "autoPlayEffectName", "streamingDistance"),
              "CGameplayEntity": ("components", "name"),
              }


def report(path, data, base, depth=0):
    c = CR2W(data, base)
    pad = "  " * depth
    print(f"{pad}CR2W v{c.version} @+{base}: names={len(c.names)} imports={len(c.imports)} "
          f"exports={len(c.exports)} buffers={len(c.buffers)} embedded={len(c.embedded)}")
    classes = {}
    for x in c.exports:
        classes[x["cls"]] = classes.get(x["cls"], 0) + 1
    print(f"{pad} export classes: " + ", ".join(f"{k}x{v}" if v > 1 else k for k, v in classes.items()))
    for imp, cls, fl in c.imports:
        print(f"{pad} import [{cls}] {imp}")
    for i, x in enumerate(c.exports):
        keys = SHOW_PROPS.get(x["cls"])
        if not keys:
            continue
        pr = c.export_props(i)
        shown = [(k, v) for k, t, v in pr if k in keys]
        allk = [k for k, t, v in pr]
        print(f"{pad} chunk{i} {x['cls']}: " + "; ".join(f"{k}={v}" for k, v in shown)
              + f"   [props: {', '.join(allk)}]")
    nameset = [n for n in c.names if n and not n[0].isupper() and ":" not in n]
    print(f"{pad} lowercase names: {nameset[:60]}")
    return c


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for path in sys.argv[1:]:
        data = open(path, "rb").read()
        print("=" * 100)
        print(path, len(data), "bytes")
        offs = list(find_all(data, b"CR2W"))
        for k, o in enumerate(offs):
            try:
                report(path, data, o, 0 if o == 0 else 1)
            except Exception as ex:
                print(f"  (CR2W-like at {o} not parsed: {ex})")


if __name__ == "__main__":
    main()
