"""Координаты Minecraft <-> Witcher 3 и строки протокола modMCW (разбор ответов, сборка mcw_batch). Только stdlib.

Оси.
  Ведьмак: метры, X восток, Y север, Z вверх. Yaw — градусы против часовой от +Y: forward = (-sin yaw, cos yaw, 0)
    (так поворачивает ваниль: photomodeMenu.ws:3829-3830).
  Майнкрафт: блок = 1 м, X восток, Y вверх, Z юг. Yaw 0 = +Z (юг), 90 = -X (запад); pitch > 0 — взгляд вниз.
Позиции и блоки — одно правило: сначала непрерывная позиция Майнкрафта, блок = её floor, как в самом Майнкрафте.
  mc_pos = (w.x, w.z + Y_OFFSET, 1 - w.y)          игрок, сущности, точки попадания луча
  блок   = floor(mc_pos) = (floor(w.x), floor(w.z) + Y_OFFSET, floor(1 - w.y))
  центр блока в Ведьмаке = (mc.x + 0.5, -mc.z + 0.5, mc.y - Y_OFFSET + 0.5)   (как в docs/PLAN.md)
  Сдвиг на 1 по Z обязателен: голое mc_pos.z = -w.y даёт соседний блок для любой дробной w.y.
  Отличие от docs/PLAN.md "mc.z = -floor(w.y)": только при точно целой w.y (граница клеток). Здесь клетка блока m по
  оси Y Ведьмака (-m, 1-m], в PLAN [-m, 1-m); центр тот же. Протокол даёт целые см, поэтому граница попадается
  в 1% опросов, и по формуле PLAN игрок на границе стоял бы не в том блоке, что floor(его mc_pos). Блок по
  ответу игры считать только через w_to_block / cm_to_block / Pos.block, не своей формулой.

Числа в протоколе целые (КОНТРАКТ): координаты в см, углы в сотых градуса, направления x10000, время в мс.
Каждая строка ответа: "[MCW#<seq>] <kind> ...", последняя — "[MCW#<seq>] end".
mcw_batch(seq, "set:x:y:z:t|clear:x:y:z|..."): x, y, z — мировые см точки, куда ставится origin сущности
(центр блока минус pivot шаблона), t — индекс шаблона 0..12 (0 cont_crate, 1 barrel, 2..12 блоки modMCWBlocks;
поле batch_t в templates.json).
Команда целиком не длиннее MAX_CMD = 2047 байт utf8: длиннее игра молча выбрасывает (замер 5.00c, w3net.EXEC_MAX_CMD).
mcw_ents(seq, "spawn:id:x:y:z:yaw:t|move:id:x:y:z:yaw|kill:id|wipe"): мобы моста по слоту id (0..63), origin — низ
bbox шаблона на ногах моба (ent_origin_w), yaw — курс Ведьмака в сотых градуса.
"""
import json
import math
import os
import re
from dataclasses import dataclass, field

Y_OFFSET = 0          # подбирается под dimension_type трека mc; диапазон высот мира Ведьмака [проверить] на спайке
# потолок команды exec в байтах utf8 = w3net.EXEC_MAX_CMD (замер 5.00c 2026-10-03: команду от 2048 байт игра молча
# выбрасывает, ответа нет). Число повторено, чтобы coords не тянул сетевой модуль; совпадение сверяет test_coords.
MAX_CMD = 2047
DEFAULT_BATCH = 1900  # рабочий размер команды mcw_batch (запас до MAX_CMD), как batch.bytes моста
TEMPLATES_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates.json")


# ---- блоки и позиции ---------------------------------------------------------------------------------------
def w_to_block(wx, wy, wz, y_offset=Y_OFFSET):
    """Точка Ведьмака (м) -> блок Майнкрафта (int x, y, z) = floor(w_to_mc_pos(...))."""
    return math.floor(wx), math.floor(wz) + y_offset, math.floor(1.0 - wy)


