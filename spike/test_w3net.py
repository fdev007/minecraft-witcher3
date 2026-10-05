"""Тесты w3net + fake_w3 + w3cmd + bench_w3.  Запуск:  python -m pytest -q test_w3net.py
(или python -m unittest -v test_w3net). Временные файлы - в %W3NET_TMP% (если задан), иначе в системном temp.

Байтовые векторы: у rw3d тесты только туда-обратно, поэтому hex ниже собран руками по исходникам rw3d v0.8.0
@5a0cf7d и сверен с собранным rw3d_net (verify_protocol/cmp_rw3d.py, 0 расхождений):
  теги + big-endian ............ crates/net/src/protocol/encoding.rs:40-190
  строка = тег + i16-длина ..... encoding.rs:209-223 (UTF-8, длина в байтах), 287-301 (UTF-16, в кодовых единицах)
  пакет = DEAD len BEEF ........ crates/net/src/protocol/packet.rs:28-51 (len включает 6 байт рамки)
  сообщения .................... crates/net/src/messages/{notifications,requests}.rs
"""
import contextlib
import io
import json
import logging
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest

import bench_w3
import fake_w3
import w3cmd
import w3net
from w3net import (I8, I16, I32, U32, I64, U8, U16, Name, PacketStream, ProtocolError, W3Client, classify,
                   decode_items, decode_packet, encode_item, encode_packet)

H = bytes.fromhex
POLISH = "Za\u017c\u00f3\u0142\u0107 g\u0119\u015bl\u0105 ja\u017a\u0144"   # тестовая строка rw3d для UTF-16


def tmpdir(tc: unittest.TestCase) -> str:
    base = os.environ.get("W3NET_TMP")
    if base:
        os.makedirs(base, exist_ok=True)
    d = tempfile.mkdtemp(prefix="w3t_", dir=base)
    tc.addCleanup(shutil.rmtree, d, True)
    return d


def wait_until(pred, t: float = 5.0) -> bool:
    end = time.time() + t
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def exec_pkt(text: str, utf16: bool = False):
    return [I32(w3net.EXEC_MAGIC1), I32(w3net.EXEC_MAGIC2), (U16 if utf16 else U8)(text)]


# =========================================================================== формат (из старого набора)

class Primitives(unittest.TestCase):
    def test_int_vectors(self):
        self.assertEqual(encode_item(I8(18)), H("81 08 12"))
        self.assertEqual(encode_item(I16(25564)), H("81 16 63 dc"))
        self.assertEqual(encode_item(I32(912739132)), H("81 32 36 67 4b 3c"))
        self.assertEqual(encode_item(U32(912739132)), H("71 32 36 67 4b 3c"))
        self.assertEqual(encode_item(I64(-31742921364135)), H("81 64 ff ff e3 21 46 72 ad 59"))

    def test_string_vectors(self):
        self.assertEqual(encode_item(U8("Gaderypoluki")), H("ac 08 81 16 00 0c") + b"Gaderypoluki")
        u16 = encode_item(U16(POLISH))
        self.assertEqual(len(u16), 40)                                               # rw3d: 40
        self.assertEqual(u16[:6], H("9c 16 81 16 00 11"))                            # 17 кодовых единиц
        self.assertEqual(u16[6:8], H("00 5a"))

    def test_roundtrip_all_kinds(self):
        items = [I8(-5), I16(-2), I32(0x81160008), U32(0xCC00CC00), I64(1 << 40), U8("x y"), U16(POLISH), U8("")]
        self.assertEqual(decode_items(b"".join(encode_item(i) for i in items)), items)

    def test_magic_numbers_are_signed_on_wire(self):
        self.assertEqual(encode_item(I32(0x81160008)), H("81 32 81 16 00 08"))
        self.assertEqual(encode_item(I32(0xCC00CC00)), H("81 32 cc 00 cc 00"))

    def test_limits(self):
        encode_item(U8("a" * 32767))
        with self.assertRaises(w3net.CommandTooLong):
            encode_item(U8("a" * 32768))
        with self.assertRaises(ValueError):                                           # CommandTooLong - ValueError
            encode_packet([U8("a" * 32000), U8("b" * 32000), U8("c" * 2000)])
        self.assertEqual(w3net.exec_packet_size("x" * 32767, limit=w3net.MAX_STR), 32767 + 36)   # предел протокола

    def test_exec_limits_measured_on_500c(self):
        self.assertEqual((w3net.EXEC_MAX_CMD, w3net.EXEC_MAX_REPLY), (2047, 1999))
        self.assertEqual(w3net.NEAR_LIMIT, 1799)
        self.assertEqual(w3net.exec_packet_size("x" * 2047), 2047 + 36)
        with self.assertRaises(w3net.CommandTooLong):
            w3net.exec_packet_size("x" * 2048)                                        # игра молча выбросила бы
        with self.assertRaises(w3net.CommandTooLong):
            w3net.exec_packet_size("ж" * 1024)                                   # 2048 байт UTF-8, не символов
        self.assertEqual(w3net.check_exec_cmd("ж" * 1023), 2046)


class Messages(unittest.TestCase):
    def test_bind_remote_bytes(self):
        self.assertEqual(encode_packet(w3net.msg_bind("Remote")),
                         H("de ad 00 1c ac 08 81 16 00 04") + b"BIND" + H("ac 08 81 16 00 06") + b"Remote" + H("be ef"))

    def test_reload_bytes(self):
        self.assertEqual(encode_packet(w3net.msg_reload()),
                         H("de ad 00 1f ac 08 81 16 00 07") + b"scripts" + H("ac 08 81 16 00 06") + b"reload" + H("be ef"))

    def test_exec_request_bytes(self):
        cmd = "additem('griffin_sword', 1)"
        expect = (H("de ad 00 3f") + H("ac 08 81 16 00 06") + b"Remote" + H("81 32 12 34 56 78") +
                  H("81 32 81 16 00 08") + H("ac 08 81 16 00 1b") + cmd.encode() + H("be ef"))
        self.assertEqual(encode_packet(w3net.msg_exec(cmd)), expect)
        self.assertEqual(w3net.exec_packet_size(cmd), 63)

    def test_exec_response_bytes_and_classify(self):
        raw = (H("de ad 00 3d 81 32 12 34 56 78 81 32 81 16 00 08 ac 08 81 16 00 25") +
               b"Spam: Command executed without errors" + H("be ef"))
        items = decode_packet(raw)
        self.assertEqual(classify(items), ("exec", w3net.EXEC_OK_SILENT))
        self.assertEqual(encode_packet(items), raw)

    def test_root_path_request_bytes(self):
        self.assertEqual(encode_packet(w3net.msg_root_path()),
                         H("de ad 00 28 ac 08 81 16 00 0e") + b"ScriptCompiler" + H("ac 08 81 16 00 08") + b"RootPath" + H("be ef"))

    def test_var_list_bytes(self):
        self.assertEqual(encode_packet(w3net.msg_var_list()),
                         H("de ad 00 2e ac 08 81 16 00 06") + b"Config" + H("81 32 cc 00 cc 00 ac 08 81 16 00 04") + b"list" +
                         H("ac 08 81 16 00 00 ac 08 81 16 00 00 be ef"))

    def test_var_set_both_magics(self):
        self.assertIn(H("81 32 cc 00 cc 00"), encode_packet(w3net.msg_var_set("S", "N", "1")))
        self.assertIn(H("81 32 00 cc 00 cc"), encode_packet(w3net.msg_var_set("S", "N", "1", wk7_magic=True)))

    def test_opcodes_bytes(self):
        pkt = encode_packet(w3net.msg_opcodes("IsCiri", "CR4Player"))
        self.assertEqual(len(pkt), 0x63)
        self.assertEqual(pkt[:4], H("de ad 00 63"))
        self.assertIn(H("9c 16 81 16 00 06") + "IsCiri".encode("utf-16-be") + H("81 08 01 9c 16 81 16 00 09"), pkt)

    def test_rw3d_style_roundtrips(self):
        for items in (w3net.msg_bind("ScriptDebugger"), w3net.msg_bind("scripts"), w3net.msg_reload(),
                      w3net.msg_root_path(), w3net.msg_exec("additem('griffin_sword', 1)"), w3net.msg_mod_list(),
                      w3net.msg_opcodes("IsCiri", "CR4Player"), w3net.msg_opcodes("GetPlayerWitcher"),
                      w3net.msg_var_list("Graphics", ""), w3net.msg_var_set("VarSection", "VarName", "false")):
            self.assertEqual(decode_packet(encode_packet(items)), items)

    def test_classify_reload_progress_utf16_and_utf8(self):
        sc = U8("ScriptCompiler")
        for S in (U16, U8):     # до 5.0 UTF-16, на 5.0 UTF-8 (rw3d PR #12)
            k, ev = classify([sc, U8("warn"), U32(120), S("game/imports.ws"), S("Function used but not exported from C++")])
            self.assertEqual((k, ev.kind, ev.line, ev.path), ("reload", "warn", 120, "game/imports.ws"))
            k, ev = classify([sc, U8("error"), U32(7), S("local/x.ws"), S("boom")])
            self.assertEqual((ev.kind, ev.line, ev.path, ev.message), ("error", 7, "local/x.ws", "boom"))
            k, ev = classify([sc, U8("log"), S("Compiling scripts...")])
            self.assertEqual(ev.message, "Compiling scripts...")
        self.assertTrue(classify([sc, U8("finished"), I8(0)])[1].success)
        self.assertFalse(classify([sc, U8("finished"), I8(1)])[1].success)
        self.assertIsNone(classify([sc, U8("finished")])[1].success)

    def test_classify_accepts_utf8_where_utf16_expected(self):
        for S in (U16, U8):
            k, path = classify([U8("ScriptCompiler"), U8("RootPathConfirm"), S("C:\\scripts\\")])
            self.assertEqual((k, path), ("root_path", "C:\\scripts\\"))
            k, pk = classify([U8("scripts"), U8("pkgSyncListing"), I32(1), S(POLISH), S("C:\\x")])
            self.assertEqual(pk, [(POLISH, "C:\\x")])
            self.assertEqual(classify(exec_pkt("[MCW#1] end", utf16=(S is U16))), ("exec", "[MCW#1] end"))

    def test_classify_vars(self):
        items = [I32(0xCC00CC00), U8("vars"), I8(1), I8(0), U8("FXAA"), U8("Graphics"), U8("true"),
                 I8(3), I8(0), U8("Shadow Distance"), U8("Graphics"), U8("50.25"), I8(0)]
        k, v = classify(items)
        self.assertEqual(k, "vars")
        self.assertEqual([(x["name"], x["section"], x["value"], x["type"]) for x in v],
                         [("FXAA", "Graphics", "true", 1), ("Shadow Distance", "Graphics", "50.25", 3)])

    def test_exec_result_semantics(self):
        r = w3net.ExecResult("x", w3net.EXEC_FAIL, 0.0)
        self.assertFalse(r.ok)
        self.assertTrue(r.failed)
        self.assertEqual(r.lines, [])
        r = w3net.ExecResult("x", "Hello\nWorld!", 0.0)            # rw3d exec_command_resp_encode_test
        self.assertTrue(r.ok)
        self.assertEqual(r.lines, ["Hello", "World!"])
        r = w3net.ExecResult("mcw_poll(5)", "[MCW#5] pos 1 2 3 4\n[MCW#5] area AN_Velen\n[MCW#5] end", 0.0, seq=5)
        self.assertEqual(r.body, ["pos 1 2 3 4", "area AN_Velen"])
        self.assertEqual(r.ints("pos"), [1, 2, 3, 4])
        self.assertEqual(r.tokens("area"), ["AN_Velen"])
        self.assertIsNone(r.ints("area"))
        self.assertIsNone(r.tokens("cam"))
        self.assertFalse(w3net.ExecResult("mcw_ping(5)", "[MCW#5] pong 1", 0.0, seq=5, complete=False).ok)
        r = w3net.ExecResult("c", "[MCW#1] spawned 1 comps 3 scaled 3\n[MCW#1] end", 0.0, seq=1)
        self.assertIsNone(r.ints("spawned"))                        # слова вперемешку с числами - это kv()
        self.assertEqual(r.kv("spawned"), {"spawned": 1, "comps": 3, "scaled": 3})
        self.assertEqual(w3net.ExecResult("c", "[MCW#1] area AN_Velen x\n[MCW#1] end", 0.0, seq=1).kv("area"),
                         {"area": "AN_Velen", "x": None})
        self.assertIsNone(r.kv("nope"))

    def test_classify_reload_nonstandard_shapes_keep_errors(self):
        sc = U8("ScriptCompiler")
        k, ev = classify([sc, U8("error"), U32(5), U8("local\\x.ws")])        # 4 поля вместо 5
        self.assertEqual((k, ev.kind, ev.line, ev.message), ("reload", "error", 5, "local\\x.ws"))
        self.assertTrue(ev.raw)
        k, ev = classify([sc, U8("warn"), U8("a.ws"), U8("unused"), U8("more")])   # без номера строки
        self.assertEqual((ev.kind, ev.line, ev.path, ev.message), ("warn", 0, "a.ws", "unused more"))
        k, ev = classify([sc, U8("progress"), I32(50)])                          # подвид, которого не знаем
        self.assertEqual((k, ev.kind, ev.message, ev.raw), ("reload", "raw", "progress", [50]))
        self.assertIn("raw=", str(ev))


