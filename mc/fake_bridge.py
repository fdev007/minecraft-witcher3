"""Фейковый мост к моду MCW: протокол JSON-строк по TCP (127.0.0.1:25600), зеркало мира, печать событий.

Нужен, пока нет настоящего bridge.py с Ведьмаком: проверить мод и сервер руками и в smoke_0prime.py.

    python fake_bridge.py hello                 # стать мостом: снапшот вокруг Геральта, потом события 10 с
    python fake_bridge.py watch 60              # то же, события 60 с
    python fake_bridge.py cmd "setblock 2 70 0 minecraft:stone"
    python fake_bridge.py stop                  # мягко остановить сервер (сохранит мир и Геральта)
    python fake_bridge.py pos 0.5 70 0.5 [yaw] [pitch]
    python fake_bridge.py act break 2 70 0 [face] [slot]
    python fake_bridge.py act place 2 70 0 up [slot]
    python fake_bridge.py recipes               # что можно скрафтить из инвентаря Геральта (верстак рядом — и 3x3)
    python fake_bridge.py craft minecraft:oak_planks [n]
    python fake_bridge.py spawn [minecraft:creeper] [d]   # моб в d блоках перед Геральтом (тест этапа 5)
    python fake_bridge.py raw '{"t":"ping","ts":1}'

Мостом мод считает соединение, приславшее hello: hello и watch вытесняют живой мост (bridge.py), остальные
команды — нет, их ответы (cmd_out, ack, dig, pong, error) приходят этому соединению, а события мира (blocks,
chunk, inv...) — по-прежнему мосту. Поэтому act отсюда печатает только ack/dig; блоки видно в watch.

Протокол: mc/protocol_examples.jsonl. Только stdlib, Python 3.12.
"""
from __future__ import annotations

import json
import queue
import socket
import sys
import threading
import time
from typing import Any, Callable, Iterable

HOST = "127.0.0.1"
PORT = 25600


