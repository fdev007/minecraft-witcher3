"""Извлечь из одного .bundle все записи, в депо-пути которых есть подстрока. Только чтение бандла.

    python bundle_extract.py <bundle> <подстрока-пути> <outdir>

outdir обязателен и не может лежать ни внутри папки игры, ни внутри проекта minecraft-witcher3. Извлечённое — копии игровых файлов: держать во временной
папке, в проект и в мод не класть. Раскладку записей (0x130, u64-смещения, crc) разбирает w3bundle.read_table,
здесь только отбор по подстроке и проверка CRC.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import w3bundle  # noqa: E402


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    bundle, needle, outdir = sys.argv[1], sys.argv[2].lower(), sys.argv[3]
    w3bundle.refuse_extract_target(outdir)
    _, items = w3bundle.read_table(bundle)
    hits = [it for it in items if it["name"] and needle in it["name"].lower()]
    if not hits:
        sys.exit(f"no entries matching {needle!r} in {bundle}")
    for it in hits:
        try:
            data, ok = w3bundle.extract(bundle, it)
        except NotImplementedError as e:
            print(f"{it['name']}: {e} (size={it['size']} zsize={it['zsize']})")
            continue
        p = os.path.join(outdir, it["name"].replace("\\", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
        print(f"{it['name']}: comp={w3bundle.COMP.get(it['comp'], it['comp'])} size={it['size']} crc_ok={ok} -> {p}")


if __name__ == "__main__":
    main()