def cm_to_block(x_cm, y_cm, z_cm, y_offset=Y_OFFSET):
    """То же для целых см из протокола, без float: // в Python округляет вниз и для отрицательных."""
    return x_cm // 100, z_cm // 100 + y_offset, (100 - y_cm) // 100


def block_center_w(bx, by, bz, y_offset=Y_OFFSET):
    """Блок Майнкрафта -> центр блока в Ведьмаке (м)."""
    return bx + 0.5, -bz + 0.5, by - y_offset + 0.5


def w_to_mc_pos(wx, wy, wz, y_offset=Y_OFFSET):
    """Непрерывная позиция Ведьмака (м) -> позиция Майнкрафта; floor() даёт тот же блок, что w_to_block."""
    return wx, wz + y_offset, 1.0 - wy


def mc_pos_to_w(mx, my, mz, y_offset=Y_OFFSET):
    return mx, 1.0 - mz, my - y_offset


def _norm180(a):
    """Угол в (-180, 180]."""
    a = math.fmod(a, 360.0)
    if a <= -180.0:
        a += 360.0
    elif a > 180.0:
        a -= 360.0
    return a


def w_yaw_to_mc(yaw):
    """Yaw Ведьмака (град) -> yaw Майнкрафта (град, (-180, 180]). Север: 0 -> 180, запад: 90 -> 90."""
    return _norm180(180.0 - yaw)


def mc_yaw_to_w(yaw):
    return _norm180(180.0 - yaw)


def w_dir_to_mc_angles(dx, dy, dz):
    """Вектор взгляда Ведьмака -> (yaw, pitch) Майнкрафта в градусах. Длина вектора любая, кроме нулевой."""
    mx, my, mz = dx, dz, -dy
    n = math.sqrt(mx * mx + my * my + mz * mz)
    if n == 0:
        raise ValueError("нулевой вектор направления")
    yaw = math.degrees(math.atan2(-mx, mz)) if (mx or mz) else 0.0
    pitch = -math.degrees(math.asin(max(-1.0, min(1.0, my / n))))
    return _norm180(yaw), pitch


# ---- шаблоны и pivot -----------------------------------------------------------------------------------------
# Масштаб — как в моде: mcw_alias(seq, alias, scale) при scale > 0 делает SetScale(Vector(s, s, s)) каждому
# меш-компоненту, то есть ЗАМЕНЯЕТ scale компонента, а его pos остаётся. scale=None (или <= 0) — шаблон как есть
# (mcw_batch, mcw_cube и mcw_stress масштаб не трогают).
@dataclass(frozen=True)
class MeshComp:
    """Меш-компонент шаблона: transform в сущности и bbox меша в его собственном пространстве (м)."""
    pos: tuple
    scale: tuple
    mesh_min: tuple
    mesh_max: tuple
    rotated: bool = False


@dataclass(frozen=True)
class Template:
    id: str
    path: str
    alias: str | None = None
    batch_t: int | None = None
    bbox_min: tuple = (-0.5, -0.5, -0.5)
    bbox_max: tuple = (0.5, 0.5, 0.5)
    known_bbox: bool = True
    comps: tuple = ()

    @property
    def size(self):
        return tuple(self.bbox_max[a] - self.bbox_min[a] for a in range(3))

    def bbox_at(self, scale=None):
        """AABB сущности (м) при масштабе мода: (min, max). Без scale — bbox из templates.json."""
        if scale is None or scale <= 0:
            return self.bbox_min, self.bbox_max
        if not self.comps:
            if self.known_bbox:
                raise ValueError(f"{self.id}: в templates.json нет mesh_components, масштаб не посчитать")
            # bbox неизвестен (barrel: APEX): условный куб 1 м с центром в origin
            return tuple(-0.5 * scale for _ in range(3)), tuple(0.5 * scale for _ in range(3))
        if any(c.rotated for c in self.comps):
            raise ValueError(f"{self.id}: компонент повёрнут, bbox при SetScale здесь не считается")
        mn, mx = [math.inf] * 3, [-math.inf] * 3
        for c in self.comps:
            for a in range(3):
                lo, hi = sorted((c.pos[a] + scale * c.mesh_min[a], c.pos[a] + scale * c.mesh_max[a]))
                mn[a], mx[a] = min(mn[a], lo), max(mx[a], hi)
        return tuple(mn), tuple(mx)

    def pivot_offset(self, scale=None):
        """Центр bbox относительно origin сущности (м) при масштабе мода. Для cont_crate почти (0, 0, 0)."""
        mn, mx = self.bbox_at(scale)
        return tuple((mn[a] + mx[a]) / 2 for a in range(3))


