"""Собирает spike/templates.json: метаданные кандидатов-шаблонов из бандлов игры. Игру только читает.

    python gen_templates.py <out.json> [--candidates <file.tsv>] [--game <папка игры>]

Кандидаты: CANDIDATES ниже (депо-пути, отобранные при разведке ассетов) + первая колонка --candidates (TSV с
заголовком) + все Furniture из game\\photomode_props.csv (читается прямо из startup.bundle, на диск не пишется).
Алиасы — gameplay\\globals\\resources\\gameplay.xml (xml.bundle), коллизия — content0\\collision.cache.
В json попадают только метаданные: пути, классы, bbox, transform, physmat. Извлечённые байты нигде не сохраняются.
После хотфикса игры: перезапустить и сравнить diff templates.json.
"""
import argparse
import csv
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collcache  # noqa: E402
import cr2w  # noqa: E402
import w3bundle  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "spike"))
import w3paths  # noqa: E402  (spike\w3paths.py: где стоят игра, REDkit, Minecraft)

GAME = w3paths.game_dir() or r"C:\Program Files (x86)\Steam\steamapps\common\The Witcher 3"

CANDIDATES = [
    r"gameplay\containers\_container_definitions\_mesh_entities\cont_crate.w2ent",
    r"gameplay\interactive_objects\oil_barrel\oil_barrel.w2ent",
    r"gameplay\explorations_reference_entiti\box_250cm.w2ent",
    r"quests\sidequests\skellige\quest_files\sq204_forest_spirit\entities\box_05_05_1m.w2ent",
    r"dlc\bob\data\gameplay\container_definitions\ep2_loot_crafting__crate_close_a.w2ent",
    r"dlc\bob\data\quests\main_quests\quest_files\q704b_fairy_tale\entities\q704_ft_barrier_block.w2ent",
    r"dlc\bob\data\quests\main_quests\quest_files\q705_epilog\entities\q705_invisible_wall.w2ent",
    r"dlc\bob\data\quests\minor_quests\quest_files\mq7006_the_paths_of_destiny\entities\water_barrier.w2ent",
    r"dlc\bob\data\quests\minor_quests\quest_files\mq7023_mutations\entities\mq7023_portal_trigger.w2ent",
    r"dlc\dlc10\data\quests\entities\toxic_gas_7m_cube.w2ent",
    r"dlc\ep1\data\quests\quest_files\q601_intro\entities\q601_arena_block.w2ent",
    r"engine\reference_level\level_assets\tags\on.w2ent",
    r"engine\templates\editor\sticker.w2ent",
    r"gameplay\abilities\elemental\elemental_dao_pillar_arena.w2ent",
    r"gameplay\containers\_container_definitions\autogen\crate__generic_crate__everywhere.w2ent",
    r"gameplay\containers\_container_definitions\_unique_containers\_tree_cont_spiral.w2ent",
    r"gameplay\interactive_objects\rift\rift_bomb_collision.w2ent",
    r"items\work\carry_crate\carry_crate.w2ent",
    r"quests\generic_quests\no_mans_land\quest_files\mh101_cockatrice\entities\mh101_bomb_target.w2ent",
]
# Индекс шаблона в mcw_batch (КОНТРАКТ: 0 cont_crate, 1 barrel)
BATCH_T = {CANDIDATES[0]: 0, CANDIDATES[1]: 1}

NOTES = {
    CANDIDATES[0]: "основной куб спайка (batch t=0): голый CEntity, ящик ~1.03 м, pivot в центре; шаблон в бандле DLC bob",
    CANDIDATES[1]: "алиас barrel (batch t=1, mcw_boom): COilBarrelEntity, взрывается от SetOnFire; визуал — "
                   "APEX-разрушаемый CDestructionSystemComponent, поэтому bbox по мешу не снят [проверить в игре]",
    CANDIDATES[2]: "алиас denied_area: drawableFlags={} — НЕВИДИМ, только коллизия + 4 CExplorationComponent "
                   "(лазание); pivot снизу; physmat carpet; для куба 1 м SetScale(0.4)",
    CANDIDATES[3]: "невидимый коллайдер (drawableFlags={}), компонент НЕ стримится; эксперимент "
                   "SetScale(1,1,1)+SetVisible(true) даст серый куб 1 м [проверить]",
    CANDIDATES[14]: "тот же crate.w2mesh, но W3AnimatedContainer: лут и подсказка «Обыскать» — не для блоков",
    CANDIDATES[17]: "CItemEntity, ящик масштабом 0.5 — предмет в руках, не блок",
}

