"""Оффлайн-тесты fake_bridge: фрейминг JSON-строк, зеркало, разбор protocol_examples.jsonl, TCP-петля без сервера.

    python -m unittest test_fake_bridge -v
"""
import json
import socket
import threading
import time
import unittest
from pathlib import Path

import fake_bridge as fb

HERE = Path(__file__).parent


class FramerTest(unittest.TestCase):
    def test_split_line(self):
        fr = fb.LineFramer()
        self.assertEqual(fr.feed(b'{"t":"pi'), [])
        self.assertEqual(fr.pending, 8)
        self.assertEqual(fr.feed(b'ng","ts":1}\n'), [{"t": "ping", "ts": 1}])
        self.assertEqual(fr.pending, 0)

    def test_many_in_one_chunk_and_blank_lines(self):
        fr = fb.LineFramer()
        got = fr.feed(b'{"a":1}\n\n{"a":2}\r\n{"a":3}\n{"a"')
        self.assertEqual(got, [{"a": 1}, {"a": 2}, {"a": 3}])
        self.assertEqual(fr.feed(b":4}\n"), [{"a": 4}])

    def test_utf8_split_inside_char(self):
        fr = fb.LineFramer()
        data = fb.encode({"t": "cmd_out", "text": "Геральт ✓"})
        cut = data.index("✓".encode()) + 1  # посреди 3-байтного символа
        self.assertEqual(fr.feed(data[:cut]), [])
        self.assertEqual(fr.feed(data[cut:]), [{"t": "cmd_out", "text": "Геральт ✓"}])

    def test_byte_by_byte(self):
        fr = fb.LineFramer()
        out = []
        for b in fb.encode({"t": "x", "s": "a\nb"}) * 3:
            out += fr.feed(bytes([b]))
        self.assertEqual(out, [{"t": "x", "s": "a\nb"}] * 3)

    def test_bad_json_raises(self):
        with self.assertRaises(json.JSONDecodeError):
            fb.LineFramer().feed(b"{oops}\n")

    def test_encode_single_line(self):
        raw = fb.encode({"t": "cmd", "c": "say a\nb", "n": [1, 2]})
        self.assertTrue(raw.endswith(b"\n"))
        self.assertEqual(raw.count(b"\n"), 1)
        self.assertEqual(json.loads(raw), {"t": "cmd", "c": "say a\nb", "n": [1, 2]})