def _mesh_comps(r):
    out = []
    for m in r.get("mesh_components") or ():
        if m.get("mesh_bbox_min") is None:
            continue
        out.append(MeshComp(pos=tuple(m["pos"]), scale=tuple(m["scale"]), mesh_min=tuple(m["mesh_bbox_min"]),
                            mesh_max=tuple(m["mesh_bbox_max"]), rotated=m.get("rot") is not None))
    return tuple(out)


def load_templates(path=TEMPLATES_JSON):
    """-> {id: Template}. Шаблон без bbox (barrel: APEX) получает куб 1 м с pivot в центре и known_bbox=False."""
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    out = {}
    for r in doc["templates"]:
        has = r.get("bbox_min") is not None
        out[r["id"]] = Template(
            id=r["id"], path=r["path"], alias=r.get("alias"), batch_t=r.get("batch_t"),
            bbox_min=tuple(r["bbox_min"]) if has else (-0.5, -0.5, -0.5),
            bbox_max=tuple(r["bbox_max"]) if has else (0.5, 0.5, 0.5),
            known_bbox=has, comps=_mesh_comps(r))
    return out


def by_batch_t(templates):
    """{batch_t: Template} для шаблонов, у которых есть индекс в mcw_batch."""
    return {t.batch_t: t for t in templates.values() if t.batch_t is not None}


def entity_origin_w(bx, by, bz, tpl=None, scale=None, y_offset=Y_OFFSET):
    """Куда ставить origin сущности (м), чтобы центр её bbox совпал с центром блока."""
    c = block_center_w(bx, by, bz, y_offset)
    p = tpl.pivot_offset(scale) if tpl else (0.0, 0.0, 0.0)
    return tuple(c[a] - p[a] for a in range(3))


def to_cm(v):
    return int(round(v * 100.0))


def entity_origin_cm(bx, by, bz, tpl=None, scale=None, y_offset=Y_OFFSET):
    return tuple(to_cm(v) for v in entity_origin_w(bx, by, bz, tpl, scale, y_offset))


# ---- разбор ответов modMCW -----------------------------------------------------------------------------------
# Префикс перед [MCW#..] допускается: как Log() выглядит в ответе на 5.00c — [проверить] на спайке
LINE = re.compile(r"\[MCW#(\d+)\]\s+(\S+)(?:\s+(.*?))?\s*$")


@dataclass
class McwLine:
    seq: int
    kind: str
    args: list = field(default_factory=list)
    rest: str = ""


def parse_line(line):
    """'[MCW#7] pos 1 2 3 4' -> McwLine(7, 'pos', ['1','2','3','4'], '1 2 3 4'); чужая строка -> None."""
    m = LINE.search(line)
    if not m:
        return None
    rest = m.group(3) or ""
    return McwLine(int(m.group(1)), m.group(2), rest.split(), rest)


def reply_lines(text, seq):
    """Строки ответа для seq и признак полноты (пришла ли '[MCW#seq] end')."""
    out, complete = [], False
    for raw in text.splitlines():
        ln = parse_line(raw)
        if ln is None or ln.seq != seq:
            continue
        if ln.kind == "end":
            complete = True
            continue
        out.append(ln)
    return out, complete