class LineFramer:
    """Байты из сокета -> JSON-объекты. Строка может прийти частями, несколько строк — одним куском,
    многобайтный UTF-8 символ — разрезанным между кусками. Пустые строки пропускаем."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        self._buf += data
        out: list[dict[str, Any]] = []
        while True:
            i = self._buf.find(b"\n")
            if i < 0:
                return out
            raw = bytes(self._buf[:i])
            del self._buf[: i + 1]
            line = raw.decode("utf-8").strip()
            if line:
                out.append(json.loads(line))

    @property
    def pending(self) -> int:
        """Сколько байт недописанной строки ждёт продолжения."""
        return len(self._buf)


def encode(msg: dict[str, Any]) -> bytes:
    """Одно сообщение -> строка. json.dumps без indent не выдаёт \\n внутри (в строках он экранирован)."""
    return (json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


class Mirror:
    """То же зеркало, что держит мод: по чанкам (cx, cz) -> {(x, y, z): имя состояния}.
    Строится из chunk + blocks + unchunk; после переподключения снапшот должен с ним совпасть.
    chunk и unchunk заменяют/убирают один вложенный словарь — без обхода всего зеркала."""

    def __init__(self) -> None:
        self.by_chunk: dict[tuple[int, int], dict[tuple[int, int, int], str]] = {}
        self.chunks: set[tuple[int, int]] = set()
        self.snap_done = False

    def apply(self, m: dict[str, Any]) -> None:
        t = m.get("t")
        if t == "chunk":
            cx, cz = m["c"]
            pal, b = m["pal"], m["b"]
            self.by_chunk[(cx, cz)] = {(b[i], b[i + 1], b[i + 2]): pal[b[i + 3]] for i in range(0, len(b), 4)}
            self.chunks.add((cx, cz))
        elif t == "unchunk":
            c = (m["c"][0], m["c"][1])
            self.by_chunk.pop(c, None)
            self.chunks.discard(c)
        elif t == "blocks":
            pal, s, c = m["pal"], m["set"], m["clear"]
            for i in range(0, len(s), 4):
                self.by_chunk.setdefault((s[i] >> 4, s[i + 2] >> 4), {})[(s[i], s[i + 1], s[i + 2])] = pal[s[i + 3]]
            for i in range(0, len(c), 3):
                ch = self.by_chunk.get((c[i] >> 4, c[i + 2] >> 4))
                if ch is not None:
                    ch.pop((c[i], c[i + 1], c[i + 2]), None)
        elif t == "snap_end":
            self.snap_done = True
        elif t == "hello":
            # новый снапшот заменяет всё
            self.by_chunk.clear()
            self.chunks.clear()
            self.snap_done = False

    @property
    def blocks(self) -> dict[tuple[int, int, int], str]:
        """Все блоки одним словарём (копия, O(n)) — для проверок и печати, не для горячего пути."""
        out: dict[tuple[int, int, int], str] = {}
        for ch in self.by_chunk.values():
            out.update(ch)
        return out

    def size(self) -> int:
        return sum(len(ch) for ch in self.by_chunk.values())

    def get(self, x: int, y: int, z: int) -> str | None:
        ch = self.by_chunk.get((x >> 4, z >> 4))
        return None if ch is None else ch.get((x, y, z))

    def copy(self) -> "Mirror":
        m = Mirror()
        m.by_chunk = {k: dict(v) for k, v in self.by_chunk.items()}
        m.chunks = set(self.chunks)
        m.snap_done = self.snap_done
        return m

    def diff(self, other: "Mirror") -> list[str]:
        """Расхождения с другим зеркалом (пусто = совпадают)."""
        out = []
        a_all, b_all = self.blocks, other.blocks
        for k in sorted(set(a_all) | set(b_all)):
            a, b = a_all.get(k), b_all.get(k)
            if a != b:
                out.append(f"{k}: {a} != {b}")
        if self.chunks != other.chunks:
            out.append(f"chunks: {len(self.chunks)} != {len(other.chunks)}")
        return out


class Ents:
    """Сущности из потока ent (мобы и предметы у Геральта), как их держит мост: id -> dict(type, p, yaw, hp, fuse,
    item, count). hello начинает заново: мод после него шлёт всех как spawn."""

    def __init__(self) -> None:
        self.by_id: dict[int, dict[str, Any]] = {}
        self.removed: list[int] = []

    def apply(self, m: dict[str, Any]) -> None:
        t = m.get("t")
        if t == "hello":
            self.by_id.clear()
            return
        if t != "ent":
            return
        for i, typ, x, y, z, yaw, hp in m["spawn"]:
            self.by_id[i] = {"type": typ, "p": (x, y, z), "yaw": yaw, "hp": hp, "fuse": 0, "item": None, "count": 0}
        for i, x, y, z, yaw in m["move"]:
            if i in self.by_id:
                self.by_id[i].update(p=(x, y, z), yaw=yaw)
        for i, hp in m["hp"]:
            if i in self.by_id:
                self.by_id[i]["hp"] = hp
        for i, f in m["fuse"]:
            if i in self.by_id:
                self.by_id[i]["fuse"] = f
        for i, item, n in m["item"]:
            if i in self.by_id:
                self.by_id[i].update(item=item, count=n)
        for i in m["remove"]:
            if self.by_id.pop(i, None) is not None:
                self.removed.append(i)

    def of_type(self, typ: str) -> dict[int, dict[str, Any]]:
        return {i: e for i, e in self.by_id.items() if e["type"] == typ}


def blocks_set(m: dict[str, Any]) -> list[tuple[int, int, int, str]]:
    pal, s = m["pal"], m["set"]
    return [(s[i], s[i + 1], s[i + 2], pal[s[i + 3]]) for i in range(0, len(s), 4)]


def blocks_clear(m: dict[str, Any]) -> list[tuple[int, int, int]]:
    c = m["clear"]
    return [(c[i], c[i + 1], c[i + 2]) for i in range(0, len(c), 3)]


class Link:
    """Клиент мода. Поток чтения кладёт (время прихода, сообщение) в очередь и обновляет зеркало."""

    def __init__(self, host: str = HOST, port: int = PORT, timeout: float = 5.0) -> None:
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.settimeout(None)
        self.inbox: queue.Queue[tuple[float, dict[str, Any]]] = queue.Queue()
        self.mirror = Mirror()
        self.ents = Ents()
        self.closed = False
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        fr = LineFramer()
        try:
            while True:
                data = self.sock.recv(1 << 16)
                if not data:
                    break
                now = time.perf_counter()
                for m in fr.feed(data):
                    with self._lock:
                        self.mirror.apply(m)
                        self.ents.apply(m)
                    self.inbox.put((now, m))
        except OSError:
            pass
        self.closed = True
        self.inbox.put((time.perf_counter(), {"t": "_closed"}))

    def send(self, msg: dict[str, Any]) -> float:
        """Отправить; возвращает perf_counter момента отправки (для замеров задержки)."""
        t = time.perf_counter()
        self.sock.sendall(encode(msg))
        return t

    def recv(self, timeout: float = 5.0) -> tuple[float, dict[str, Any]] | None:
        try:
            return self.inbox.get(timeout=timeout)
        except queue.Empty:
            return None

    def wait(self, pred: Callable[[dict[str, Any]], bool], timeout: float = 10.0,
             on_other: Callable[[dict[str, Any]], None] | None = None) -> tuple[float, dict[str, Any]] | None:
        """Ждать сообщение, для которого pred истинно; остальные отдать on_other."""
        end = time.perf_counter() + timeout
        while True:
            left = end - time.perf_counter()
            if left <= 0:
                return None
            got = self.recv(left)
            if got is None:
                return None
            if pred(got[1]):
                return got
            if on_other:
                on_other(got[1])

    def drain(self, quiet: float = 0.5, limit: float = 10.0) -> list[dict[str, Any]]:
        """Забрать всё, пока поток не затихнет на quiet секунд (но не дольше limit)."""
        out = []
        end = time.perf_counter() + limit
        while time.perf_counter() < end:
            got = self.recv(quiet)
            if got is None:
                break
            out.append(got[1])
        return out

    def hello(self, radius: int | None = None, world: str = "novigrad", timeout: float = 30.0) -> tuple[dict, dict]:
        """hello -> (ответ hello, snap_end). Зеркало после этого = снапшот."""
        msg: dict[str, Any] = {"t": "hello", "v": 1, "world": world}
        if radius is not None:
            msg["want"] = {"radius": radius}
        self.send(msg)
        h = self.wait(lambda m: m.get("t") == "hello", timeout)
        if h is None:
            raise TimeoutError("нет hello от мода")
        e = self.wait(lambda m: m.get("t") == "snap_end", timeout)
        if e is None:
            raise TimeoutError("нет snap_end")
        return h[1], e[1]

    def cmd(self, c: str, timeout: float = 10.0) -> str:
        self.send({"t": "cmd", "c": c})
        got = self.wait(lambda m: m.get("t") == "cmd_out" and m.get("c") == c, timeout)
        if got is None:
            raise TimeoutError(f"нет cmd_out для {c!r}")
        return got[1]["text"]

    def mirror_copy(self) -> Mirror:
        with self._lock:
            return self.mirror.copy()

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


def connect(host: str = HOST, port: int = PORT, wait: float = 0.0) -> Link:
    """Подключиться; wait > 0 — повторять, пока порт не откроется (сервер ещё грузится)."""
    end = time.perf_counter() + wait
    while True:
        try:
            return Link(host, port)
        except OSError:
            if time.perf_counter() >= end:
                raise
            time.sleep(0.5)


def short(m: dict[str, Any]) -> str:
    """Строка для печати: большие массивы сокращаем."""
    t = m.get("t")
    if t == "chunk":
        return f"chunk c={m['c']} blocks={len(m['b']) // 4} pal={len(m['pal'])}"
    if t == "blocks":
        s, c = blocks_set(m), blocks_clear(m)
        head = ", ".join(f"{x},{y},{z}={n}" for x, y, z, n in s[:6])
        return f"blocks tick={m['tick']} set={len(s)} [{head}{' ...' if len(s) > 6 else ''}] clear={len(c)} {c[:6]}{' ...' if len(c) > 6 else ''}"
    if t == "ent":
        return (f"ent tick={m['tick']} spawn={[(s[0], s[1]) for s in m['spawn']]} move={len(m['move'])} hp={m['hp']} "
                f"fuse={m['fuse']} item={m['item']} remove={m['remove']}")
    if t == "recipes":
        return f"recipes table={m['table']} n={len(m['r'])}: " + ", ".join(f"{r[0]} -> {r[2]} {r[1]} x{r[3]} ({r[4]}x{r[4]})" for r in m["r"])
    return json.dumps(m, ensure_ascii=False)


def _print_all(link: Link, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        got = link.recv(end - time.perf_counter())
        if got is None:
            break
        print(short(got[1]))
        if got[1].get("t") == "_closed":
            break


def main(argv: Iterable[str]) -> int:
    args = list(argv)
    if not args:
        print(__doc__)
        return 2
    op, rest = args[0], args[1:]
    link = connect(wait=5.0)
    try:
        if op in ("hello", "watch"):
            secs = float(rest[0]) if rest else 10.0
            h, e = link.hello()
            print(short(h))
            print(short(e), f"mirror={link.mirror.size()}")
            _print_all(link, secs)
        elif op == "cmd":
            print(link.cmd(" ".join(rest)))
        elif op == "stop":
            # мягкая остановка с сохранением; cmd_out может не успеть прийти — не ждём
            link.send({"t": "cmd", "c": "stop"})
            print("stop отправлен")
        elif op == "pos":
            x, y, z = map(float, rest[:3])
            msg: dict[str, Any] = {"t": "pos", "seq": 1, "p": [x, y, z]}
            if len(rest) > 3:
                msg["yaw"] = float(rest[3])
            if len(rest) > 4:
                msg["pitch"] = float(rest[4])
            link.send(msg)
            print(link.cmd("data get entity @p Pos"))
        elif op == "act":
            a = rest[0]
            msg = {"t": "act", "id": 1, "a": a}
            if a in ("break", "place"):
                msg["b"] = [int(v) for v in rest[1:4]]
                if len(rest) > 4:
                    msg["face"] = rest[4]
                if len(rest) > 5:
                    msg["slot"] = int(rest[5])
            elif a == "select":
                msg["slot"] = int(rest[1])
            # без hello: живой мост не вытесняем; сюда придут только ack и dig
            link.send(msg)
            _print_all(link, 3.0)
        elif op == "recipes":
            link.send({"t": "recipes", "id": 1})
            got = link.wait(lambda m: m.get("t") == "recipes", 5)
            print(short(got[1]) if got else "нет ответа recipes")
        elif op == "craft":
            link.send({"t": "craft", "id": 1, "recipe": rest[0], "n": int(rest[1]) if len(rest) > 1 else 1})
            got = link.wait(lambda m: m.get("t") == "ack", 5)
            print(json.dumps(got[1], ensure_ascii=False) if got else "нет ack")
        elif op == "spawn":
            msg = {"t": "spawn", "id": 1, "type": rest[0] if rest else "minecraft:creeper"}
            if len(rest) > 1:
                msg["d"] = float(rest[1])
            link.send(msg)
            got = link.wait(lambda m: m.get("t") == "ack", 5)
            print(json.dumps(got[1], ensure_ascii=False) if got else "нет ack")
        elif op == "raw":
            link.send(json.loads(" ".join(rest)))
            _print_all(link, 2.0)
        else:
            print(__doc__)
            return 2
    finally:
        link.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