class Commands(unittest.TestCase):
    def test_cmd_seq(self):
        self.assertEqual(w3net.cmd_seq("mcw_ping(17)"), 17)
        self.assertEqual(w3net.cmd_seq(" mcw_echo( 3 , \"a\")"), 3)
        self.assertEqual(w3net.cmd_seq("mcwp_put(4, \"k\")"), 4)
        self.assertIsNone(w3net.cmd_seq("logstats()"))
        self.assertIsNone(w3net.cmd_seq("xmcw_ping(1)"))
        self.assertIsNone(w3net.cmd_seq("mcw_echo(\"5\")"))

    def test_line_seq(self):
        self.assertEqual(w3net.line_seq("[MCW#17] pong 5"), (17, "pong 5"))
        self.assertEqual(w3net.line_seq("[MCW] [MCW#3] via LogChannel"), (3, "via LogChannel"))
        self.assertEqual(w3net.line_seq("[MCW#5] echo 9 [MCW#6] end"), (5, "echo 9 [MCW#6] end"))
        self.assertIsNone(w3net.line_seq("[MCW] pong"))

    def test_build_call(self):
        self.assertEqual(w3net.build_call("mcw_alias", 4, Name("barrel"), 0.0), "mcw_alias(4, 'barrel', 0.0)")
        self.assertEqual(w3net.build_call("mcw_alias", 4, Name("x"), 0.4), "mcw_alias(4, 'x', 0.4)")
        self.assertEqual(w3net.build_call("f", True, -2), "f(true, -2)")
        # замер 5.00c: экранирования у парсера exec нет - '\' уходит как есть и доходит как есть (a\\b -> StrLen 4)
        self.assertEqual(w3net.build_call("mcw_echo", 3, "a\\\\b"), 'mcw_echo(3, "a\\\\b")')
        self.assertEqual(fake_w3.parse_exec(w3net.build_call("mcw_echo", 3, "a\\\\b")), ("mcw_echo", ["3", "a\\\\b"]))
        self.assertEqual(w3net.build_call("mcw_notify", 3, "Привет, мир"), 'mcw_notify(3, "Привет, мир")')
        self.assertEqual(w3net.build_call("mcw_cube", 3, " "), 'mcw_cube(3, " ")')     # пробел парсер пропускает
        with self.assertRaises(ValueError):
            w3net.build_call("mcw_echo", 1, "two\nlines")
        with self.assertRaises(TypeError):
            w3net.build_call("mcw_echo", 1, [1])

    def test_build_call_refuses_what_500c_cannot_parse(self):
        # кавычка внутри строки и пустая строка "" - вся команда 'Warn: Failed to process command'
        for bad in ('a"b', "a'b", '"', "", "\r"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                w3net.build_call("mcw_echo", 1, bad)
        for bad in ("", "a'b", 'a"b'):
            with self.assertRaises(ValueError, msg=repr(bad)):
                w3net.build_call("mcw_alias", 1, Name(bad), 0.0)
        with self.assertRaises(ValueError) as cm:
            w3net.build_call("mcw_cube", 1, "")
        self.assertIn('"-"', str(cm.exception))                                     # подсказка: путь по умолчанию
        self.assertEqual(set(w3net.EXEC_STR_FORBIDDEN), set("\"'\r\n"))


class Stream(unittest.TestCase):
    def setUp(self):
        self.p1 = encode_packet(w3net.msg_exec("mcw_ping(1)"))
        self.p2 = encode_packet(exec_pkt("[MCW#1] pong 1"))
        self.p3 = encode_packet(w3net.msg_bind("Utility"))

    def test_byte_by_byte(self):
        s, got = PacketStream(), []
        for b in self.p1 + self.p2 + self.p3:
            got += s.feed(bytes([b]))
        self.assertEqual(len(got), 3)
        self.assertEqual(got[1][2].value, "[MCW#1] pong 1")
        self.assertEqual(s.pending, 0)

    def test_coalesced(self):
        got = PacketStream().feed(self.p1 + self.p2 + self.p3 + self.p1)
        self.assertEqual([classify(g)[0] for g in got], ["unknown", "exec", "unknown", "unknown"])

    def test_split_inside_length_field(self):
        s = PacketStream()
        self.assertEqual(s.feed(self.p2[:3]), [])
        self.assertEqual(s.feed(self.p2[3:10]), [])
        self.assertEqual(len(s.feed(self.p2[10:])), 1)

    def test_resync_on_garbage_and_bad_tail(self):
        s = PacketStream()
        broken = bytearray(self.p2)
        broken[-1] = 0x00
        got = s.feed(b"\x00\x01junk\xde" + bytes(broken) + self.p3)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0], w3net.msg_bind("Utility"))
        self.assertGreater(s.resyncs, 0)

    def test_unknown_tag_does_not_poison_stream(self):
        weird = H("de ad 00 0d 81 08 01 ee ee 01 02 be ef")
        got = PacketStream().feed(weird + self.p3)
        self.assertEqual(got[0][0], I8(1))
        self.assertEqual(got[0][1].kind, "unknown")
        self.assertEqual(got[1], w3net.msg_bind("Utility"))

    def test_decode_packet_errors(self):
        with self.assertRaises(ProtocolError):
            decode_packet(self.p2[:-1])
        with self.assertRaises(ProtocolError):
            decode_items(H("ac 08 81 16 00 05 61"))


# =========================================================== сопоставление ответов без сокета (детерминированно)