ABOUT = (
    "Кандидаты-шаблоны для блоков. Только метаданные из бандлов игры (без игровых файлов). Координаты — метры "
    "в пространстве сущности (Z вверх). bbox_min/bbox_max — AABB всех мешей (видимых и невидимых) с transform "
    "компонента как в шаблоне (pos + scale*bbox меша, поворот не учтён — флаг component_rotation). "
    "mesh_components — меш-компоненты по отдельности: pos, scale, rot компонента и mesh_bbox_* в пространстве меша; "
    "по ним coords.py считает bbox при SetScale из мода (SetScale заменяет scale компонента, pos остаётся). "
    "bundles — все бандлы, где лежит шаблон. batch_t — индекс шаблона в mcw_batch. physmat, collision_shapes, "
    "collision_tail3f — главного (самого объёмного) меша из collision.cache (tools/collcache.py show); "
    "collision_tail3f — полуразмеры только у одиночного box-шейпа. streamed=true: компонент лежит в "
    "streamingDataBuffer и грузится по дистанции; streamingDistance — из CEntity шаблона, единицы [проверить] "
    "(вероятно метры)."
)


class Depot:
    """Индекс депо по таблицам всех бандлов: путь (lower) -> [(bundle, item)]. Читаются только таблицы."""

    def __init__(self, game):
        self.bd = os.path.join(game, "content", "content0", "bundles")
        self.index = {}
        for bn in sorted(os.listdir(self.bd)):
            if bn.endswith(".bundle"):
                _, items = w3bundle.read_table(os.path.join(self.bd, bn))
                for it in items:
                    if it["name"]:
                        self.index.setdefault(it["name"].lower(), []).append((bn, it))

    def read(self, path):
        """-> (bytes | None, [bundle, ...]). Байты только в памяти."""
        hits = self.index.get(path.lower())
        if not hits:
            return None, []
        bn, it = hits[0]
        data, ok = w3bundle.extract(os.path.join(self.bd, bn), it)
        if not ok:
            raise ValueError(f"{path}: CRC не сошёлся в {bn}")
        return data, [b for b, _ in hits]


class Gen:
    def __init__(self, game):
        self.depot = Depot(game)
        self.cc = collcache.CollisionCache(os.path.join(game, "content", "content0", "collision.cache"))
        xml, _ = self.depot.read(r"gameplay\globals\resources\gameplay.xml")
        self.alias = {}       # путь (lower) -> алиас
        self.alias_path = {}  # алиас -> путь
        for a, p in re.findall(rb'<res\s+alias="([^"]+)"\s+path="([^"]+)"', xml):
            self.alias.setdefault(p.decode().lower(), a.decode())
            self.alias_path[a.decode()] = p.decode()
        self.mesh_cache = {}

    def mesh_info(self, path):
        if path in self.mesh_cache:
            return self.mesh_cache[path]
        data, _ = self.depot.read(path)
        r = dict(bbox=None, autoHideDistance=None)
        if data:
            c = cr2w.CR2W(data)
            for i, x in enumerate(c.exports):
                if x["cls"] == "CMesh":
                    for k, _t, v in c.export_props(i):
                        if k == "boundingBox" and isinstance(v, dict):
                            r["bbox"] = ([v["Min"]["X"], v["Min"]["Y"], v["Min"]["Z"]],
                                         [v["Max"]["X"], v["Max"]["Y"], v["Max"]["Z"]])
                        if k == "autoHideDistance":
                            r["autoHideDistance"] = v
                    break
        s = self.cc.summary(path)
        r["collision"] = s and dict(physmat=s["physmat"], shapes=s["shapes"],
                                    tail3f=list(s["tail3f"]) if s["tail3f"] else None)
        self.mesh_cache[path] = r
        return r

    def furniture(self):
        """-> [(путь, source, note)] для Furniture из game\\photomode_props.csv."""
        raw, _ = self.depot.read(r"game\photomode_props.csv")
        if raw is None:
            print("photomode_props.csv нет в бандлах: Furniture пропущены", file=sys.stderr)
            return []
        out = []
        for row in csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), delimiter=";"):
            p = self.alias_path.get(row.get("Entity", ""))
            if row.get("Categories") == "Furniture" and p:
                out.append((p, "game/photomode_props.csv (Furniture)",
                            f"реквизит фоторежима «{row['Name']}»: ваниль 5.0 спавнит его через LoadResource(alias)"))
        return out