@dataclass(frozen=True)
class Pos:
    """'pos <x> <y> <z> <yaw>': см и сотые градуса, как пришло из игры."""
    x_cm: int
    y_cm: int
    z_cm: int
    yaw_cdeg: int

    @property
    def w(self):
        return self.x_cm / 100.0, self.y_cm / 100.0, self.z_cm / 100.0

    @property
    def yaw(self):
        return self.yaw_cdeg / 100.0

    def block(self, y_offset=Y_OFFSET):
        return cm_to_block(self.x_cm, self.y_cm, self.z_cm, y_offset=y_offset)

    def mc(self, y_offset=Y_OFFSET):
        """-> (x, y, z, yaw) Майнкрафта."""
        return (*w_to_mc_pos(*self.w, y_offset=y_offset), w_yaw_to_mc(self.yaw))


@dataclass(frozen=True)
class Cam:
    """'cam <x> <y> <z> <dx> <dy> <dz>': позиция в см, направление x10000."""
    x_cm: int
    y_cm: int
    z_cm: int
    dx: int
    dy: int
    dz: int

    @property
    def w(self):
        return self.x_cm / 100.0, self.y_cm / 100.0, self.z_cm / 100.0

    @property
    def dir(self):
        return self.dx / 10000.0, self.dy / 10000.0, self.dz / 10000.0

    def mc_angles(self):
        return w_dir_to_mc_angles(*self.dir)


def _ints(ln, n):
    if len(ln.args) != n:
        raise ValueError(f"{ln.kind}: ждали {n} чисел, пришло {ln.args!r}")
    return [int(a) for a in ln.args]


def parse_pos(ln):
    return Pos(*_ints(ln, 4))


def parse_cam(ln):
    return Cam(*_ints(ln, 6))


def parse_poll(text, seq):
    """Ответ mcw_poll -> dict(pos=Pos, cam=Cam, area=str, world=str, complete=bool); чего нет — None."""
    lines, complete = reply_lines(text, seq)
    r = dict(pos=None, cam=None, area=None, world=None, complete=complete)
    for ln in lines:
        if ln.kind == "pos":
            r["pos"] = parse_pos(ln)
        elif ln.kind == "cam":
            r["cam"] = parse_cam(ln)
        elif ln.kind in ("area", "world"):
            r[ln.kind] = ln.rest
    return r


# ---- сборка mcw_batch ----------------------------------------------------------------------------------------
OP_OK = re.compile(r"^(set:-?\d+:-?\d+:-?\d+:\d+|clear:-?\d+:-?\d+:-?\d+)$")


def set_op(bx, by, bz, tpl, scale=None, y_offset=Y_OFFSET):
    """Поставить блок шаблоном tpl (Template с batch_t). Координаты — origin сущности в см."""
    if tpl.batch_t is None:
        raise ValueError(f"{tpl.id}: нет batch_t, в mcw_batch его не поставить")
    x, y, z = entity_origin_cm(bx, by, bz, tpl, scale, y_offset)
    return f"set:{x}:{y}:{z}:{tpl.batch_t}"


def clear_op(bx, by, bz, tpl, scale=None, y_offset=Y_OFFSET):
    """Убрать блок. tpl — тот же шаблон, что ставил его: координаты обязаны совпасть с set."""
    x, y, z = entity_origin_cm(bx, by, bz, tpl, scale, y_offset)
    return f"clear:{x}:{y}:{z}"


def batch_call(seq, ops):
    """-> 'mcw_batch(seq, "op|op|...")' (синтаксис как в консоли). Без ops — ValueError: пустую строку "" парсер exec
    5.00c не разбирает (вся команда — 'Warn: Failed to process command')."""
    if not ops:
        raise ValueError("пустой mcw_batch: \"\" игра не разбирает — не слать вовсе")
    for op in ops:
        if not OP_OK.match(op):
            raise ValueError(f"плохая команда пакета: {op!r}")
    return f'mcw_batch({int(seq)}, "{"|".join(ops)}")'


def utf8_len(s):
    return len(s.encode("utf-8"))