class MirrorTest(unittest.TestCase):
    def test_chunk_blocks_unchunk(self):
        m = fb.Mirror()
        m.apply({"t": "hello"})
        m.apply({"t": "chunk", "c": [0, -1], "pal": ["minecraft:stone", "minecraft:sand"], "b": [1, 70, -5, 0, 2, 71, -5, 1]})
        m.apply({"t": "chunk", "c": [-1, 0], "pal": ["minecraft:dirt"], "b": [-3, 64, 2, 0]})
        m.apply({"t": "snap_end"})
        self.assertTrue(m.snap_done)
        self.assertEqual(m.get(2, 71, -5), "minecraft:sand")
        m.apply({"t": "blocks", "pal": ["minecraft:tnt"], "set": [1, 71, -5, 0], "clear": [2, 71, -5]})
        self.assertEqual(m.get(1, 71, -5), "minecraft:tnt")
        self.assertIsNone(m.get(2, 71, -5))
        m.apply({"t": "unchunk", "c": [0, -1]})
        self.assertEqual(m.blocks, {(-3, 64, 2): "minecraft:dirt"})  # отрицательные координаты: >> 4 = floor
        self.assertEqual(m.chunks, {(-1, 0)})

    def test_chunk_replaces_old_content(self):
        m = fb.Mirror()
        m.apply({"t": "chunk", "c": [0, 0], "pal": ["a"], "b": [1, 1, 1, 0, 2, 2, 2, 0]})
        m.apply({"t": "chunk", "c": [0, 0], "pal": ["b"], "b": [1, 1, 1, 0]})
        self.assertEqual(m.blocks, {(1, 1, 1): "b"})

    def test_snap_smaller_radius_matches_fresh_hello(self):
        """Как шлёт мод на snap r=1 после снапшота r=2: unchunk для 16 внешних чанков, затем 9 chunk заново.
        Итог должен совпасть со свежим hello r=1 — внешние чанки у моста не остаются."""
        def chunk(cx, cz):
            return {"t": "chunk", "c": [cx, cz], "pal": ["minecraft:stone"], "b": [cx * 16 + 1, 64, cz * 16 + 1, 0]}

        big = fb.Mirror()
        big.apply({"t": "hello"})
        for cx in range(-2, 3):
            for cz in range(-2, 3):
                big.apply(chunk(cx, cz))
        big.apply({"t": "snap_end"})
        for cx in range(-2, 3):
            for cz in range(-2, 3):
                if max(abs(cx), abs(cz)) > 1:
                    big.apply({"t": "unchunk", "c": [cx, cz]})
        inner = [(cx, cz) for cx in range(-1, 2) for cz in range(-1, 2)]
        for cx, cz in inner:
            big.apply(chunk(cx, cz))
        big.apply({"t": "snap_end"})

        fresh = fb.Mirror()
        fresh.apply({"t": "hello"})
        for cx, cz in inner:
            fresh.apply(chunk(cx, cz))
        self.assertEqual(big.diff(fresh), [])
        self.assertEqual(len(big.chunks), 9)
        self.assertIsNone(big.get(2 * 16 + 1, 64, 1))

    def test_blocks_go_to_their_chunk(self):
        m = fb.Mirror()
        m.apply({"t": "chunk", "c": [-1, -1], "pal": [], "b": []})
        m.apply({"t": "blocks", "pal": ["minecraft:sand"], "set": [-1, 70, -16, 0], "clear": [500, 70, 500]})
        self.assertEqual(m.by_chunk[(-1, -1)], {(-1, 70, -16): "minecraft:sand"})
        self.assertEqual(m.size(), 1)
        m.apply({"t": "chunk", "c": [-1, -1], "pal": [], "b": []})  # новый снапшот чанка заменяет его целиком
        self.assertEqual(m.size(), 0)

    def test_copy_is_deep(self):
        m = fb.Mirror()
        m.apply({"t": "chunk", "c": [0, 0], "pal": ["s"], "b": [1, 2, 3, 0]})
        c = m.copy()
        m.apply({"t": "blocks", "pal": [], "set": [], "clear": [1, 2, 3]})
        self.assertEqual(c.get(1, 2, 3), "s")
        self.assertIsNone(m.get(1, 2, 3))

    def test_chunk_message_does_not_scan_whole_mirror(self):
        """Снапшот из 200 чанков по 1000 блоков поверх такого же зеркала. Прежний _drop_chunk перебирал всё
        зеркало на каждый chunk (200 x 200 тыс.) — секунды; по чанкам — доли секунды."""
        msgs = []
        for k in range(200):
            cx, cz = k % 20, k // 20
            b = []
            for i in range(1000):
                b += [cx * 16 + i % 16, 64 + i // 256, cz * 16 + (i // 16) % 16, 0]
            msgs.append({"t": "chunk", "c": [cx, cz], "pal": ["minecraft:stone"], "b": b})
        m = fb.Mirror()
        for msg in msgs:
            m.apply(msg)
        t = time.perf_counter()
        for msg in msgs:
            m.apply(msg)
        dt = time.perf_counter() - t
        self.assertEqual(m.size(), 200 * 1000)
        self.assertLess(dt, 2.0)

    def test_diff(self):
        a, b = fb.Mirror(), fb.Mirror()
        for m in (a, b):
            m.apply({"t": "chunk", "c": [0, 0], "pal": ["s"], "b": [1, 2, 3, 0]})
        self.assertEqual(a.diff(b), [])
        b.apply({"t": "blocks", "pal": [], "set": [], "clear": [1, 2, 3]})
        self.assertEqual(len(a.diff(b)), 1)


class EntsTest(unittest.TestCase):
    def test_deltas_and_hello(self):
        e = fb.Ents()
        e.apply({"t": "ent", "tick": 1, "spawn": [[7, "minecraft:creeper", 1.0, 70.0, 2.0, 90.0, 20.0]], "move": [], "hp": [],
                 "fuse": [], "item": [], "remove": []})
        e.apply({"t": "ent", "tick": 2, "spawn": [], "move": [[7, 1.5, 70.0, 2.0, 80.0], [99, 0, 0, 0, 0]], "hp": [[7, 12.5]],
                 "fuse": [[7, 1]], "item": [], "remove": []})
        self.assertEqual(e.by_id[7] | {}, {"type": "minecraft:creeper", "p": (1.5, 70.0, 2.0), "yaw": 80.0, "hp": 12.5,
                                           "fuse": 1, "item": None, "count": 0})
        self.assertEqual(list(e.of_type("minecraft:creeper")), [7])
        e.apply({"t": "hello", "v": 1, "tick": 3})          # новый hello: мод пришлёт всех заново
        self.assertEqual(e.by_id, {})


class ProtocolExamplesTest(unittest.TestCase):
    def setUp(self):
        self.lines = [json.loads(s) for s in (HERE / "protocol_examples.jsonl").read_text(encoding="utf-8").splitlines() if s.strip()]

    def test_all_parse_and_roundtrip(self):
        self.assertGreater(len(self.lines), 20)
        fr = fb.LineFramer()
        for m in self.lines:
            self.assertEqual(fr.feed(fb.encode(m)), [m])

    def test_mirror_accepts_every_mc_message(self):
        m = fb.Mirror()
        types = set()
        for msg in self.lines:
            if msg.get("_dir") == "mc->bridge":
                m.apply(msg)
                types.add(msg["t"])
                if msg["t"] == "blocks":
                    fb.short(msg)  # печать не падает на своих же примерах
        for t in ("hello", "chunk", "snap_end", "blocks", "ack", "dig", "inv", "hp", "explosion", "pong", "cmd_out",
                  "pteleport", "death", "respawn", "unchunk", "ground_ok", "error"):
            self.assertIn(t, types)

    def test_every_mc_message_has_tick(self):
        for msg in self.lines:
            if msg.get("_dir") == "mc->bridge":
                self.assertIn("tick", msg, msg["t"])

    def test_numbers_are_json(self):
        """Мод пишет NaN как null: в примерах (и в выводе мода) не должно быть NaN/Infinity."""
        def strict(x):
            raise ValueError(f"не JSON: {x}")
        text = (HERE / "protocol_examples.jsonl").read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.strip():
                json.loads(line, parse_constant=strict)

    def test_dim_on_teleport_and_respawn(self):
        for msg in self.lines:
            if msg.get("t") in ("pteleport", "respawn", "hello") and msg.get("_dir") == "mc->bridge":
                self.assertTrue(msg["dim"].startswith("minecraft:"), msg)

    def test_stage35_messages(self):
        """ent, recipes, craft и spawn: формы списков и то, что зеркало сущностей их принимает."""
        ents = fb.Ents()
        seen = set()
        for msg in self.lines:
            t = msg.get("t")
            seen.add((msg.get("_dir"), t))
            if t == "ent":
                for k in ("spawn", "move", "hp", "fuse", "item", "remove"):
                    self.assertIsInstance(msg[k], list, k)
                self.assertTrue(all(len(s) == 7 for s in msg["spawn"]))
                self.assertTrue(all(len(s) == 5 for s in msg["move"]))
                self.assertTrue(all(len(s) == 3 for s in msg["item"]))
                fb.short(msg)
            if t == "recipes" and msg["_dir"] == "mc->bridge":
                self.assertTrue(all(len(r) == 5 and r[4] in (2, 3) for r in msg["r"]))
                fb.short(msg)
            if t == "inv":
                self.assertTrue(all(len(d) == 3 for d in msg["dmg"]))
            ents.apply(msg)
        for d, t in (("bridge->mc", "recipes"), ("mc->bridge", "recipes"), ("bridge->mc", "craft"), ("bridge->mc", "spawn"),
                     ("mc->bridge", "ent")):
            self.assertIn((d, t), seen)
        self.assertEqual(ents.by_id, {})                    # крипер взорвался, предмет подобран
        self.assertEqual(ents.removed, [1240, 1234])

    def test_block_lists_are_well_formed(self):
        for msg in self.lines:
            if msg.get("t") == "chunk":
                self.assertEqual(len(msg["b"]) % 4, 0)
                self.assertTrue(all(i < len(msg["pal"]) for i in msg["b"][3::4]))
            if msg.get("t") == "blocks":
                self.assertEqual(len(msg["set"]) % 4, 0)
                self.assertEqual(len(msg["clear"]) % 3, 0)
                self.assertTrue(all(i < len(msg["pal"]) for i in msg["set"][3::4]))


class LoopbackTest(unittest.TestCase):
    """Link против маленького TCP-сервера в потоке: отвечает на hello снапшотом, режет вывод на куски."""

    def setUp(self):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.got: list[dict] = []
        self.th = threading.Thread(target=self._serve, daemon=True)
        self.th.start()

    def tearDown(self):
        self.srv.close()

    def _serve(self):
        conn, _ = self.srv.accept()
        fr = fb.LineFramer()
        with conn:
            while True:
                data = conn.recv(4096)
                if not data:
                    return
                for m in fr.feed(data):
                    self.got.append(m)
                    if m["t"] == "hello":
                        out = b"".join(fb.encode(x) for x in (
                            {"t": "hello", "v": 1, "tick": 5},
                            {"t": "chunk", "tick": 5, "c": [0, 0], "pal": ["minecraft:stone"], "b": [1, 64, 1, 0]},
                            {"t": "snap_end", "tick": 5, "chunks": 1, "blocks": 1},
                        ))
                        for i in range(0, len(out), 7):  # кусками по 7 байт
                            conn.sendall(out[i:i + 7])
                    elif m["t"] == "cmd":
                        conn.sendall(fb.encode({"t": "cmd_out", "c": m["c"], "text": "ок"}))

    def test_hello_snapshot_and_cmd(self):
        link = fb.Link("127.0.0.1", self.port)
        try:
            h, e = link.hello(radius=2, timeout=5)
            self.assertEqual(h["tick"], 5)
            self.assertEqual(e["blocks"], 1)
            self.assertEqual(link.mirror.get(1, 64, 1), "minecraft:stone")
            self.assertEqual(link.cmd("time set day", timeout=5), "ок")
            self.assertEqual(self.got[0], {"t": "hello", "v": 1, "world": "novigrad", "want": {"radius": 2}})
        finally:
            link.close()


if __name__ == "__main__":
    unittest.main()