def components(data):
    """-> (entity_class, streamingDistance, [component dict]) по всем вложенным CR2W, без дублей по (класс, имя)."""
    top = cr2w.CR2W(data)
    top_cls = [x["cls"] for x in top.exports]
    ent_cls = top_cls[1] if len(top_cls) > 1 else "?"
    stream_dist = None
    if len(top.exports) > 1:
        for k, _t, v in top.export_props(1):
            if k == "streamingDistance":
                stream_dist = v
    out, seen = [], set()
    for o in cr2w.find_all(data, b"CR2W"):
        try:
            c = cr2w.CR2W(data, o)
        except Exception:  # noqa: BLE001 - похожие на CR2W байты внутри буферов
            continue
        for i, x in enumerate(c.exports):
            cls = x["cls"]
            if "MeshComponent" not in cls and cls != "CDestructionSystemComponent":
                continue
            p = {k: v for k, _t, v in c.export_props(i)}
            name = p.get("name", "")
            if (cls, name) in seen:
                continue
            seen.add((cls, name))
            res = p.get("mesh") or p.get("resource")
            if cls == "CDestructionSystemComponent":
                res = next((imp for imp, ic, _ in c.imports if ic == "CApexDestructionResource"), None)
            res = str(res).split(":", 1)[-1].strip() if res else None
            out.append(dict(cls=cls, name=name.strip("'"), res=res, transform=p.get("transform", "identity"),
                            drawableFlags=p.get("drawableFlags", "default"),
                            streamed=(o != 0 and cls not in top_cls)))
    return ent_cls, stream_dist, out


def _r3(v):
    return [round(float(a), 4) for a in v]


def mesh_components(gen, comps):
    """Меш-компоненты с transform и bbox меша в его собственном пространстве (для SetScale в coords.py)."""
    out = []
    for cp in comps:
        tr = cp["transform"] if isinstance(cp["transform"], dict) else {}
        mi = gen.mesh_info(cp["res"]) if cp["res"] and cp["res"].endswith(".w2mesh") else {}
        bb = mi.get("bbox") if mi else None
        out.append(dict(cls=cp["cls"], name=cp["name"], mesh=cp["res"],
                        pos=_r3(tr.get("pos", (0, 0, 0))), scale=_r3(tr.get("scale", (1, 1, 1))),
                        rot=_r3(tr["rot"]) if "rot" in tr else None,
                        mesh_bbox_min=_r3(bb[0]) if bb else None, mesh_bbox_max=_r3(bb[1]) if bb else None,
                        drawableFlags=str(cp["drawableFlags"]), streamed=cp["streamed"]))
    return out


def entity_bbox(mcs):
    """AABB мешей в пространстве сущности: pos + scale * bbox меша. Поворот не учитываем, только флаг."""
    mn, mx, rotated = [1e9] * 3, [-1e9] * 3, False
    for m in mcs:
        if m["mesh_bbox_min"] is None:
            continue
        rotated |= m["rot"] is not None
        for a in range(3):
            lo, hi = sorted((m["pos"][a] + m["scale"][a] * m["mesh_bbox_min"][a],
                             m["pos"][a] + m["scale"][a] * m["mesh_bbox_max"][a]))
            mn[a], mx[a] = min(mn[a], lo), max(mx[a], hi)
    if mn[0] > mx[0]:
        return None, rotated
    return ([round(v, 3) for v in mn], [round(v, 3) for v in mx]), rotated


def pivot_kind(bb):
    mn, mx = bb
    c = [(mn[a] + mx[a]) / 2 for a in range(3)]
    size = [mx[a] - mn[a] for a in range(3)]
    xy_center = abs(c[0]) < 0.1 * max(size[0], 0.1) and abs(c[1]) < 0.1 * max(size[1], 0.1)
    if xy_center and abs(c[2]) < 0.1 * max(size[2], 0.1):
        return "center"
    if xy_center and abs(mn[2]) < 0.05:
        return "bottom-center"
    return "offset"