def build_batches(ops, seq0, limit=DEFAULT_BATCH):
    """Режет ops на команды mcw_batch не длиннее limit байт utf8 (limit <= MAX_CMD = 2047: команду длиннее игра
    молча выбрасывает). -> [(seq, command)].

    seq растёт на 1 с каждой командой. Порядок ops сохраняется; одна op никогда не делится.
    Ответ mcw_batch — одна короткая строка 'batch ...', предел ответа (1999 байт) ему не грозит.
    """
    if not 0 < limit <= MAX_CMD:
        raise ValueError(f"limit {limit} вне (0, {MAX_CMD}]")
    out, cur, seq = [], [], seq0
    for op in ops:
        if utf8_len(batch_call(seq, [op])) > limit:
            raise ValueError(f"одна команда {op!r} не влезает в {limit} байт")
        if cur and utf8_len(batch_call(seq, cur + [op])) > limit:
            out.append((seq, batch_call(seq, cur)))
            seq += 1
            cur = []
        cur.append(op)
    if cur:
        out.append((seq, batch_call(seq, cur)))
    return out


# ---- сущности моста (мобы): mcw_ents ------------------------------------------------------------------------------
# mcw_ents(seq, "spawn:id:x:y:z:yaw:t|move:id:x:y:z:yaw|kill:id|wipe"): id — слот моста 0..ENT_SLOTS-1 (у мода — тег
# MCW_E<id>), x, y, z — мировые см origin сущности, yaw — курс Ведьмака в сотых градуса, t — как в mcw_batch.
ENT_SLOTS = 64
OP_ENT_OK = re.compile(r"^(spawn:\d+:-?\d+:-?\d+:-?\d+:-?\d+:\d+|move:\d+:-?\d+:-?\d+:-?\d+:-?\d+|kill:\d+|wipe)$")


def ent_origin_w(mx, my, mz, tpl=None, y_offset=Y_OFFSET):
    """Позиция моба Майнкрафта (ноги) -> origin сущности-плейсхолдера (м): низ bbox шаблона на уровне ног, центр bbox
    по горизонтали — над ногами. У cont_crate pivot в центре: origin на 0.521 м выше ног."""
    wx, wy, wz = mc_pos_to_w(mx, my, mz, y_offset=y_offset)
    if tpl is None:
        return wx, wy, wz
    mn, mx_ = tpl.bbox_at()
    return wx - (mn[0] + mx_[0]) / 2, wy - (mn[1] + mx_[1]) / 2, wz - mn[2]


def ent_origin_cm(mx, my, mz, tpl=None, y_offset=Y_OFFSET):
    return tuple(to_cm(v) for v in ent_origin_w(mx, my, mz, tpl, y_offset))


def yaw_cdeg(mc_yaw):
    """Yaw Майнкрафта (град) -> курс Ведьмака в сотых градуса (целое, как в протоколе modMCW)."""
    return int(round(mc_yaw_to_w(mc_yaw) * 100.0))


def _slot(slot):
    if not 0 <= int(slot) < ENT_SLOTS:
        raise ValueError(f"слот {slot} вне 0..{ENT_SLOTS - 1}")
    return int(slot)


def ent_spawn_op(slot, mx, my, mz, mc_yaw, tpl, y_offset=Y_OFFSET):
    if tpl.batch_t is None:
        raise ValueError(f"{tpl.id}: нет batch_t, в mcw_ents его не поставить")
    x, y, z = ent_origin_cm(mx, my, mz, tpl, y_offset)
    return f"spawn:{_slot(slot)}:{x}:{y}:{z}:{yaw_cdeg(mc_yaw)}:{tpl.batch_t}"


def ent_move_op(slot, mx, my, mz, mc_yaw, tpl, y_offset=Y_OFFSET):
    x, y, z = ent_origin_cm(mx, my, mz, tpl, y_offset)
    return f"move:{_slot(slot)}:{x}:{y}:{z}:{yaw_cdeg(mc_yaw)}"


def ent_kill_op(slot):
    return f"kill:{_slot(slot)}"