class Router(unittest.TestCase):
    """Пакеты подаются прямо в _dispatch; отправка заглушена, таймауты снимаются вручную _expire_due()."""

    def setUp(self):
        self.cli = W3Client(timeout=5.0)
        self.cli._sendall = lambda data: None            # type: ignore[assignment]
        self.unsol = []
        self.cli.on_unsolicited(self.unsol.append)

    def feed(self, text, utf16=False):
        self.cli._dispatch(exec_pkt(text, utf16))

    def expire(self):
        self.cli._next_expire = 0.0
        self.cli._expire_due()

    def test_single_packet(self):
        f = self.cli.exec_async("mcw_ping(7)")
        self.feed("[MCW#7] pong 100\n[MCW#7] end")
        r = f.result(0)
        self.assertEqual((r.seq, r.body, r.packets, r.complete, r.ok), (7, ["pong 100"], 1, True, True))

    def test_packet_per_line(self):
        f = self.cli.exec_async("mcw_poll(3)")
        for ln in ("[MCW#3] pos 1 2 3 0", "[MCW#3] cam 1 2 3 0 0 0", "[MCW#3] area A", "[MCW#3] world w"):
            self.feed(ln)
            self.assertFalse(f.done())
        self.feed("[MCW#3] end")
        r = f.result(0)
        self.assertEqual((r.packets, len(r.body), r.ints("pos")), (5, 4, [1, 2, 3, 0]))

    def test_newer_seq_marks_older_lost(self):
        fa = self.cli.exec_async("mcw_ping(1)")
        fb = self.cli.exec_async("mcw_ping(2)")
        fc = self.cli.exec_async("mcw_ping(3)")
        self.feed("[MCW#2] pong 5\n[MCW#2] end")
        self.assertEqual(fb.result(0).body, ["pong 5"])
        with self.assertRaises(w3net.ReplyLost):
            fa.result(0)
        self.assertFalse(fc.done())
        self.feed("[MCW#3] pong 6\n[MCW#3] end")
        self.assertEqual(fc.result(0).body, ["pong 6"])
        self.assertEqual(self.cli.stats["lost"], 1)
        self.assertEqual(self.cli.in_flight, 0)

    def test_lost_keeps_partial(self):
        fa = self.cli.exec_async("mcw_poll(1)")
        fb = self.cli.exec_async("mcw_ping(2)")
        self.feed("[MCW#1] pos 1 2 3 4")                      # пакет на строку, 'end' потерян
        self.feed("[MCW#2] pong 1\n[MCW#2] end")
        with self.assertRaises(w3net.ReplyLost) as cm:
            fa.result(0)
        self.assertEqual(cm.exception.partial.body, ["pos 1 2 3 4"])
        self.assertTrue(fb.result(0).ok)

    def test_unknown_seq_is_unsolicited(self):
        f = self.cli.exec_async("mcw_ping(1)")
        self.feed("[MCW#99] pong 1\n[MCW#99] end")
        self.assertEqual(len(self.unsol), 1)
        self.assertFalse(f.done())

    def test_late_seq_after_timeout_is_dropped(self):
        f = self.cli.exec_async("mcw_ping(1)", timeout=0.0)
        self.expire()
        with self.assertRaises(TimeoutError):
            f.result(0)
        g = self.cli.exec_async("mcw_ping(2)")
        self.feed("[MCW#1] pong 1\n[MCW#1] end")              # опоздал
        self.assertEqual((self.cli.stats["late"], self.unsol, g.done()), (1, [], False))
        self.feed("[MCW#2] pong 2\n[MCW#2] end")
        self.assertTrue(g.result(0).ok)

    def test_fifo_timeout_removes_entry_and_late_is_dropped(self):
        f = self.cli.exec_async("spawnbarrel()", timeout=0.0)
        self.expire()
        with self.assertRaises(TimeoutError):
            f.result(0)
        self.assertEqual(self.cli.in_flight, 0)
        self.feed(w3net.EXEC_OK_SILENT)                        # опоздавший ответ, никто не ждёт
        self.assertEqual((self.cli.stats["late"], self.unsol), (1, []))
        g = self.cli.exec_async("spawnbarrel()")
        self.feed(w3net.EXEC_OK_SILENT)
        self.assertTrue(g.result(0).ok)

    def test_fifo_and_seq_interleaved(self):
        f1 = self.cli.exec_async("spawnbarrel()")
        f2 = self.cli.exec_async("mcw_ping(4)")
        f3 = self.cli.exec_async("additem('x', 1)")
        self.feed(w3net.EXEC_OK_SILENT)
        self.feed("[MCW#4] pong 1\n[MCW#4] end")
        self.feed(w3net.EXEC_FAIL)
        self.assertTrue(f1.result(0).ok)
        self.assertTrue(f2.result(0).ok)
        self.assertFalse(f3.result(0).ok)

    def test_seq_marker_proves_older_fifo_lost(self):
        f1 = self.cli.exec_async("spawnbarrel()")
        f2 = self.cli.exec_async("mcw_ping(4)")
        self.feed("[MCW#4] pong 1\n[MCW#4] end")
        with self.assertRaises(w3net.ReplyLost):
            f1.result(0)
        self.assertTrue(f2.result(0).ok)

    def test_seq_function_missing(self):
        f = self.cli.exec_async("mcwp_state(1)")
        self.feed(w3net.EXEC_FAIL)
        r = f.result(0)
        self.assertEqual((r.ok, r.failed, r.complete), (False, True, False))

    def test_silent_without_log_feed_completes_incomplete(self):
        self.cli.status_grace = 0.0                            # без ожидания строк после статуса
        f = self.cli.exec_async("mcw_ping(1)")
        self.feed(w3net.EXEC_OK_SILENT)                        # Log() не дошёл до ответа
        r = f.result(0)
        self.assertEqual((r.ok, r.silent, r.complete), (False, True, False))

    def test_silent_completes_after_status_grace(self):
        self.cli.status_grace = 0.05
        f = self.cli.exec_async("mcw_ping(1)")
        self.feed(w3net.EXEC_OK_SILENT)
        self.assertFalse(f.done())                             # статус мог прийти раньше строк - ждём
        time.sleep(0.07)
        self.expire()
        r = f.result(0)
        self.assertEqual((r.ok, r.silent, r.complete, r.text), (False, True, False, w3net.EXEC_OK_SILENT))
        self.assertEqual(self.cli.stats["silent"], 1)
        # строки пришли позже grace: поздние, но доказывают, что Log() в ответе - дальше 'Spam' чужой
        self.feed("[MCW#1] pong 1\n[MCW#1] end")
        self.assertEqual((self.cli.stats["late"], self.unsol), (1, []))
        g = self.cli.exec_async("mcw_ping(2)")
        self.feed(w3net.EXEC_OK_SILENT)
        self.feed("[MCW#2] pong 2\n[MCW#2] end")
        self.assertTrue(g.result(0).ok)

    def test_status_before_lines_one_packet_and_per_line(self):
        # rw3d PR #12 «streams»: 'Spam' сразу, строки Log() следом. Новый клиент - маркеров ещё не видел.
        f = self.cli.exec_async("mcw_ping(1)")
        self.feed(w3net.EXEC_OK_SILENT)
        self.assertFalse(f.done())
        self.feed("[MCW#1] pong 7\n[MCW#1] end")
        r = f.result(0)
        self.assertEqual((r.ok, r.body, r.packets), (True, ["pong 7"], 2))
        g = self.cli.exec_async("mcw_poll(2)")
        self.feed(w3net.EXEC_OK_SILENT)                        # маркеры уже видели: хвост/голова-статус - чужой
        for ln in ("[MCW#2] pos 1 2 3 4", "[MCW#2] area A", "[MCW#2] end"):
            self.feed(ln)
        self.assertEqual(g.result(0).ints("pos"), [1, 2, 3, 4])
        self.assertEqual((self.cli.stats["stray_status"], self.cli.stats["silent"], self.unsol), (1, 0, []))

    def test_status_before_lines_pipelined_statuses_first(self):
        futs = [self.cli.exec_async(f"mcw_ping({i})") for i in (1, 2, 3)]
        for _ in range(3):
            self.feed(w3net.EXEC_OK_SILENT)                    # все статусы при постановке в очередь
        self.assertFalse(any(f.done() for f in futs))
        for i in (1, 2, 3):
            self.feed(f"[MCW#{i}] pong {i}")
            self.feed(f"[MCW#{i}] end")
        self.assertEqual([f.result(0).ints("pong") for f in futs], [[1], [2], [3]])
        self.assertEqual(self.unsol, [])

    def test_late_fifo_status_not_given_to_seq_command(self):
        # FIFO снят по таймауту (экран загрузки), следом mcw-команда: опоздавший 'Spam' FIFO - поздний, не её
        f = self.cli.exec_async("spawnbarrel()", timeout=0.0)
        self.expire()
        self.assertRaises(TimeoutError, f.result, 0)
        g = self.cli.exec_async("mcw_ping(5)")
        self.feed(w3net.EXEC_OK_SILENT)
        self.assertFalse(g.done())
        self.assertEqual((self.cli.stats["late"], self.cli._fifo_orphans), (1, []))
        self.feed("[MCW#5] pong 1\n[MCW#5] end")
        self.assertTrue(g.result(0).ok)

    def test_late_fifo_warn_not_given_to_seq_after_markers_seen(self):
        ok = self.cli.exec_async("mcw_ping(1)")
        self.feed("[MCW#1] pong 1\n[MCW#1] end")
        self.assertTrue(ok.result(0).ok)
        f = self.cli.exec_async("additem('nonexistent_typo')", timeout=0.0)
        self.expire()
        self.assertRaises(TimeoutError, f.result, 0)
        g = self.cli.exec_async("mcw_ping(2)")
        self.feed(w3net.EXEC_FAIL)                             # поздний ответ additem
        self.feed("[MCW#2] pong 2\n[MCW#2] end")
        r = g.result(0)
        self.assertEqual((r.ok, r.failed, self.cli.stats["late"]), (True, False, 1))

    def test_late_fifo_reply_goes_to_next_fifo_documented(self):
        # ограничение (docstring w3net): без id поздний FIFO-ответ достаётся следующей ждущей FIFO-команде;
        # съедать его нельзя - если ответ сироты потерян совсем, каждая следующая FIFO-команда теряла бы свой
        f = self.cli.exec_async("cmd_a()", timeout=0.0)
        self.expire()
        self.assertRaises(TimeoutError, f.result, 0)
        g = self.cli.exec_async("cmd_b()")
        self.feed("output of A")
        self.assertEqual(g.result(0).text, "output of A")

    def test_trailing_status_is_stray_after_markers_seen(self):
        f = self.cli.exec_async("mcw_ping(1)")
        self.feed("[MCW#1] pong 1")
        self.feed("[MCW#1] end")
        self.assertTrue(f.result(0).ok)
        g = self.cli.exec_async("mcw_ping(2)")
        self.feed(w3net.EXEC_OK_SILENT)                        # хвостовой статус прошлой команды
        self.assertFalse(g.done())
        self.feed("[MCW#2] pong 2\n[MCW#2] end")
        self.assertTrue(g.result(0).ok)
        self.feed(w3net.EXEC_OK_SILENT)
        self.assertEqual((self.cli.stats["stray_status"], self.unsol), (2, []))

    def test_log_feed_completes_when_reply_is_silent(self):
        self.cli._log_fed = True
        f = self.cli.exec_async("mcw_logtest(9)")
        self.feed(w3net.EXEC_OK_SILENT)
        self.assertFalse(f.done())
        g = self.cli.exec_async("spawnbarrel()")                 # ждущий лога не держит FIFO
        self.feed(w3net.EXEC_OK_SILENT)
        self.assertTrue(g.result(0).ok)
        self.cli.feed_log_line("[MCW#9] via Log")
        self.cli.feed_log_line("[MCW] [MCW#9] via LogChannel")
        self.cli.feed_log_line("[MCW#9] end")
        r = f.result(0)
        self.assertEqual((r.source, r.body, r.ok), ("log", ["via Log", "via LogChannel"], True))

    def test_plain_lines_stick_to_marker_and_echo_marker_inside_text(self):
        f = self.cli.exec_async("mcw_echo(5, \"x\")")
        self.feed("engine noise\n[MCW#5] echo 9 [MCW#6] end\n[MCW#5] end")
        r = f.result(0)
        self.assertEqual(r.body, ["echo 9 [MCW#6] end"])
        self.assertIn("engine noise", r.lines)

    def test_plain_text_while_seq_in_progress_belongs_to_it(self):
        f = self.cli.exec_async("mcw_poll(5)")
        self.feed("[MCW#5] pos 1 2 3 4")
        self.feed("vanilla log line")
        self.feed("[MCW#5] end")
        self.assertIn("vanilla log line", f.result(0).lines)
        self.assertEqual(self.unsol, [])

    def test_unsolicited_plain_when_idle(self):
        self.feed("netlog: hello")
        self.cli._dispatch([U8("Utility"), U8("log"), U8("x")])
        self.assertEqual(len(self.unsol), 2)

    def test_utf16_reply(self):
        f = self.cli.exec_async("mcw_echo(2, \"x\")")
        self.feed("[MCW#2] echo 1 " + POLISH + "\n[MCW#2] end", utf16=True)
        self.assertEqual(f.result(0).body, ["echo 1 " + POLISH])

    def test_near_limit_warning(self):
        f = self.cli.exec_async("mcw_echo(2, \"x\")")
        with self.assertLogs("w3net", logging.WARNING):
            self.feed("[MCW#2] echo " + "x" * 1800 + "\n[MCW#2] end")         # 1827 байт: 90 % от 1999
        self.assertTrue(f.result(0).near_limit)
        self.assertEqual(self.cli.stats["near_limit"], 1)
        g = self.cli.exec_async("mcw_echo(3, \"x\")")
        self.feed("[MCW#3] echo " + "x" * 1700 + "\n[MCW#3] end")
        self.assertFalse(g.result(0).near_limit)

    def test_truncated_line_flagged_counted_and_warned(self):
        # замер 5.00c: строка Log() длиннее ~1999 символов приходит как '......(truncated).......', end - на месте
        f = self.cli.exec_async("mcw_echo(91027094, \"x\")")
        with self.assertLogs("w3net", logging.WARNING) as cm:
            self.feed(w3net.EXEC_TRUNCATED + "\n[MCW#91027094] end")
        r = f.result(0)
        self.assertEqual((r.ok, r.complete, r.truncated, r.body, r.tokens("echo")), (True, True, True, [], None))
        self.assertIn("truncated", "\n".join(cm.output))
        self.assertEqual(self.cli.stats["truncated"], 1)
        g = self.cli.exec_async("mcw_poll(5)")                                 # пакет на строку: обрезана средняя
        with self.assertLogs("w3net", logging.WARNING):
            for ln in ("[MCW#5] pos 1 2 3 4", "......(truncated)........", "[MCW#5] end"):   # точек может быть иначе
                self.feed(ln)
        r = g.result(0)
        self.assertEqual((r.ok, r.truncated, r.ints("pos")), (True, True, [1, 2, 3, 4]))
        h = self.cli.exec_async("spawnbarrel()")                               # FIFO
        with self.assertLogs("w3net", logging.WARNING):
            self.feed(w3net.EXEC_TRUNCATED)
        self.assertTrue(h.result(0).truncated)
        k = self.cli.exec_async("mcw_ping(6)")
        self.feed("[MCW#6] pong 1\n[MCW#6] end")
        self.assertFalse(k.result(0).truncated)
        self.assertEqual(self.cli.stats["truncated"], 3)

    def test_duplicate_seq_rejected(self):
        self.cli.exec_async("mcw_ping(1)")
        with self.assertRaises(ValueError):
            self.cli.exec_async("mcw_ping(1)")

    def test_command_too_long(self):
        with self.assertRaises(w3net.CommandTooLong):
            self.cli.exec_async("mcw_echo(1, \"" + "a" * 32760 + "\")")
        self.assertEqual(self.cli.in_flight, 0)

    def test_command_over_2047_bytes_rejected_before_register(self):
        # замер 5.00c: команду от 2048 байт игра молча выбрасывает - клиент её не шлёт и ничего не регистрирует
        sent = []
        self.cli._sendall = sent.append                                         # type: ignore[assignment]
        cmd = w3net.build_call("mcw_echo", 91027094, "x" * (2048 - len('mcw_echo(91027094, "")')))
        self.assertEqual(len(cmd.encode()), 2048)
        with self.assertRaises(w3net.CommandTooLong):
            self.cli.exec_async(cmd)
        self.assertEqual((self.cli.in_flight, sent, self.cli.stats["exec_sent"]), (0, [], 0))
        f = self.cli.exec_async(cmd[:-3] + "\")")                               # 2047 байт - уходит
        self.assertEqual((self.cli.in_flight, len(sent)), (1, 1))
        self.feed("[MCW#91027094] end")
        self.assertTrue(f.result(0).ok)
        self.cli.max_cmd = w3net.MAX_STR                                        # замер предела (bench maxlen)
        self.cli.exec_async(cmd)
        self.assertEqual(len(sent), 2)

    def test_explicit_seq_and_fifo_override(self):
        f = self.cli.exec_async("custom_thing()", seq=11)
        self.feed("[MCW#11] end")
        self.assertTrue(f.result(0).complete)
        g = self.cli.exec_async("mcw_ping(12)", fifo=True)
        self.feed("[MCW#12] pong 1\n[MCW#12] end")             # FIFO: seq не ищется, пакет целиком - голове
        self.assertEqual((g.result(0).seq, g.result(0).lines[-1]), (None, "[MCW#12] end"))
        self.feed("[MCW#13] pong 1\n[MCW#13] end")             # никто не ждёт
        self.assertEqual(len(self.unsol), 1)


# ======================================================================== интеграция с фейковой игрой