def entry(gen, path, source, extra_note=""):
    data, bundles = gen.depot.read(path)
    rec = dict(id=gen.alias.get(path.lower()) or os.path.splitext(os.path.basename(path))[0],
               path=path, alias=gen.alias.get(path.lower()), source=source, bundles=bundles)
    if path in BATCH_T:
        rec["batch_t"] = BATCH_T[path]
    if data is None:
        rec["note"] = "нет в бандлах этой версии игры"
        return rec
    ent_cls, sdist, comps = components(data)
    mcs = mesh_components(gen, comps)
    bb, rotated = entity_bbox(mcs)
    # drawableFlags не записан (default) — меш-компонент по умолчанию видим; APEX-разрушаемое тоже рисуется
    vis = [cp for cp in comps if "DF_IsVisible" in str(cp["drawableFlags"]) or cp["drawableFlags"] == "default"]

    def vol(m):
        b = gen.mesh_info(m)["bbox"]
        return (b[1][0] - b[0][0]) * (b[1][1] - b[0][1]) * (b[1][2] - b[0][2]) if b else 0

    meshes = [cp["res"] for cp in comps if cp["res"] and cp["res"].endswith(".w2mesh")]
    mesh0 = max(meshes, key=vol) if meshes else None  # главный меш = самый объёмный
    mi = gen.mesh_info(mesh0) if mesh0 else {}
    coll = (mi.get("collision") or {}) if mi else {}
    rec.update(
        entity_class=ent_cls,
        components=sorted({cp["cls"] for cp in comps}),
        meshes=sorted({cp["res"] for cp in comps if cp["res"]}),
        visible=bool(vis),
        drawableFlags=sorted({str(cp["drawableFlags"]) for cp in comps}),
        streamed=any(cp["streamed"] for cp in comps) if comps else None,
        streamingDistance=sdist,
        bbox_min=bb[0] if bb else None,
        bbox_max=bb[1] if bb else None,
        size=[round(bb[1][a] - bb[0][a], 3) for a in range(3)] if bb else None,
        pivot=pivot_kind(bb) if bb else None,
        component_rotation=rotated or None,
        mesh_components=mcs,
        main_mesh=mesh0,
        physmat=coll.get("physmat"),
        collision_shapes=coll.get("shapes"),
        collision_tail3f=coll.get("tail3f"),
        autoHideDistance=mi.get("autoHideDistance") if mi else None,
    )
    rec["note"] = "; ".join(x for x in (NOTES.get(path, ""), extra_note) if x) or None
    return rec


def mod_block_entries():
    """Записи блоков modMCWBlocks (tools/build_blocks.py): batch_t 2.. по порядку MVP, куб 1 м с pivot в центре,
    один нестримящийся CStaticMeshComponent. В бандлах игры их нет, поэтому записи строятся из констант сборки;
    в игре проверено 2026-10-03 (spike/RESULTS.md, «Шаблоны и блоки»)."""
    import build_blocks  # noqa: PLC0415 - тяжёлый модуль нужен только здесь
    out = []
    for i, b in enumerate(build_blocks.MVP):
        path = "mcw\\blocks\\" + b + ".w2ent"
        mesh = "mcw\\blocks\\" + b + "\\" + b + ".w2mesh"
        out.append(dict(
            id="mcw_" + b, path=path, alias=None, source="modMCWBlocks", bundles=[], batch_t=2 + i,
            entity_class="CEntity", components=["CStaticMeshComponent"], meshes=[mesh], visible=True,
            drawableFlags=["{DF_IsVisible|DF_CastShadows}"], streamed=False, streamingDistance=None,
            bbox_min=[-0.5, -0.5, -0.5], bbox_max=[0.5, 0.5, 0.5], size=[1.0, 1.0, 1.0], pivot="center",
            component_rotation=None,
            mesh_components=[dict(cls="CStaticMeshComponent", name="mcw_" + b, mesh=mesh, pos=[0.0, 0.0, 0.0],
                                  scale=[1.0, 1.0, 1.0], rot=None, mesh_bbox_min=[-0.5, -0.5, -0.5],
                                  mesh_bbox_max=[0.5, 0.5, 0.5], drawableFlags="{DF_IsVisible|DF_CastShadows}",
                                  streamed=False)],
            main_mesh=mesh, physmat=build_blocks.PHYSMAT.get(b, "wood_solid"), collision_shapes=1,
            collision_tail3f=[0.5, 0.5, 0.5], autoHideDistance=None,
            note="блок modMCWBlocks (tools/build_blocks.py); mcw_batch t=%d" % (2 + i)))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", help="куда писать json (например spike\\templates.json)")
    ap.add_argument("--candidates", help="TSV с заголовком, депо-путь в первой колонке")
    ap.add_argument("--game", default=GAME)
    a = ap.parse_args(argv)
    w3bundle.refuse_inside_game(a.out)

    paths = [(p, "candidates", "") for p in CANDIDATES]
    if a.candidates:
        with open(a.candidates, encoding="utf-8") as f:
            for row in list(csv.reader(f, delimiter="\t"))[1:]:
                if row and row[0] not in CANDIDATES:
                    paths.append((row[0], os.path.basename(a.candidates), ""))
    gen = Gen(a.game)
    paths += gen.furniture()

    recs, seen = [], set()
    for p, src, note in paths:
        if p.lower() in seen:
            continue
        seen.add(p.lower())
        recs.append(entry(gen, p, src, note))
    recs += mod_block_entries()
    doc = {"_about": ABOUT, "_generated_by": "tools/gen_templates.py (tools/w3bundle.py, cr2w.py, collcache.py)",
           "templates": recs}
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.write("\n")
    for r in recs:
        print(r["id"], r.get("entity_class"), r.get("size"), r.get("pivot"), r.get("streamed"),
              r.get("streamingDistance"), r.get("physmat"), r.get("visible"), len(r["bundles"]))


if __name__ == "__main__":
    main()