def ents_call(seq, ops):
    """-> 'mcw_ents(seq, "op|op|...")' (синтаксис как в консоли). Без ops — ValueError, как у batch_call."""
    if not ops:
        raise ValueError("пустой mcw_ents: \"\" игра не разбирает — не слать вовсе")
    for op in ops:
        if not OP_ENT_OK.match(op):
            raise ValueError(f"плохая команда mcw_ents: {op!r}")
    return f'mcw_ents({int(seq)}, "{"|".join(ops)}")'


def disk_blocks(pos, r_out=9.5, r_in=2.0, layers=4, y_offset=Y_OFFSET):
    """Плотная кладка вокруг игрока: блоки, чей центр по горизонтали в [r_in, r_out] м от pos, layers слоёв
    от блока ног вверх. Нужна, когда меш шаблона грузится только вблизи (cont_crate: streamingDistance 10):
    mcw_stress ставит сетку на ~50 м вглубь, и FPS там мерится по горстке видимых кубов."""
    wx, wy, _ = pos.w
    bx0, by0, bz0 = pos.block(y_offset)
    r = math.ceil(r_out) + 1
    out = []
    for bx in range(bx0 - r, bx0 + r + 1):
        for bz in range(bz0 - r, bz0 + r + 1):
            cx, cy, _ = block_center_w(bx, by0, bz, y_offset)
            if r_in <= math.hypot(cx - wx, cy - wy) <= r_out:
                out += [(bx, by0 + k, bz) for k in range(layers)]
    return out


def _main(argv=None):
    import argparse
    import sys
    ap = argparse.ArgumentParser(description="Быстрые переводы координат MC <-> W3 и пакеты mcw_batch")
    ap.add_argument("--y-offset", type=int, default=Y_OFFSET)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("w2b", help="точка Ведьмака (м) -> блок MC")
    a.add_argument("xyz", type=float, nargs=3)
    b = sub.add_parser("b2w", help="блок MC -> центр в Ведьмаке (м) и origin cont_crate (см)")
    b.add_argument("xyz", type=int, nargs=3)
    d = sub.add_parser("disk", help="команды mcw_batch: кольцо блоков вокруг игрока (для w3cmd.py -f)")
    d.add_argument("--pos", required=True, help="строка ответа mcw_poll: '[MCW#5] pos x y z yaw' или 'x y z yaw'")
    d.add_argument("--r-out", type=float, default=9.5, help="внешний радиус, м")
    d.add_argument("--r-in", type=float, default=2.0, help="внутренний радиус (место игроку), м")
    d.add_argument("--layers", type=int, default=4)
    d.add_argument("--tpl", default="cont_crate", help="id шаблона с batch_t из templates.json")
    d.add_argument("--seq", type=int, default=1000, help="seq первой команды")
    d.add_argument("--limit", type=int, default=DEFAULT_BATCH, help=f"байт на команду (не больше {MAX_CMD})")
    args = ap.parse_args(argv)
    if args.cmd == "w2b":
        print(w_to_block(*args.xyz, y_offset=args.y_offset))
    elif args.cmd == "b2w":
        tpl = load_templates().get("cont_crate")
        print("center_m", block_center_w(*args.xyz, y_offset=args.y_offset))
        print("cont_crate_origin_cm", entity_origin_cm(*args.xyz, tpl=tpl, y_offset=args.y_offset))
    else:
        ln = parse_line(args.pos) or McwLine(0, "pos", args.pos.split(), args.pos)
        pos = parse_pos(ln)
        tpl = load_templates()[args.tpl]
        blocks = disk_blocks(pos, args.r_out, args.r_in, args.layers, args.y_offset)
        cmds = build_batches([set_op(*bl, tpl, y_offset=args.y_offset) for bl in blocks], args.seq, args.limit)
        for _, cmd in cmds:
            print(cmd)
        print(f"{len(blocks)} блоков, {len(cmds)} команд mcw_batch, до {max(utf8_len(c) for _, c in cmds)} байт; "
              f"seq {args.seq}..{args.seq + len(cmds) - 1}", file=sys.stderr)


if __name__ == "__main__":
    _main()