class Integration(unittest.TestCase):
    def make(self, cli_kw=None, **kw):
        srv = fake_w3.FakeWitcher(port=0, **kw).start()
        self.addCleanup(srv.stop)
        opts = {"timeout": 3.0}
        opts.update(cli_kw or {})
        cli = W3Client(port=srv.port, **opts).connect()
        self.addCleanup(cli.close)
        return srv, cli

    def test_binds_all_namespaces_first(self):
        srv, cli = self.make()
        cli.exec("spawnbarrel()")
        self.assertEqual(srv.bound_seen, list(w3net.ALL_NAMESPACES))

    def test_default_ports_and_fallback(self):
        self.assertEqual(w3net.DEFAULT_PORTS, (37001, 37000))
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        closed = s.getsockname()[1]
        s.close()
        srv = fake_w3.FakeWitcher(port=0).start()
        self.addCleanup(srv.stop)
        cli = W3Client(ports=(closed, srv.port), connect_timeout=1.0).connect()
        self.addCleanup(cli.close)
        self.assertEqual(cli.port, srv.port)
        self.assertTrue(cli.call("mcw_ping").ok)
        with self.assertRaises(ConnectionError):
            W3Client(ports=(closed,), connect_timeout=0.5).connect()

    def test_exec_variants(self):
        srv, cli = self.make()
        r = cli.exec("mcw_ping(7)")
        self.assertTrue(r.ok)
        self.assertEqual(r.lines[-1], "[MCW#7] end")
        self.assertEqual(len(r.ints("pong")), 1)
        self.assertGreater(r.rtt, 0)
        self.assertEqual(cli.exec("spawnbarrel()").text, w3net.EXEC_OK_SILENT)
        self.assertEqual(cli.exec("logstats()").text, w3net.EXEC_OK_SILENT)   # LogChannel('Stats') - в файл
        r = cli.exec("mcw_nope()")                                          # первая проверка протокола
        self.assertEqual((r.ok, r.failed, r.seq), (False, True, None))
        self.assertFalse(cli.exec("mcw_ping(\"notint\")", seq=8).ok)       # аргумент не разобрался
        self.assertEqual(len(cli.exec("mcw_poll(9)").body), 4)
        s = "set:1:64:-3:0|clear:2:64:-3|" * 50
        self.assertEqual(cli.call("mcw_echo", s).body[0], f"echo {len(s)} {s}")

    def test_contract_all_functions(self):
        srv, cli = self.make()
        pos = cli.call("mcw_poll")
        x, y, z, yaw = pos.ints("pos")
        self.assertEqual(len(pos.ints("cam")), 6)
        self.assertEqual(pos.tokens("area"), ["AN_Velen"])
        self.assertTrue(pos.tokens("world")[0].endswith(".w2w"))
        self.assertEqual(cli.call("mcw_count").ints("count"), [0])
        self.assertEqual(cli.call("mcw_cube", "-").kv("spawned"), {"spawned": 1, "comps": 1})
        self.assertEqual(cli.call("mcw_cube", "nope.txt").kv("spawned"), {"spawned": 0, "comps": 0})
        self.assertEqual(cli.call("mcw_alias", Name("barrel"), 0.0).kv("spawned"),
                         {"spawned": 1, "comps": 3, "scaled": 0})
        # ws:354-363: масштаб задаётся каждому CMeshComponent -> при scale>0 scaled == comps
        self.assertEqual(cli.call("mcw_alias", Name("denied_area"), 0.4).kv("spawned"),
                         {"spawned": 1, "comps": 5, "scaled": 5})
        self.assertEqual(cli.call("mcw_alias", Name("nope"), 0.4).kv("spawned"),
                         {"spawned": 0, "comps": 0, "scaled": 0})
        look = cli.call("mcw_look").ints("look")
        self.assertEqual((look[0], look[4]), (1, 1))                 # куб в 2 м перед Геральтом под лучом камеры
        self.assertEqual(cli.call("mcw_ground", x, y).ints("ground"), [1, z])
        self.assertEqual(cli.call("mcw_boom").ints("boom"), [1])
        self.assertEqual(cli.call("mcw_notify", "Привет, Геральт").body, ["notified"])
        self.assertEqual(srv.world.notifications, ["Привет, Геральт"])
        st = cli.call("mcw_stress", 100, 150, "-", timeout=10).ints("stress")
        self.assertEqual(st[0], 100)
        self.assertEqual(cli.call("mcw_count").ints("count"), [104])
        # ws:427-513: сначала все clear (по всем MCW_BLOCK), потом все set; clear-промах и плохой t - ошибки
        b = cli.call("mcw_batch", "set:0:0:0:0|set:100:0:0:1|set:0:0:0:0|clear:100:0:0|clear:5:5:5|set:1:2:3:13|junk")
        self.assertEqual(b.ints("batch")[:3], [3, 0, 4])
        self.assertEqual(cli.call("mcw_count").ints("count"), [107])
        self.assertEqual(cli.call("mcw_clearall").ints("cleared"), [107])
        lt = cli.call("mcw_logtest")
        self.assertEqual(lt.body, ["via Log"])                        # LogChannel - только в файл (так в фейке)

    def test_probe_contract_and_respawn(self):
        srv, cli = self.make()
        tok = cli.call("mcwp_state").ints("state")
        self.assertEqual(tok[1:], [0, 0])
        self.assertEqual(cli.call("mcwp_put", "a").tokens("put"), ["a", "1"])
        self.assertEqual(cli.call("mcwp_put", "b").tokens("put"), ["b", "2"])
        self.assertEqual(cli.call("mcwp_get", "a").tokens("get"), ["a", "1"])
        self.assertEqual(cli.call("mcwp_del", "a").tokens("del"), ["a", "1"])
        self.assertEqual(cli.call("mcwp_get", "a").tokens("get"), ["a", "0"])
        self.assertEqual(cli.call("mcwp_del", "zzz").tokens("del"), ["zzz", "0"])
        srv.world.respawn_player()                                    # загрузка сейва: новый CR4Player
        tok2 = cli.call("mcwp_state").ints("state")
        self.assertNotEqual(tok2[0], tok[0])
        self.assertEqual(tok2[1:], [0, 0])
        srv.world.has_player = False                                  # главное меню
        self.assertEqual(cli.call("mcwp_state").ints("state"), [-1, -1, -1])
        self.assertEqual([b.split()[0] for b in cli.call("mcw_poll").body], ["cam", "area", "world"])

    def test_no_probe(self):
        srv, cli = self.make(probe=False)
        r = cli.call("mcwp_state")
        self.assertTrue(r.failed)
        self.assertTrue(cli.call("mcw_ping").ok)

    def test_pipelined_seq(self):
        srv, cli = self.make(frame_time=0.001)
        futs = [cli.exec_async(f"mcw_ping({i})") for i in range(1, 201)]
        got = [f.result(10).seq for f in futs]
        self.assertEqual(got, list(range(1, 201)))
        self.assertTrue(all(f.result().ok for f in futs))
        self.assertEqual(cli.in_flight, 0)

    def test_threads_share_one_client(self):
        srv, cli = self.make()
        errors = []

        def worker(k):
            for i in range(50):
                r = cli.call("mcw_echo", f"t{k}-{i}")
                if r.tokens("echo") != [str(len(f"t{k}-{i}")), f"t{k}-{i}"]:
                    errors.append((k, i, r.text))

        ts = [threading.Thread(target=worker, args=(k,)) for k in range(4)]
        [t.start() for t in ts]
        [t.join(20) for t in ts]
        self.assertEqual(errors, [])

    def test_fragmented_and_coalesced_replies(self):
        for kw in ({"chunk_size": 1}, {"chunk_size": 7, "chunk_delay": 0.0005}, {"coalesce": True, "frame_time": 0.01}):
            srv, cli = self.make(**kw)
            futs = [cli.call_async("mcw_poll") for _ in range(30)]
            self.assertTrue(all(len(f.result(10).body) == 4 for f in futs), kw)

    def test_per_line_mode(self):
        srv, cli = self.make(per_line=True)
        r = cli.call("mcw_poll")
        self.assertEqual((r.packets, len(r.body)), (5, 4))
        futs = [cli.call_async("mcw_poll") for _ in range(20)]
        self.assertTrue(all(f.result(5).ok for f in futs))

    def test_per_line_with_trailing_status(self):
        srv, cli = self.make(per_line=True, trailing_status=True)
        for _ in range(10):
            self.assertTrue(cli.call("mcw_poll").ok)
        futs = [cli.call_async("mcw_ping") for _ in range(20)]
        self.assertTrue(all(f.result(5).ok for f in futs))
        self.assertTrue(wait_until(lambda: cli.stats["stray_status"] == 30))

    def test_utf16_server(self):
        srv, cli = self.make(utf8_only=False)
        self.assertEqual(cli.call("mcw_echo", POLISH).tokens("echo"),               # StrLen 5.00c - байты UTF-8
                         [str(len(POLISH.encode("utf-8"))), *POLISH.split()])
        self.assertTrue(cli.root_path().endswith("scripts"))
        res = cli.reload_scripts(timeout=5)
        self.assertTrue(res.success)
        self.assertEqual([e.kind for e in res.events], ["started", "log", "log", "warn", "finished"])
        self.assertEqual(res.warnings[0].line, 12)

    def test_other_requests(self):
        srv, cli = self.make()
        self.assertTrue(cli.root_path().endswith("content0\\scripts"))
        self.assertEqual([m for m, _ in cli.mod_list()], ["content0", "modMCW"])
        rows = cli.opcodes("IsCiri", "CR4Player")
        self.assertEqual((rows[0][0], len(rows[0][1])), (0, 2))
        self.assertEqual([v["name"] for v in cli.var_list("Visuals", "gamma")], ["GammaValue", "HdrGamma1"])
        self.assertEqual([v["name"] for v in cli.var_list("Scripts")], ["LogDebugScripts"])

    def test_reload_progress_and_error(self):
        srv, cli = self.make()
        seen = []
        res = cli.reload_scripts(on_progress=lambda e: seen.append(e.kind), timeout=5)
        self.assertTrue(res.success)
        self.assertEqual(seen[-1], "finished")
        srv2, cli2 = self.make(reload_error=True)
        res = cli2.reload_scripts(timeout=5)
        self.assertFalse(res.success)
        e = res.errors[0]
        self.assertEqual((e.line, e.path), (42, "local\\mcw_spike.ws"))
        self.assertIn("map", e.message)

    def test_no_reply_without_bind_and_no_bind_required(self):
        srv = fake_w3.FakeWitcher(port=0).start()
        self.addCleanup(srv.stop)
        cli = W3Client(port=srv.port, namespaces=()).connect()
        self.addCleanup(cli.close)
        with self.assertRaises(TimeoutError):
            cli.exec("mcw_ping(1)", timeout=0.3)
        self.assertEqual(srv.execs_seen, ["mcw_ping(1)"])
        self.assertEqual(cli.in_flight, 0)
        srv2, cli2 = self.make(cli_kw={"namespaces": ()}, require_bind=False)
        self.assertTrue(cli2.call("mcw_ping").ok)

    def test_drop_every_sequential_seq_recovers(self):
        srv, cli = self.make(cli_kw={"timeout": 0.4}, drop_every=3)
        out = []
        for _ in range(7):
            try:
                out.append(cli.call("mcw_ping").ok)
            except TimeoutError:
                out.append("timeout")
        self.assertEqual(out, [True, True, "timeout", True, True, "timeout", True])
        self.assertEqual(cli.in_flight, 0)

    def test_drop_every_fifo_recovers(self):
        # verify_protocol/fifo_test.py: раньше после потери 3-го ответа все следующие exec() падали навсегда
        srv, cli = self.make(cli_kw={"timeout": 0.4}, drop_every=3)
        out = []
        for i in range(1, 8):
            try:
                r = cli.exec(f"mcw_ping({i})", fifo=True)
                out.append(w3net.line_seq(r.lines[0])[0])
            except TimeoutError:
                out.append("timeout")
        self.assertEqual(out, [1, 2, "timeout", 4, 5, "timeout", 7])
        self.assertEqual(cli.in_flight, 0)

    def test_drop_every_pipelined_marks_lost(self):
        srv, cli = self.make(cli_kw={"timeout": 1.0}, drop_every=3)
        futs = [cli.call_async("mcw_ping") for _ in range(9)]
        kinds = []
        for f in futs:
            try:
                f.result(5)
                kinds.append("ok")
            except w3net.ReplyLost:
                kinds.append("lost")
            except TimeoutError:
                kinds.append("timeout")
        self.assertEqual(kinds, ["ok", "ok", "lost", "ok", "ok", "lost", "ok", "ok", "timeout"])

    def test_freeze_late_reply_is_dropped(self):
        srv, cli = self.make(cli_kw={"timeout": 0.25}, freeze_every=2, freeze_ms=600)
        self.assertTrue(cli.call("mcw_ping").ok)
        with self.assertRaises(TimeoutError):
            cli.call("mcw_ping")                                   # игра замерла на 600 мс
        self.assertTrue(wait_until(lambda: cli.stats["late"] == 1, 3))
        self.assertTrue(cli.call("mcw_ping").ok)
        self.assertEqual(cli.stats["unsolicited"], 0)

    def test_unsolicited_utility_and_exec_kinds(self):
        for kind in ("utility", "exec"):
            got = []
            srv = fake_w3.FakeWitcher(port=0, unsolicited_ms=10, unsolicited_kind=kind).start()
            self.addCleanup(srv.stop)
            cli = W3Client(port=srv.port, timeout=3.0)
            cli.on_unsolicited(got.append)
            cli.connect()
            self.addCleanup(cli.close)
            for _ in range(20):
                r = cli.call("mcw_poll")
                self.assertEqual(len(r.body), 4, kind)
            self.assertTrue(wait_until(lambda: len(got) >= 3), kind)
            texts = [i.text for p in got for i in p if i.text]
            self.assertTrue(any("netlog tick" in t for t in texts), kind)

    def test_execs_per_frame(self):
        for mpf, expect_frames in ((0, 1), (1, 8)):
            srv, cli = self.make(frame_time=0.02, max_per_frame=mpf)
            futs = [cli.call_async("mcw_ping") for _ in range(8)]
            eng = {f.result(5).ints("pong")[0] for f in futs}
            if mpf == 0:
                self.assertLessEqual(len(eng), 2)                # пачка может разрезаться границей кадра
            else:
                self.assertEqual(len(eng), expect_frames)

    def test_log_not_in_reply_without_and_with_scriptslog(self):
        d = tmpdir(self)
        path = os.path.join(d, "scriptslog.txt")
        open(path, "w").close()
        srv, cli = self.make(log_in_reply=False, scriptslog_path=path)
        r = cli.call("mcw_ping")
        self.assertEqual((r.ok, r.silent, r.complete), (False, True, False))
        srv2, cli2 = self.make(log_in_reply=False, scriptslog_path=path)
        cli2.attach_scriptslog(path, poll_interval=0.002)
        r = cli2.call("mcw_poll")
        self.assertEqual((r.ok, r.source, len(r.body)), (True, "log", 4))
        r = cli2.call("mcw_logtest")
        self.assertEqual(r.body, ["via Log", "via LogChannel"])
        self.assertTrue(cli2.exec("spawnbarrel()").ok)

    def test_stall_send_survives_with_long_send_timeout(self):
        # verify_protocol/stall_test.py: раньше общий settimeout(0.25) ронял sendall больших пакетов. Команды - в
        # пределах exec 5.00c (1800 байт аргумента: команда ~1824, ответ ~1848 байт), зато их много
        srv, cli = self.make(cli_kw={"timeout": 10.0, "send_timeout": 10.0}, stall_ms=800, stall_after=1)
        payload = "s" * 1800
        futs = [cli.call_async("mcw_echo", payload) for _ in range(350)]
        self.assertTrue(all(f.result(15).ok for f in futs))
        self.assertTrue(cli.connected)
        self.assertEqual((srv.too_long_cmds, srv.replies_lost), ([], 0))

    def test_stall_send_timeout_short(self):
        srv, cli = self.make(cli_kw={"timeout": 10.0, "send_timeout": 0.3}, stall_ms=3000, stall_after=1)
        payload = "s" * 1800
        t0 = time.time()
        errs = []
        for _ in range(3000):
            f = cli.call_async("mcw_echo", payload)
            if f.done() and f.exception() is not None:
                errs.append(f.exception())
                break
        self.assertTrue(errs, "отправка так и не упёрлась в окно приёма")
        self.assertIsInstance(errs[0], (w3net.SendTimeout, ConnectionError))
        self.assertLess(time.time() - t0, 2.5)
        if isinstance(errs[0], ConnectionError):                   # пакет порван - соединение закрыто
            self.assertTrue(wait_until(lambda: not cli.connected, 2))

    def test_disconnect_fails_pending(self):
        srv, cli = self.make(drop_after_execs=1, frame_time=0.01)
        lost = threading.Event()
        cli.on_disconnect(lambda e: lost.set())
        f1 = cli.exec_async("mcw_ping(1)")
        f2 = cli.exec_async("mcw_ping(2)")
        with self.assertRaises(ConnectionError):
            f2.result(5)
        self.assertTrue(lost.wait(5))
        self.assertFalse(cli.connected)
        self.assertTrue(f1.done())

    def test_command_too_long_raises_before_send(self):
        srv, cli = self.make()
        with self.assertRaises(ValueError):
            cli.exec_async("mcw_echo(1, \"" + "a" * 40000 + "\")")
        self.assertEqual(cli.in_flight, 0)
        self.assertTrue(cli.call("mcw_ping").ok)

    # -- пределы exec 5.00c на фейке (замер 2026-10-03): команда 2047, ответ 1999 байт, строка Log() 1999 символов
    def test_fake_drops_command_over_2047_bytes(self):
        srv, cli = self.make(cli_kw={"timeout": 0.5, "max_cmd": w3net.MAX_STR})   # клиент сам такую не шлёт
        base = len('mcw_batch(1000000001, "")')
        cmd = w3net.build_call("mcw_batch", 1000000001, "set:1:2:3:0|" + "x" * (2048 - base - 12))
        self.assertEqual(len(cmd.encode()), 2048)
        with self.assertRaises(TimeoutError):
            cli.exec(cmd)                                          # ни ответа, ни исполнения
        self.assertEqual((srv.too_long_cmds, srv.world.count()), ([cmd], 0))
        self.assertTrue(cli.connected)
        ok = w3net.build_call("mcw_batch", 1000000002, "set:1:2:3:0|" + "x" * (2047 - base - 12))
        self.assertEqual(len(ok.encode()), 2047)
        self.assertEqual(cli.exec(ok).ints("batch")[:3], [1, 0, 1])  # 2047 байт исполнено: set + одна ошибка
        self.assertEqual(srv.world.count(), 1)
        self.assertTrue(cli.call("mcw_ping").ok)                   # соединение и порядок целы

    def test_fake_drops_reply_over_1999_bytes(self):
        d = tmpdir(self)
        path = os.path.join(d, "scriptslog.txt")
        open(path, "w").close()
        srv, cli = self.make(cli_kw={"timeout": 0.5}, scriptslog_path=path)
        n = 1999 - len("[MCW#1000000001] echo 1951 \n[MCW#1000000001] end")
        self.assertEqual(n, 1951)
        with self.assertLogs("w3net", logging.WARNING):
            r = cli.exec(w3net.build_call("mcw_echo", 1000000001, "x" * n))
        self.assertEqual((r.ok, len(r.text.encode()), r.near_limit, r.truncated), (True, 1999, True, False))
        with self.assertRaises(TimeoutError):                      # 2000 байт: ответ теряется целиком
            cli.exec(w3net.build_call("mcw_echo", 1000000002, "x" * (n + 1)))
        self.assertEqual(srv.replies_lost, 1)
        with open(path, encoding="utf-8") as f:                     # но функция отработала: строки в scriptslog
            self.assertIn("[MCW#1000000002] end", f.read())
        self.assertTrue(cli.call("mcw_ping").ok)

    def test_fake_truncates_log_line_over_1999_chars(self):
        srv, cli = self.make()
        n = 2047 - len('mcw_echo(1000000001, "")')                # самая длинная echo, что влезает в команду
        with self.assertLogs("w3net", logging.WARNING):
            r = cli.exec(w3net.build_call("mcw_echo", 1000000001, "x" * n))
        self.assertEqual((r.ok, r.truncated, r.body, r.text.split("\n")[0]), (True, True, [], w3net.EXEC_TRUNCATED))
        self.assertEqual((srv.lines_truncated, srv.replies_lost, cli.stats["truncated"]), (1, 0, 1))
        r = cli.exec(w3net.build_call("mcw_echo", 1000000002, "x" * 1900))
        self.assertEqual((r.ok, r.truncated, r.tokens("echo")[0]), (True, False, "1900"))

    def test_fake_legacy_limits_opt_out(self):
        srv, cli = self.make(cli_kw={"max_cmd": w3net.MAX_STR}, legacy_limits=True)
        r = cli.call("mcw_echo", "x" * 4000)                       # команда и ответ ~4 КБ: только i16 протокола
        self.assertEqual((r.ok, r.truncated, r.tokens("echo")[0]), (True, False, "4000"))
        self.assertEqual((srv.too_long_cmds, srv.replies_lost, srv.lines_truncated), ([], 0, 0))

    def test_fake_parses_string_args_like_500c(self):
        # charset-замер 5.00c: кавычка внутри строки и "" - 'Warn: Failed' всей команде; '\' как есть; StrLen - байты
        srv, cli = self.make()
        for cmd in ('mcw_echo(11, "a"b")', "mcw_echo(12, \"a'b\")", 'mcw_echo(13, "")', 'mcw_cube(14, "")',
                    'mcw_stress(15, 10, 150, "")', "mcw_alias(16, '', 0.0)"):
            r = cli.exec(cmd)
            self.assertEqual((r.ok, r.failed), (False, True), cmd)
        self.assertEqual(srv.world.count(), 0)                                  # ничего не исполнилось
        self.assertEqual(cli.exec('mcw_echo(17, "a\\\\b")').body, ["echo 4 a\\\\b"])   # оба слэша доходят
        self.assertEqual(cli.call("mcw_echo", "aжb").body, ["echo 4 aжb"])   # StrLen считает байты UTF-8
        self.assertEqual(cli.call("mcw_echo", " ").tokens("echo"), ["1"])             # пробел проходит
        self.assertEqual(cli.call("mcw_cube", "-").kv("spawned"), {"spawned": 1, "comps": 1})   # "-" - cont_crate
        self.assertEqual(cli.call("mcw_stress", 3, 100, "-").ints("stress")[0], 3)
        self.assertEqual({e.template for e in srv.world.ents.values()}, {fake_w3.CRATE})


