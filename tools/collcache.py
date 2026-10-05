"""Чтение collision.cache Witcher 3 5.0 (CC3W v11; раскладка разобрана на этой установке). Только чтение.

    python collcache.py list <out.tsv> [<collision.cache>]
    python collcache.py show <depot_path.w2mesh> [...] [--cache <collision.cache>]

Заголовок: 0x00 'CC3W', 0x04 u32 version (11), 0x10 u64 смещение таблицы записей, 0x18 u32 число записей,
0x1C u32 смещение таблицы имён, 0x24 u32 её размер.
Запись 64 байта: 0x00 u32 смещение имени, 0x0C u64 смещение данных, 0x14 u32 zsize, 0x18 u32 size,
0x2C 4×f32 (колонка 'bbox?': похоже на полуразмеры AABB, не доказано), 0x3C u8 (колонка 'comp').
Данные записи сжаты zlib. Что в них видно (проверено на выборке 1909 записей, не полный разбор):
  0x0C u32 число шейпов; 0x10 u8 длина + имя physmat первого шейпа ('wood_solid', 'stone_solid', 'default' ...).
  Если имя 'None' и за ним не 0x80, дальше u8 k и k имён (материалы по треугольникам trimesh).
'show' печатает physmat (первое осмысленное имя), physmats (все имена с длиной-префиксом), shapes и tail3f —
последние 12 байт как 3×f32. tail3f — полуразмеры только у одиночного box-шейпа (запись ~110–120 байт);
у капсулы и прочих форм это другие параметры, у больших записей — мусор (тогда tail3f не печатается).
"""
import os
import re
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from w3bundle import refuse_inside_game  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "spike"))
import w3paths  # noqa: E402  (spike\w3paths.py: где стоят игра, REDkit, Minecraft)

DEFAULT = os.path.join(w3paths.game_dir() or r"C:\Program Files (x86)\Steam\steamapps\common\The Witcher 3", "content", "content0", "collision.cache")
NAME = re.compile(rb"([\x03-\x40])([A-Za-z_]{3,64})")
PRIMITIVE_MAX = 120  # байт: одиночный box/капсула; больше — convex/trimesh


class CollisionCache:
    def __init__(self, path=DEFAULT):
        self.path = path
        with open(path, "rb") as f:
            h = f.read(0x40)
            if h[:4] != b"CC3W":
                raise ValueError(f"{path}: bad magic {h[:4]!r}")
            self.version = struct.unpack_from("<I", h, 4)[0]
            info_off, n, names_off = struct.unpack_from("<QII", h, 0x10)
            names_size = struct.unpack_from("<I", h, 0x24)[0]
            f.seek(names_off)
            names = f.read(names_size)
            f.seek(info_off)
            info = f.read(64 * n)
        self.entries = []
        for i in range(n):
            e = info[i * 64:(i + 1) * 64]
            no = struct.unpack_from("<I", e, 0)[0]
            name = names[no:names.index(b"\0", no)].decode("latin-1")
            off, zs, sz = struct.unpack_from("<QII", e, 12)
            self.entries.append(dict(name=name, offset=off, zsize=zs, size=sz, comp=e[60],
                                     bbox=struct.unpack_from("<4f", e, 44)))
        self.by_name = {x["name"].lower(): x for x in self.entries}

    def data(self, entry):
        with open(self.path, "rb") as f:
            f.seek(entry["offset"])
            return zlib.decompress(f.read(entry["zsize"]))

    def summary(self, mesh_path):
        """-> dict(physmat, physmats, shapes, tail3f, size) или None, если меша нет в кэше."""
        e = self.by_name.get(mesh_path.lower())
        if not e:
            return None
        d = self.data(e)
        shapes = struct.unpack_from("<I", d, 0x0C)[0] if len(d) >= 0x11 else None
        names = [m.group(2).decode() for m in NAME.finditer(d) if m.group(1)[0] == len(m.group(2))]
        first = None
        if len(d) > 0x11 and d[0x10] and d[0x11:0x11 + d[0x10]].replace(b"_", b"").isalpha():
            first = d[0x11:0x11 + d[0x10]].decode()
        physmat = first if first and first != "None" else next((n for n in names if n != "None"), first)
        tail = None
        if shapes == 1 and len(d) <= PRIMITIVE_MAX:
            tail = tuple(round(v, 3) for v in struct.unpack_from("<3f", d, len(d) - 12))
        return dict(physmat=physmat, physmats=sorted(set(names) - {"None"}), shapes=shapes, tail3f=tail,
                    size=len(d))


def main():
    args = sys.argv[1:]
    if not args or args[0] not in ("list", "show"):
        sys.exit(__doc__)
    cache = DEFAULT
    if "--cache" in args:
        i = args.index("--cache")
        cache = args[i + 1]
        del args[i:i + 2]
    if args[0] == "list":
        if len(args) < 2:
            sys.exit(__doc__)
        if len(args) > 2:
            cache = args[2]
        refuse_inside_game(args[1])
        cc = CollisionCache(cache)
        with open(args[1], "w", encoding="utf-8", newline="\n") as out:
            out.write("path\tpage_offset\tzsize\tsize\tcomp\tbbox?\n")
            for x in cc.entries:
                out.write(f"{x['name']}\t{x['offset']}\t{x['zsize']}\t{x['size']}\t{x['comp']}\t{x['bbox']}\n")
        print("version", cc.version, "entries", len(cc.entries))
    else:
        cc = CollisionCache(cache)
        for m in args[1:]:
            print(m, cc.summary(m) or "not in collision.cache")


if __name__ == "__main__":
    main()