class Contract(unittest.TestCase):
    """Фейк против mcw_spike.ws буква в букву там, где на нём будет отлаживаться мост."""

    def setUp(self):
        self.srv = fake_w3.FakeWitcher(port=0).start()
        self.addCleanup(self.srv.stop)
        self.cli = W3Client(port=self.srv.port, timeout=3.0).connect()
        self.addCleanup(self.cli.close)

    def batch(self, s):
        return self.cli.call("mcw_batch", s).ints("batch")[:3]

    def count(self):
        return self.cli.call("mcw_count").ints("count")[0]

    def test_batch_clears_before_sets(self):
        self.assertEqual(self.batch("set:500:0:0:0|clear:500:0:0"), [1, 0, 1])   # clear промахнулся раньше set
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.batch("clear:500:0:0|set:500:0:0:1"), [1, 1, 0])   # замена клетки одной пачкой
        self.assertEqual(self.count(), 1)

    def test_batch_set_does_not_check_occupancy_and_clear_hits_all(self):
        self.assertEqual(self.batch("set:0:0:0:0|set:0:0:0:0"), [2, 0, 0])
        self.assertEqual(self.count(), 2)
        self.assertEqual(self.batch("clear:0:0:0|clear:0:0:0"), [0, 2, 1])      # обе сущности - первому ключу
        self.assertEqual(self.count(), 0)

    def test_batch_clear_miss_parse_errors_and_empty_items(self):
        self.assertEqual(self.batch("clear:9:9:9"), [0, 0, 1])
        self.assertEqual(self.batch("||set:1:2:3:0||"), [1, 0, 0])               # пустые элементы пропускаются
        self.assertEqual(self.batch(" set:1:2:3:0|set:1:2:x:0|set:1:2:3|clear:1:2|set:1:2:3:13|set:1:2:3:-1"),
                         [0, 0, 6])                                     # t вне 0..12 - ошибка

    def test_batch_and_ents_block_templates_0_to_12(self):
        # MCW_TemplateByIndex: 0 cont_crate, 1 barrel, 2..12 - блоки modMCWBlocks (mcw\blocks\<имя>.w2ent)
        ops = "|".join(f"set:{t * 100}:0:0:{t}" for t in range(13))
        self.assertEqual(self.batch(ops), [13, 0, 0])
        tpl = {e.x // 100: e.template for e in self.srv.world.ents.values()}
        self.assertEqual((tpl[0], tpl[1], tpl[4], tpl[12]), (fake_w3.CRATE, fake_w3.BARREL,
                                                             "mcw\\blocks\\stone.w2ent", "mcw\\blocks\\_unknown.w2ent"))
        self.assertEqual(self.batch("|".join(f"clear:{t * 100}:0:0" for t in range(13))), [0, 13, 0])
        r = self.cli.call("mcw_ents", "spawn:0:0:0:0:0:9|spawn:1:0:0:0:0:13").ints("ents")
        self.assertEqual(r[:4], [1, 0, 0, 1])
        self.assertEqual(self.srv.mobs()[0].template, "mcw\\blocks\\crafting_table.w2ent")

    def test_batch_clear_finds_any_mcw_block_by_position(self):
        self.cli.call("mcw_cube", "-")
        e = next(iter(self.srv.world.ents.values()))
        self.assertEqual(self.batch(f"clear:{e.x}:{e.y}:{e.z}"), [0, 1, 0])
        self.assertEqual(self.count(), 0)

    def test_no_player_spawns_nothing(self):
        self.srv.world.has_player = False                                   # главное меню
        self.assertEqual(self.cli.call("mcw_cube", "-").kv("spawned"), {"spawned": 0, "comps": 0})
        self.assertEqual(self.cli.call("mcw_alias", Name("barrel"), 0.5).kv("spawned"),
                         {"spawned": 0, "comps": 0, "scaled": 0})
        self.assertEqual(self.cli.call("mcw_stress", 5, 100, "-").ints("stress")[0], 0)
        self.assertEqual(self.cli.call("mcw_boom").ints("boom"), [0])
        self.assertEqual(self.batch("set:1:2:3:0"), [1, 0, 0])               # mcw_batch игрока не требует
        self.assertEqual(self.count(), 1)

    def test_spawn_heights_and_tags(self):
        w = self.srv.world
        self.cli.call("mcw_cube", "-")
        self.cli.call("mcw_alias", Name("barrel"), 0.0)
        self.cli.call("mcw_stress", 1, 0, "-")                              # spacing <= 0 -> 100 см
        cube, alias, st = list(w.ents.values())
        self.assertEqual((cube.z, alias.z, st.z), (w.player[2] + 50, w.player[2], w.player[2] + 50))
        self.assertEqual((cube.tags, st.tags), ({"MCW_BLOCK", "MCW_TEST"}, {"MCW_BLOCK"}))

    def test_countblocks_excludes_probes(self):
        self.cli.call("mcw_cube", "-")                                       # MCW_BLOCK + MCW_TEST
        self.cli.call("mcw_boom")                                           # тоже проба
        self.batch("set:0:0:0:0|set:100:0:0:1")                             # блоки моста
        self.cli.call("mcw_stress", 3, 100, "-")                            # stress — без MCW_TEST
        self.assertEqual(self.count(), 7)                                   # mcw_count не менялся
        self.assertEqual(self.cli.call("mcw_countblocks").ints("countblocks"), [5, 2])

    def test_look_normal_line(self):
        r = self.cli.call("mcw_look")                                       # пусто: луч в землю
        self.assertEqual(r.ints("look")[0], 1)
        self.assertEqual(r.ints("normal"), [0, 0, 10000])
        self.cli.call("mcw_cube", "-")                                       # куб в 2 м перед Геральтом
        r = self.cli.call("mcw_look")
        self.assertEqual((r.ints("look")[0], r.ints("look")[4]), (1, 1))
        self.assertEqual(r.ints("normal"), [10000, 0, 0])                   # курс 90: камера восточнее, грань +x
        self.assertEqual([b.split()[0] for b in r.body], ["look", "normal"])
        self.srv.world.has_player = False
        self.srv.world.ents.clear()
        self.srv.world.pitch_deg = -10.0                                    # смотрит вверх: ни земли, ни кубов
        r = self.cli.call("mcw_look")
        self.assertEqual(r.body, ["look 0 0 0 0 0"])                        # без normal

    def test_state_resume_roundtest_quit(self):
        w = self.srv.world
        r = self.cli.call("mcw_state")
        t = r.tokens("state")
        st = dict(zip(t[::2], t[1::2]))
        self.assertEqual(list(st), ["active", "paused", "apause", "stopped", "menu", "gtime", "ems"])
        self.assertEqual([st[k] for k in ("active", "paused", "apause", "stopped", "menu")], ["1", "0", "0", "0", "0"])
        self.assertTrue(st["gtime"].isdigit() and st["ems"].isdigit())
        self.assertEqual((r.tokens("reasons"), r.body[1]), ([], "reasons"))
        # потеря фокуса: движок открыл меню и поставил паузу
        w.active, w.menu, w.paused, w.pause_reasons = False, True, True, ["menus", "IngameMenu"]
        r = self.cli.call("mcw_state")
        t = r.tokens("state")
        self.assertEqual((t[1], t[3], t[9]), ("0", "1", "1"))                       # active 0, paused 1, menu 1
        self.assertEqual(r.tokens("reasons"), ["'IngameMenu'", "'menus'"])        # в порядке списка ws
        self.assertEqual(self.cli.call("mcw_resume").tokens("resume"), ["1", "paused", "0"])
        self.assertEqual(self.cli.call("mcw_resume").tokens("resume"), ["0", "paused", "0"])
        # RoundF 5.00c режет к нулю; 'trip' - сколько см не переживают float32 (замер в игре: 2286 и 0)
        r = self.cli.call("mcw_roundtest", timeout=10)
        self.assertEqual(r.ints("round"), [-2, -1, 0, 0, 1, 2, -1, -1, 1, 1])
        self.assertEqual(r.ints("trip"), [2286, 0])
        r = self.cli.call("mcw_quit")
        self.assertEqual((r.ok, r.body, self.srv.quit_requested), (True, ["quit"], True))

    def test_boomat(self):
        w = self.srv.world
        x, y, z = w.player
        self.assertEqual(self.cli.call("mcw_boomat", x + 1000, y, z + 80).ints("boom"), [1])     # в 0.8 м над землёй
        self.assertEqual(self.cli.call("mcw_boomat", x - 1000, y, z + 900).ints("boom"), [1])    # опоры в окне нет
        a, b = list(w.ents.values())
        self.assertEqual((a.x, a.y, a.z, b.z), (x + 1000, y, z, z + 900))
        self.assertTrue(a.on_fire and b.on_fire)
        self.assertEqual(a.tags, {"MCW_BLOCK", "MCW_TEST"})
        self.assertEqual(self.cli.call("mcw_countblocks").ints("countblocks"), [0, 2])
        w.has_player = False
        self.assertEqual(self.cli.call("mcw_boomat", x, y, z).ints("boom"), [0])

    def test_legacy_mod_without_bridge_extensions(self):
        srv = fake_w3.FakeWitcher(port=0, legacy=True).start()
        self.addCleanup(srv.stop)
        cli = W3Client(port=srv.port, timeout=3.0).connect()
        self.addCleanup(cli.close)
        self.assertTrue(cli.call("mcw_countblocks").failed)                # 'Warn: Failed to process command'
        self.assertTrue(cli.call("mcw_boomat", 0, 0, 0).failed)
        self.assertEqual(cli.call("mcw_count").ints("count"), [0])
        cli.call("mcw_cube", "-")
        r = cli.call("mcw_look")
        self.assertEqual((r.ints("look")[4], r.ints("normal")), (1, None))
        self.assertEqual(cli.call("mcw_boom").ints("boom"), [1])

    def test_contract_signatures_match_ws_exec_functions(self):
        import re
        tmap = {"int": "i", "float": "f", "name": "n", "string": "s"}
        found = {}
        here = os.path.dirname(os.path.abspath(__file__))
        for p in (os.path.join(here, "mod", "modMCW", "content", "scripts", "local", "mcw_spike.ws"),
                  os.path.join(here, "mod", "modMCW", "content", "scripts", "local", "mcw_steve.ws"),
                  os.path.join(here, "mod", "modMCW", "content", "scripts", "local", "mcw_collide.ws"),
                  os.path.join(here, "mod", "modMCW", "content", "scripts", "local", "mcw_models.ws"),
                  os.path.join(here, "mod", "modMCWProbe", "content", "scripts", "local", "mcw_probe.ws")):
            if not os.path.exists(p):
                self.skipTest(f"нет {p}")
            with open(p, encoding="utf-8") as f:
                src = f.read()
            for m in re.finditer(r"exec function (\w+)\s*\(([^)]*)\)", src):
                args = [a.split(":")[1].strip() for a in m.group(2).split(",") if a.strip()]
                found[m.group(1)] = "".join(tmap[a] for a in args)
        self.assertEqual(found, fake_w3.CONTRACT)

    # -- mcw_ents: мобы моста по слотам, отдельно от блоков
    def ents(self, s):
        return self.cli.call("mcw_ents", s).ints("ents")[:4]

    def test_ents_spawn_move_kill_in_order_and_apart_from_blocks(self):
        self.batch("set:0:0:0:0")
        self.assertEqual(self.ents("spawn:3:100:200:300:9000:0|spawn:5:0:0:0:0:1"), [2, 0, 0, 0])
        mobs = self.srv.mobs()
        self.assertEqual(sorted(mobs), [3, 5])
        self.assertEqual((mobs[3].x, mobs[3].y, mobs[3].z, mobs[3].yaw), (100, 200, 300, 9000))
        self.assertEqual(mobs[3].tags, {"MCW_ENT", "MCW_E3"})
        self.assertEqual(self.count(), 1)                                     # mcw_count — только MCW_BLOCK
        self.assertEqual(self.cli.call("mcw_countblocks").ints("countblocks"), [1, 0])
        self.assertEqual(self.ents("move:3:150:200:300:-4500"), [0, 1, 0, 0])
        self.assertEqual((mobs[3].x, mobs[3].yaw, mobs[3].moves), (150, -4500, 1))
        # по порядку: move до kill того же слота проходит, после — ошибка
        self.assertEqual(self.ents("move:5:1:1:1:0|kill:5|move:5:2:2:2:0"), [0, 1, 1, 1])
        self.assertEqual(sorted(self.srv.mobs()), [3])
        self.assertEqual(self.cli.call("mcw_clearall").ints("cleared"), [1])  # блоки — да, мобы — нет
        self.assertEqual(sorted(self.srv.mobs()), [3])
        self.assertEqual(self.batch("clear:150:200:300"), [0, 0, 1])           # clear в mcw_batch моба не видит

    def test_ents_spawn_is_idempotent_and_wipe(self):
        self.assertEqual(self.ents("spawn:7:0:0:0:0:0|spawn:7:10:0:0:0:0"), [2, 0, 0, 0])
        self.assertEqual(len([e for e in self.srv.world.ents.values() if "MCW_E7" in e.tags]), 1)   # без дублей
        self.ents("spawn:8:0:0:0:0:0")
        self.batch("set:0:0:0:0")
        self.assertEqual(self.ents("wipe"), [0, 0, 2, 0])
        self.assertEqual(self.srv.mobs(), {})
        self.assertEqual(self.count(), 1)

    def test_ents_errors(self):
        self.assertEqual(self.ents("kill:4|move:4:0:0:0:0|spawn:64:0:0:0:0:0|spawn:1:0:0:0:0:13|spawn:1:0:x:0:0:0"
                                   "|move:1:0:0:0|wipe:1|junk| spawn:1:0:0:0:0:0||"), [0, 0, 0, 9])
        self.assertEqual(self.srv.mobs(), {})
        # мобы моста не блоки и для луча: isBlock 0
        x, y, z = self.srv.world.ahead(200)
        self.ents(f"spawn:0:{x}:{y}:{z + 50}:0:0")
        self.assertEqual(self.cli.call("mcw_look").ints("look")[4], 0)

    def test_legacy_mod_has_no_ents(self):
        srv = fake_w3.FakeWitcher(port=0, legacy=True).start()
        self.addCleanup(srv.stop)
        cli = W3Client(port=srv.port, timeout=3.0).connect()
        self.addCleanup(cli.close)
        self.assertTrue(cli.call("mcw_ents", "wipe").failed)


class RawGame:
    """Сервер-сценарий: script(sock, read_exec) в своём потоке шлёт любые байты, рвёт соединение, молчит."""

    def __init__(self, tc: unittest.TestCase, script):
        self.l = socket.socket()
        self.l.bind(("127.0.0.1", 0))
        self.l.listen(1)
        self.port = self.l.getsockname()[1]
        self.script = script
        tc.addCleanup(self.l.close)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            s, _ = self.l.accept()
        except OSError:
            return
        stream = PacketStream()

        def read_exec(timeout=5.0):
            """Ждать следующий exec-запрос; вернуть текст команды или None."""
            s.settimeout(0.05)
            end = time.time() + timeout
            while time.time() < end:
                try:
                    data = s.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    return None
                for items in stream.feed(data):
                    if len(items) >= 4 and items[0].text == "Remote":
                        return items[3].text
            return None

        try:
            self.script(s, read_exec)
        except OSError:
            pass


def xpkt(text):
    return encode_packet(exec_pkt(text))


class Streaming(unittest.TestCase):
    """Статус 'Spam' раньше строк Log() (rw3d PR #12: «streams its script log output»)."""

    def test_raw_status_then_lines_in_separate_packets(self):
        def script(s, read_exec):
            for k in range(3):
                seq = w3net.cmd_seq(read_exec() or "")
                s.sendall(xpkt(w3net.EXEC_OK_SILENT))
                s.sendall(xpkt(f"[MCW#{seq}] pong {k}"))
                s.sendall(xpkt(f"[MCW#{seq}] end"))
            time.sleep(0.5)

        g = RawGame(self, script)
        cli = W3Client(port=g.port, timeout=2.0).connect()
        self.addCleanup(cli.close)
        res = [cli.exec(f"mcw_ping({i})") for i in (1, 2, 3)]
        self.assertEqual([(r.ok, r.silent) for r in res], [(True, False)] * 3)
        self.assertEqual(cli.stats["late"], 0)

    def test_fake_leading_status_modes(self):
        for kw in ({}, {"per_line": True}, {"per_line": True, "frame_time": 0.002}):
            srv = fake_w3.FakeWitcher(port=0, leading_status=True, **kw).start()
            self.addCleanup(srv.stop)
            cli = W3Client(port=srv.port, timeout=3.0).connect()
            self.addCleanup(cli.close)
            self.assertEqual(len(cli.call("mcw_poll").body), 4, kw)          # первая команда - до маркеров
            for _ in range(5):
                self.assertTrue(cli.call("mcw_ping").ok, kw)
            futs = [cli.call_async("mcw_poll") for _ in range(30)]            # конвейер
            self.assertTrue(all(len(f.result(5).body) == 4 for f in futs), kw)
            self.assertEqual((cli.stats["silent"], cli.stats["lost"], cli.stats["unsolicited"]), (0, 0, 0), kw)

    def test_fake_leading_status_pipelined_from_cold_start(self):
        srv = fake_w3.FakeWitcher(port=0, leading_status=True, per_line=True, frame_time=0.01).start()
        self.addCleanup(srv.stop)
        cli = W3Client(port=srv.port, timeout=3.0).connect()
        self.addCleanup(cli.close)
        futs = [cli.call_async("mcw_poll") for _ in range(20)]               # маркеров ещё не видели
        self.assertTrue(all(f.result(5).ok for f in futs))

    def test_fake_log_not_in_reply_still_silent_after_grace(self):
        srv = fake_w3.FakeWitcher(port=0, log_in_reply=False).start()
        self.addCleanup(srv.stop)
        cli = W3Client(port=srv.port, timeout=3.0, status_grace=0.1).connect()
        self.addCleanup(cli.close)
        t0 = time.perf_counter()
        r = cli.call("mcw_ping")
        self.assertEqual((r.ok, r.silent, r.complete), (False, True, False))
        self.assertLess(time.perf_counter() - t0, 1.0)

    def test_exec_shaped_netlog_does_not_break_seq_after_fifo(self):
        srv = fake_w3.FakeWitcher(port=0, unsolicited_ms=3, unsolicited_kind="exec", frame_time=0.01).start()
        self.addCleanup(srv.stop)
        cli = W3Client(port=srv.port, timeout=2.0).connect()
        self.addCleanup(cli.close)
        for _ in range(30):
            cli.exec("spawnbarrel()")                     # FIFO: часть получит чужой пакет - это оговорено
        ok = sum(cli.call("mcw_poll").ok for _ in range(30))
        self.assertEqual(ok, 30)


class Robustness(unittest.TestCase):
    def test_exec_after_disconnect_is_connection_error_fast(self):
        def script(s, read_exec):
            read_exec(timeout=0.3)
            s.close()

        g = RawGame(self, script)
        cli = W3Client(port=g.port, timeout=1.0).connect()
        self.addCleanup(cli.close)
        lost = threading.Event()
        cli.on_disconnect(lambda e: lost.set())
        self.assertTrue(lost.wait(3))
        t0 = time.time()
        for call in (lambda: cli.exec("mcw_ping(2)"), lambda: cli.exec_async("spawnbarrel()"),
                     cli.root_path, lambda: cli.reload_scripts(timeout=1), lambda: cli.send(w3net.msg_bind("x"))):
            with self.assertRaises(ConnectionError):
                call()
        self.assertLess(time.time() - t0, 0.5)
        self.assertEqual(cli.in_flight, 0)

    def test_disconnect_mid_packet_fails_pending_fast(self):
        def script(s, read_exec):
            read_exec()
            pkt = xpkt("[MCW#1] pong 1\n[MCW#1] end")
            s.sendall(pkt[:len(pkt) // 2])
            time.sleep(0.1)
            s.close()

        g = RawGame(self, script)
        cli = W3Client(port=g.port, timeout=5.0).connect()
        self.addCleanup(cli.close)
        t0 = time.time()
        f = cli.exec_async("mcw_ping(1)")
        with self.assertRaises(ConnectionError):
            f.result(3)
        self.assertLess(time.time() - t0, 1.0)

    def test_corrupt_length_is_salvaged(self):
        def script(s, read_exec):
            read_exec()
            s.sendall(b"\xDE\xAD\xFF\xFF" + b"x" * 100)                 # длина 0xFFFF, тела нет
            s.sendall(xpkt("[MCW#1] pong 1\n[MCW#1] end"))
            seq = w3net.cmd_seq(read_exec() or "")
            s.sendall(xpkt(f"[MCW#{seq}] pong 2\n[MCW#{seq}] end"))
            time.sleep(0.5)

        g = RawGame(self, script)
        cli = W3Client(port=g.port, timeout=1.5).connect()
        self.addCleanup(cli.close)
        with self.assertLogs("w3net", logging.WARNING):
            self.assertTrue(cli.exec("mcw_ping(1)").ok)
        self.assertTrue(cli.exec("mcw_ping(2)").ok)
        self.assertEqual((cli.stream.salvaged, cli.stream.pending), (1, 0))

    def test_salvage_does_not_cut_slow_valid_packet(self):
        s = PacketStream()
        p = xpkt("[MCW#1] echo " + "y" * 200 + "\n[MCW#1] end")
        self.assertEqual(s.feed(p[:50]), [])
        self.assertEqual(s.salvage(), [])                                    # внутри нет целого пакета
        self.assertEqual(len(s.feed(p[50:])), 1)
        inner = xpkt("[MCW#2] end")
        s.feed(b"\xDE\xAD\x01\x00" + inner)                                  # битая длина 256, внутри - целый
        self.assertEqual(len(s.salvage()), 1)

    def test_bind_delay_and_connect_error_wording(self):
        srv = fake_w3.FakeWitcher(port=0).start()
        self.addCleanup(srv.stop)
        t0 = time.perf_counter()
        cli = W3Client(port=srv.port, bind_delay=0.2).connect()
        self.addCleanup(cli.close)
        self.assertGreaterEqual(time.perf_counter() - t0, 0.2)
        self.assertTrue(cli.call("mcw_ping").ok)
        sk = socket.socket()
        sk.bind(("127.0.0.1", 0))
        closed = sk.getsockname()[1]
        sk.close()
        with self.assertRaises(ConnectionError) as cm:
            W3Client(port=closed).connect()                                  # Windows: отказ на localhost ~2 с
        self.assertIn("не слушает", str(cm.exception))
        self.assertIn("отказ", str(cm.exception))                            # а не «timed out»

    def test_reload_nonstandard_error_reaches_result(self):
        srv = fake_w3.FakeWitcher(port=0).start()
        self.addCleanup(srv.stop)
        sc = U8(w3net.NS_SCRIPT_COMPILER)
        srv._other = lambda c, items, t: ([encode_packet([sc, U8("started")]),
                                           encode_packet([sc, U8("error"), U32(5), U8("local\\x.ws")]),
                                           encode_packet([sc, U8("finished"), I8(1)])]
                                          if t and t[0] == "scripts" else [])
        cli = W3Client(port=srv.port).connect()
        self.addCleanup(cli.close)
        res = cli.reload_scripts(timeout=3)
        self.assertFalse(res.success)
        self.assertEqual((len(res.errors), res.errors[0].line, res.errors[0].message), (1, 5, "local\\x.ws"))


# =============================================================================== scriptslog.txt

class ScriptsLog(unittest.TestCase):
    def wait_for(self, got, n, t=5.0):
        wait_until(lambda: len(got) >= n, t)
        return list(got)

    def test_tail_partial_truncate_replace(self):
        path = os.path.join(tmpdir(self), "scriptslog.txt")
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("old line that must be skipped\r\n")
        got = []
        t = w3net.ScriptsLogTail(got.append, path=path, poll_interval=0.005)
        t.start()
        self.addCleanup(t.stop)
        self.assertTrue(t.ready.wait(2))
        with open(path, "a", encoding="utf-8", newline="") as f:
            f.write("[MCW] a\r\n[MCW] par")
            f.flush()
            time.sleep(0.05)
            f.write("tial\r\n")
        self.assertEqual(self.wait_for(got, 2)[-2:], ["[MCW] a", "[MCW] partial"])
        with open(path, "w", encoding="utf-8", newline="") as f:      # перезапуск игры: усечён
            f.write("new1\r\n")
        self.assertEqual(self.wait_for(got, 3)[-1], "new1")
        os.remove(path)                                                # удалён во время хвоста: не заблокирован
        time.sleep(0.05)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("after recreate line\r\n")
        self.assertIn("after recreate line", self.wait_for(got, 4))

    def test_rewrite_longer_with_new_head_between_polls(self):
        path = os.path.join(tmpdir(self), "scriptslog.txt")
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("session A line 1\r\n")
        got = []
        t = w3net.ScriptsLogTail(got.append, path=path, poll_interval=0.2, from_end=False)
        t.start()
        self.addCleanup(t.stop)
        self.assertEqual(self.wait_for(got, 1), ["session A line 1"])
        with open(path, "r+", encoding="utf-8", newline="") as f:     # тот же файл, новое начало, длиннее старого
            f.truncate(0)
            f.write("session B line 1 is much longer\r\nsession B line 2\r\n")
        self.assertEqual(self.wait_for(got, 3)[1:], ["session B line 1 is much longer", "session B line 2"])

    def test_file_appearing_later_is_read_from_start(self):
        path = os.path.join(tmpdir(self), "scriptslog.txt")
        got = []
        t = w3net.ScriptsLogTail(got.append, path=path, poll_interval=0.005)
        t.start()
        self.addCleanup(t.stop)
        self.assertTrue(t.ready.wait(2))
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("first\r\nsecond\r\n")
        self.assertEqual(self.wait_for(got, 2), ["first", "second"])

    def test_utf16_bom_file(self):
        path = os.path.join(tmpdir(self), "scriptslog.txt")
        with open(path, "wb") as f:
            f.write("\ufeff".encode("utf-16-le"))
        got = []
        t = w3net.ScriptsLogTail(got.append, path=path, poll_interval=0.005, from_end=False)
        t.start()
        self.addCleanup(t.stop)
        with open(path, "ab") as f:
            f.write(("[MCW] " + POLISH + "\r\n").encode("utf-16-le"))
        self.assertEqual(self.wait_for(got, 1), ["[MCW] " + POLISH])
        self.assertEqual(t.encoding, "utf-16-le")

    def test_utf8_bom_and_bad_bytes(self):
        path = os.path.join(tmpdir(self), "scriptslog.txt")
        with open(path, "wb") as f:
            f.write(b"\xef\xbb\xbf" + "ж\r\n".encode() + b"bad \xff byte\r\n")
        got = []
        t = w3net.ScriptsLogTail(got.append, path=path, poll_interval=0.005, from_end=False)
        t.start()
        self.addCleanup(t.stop)
        self.assertEqual(self.wait_for(got, 2), ["ж", "bad \ufffd byte"])

    def test_fake_server_writes_scriptslog(self):
        path = os.path.join(tmpdir(self), "scriptslog.txt")
        open(path, "w").close()
        srv = fake_w3.FakeWitcher(port=0, scriptslog_path=path).start()
        self.addCleanup(srv.stop)
        got = []
        with W3Client(port=srv.port) as cli:
            tail = cli.listen_scripts_log(got.append, path=path, poll_interval=0.005)
            self.addCleanup(tail.stop)
            cli.exec("mcw_logtest(42)")
            self.assertEqual(self.wait_for(got, 3), ["[MCW#42] via Log", "[MCW] [MCW#42] via LogChannel", "[MCW#42] end"])
            self.assertEqual(cli.exec("logstats()").text, w3net.EXEC_OK_SILENT)  # LogChannel('Stats') - только в файл
            self.assertEqual(sum(ln.startswith("[Stats]") for ln in self.wait_for(got, 303)), 300)

    def test_default_path_shape(self):
        p = w3net.default_scriptslog_path()
        self.assertTrue(p.endswith(os.path.join("The Witcher 3", "scriptslog.txt")))


# ======================================================================== w3cmd и bench против фейка

class Cli(unittest.TestCase):
    def run_cmd(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = w3cmd.main(list(args))
        return rc, buf.getvalue()

    def test_w3cmd_exec_ok_fail(self):
        srv = fake_w3.FakeWitcher(port=0).start()
        self.addCleanup(srv.stop)
        p = str(srv.port)
        rc, out = self.run_cmd("--port", p, "mcw_ping(1)")
        self.assertEqual(rc, 0)
        self.assertIn("[MCW#1] end", out)
        self.assertTrue(out.strip().splitlines()[-1].startswith("ok"))
        rc, out = self.run_cmd("--port", p, "nope()")
        self.assertEqual(rc, 1)
        self.assertIn("fail", out)
        rc, out = self.run_cmd("--port", p, "mcw_echo(#, \"hi there\")", "mcw_count(#)")
        self.assertEqual(rc, 0)
        self.assertIn("echo 8 hi there", out)
        self.assertEqual(out.count("\nok "), 2)
        rc, out = self.run_cmd("--port", p, "mcw_nope()")                   # первая проверка протокола
        self.assertEqual(rc, 1)
        self.assertIn(w3net.EXEC_FAIL, out)
        rc, out = self.run_cmd("--port", p, "--fifo", "--bind-delay", "0.1", "spawnbarrel()")
        self.assertEqual(rc, 0)
        self.assertIn(w3net.EXEC_OK_SILENT, out)

    def test_w3cmd_one_shot_with_status_before_lines(self):
        # w3cmd - новый процесс и новый клиент на каждый вызов: маркеров он ещё не видел
        for kw in ({"leading_status": True}, {"leading_status": True, "per_line": True}):
            srv = fake_w3.FakeWitcher(port=0, **kw).start()
            self.addCleanup(srv.stop)
            rc, out = self.run_cmd("--port", str(srv.port), "mcw_ping(#)", "mcw_poll(#)")
            self.assertEqual((rc, out.count("\nok ")), (0, 2), out)
        srv = fake_w3.FakeWitcher(port=0, log_in_reply=False).start()
        self.addCleanup(srv.stop)
        rc, out = self.run_cmd("--port", str(srv.port), "mcw_ping(#)")
        self.assertEqual(rc, 1)
        self.assertIn("Log() не дошёл", out)

    def test_w3cmd_reload_prints_nonstandard_compiler_packets(self):
        srv = fake_w3.FakeWitcher(port=0).start()
        self.addCleanup(srv.stop)
        sc = U8(w3net.NS_SCRIPT_COMPILER)
        srv._other = lambda c, items, t: ([encode_packet([sc, U8("started")]),
                                           encode_packet([sc, U8("error"), U8("local\\x.ws(7): boom")]),
                                           encode_packet([sc, U8("finished")])]
                                          if t and t[0] == "scripts" else [])
        rc, out = self.run_cmd("--port", str(srv.port), "--reload", "--timeout", "3")
        self.assertEqual(rc, 1)
        self.assertIn("local\\x.ws(7): boom", out)
        self.assertIn("finished без кода", out)

    def test_w3cmd_reload_info_tail_and_no_game(self):
        srv = fake_w3.FakeWitcher(port=0).start()
        self.addCleanup(srv.stop)
        rc, out = self.run_cmd("--port", str(srv.port), "--reload")
        self.assertEqual(rc, 0)
        self.assertIn("finished: ok", out)
        bad = fake_w3.FakeWitcher(port=0, reload_error=True).start()
        self.addCleanup(bad.stop)
        rc, out = self.run_cmd("--port", str(bad.port), "--reload")
        self.assertEqual(rc, 1)
        self.assertIn("error: local\\mcw_spike.ws:42", out)
        rc, out = self.run_cmd("--port", str(srv.port), "--info")
        self.assertEqual(rc, 0)
        self.assertIn("modMCW", out)
        path = os.path.join(tmpdir(self), "scriptslog.txt")
        open(path, "w").close()
        srv.scriptslog_path = path
        rc, out = self.run_cmd("--port", str(srv.port), "--tail", "--log", path, "--seconds", "0.5", "mcw_logtest(#)")
        self.assertEqual(rc, 0)
        self.assertIn("[log] [MCW] [MCW#", out)
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        closed = s.getsockname()[1]
        s.close()
        rc, _ = self.run_cmd("--port", str(closed), "mcw_ping(1)")
        self.assertEqual(rc, 2)

    def test_bench_against_fake_writes_json(self):
        out_dir = tmpdir(self)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bench_w3.main(["--fake", "--fake-frame-ms", "2", "--out", out_dir, "--n", "10", "--reps", "2",
                                "--depths", "1,4", "--bursts", "1,4", "--sizes", "256,1900,1960,4096",
                                "--ops", "1,100", "--levels", "10,100", "--hold", "0.2", "all"])
        self.assertEqual(rc, 0, buf.getvalue()[-2000:])
        files = [f for f in os.listdir(out_dir) if f.endswith("_fake.json")]
        self.assertEqual(len(files), 1)
        with open(os.path.join(out_dir, files[0]), encoding="utf-8") as f:
            data = json.load(f)
        names = [r["test"] for r in data["results"]]
        for t in ("rtt", "rate_depth_4", "frame_burst_4", "poll", "echo_1900b", "batch_100", "stress_100", "probe",
                  "charset", "loglat"):
            self.assertIn(t, names)
        res = {r["test"]: r for r in data["results"]}
        self.assertEqual((res["echo_1900b"]["bad"], res["echo_1900b"]["reply_bytes"]), (0, 1948))
        self.assertIn("ответ 2008 байт", res["echo_1960b"]["skipped"])         # команда влезает, ответ - нет
        self.assertIn("2047", res["echo_4096b"]["skipped"])                     # команда не влезает
        self.assertEqual((res["batch_100"]["cmds"], res["batch_100"]["calls"]), (2, 8))   # 100 op ~2,8 КБ - две команды
        self.assertLessEqual(res["batch_100"]["packet_bytes"], w3net.EXEC_MAX_CMD + 36)
        self.assertEqual(data["client"]["stats"].get("expired", 0), 0)
        # charset: кавычки - ожидаемый 'Warn: Failed' (не bad), '\' и кириллица проходят; алфавит напечатан
        cs = res["charset"]
        self.assertEqual((cs["bad"], cs["expected_fail"], cs["mangled"], cs["unexpected_ok"]), (0, "\"'", "", ""))
        self.assertTrue({"\\", "ж", " ", "|", ":"} <= set(cs["safe_alphabet"]))
        self.assertEqual(len(cs["safe_alphabet"]), cs["calls"] - 2)
        self.assertIn("безопасный алфавит", buf.getvalue())
        self.assertEqual(names[-2:], ["stress_10", "stress_100"])               # спавн - в самом конце
        self.assertEqual(res["stress_100"]["spawned"], 100)
        for r in data["results"]:                                             # фейк исправен - плохих нет
            self.assertEqual(r.get("bad", 0), 0, r)
            self.assertNotIn("error", r)
        self.assertEqual(res["rate_depth_4"]["calls"], 10)
        self.assertTrue(res["probe"]["ok"])
        ll = res["loglat"]
        self.assertEqual((ll["missing_end"], ll["missing_chan"], ll["log_end"]["n"], ll["logchannel"]["n"]),
                         (0, 0, 10, 10))
        self.assertEqual(ll["log_in_reply"], 10)

    def bench(self, *args):
        out_dir = tmpdir(self)
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = bench_w3.main(["--out", out_dir, *args])
        rows = {d["test"]: d for d in (json.loads(ln) for ln in buf.getvalue().splitlines()
                                       if ln.startswith('{"test"'))}
        return rc, rows, err.getvalue()

    def test_bench_mod_not_compiled_is_rc1_and_counts_bad(self):
        saved = dict(fake_w3.CONTRACT)
        fake_w3.CONTRACT.pop("mcw_ping")                                       # «мод не скомпилирован»
        self.addCleanup(lambda: (fake_w3.CONTRACT.clear(), fake_w3.CONTRACT.update(saved)))
        srv = fake_w3.FakeWitcher(port=0, frame_time=0.002).start()
        self.addCleanup(srv.stop)
        rc, rows, err = self.bench("--port", str(srv.port), "--n", "20", "--depths", "1,4", "rtt", "rate")
        self.assertEqual(rc, 1)
        for t in ("rtt", "rate_depth_1", "rate_depth_4"):
            self.assertEqual(rows[t]["bad"], rows[t]["calls"], rows[t])
            self.assertIn(rows[t]["calls_per_s"], (0.0, None))
            self.assertIsNone(rows[t]["p50_ms"])
        self.assertLessEqual(rows["rtt"]["calls"], bench_w3.ABORT_AFTER)        # не мучаем игру 300 раз
        self.assertIn("ни одного годного ответа", err)

    def test_bench_log_not_in_reply_needs_via_log(self):
        args = ["--fake", "--fake-frame-ms", "2", "--fake-log-not-in-reply", "--n", "10", "--depths", "1,4"]
        rc, rows, _ = self.bench(*args, "rtt", "rate", "probe")
        self.assertEqual(rc, 1)
        self.assertEqual(rows["rtt"]["bad"], rows["rtt"]["calls"])
        self.assertEqual(rows["probe"]["available"], "no_data_channel")
        rc, rows, err = self.bench(*args, "--via-log", "rtt", "rate", "probe")
        self.assertEqual(rc, 0, err)
        self.assertEqual((rows["rtt"]["bad"], rows["rate_depth_4"]["bad"]), (0, 0))
        self.assertTrue(rows["probe"]["ok"])

    def test_bench_loglat_aborts_when_file_stays_empty(self):
        path = os.path.join(tmpdir(self), "scriptslog.txt")
        open(path, "w").close()
        srv = fake_w3.FakeWitcher(port=0, frame_time=0.002).start()             # в файл фейк не пишет
        self.addCleanup(srv.stop)
        t0 = time.perf_counter()
        rc, rows, _ = self.bench("--port", str(srv.port), "--scriptslog", path, "--n", "200", "loglat")
        self.assertLess(time.perf_counter() - t0, 15)
        self.assertEqual(rc, 1)
        self.assertEqual(rows["loglat"]["calls"], 3)
        self.assertIn("error", rows["loglat"])

    def test_bench_maxlen_finds_cmd_and_reply_limits_on_fake(self):
        # фейк повторяет замер 5.00c: команда от 2048 байт - тишина, ответ от 2000 байт - тишина, строка длиннее
        # 1999 символов - '......(truncated).......'. Тишина = короткий таймаут, поэтому быстро
        t0 = time.perf_counter()
        rc, rows, _ = self.bench("--fake", "--fake-frame-ms", "1", "--timeout", "0.3", "maxlen")
        self.assertLess(time.perf_counter() - t0, 20)
        m = rows["maxlen"]
        self.assertEqual(rc, 0, m)
        self.assertEqual((m["cmd_max_ok"], m["cmd_first_bad"], m["cmd_search_hi"]), (2047, 2048, 4096))
        self.assertEqual((m["reply_max_ok"], m["reply_first_bad"], m["reply_first_bad_outcome"]), (1999, 2000, "lost"))
        self.assertEqual((m["echo_max_ok_arg"], m["long_line"], m["stable"]), (1951, "truncated", True))
        self.assertTrue(m["cmd_match"] and m["reply_match"])
        self.assertEqual((m["echo_arg_cmd_max"], m["echo_arg_reply_max"]), (2023, 1951))
        self.assertGreater(m["timeouts"], 0)
        # старые пределы (только i16): поиск команды упирается в верх, ответа - в длину команды
        rc, rows, _ = self.bench("--fake", "--fake-frame-ms", "1", "--timeout", "0.3", "--hi", "3000", "maxlen",
                                 "--fake-legacy-limits")
        m = rows["maxlen"]
        self.assertEqual((rc, m["cmd_max_ok"], m["cmd_first_bad"], m["reply_first_bad"]), (0, 3000, None, None))
        self.assertFalse(m["cmd_match"])

    def test_bench_split_ops_and_limits(self):
        ops = [f"set:{1234500 + i}:-678900:1550:0" for i in range(300)]
        parts = bench_w3.split_ops("mcw_batch", ops)
        self.assertEqual("|".join(parts).split("|"), ops)
        lens = [len(w3net.build_call("mcw_batch", bench_w3.MAXLEN_SEQ, p).encode()) for p in parts]
        self.assertTrue(all(n <= w3net.EXEC_MAX_CMD for n in lens), lens)
        self.assertGreater(len(w3net.build_call("mcw_batch", bench_w3.MAXLEN_SEQ,
                                                parts[0] + "|" + ops[len(parts[0].split("|"))]).encode()),
                           w3net.EXEC_MAX_CMD)                                  # следующая op уже не влезла бы
        with self.assertRaises(w3net.CommandTooLong):
            bench_w3.split_ops("mcw_batch", ["x" * 2100])
        self.assertEqual(bench_w3.split_ops("mcw_batch", []), [])


class FakeParser(unittest.TestCase):
    def test_parse_exec(self):
        self.assertEqual(fake_w3.parse_exec("spawn('Nekker', 3)"), ("spawn", ["Nekker", "3"]))
        self.assertEqual(fake_w3.parse_exec('mcw_batch(1, "set:1:2:3:0|clear:4:5:6")'), ("mcw_batch", ["1", "set:1:2:3:0|clear:4:5:6"]))
        self.assertEqual(fake_w3.parse_exec("mcw_alias(4, 'barrel', 0.0)"), ("mcw_alias", ["4", "barrel", "0.0"]))
        self.assertEqual(fake_w3.parse_exec("logstats"), ("logstats", []))
        self.assertEqual(fake_w3.parse_exec("logstats()"), ("logstats", []))

    def test_parse_exec_like_500c(self):
        P = fake_w3.parse_exec
        self.assertEqual(P('mcw_echo(1, "a\\b")'), ("mcw_echo", ["1", "a\\b"]))          # '\' не экранирует
        self.assertEqual(P('mcw_echo(1, "a\\")'), ("mcw_echo", ["1", "a\\"]))            # и перед кавычкой тоже
        self.assertEqual(P('mcw_echo(1, " ")'), ("mcw_echo", ["1", " "]))
        self.assertEqual(P('mcw_echo(1, "a b" )'), ("mcw_echo", ["1", "a b"]))
        for bad in ('mcw_echo(1, "")', "mcw_cube(1, '')", 'mcw_echo(1, "a"b")', 'mcw_echo(1, "a' + "'" + 'b")',
                    "mcw_alias(1, 'a\"b', 0.0)", 'mcw_echo(1, "ab)', 'mcw_echo(1, "a" "b")', 'mcw_echo(1, a"b")'):
            self.assertEqual(P(bad)[1], None, bad)

    def test_round_like_mcw_round(self):
        # MCW_Round мода = FloorF(v + 0.5): половина вверх (RoundF 5.00c режет к нулю, round() Python - банковское)
        got = [fake_w3._rnd(v) for v in (-2.5, -1.5, -0.5, 0.5, 1.5, 2.5, -1.6, -1.4, 1.4, 1.6)]
        self.assertEqual(got, [-2, -1, 0, 1, 2, 3, -2, -1, 1, 2])
        w = fake_w3.FakeWorld()
        w.player, w.yaw = [0, 0, 0], 9000                                       # курс 90: вперёд -X
        self.assertEqual(w.ahead(251.5)[:2], (-251, 0))                         # -251.5 -> -251 (round() дал бы -252)
        w.yaw = 27000
        self.assertEqual(w.ahead(2.5)[:2], (3, 0))                              # 2.5 -> 3 (round() дал бы 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
