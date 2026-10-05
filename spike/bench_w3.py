"""bench_w3 - замеры отладочного канала Ведьмака для спайка (что и когда мерить - bench_plan.md).

Против игры (запущена пользователем с -net -debugscripts, modMCW скомпилирован):
    python bench_w3.py all --label "мир, 60 fps"         rtt rate frame poll echo probe charset loglat batch stress
    python bench_w3.py rtt --n 500
    python bench_w3.py rate --n 1000 --depths 1,4,16,64
    python bench_w3.py frame --bursts 1,2,4,8,16,32
    python bench_w3.py echo --sizes 256,512,1024,1900     байт аргумента (или --sizes-kb 0.25,1,1.9)
    python bench_w3.py batch --ops 1,10,50,300            > ~70 операций - несколько команд mcw_batch по 2047 байт
    python bench_w3.py stress --levels 100,1000,3000 --hold 5   3000 - после сохранения (bench_plan.md, п. 4)
    python bench_w3.py maxlen                             пределы команды и ответа: последним, отдельно
Пределы exec 5.00c (w3net.EXEC_MAX_CMD / EXEC_MAX_REPLY): команда до 2047 байт, ответ до 1999 байт. echo пропускает
размеры, где команда или ответ длиннее, batch режет операции на команды не длиннее 2047 байт.
    --via-log      Log() не попадает в ответ exec: строки mcw-команд брать из scriptslog.txt (attach_scriptslog)
    --bind-delay S пауза после BIND перед первым exec (если первый exec даёт TimeoutError)
Самопроверка без игры:  python bench_w3.py --fake all     (поднимает fake_w3 на свободном порту)

Каждый тест пишет calls (сколько вызовов) и bad (сколько без годного ответа: Warn, «тихий» Spam без маркера,
нет end, потерян, таймаут). Задержки и вызовы/с считаются только по годным ответам. Если в каком-то тесте все
ответы плохие (мод не скомпилирован, Log() не в ответе без --via-log), код выхода 1.
Каждый прогон пишет JSON в spike/results/bench_<время>_<game|fake>.json (--out - другая папка).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import string
import sys
import tempfile
import threading
import time
from collections import Counter, deque
from typing import Dict, List, Optional, Sequence

import w3net

HERE = os.path.dirname(os.path.abspath(__file__))
# лёгкое и безопасное - раньше; batch и stress (спавн сотен сущностей) - в конце, чтобы вылет не съел остальное
ALL = ["rtt", "rate", "frame", "poll", "echo", "probe", "charset", "loglat", "batch", "stress"]
ABORT_AFTER = 5            # столько неудач подряд без единого годного ответа - тест прерывается
MAXLEN_SEQ = 10 ** 9       # seq фиксированной ширины (10 цифр) в maxlen: длины команды и ответа предсказуемы
MAXLEN_HI = 4096           # верх поиска maxlen по умолчанию (вдвое выше замеренного предела команды)
CHARSET_UNSAFE = "\"'"     # замер 5.00c: кавычка внутри строки - вся команда 'Warn: Failed' (w3net.fmt_arg их не шлёт)


def pct(xs: Sequence[float], p: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    k = min(len(s) - 1, max(0, int(round(p / 100.0 * (len(s) - 1)))))
    return s[k]


def ms_stats(rtts: Sequence[float]) -> Dict:
    ms = [r * 1000 for r in rtts]
    r2 = lambda v: None if v is None else round(v, 2)     # noqa: E731
    return {"n": len(ms), "p50_ms": r2(pct(ms, 50)), "p95_ms": r2(pct(ms, 95)), "p99_ms": r2(pct(ms, 99)),
            "max_ms": r2(max(ms) if ms else None), "mean_ms": r2(statistics.fmean(ms) if ms else None)}


def emit(out: List[Dict], d: Dict) -> Dict:
    print(json.dumps(d, ensure_ascii=False), flush=True)
    out.append(d)
    return d


def pong(r: w3net.ExecResult) -> Optional[int]:
    """engineMs из годного ответа mcw_ping, иначе None."""
    v = r.ints("pong") if r.ok else None
    return v[0] if v else None


def all_bad(d: Dict) -> bool:
    return bool(d.get("calls")) and d.get("bad") == d.get("calls")


def echo_reply(seq: int, n: int) -> str:
    """Ответ mcw_echo(seq, "x"*n) по контракту (ws: Log 'echo <len> <s>', затем end), как его собирает клиент."""
    return f"[MCW#{seq}] echo {n} {'x' * n}\n[MCW#{seq}] end"


def utf8_len(s: str) -> int:
    return len(s.encode("utf-8"))


def call_len0(func: str, seq: int) -> int:
    """Байт в func(seq, "<s>") без самой s. Пустую строку fmt_arg не пропускает (игра её не разбирает), отсюда "x"."""
    return utf8_len(w3net.build_call(func, seq, "x")) - 1


def maxlen_limits(seq: int = MAXLEN_SEQ) -> Dict[str, int]:
    """Расчёт по константам w3net для mcw_echo(seq, "x"*n) с 10-значным seq: наибольшее n, при котором команда
    не длиннее EXEC_MAX_CMD, и наибольшее n, при котором ответ не длиннее EXEC_MAX_REPLY."""
    cmd_max = w3net.EXEC_MAX_CMD - call_len0("mcw_echo", seq)
    n = 0
    while utf8_len(echo_reply(seq, n + 1)) <= w3net.EXEC_MAX_REPLY:
        n += 1
    return {"echo_arg_cmd_max": cmd_max, "echo_arg_reply_max": n}


def split_ops(func: str, ops: Sequence[str], limit: int = w3net.EXEC_MAX_CMD) -> List[str]:
    """ops -> аргументы func(seq, "op|op|..."), каждая команда (seq из 10 цифр - самый длинный) не длиннее limit."""
    base = call_len0(func, MAXLEN_SEQ)
    out: List[str] = []
    cur: List[str] = []
    size = base
    for op in ops:
        add = utf8_len(op) + (1 if cur else 0)
        if cur and size + add > limit:
            out.append("|".join(cur))
            cur, size, add = [], base, utf8_len(op)
        if base + add > limit:
            raise w3net.CommandTooLong(f"одна операция {op!r} не влезает в {limit} байт")
        cur.append(op)
        size += add
    if cur:
        out.append("|".join(cur))
    return out


def echo_sizes(a: argparse.Namespace) -> List[int]:
    """Размеры аргумента echo в байтах: --sizes-kb (если задан, дробные КБ) или --sizes."""
    if a.sizes_kb:
        return [round(float(x) * 1024) for x in a.sizes_kb.split(",")]
    return [int(x) for x in a.sizes.split(",")]


class Bench:
    def __init__(self, cli: w3net.W3Client, a: argparse.Namespace, log_path: Optional[str]) -> None:
        self.cli, self.a, self.log_path = cli, a, log_path
        self.results: List[Dict] = []

    def call(self, func: str, *args, timeout: Optional[float] = None) -> w3net.ExecResult:
        return self.cli.call(func, *args, timeout=timeout)

    def try_call(self, func: str, *args, timeout: Optional[float] = None) -> Optional[w3net.ExecResult]:
        """Вызов, где таймаут/потеря - просто «плохой ответ». Обрыв соединения пробрасывается."""
        try:
            return self.call(func, *args, timeout=timeout)
        except ConnectionError:
            raise
        except TimeoutError:                                     # и ReplyLost
            return None

    def pos(self):
        r = self.call("mcw_poll")
        p, ground = r.ints("pos"), None
        if p is None:
            raise RuntimeError(f"mcw_poll не вернул pos: {r.text[:200]!r}")
        g = self.call("mcw_ground", p[0], p[1]).ints("ground")
        if g and g[0]:
            ground = g[1]
        return p, (ground if ground is not None else p[2])

    # -- RTT последовательных mcw_ping
    def t_rtt(self) -> None:
        rtts, bad, eng, calls = [], 0, [], 0
        self.try_call("mcw_ping")                              # прогрев
        t0 = time.perf_counter()
        for _ in range(self.a.n):
            calls += 1
            r = self.try_call("mcw_ping")
            e = pong(r) if r else None
            if e is None:
                bad += 1
                if not rtts and bad >= ABORT_AFTER:
                    break
                continue
            rtts.append(r.rtt)                                   # type: ignore[union-attr]
            eng.append(e)
        wall = time.perf_counter() - t0
        d = [b - a for a, b in zip(eng, eng[1:]) if b >= a]
        emit(self.results, {"test": "rtt", "cmd": "mcw_ping", "calls": calls, "bad": bad, **ms_stats(rtts),
                            "calls_per_s": round(len(rtts) / wall, 1) if wall > 0 else None,
                            "engine_ms_delta_p50": pct(d, 50), "engine_ms_delta_min": min(d) if d else None})

    # -- вызовов/с: последовательно (depth 1) и конвейером; считаются только годные ответы
    def t_rate(self) -> None:
        for depth in [int(x) for x in self.a.depths.split(",")]:
            q: deque = deque()
            rtts: List[float] = []
            lost = errors = not_ok = calls = 0
            t0 = time.perf_counter()

            def drain_one() -> None:
                nonlocal lost, errors, not_ok
                f = q.popleft()
                try:
                    r = f.result(self.a.timeout + 2)
                except w3net.ReplyLost:
                    lost += 1
                    return
                except ConnectionError:
                    raise
                except Exception:                                # noqa: BLE001
                    errors += 1
                    return
                if pong(r) is None:
                    not_ok += 1
                else:
                    rtts.append(r.rtt)

            for _ in range(self.a.n):
                q.append(self.cli.call_async("mcw_ping"))
                calls += 1
                while len(q) >= depth:
                    drain_one()
                if not rtts and lost + errors + not_ok >= ABORT_AFTER:
                    break
            while q:
                drain_one()
            wall = time.perf_counter() - t0
            emit(self.results, {"test": f"rate_depth_{depth}", "calls": calls, "bad": calls - len(rtts),
                                **ms_stats(rtts), "calls_per_s": round(len(rtts) / wall, 1) if wall > 0 else None,
                                "not_ok": not_ok, "lost": lost, "errors": errors})

    # -- сколько exec игра исполняет за кадр: пачка без ожидания, считаем разные engineMs в 'pong'
    def t_frame(self) -> None:
        for burst in [int(x) for x in self.a.bursts.split(",")]:
            per_frame_max, frames_used = [], []
            calls = bad = 0
            for _ in range(self.a.reps):
                futs = [self.cli.call_async("mcw_ping") for _ in range(burst)]
                eng = []
                for f in futs:
                    calls += 1
                    try:
                        e = pong(f.result(self.a.timeout + 2))
                    except ConnectionError:
                        raise
                    except Exception:                            # noqa: BLE001
                        e = None
                    if e is None:
                        bad += 1
                    else:
                        eng.append(e)
                if eng:
                    c = Counter(eng)
                    frames_used.append(len(c))
                    per_frame_max.append(max(c.values()))
                elif bad >= ABORT_AFTER:
                    break
            emit(self.results, {"test": f"frame_burst_{burst}", "reps": self.a.reps, "calls": calls, "bad": bad,
                                "frames_used_p50": pct(frames_used, 50),
                                "execs_in_one_frame_max": max(per_frame_max, default=None),
                                "execs_in_one_frame_p50": pct(per_frame_max, 50)})

    # -- mcw_poll: задержка и размер ответа
    def t_poll(self) -> None:
        rtts, size, bad, calls = [], 0, 0, 0
        for _ in range(self.a.n):
            calls += 1
            r = self.try_call("mcw_poll")
            if r is None or not (r.ok and r.tokens("pos") and r.tokens("cam")):
                bad += 1
                if not rtts and bad >= ABORT_AFTER:
                    break
                continue
            rtts.append(r.rtt)
            size = max(size, len(r.text.encode("utf-8")))
        emit(self.results, {"test": "poll", "calls": calls, "bad": bad, **ms_stats(rtts), "reply_bytes": size})

    # -- размер пакета: mcw_echo N байт туда и обратно; размеры, где команда длиннее EXEC_MAX_CMD или ответ длиннее
    #    EXEC_MAX_REPLY, пропускаются (их ищет maxlen)
    def t_echo(self) -> None:
        for n in echo_sizes(self.a):
            s = "x" * n
            name = f"echo_{n}b"
            rtts, bad, near, calls = [], 0, 0, 0
            try:
                pkt = w3net.exec_packet_size(w3net.build_call("mcw_echo", MAXLEN_SEQ, s))
            except w3net.CommandTooLong as e:
                emit(self.results, {"test": name, "skipped": str(e)})
                continue
            reply = utf8_len(echo_reply(MAXLEN_SEQ, n))
            if reply > w3net.EXEC_MAX_REPLY:
                emit(self.results, {"test": name, "skipped": f"ответ {reply} байт, игра теряет ответы длиннее "
                                                              f"{w3net.EXEC_MAX_REPLY}"})
                continue
            for _ in range(self.a.reps):
                calls += 1
                try:
                    r = self.call("mcw_echo", s, timeout=self.a.timeout + 5)
                except Exception as e:                           # noqa: BLE001
                    bad += 1
                    if not self.cli.connected:
                        emit(self.results, {"test": name, "fatal": f"{type(e).__name__}: {e}"})
                        return
                    continue
                near += r.near_limit
                t = r.tokens("echo")
                if r.ok and t and t[0] == str(len(s)) and len(t) > 1 and t[1] == s:
                    rtts.append(r.rtt)
                else:
                    bad += 1
            emit(self.results, {"test": name, "arg_bytes": n, "packet_bytes": pkt, "reply_bytes": reply,
                                "calls": calls, "bad": bad, **ms_stats(rtts), "near_limit": near})

    # -- mcw_batch: N операций set, затем столько же clear. Операции режутся на команды не длиннее EXEC_MAX_CMD
    #    (cmds в результате), команды одного прохода идут подряд; set/clear - задержки отдельных команд
    def t_batch(self) -> None:
        (px, py, pz, yaw), gz = self.pos()
        h = math.radians(yaw / 100.0)
        fx, fy = -math.sin(h), math.cos(h)
        rx, ry = fy, -fx
        for n in [int(x) for x in self.a.ops.split(",")]:
            if n <= 0:                                           # пустой mcw_batch(seq, "") игра не разберёт
                emit(self.results, {"test": f"batch_{n}", "skipped": "нет операций"})
                continue
            side = max(1, math.ceil(math.sqrt(n)))
            cells = []
            for i in range(n):
                row, col = divmod(i, side)
                d, s = 400 + row * 100, (col - side / 2) * 100
                cells.append((round(px + fx * d + rx * s), round(py + fy * d + ry * s), gz + 50))
            try:
                set_parts = split_ops("mcw_batch", [f"set:{x}:{y}:{z}:0" for x, y, z in cells])
                clr_parts = split_ops("mcw_batch", [f"clear:{x}:{y}:{z}" for x, y, z in cells])
                pkts = [w3net.exec_packet_size(w3net.build_call("mcw_batch", MAXLEN_SEQ, p)) for p in set_parts]
            except w3net.CommandTooLong as e:
                emit(self.results, {"test": f"batch_{n}", "skipped": str(e)})
                continue
            set_rtts, clr_rtts, game_ms, bad, calls, good_ops = [], [], [], 0, 0, 0
            t0 = time.perf_counter()
            for _ in range(self.a.reps):
                for parts, rtts, col in ((set_parts, set_rtts, 0), (clr_parts, clr_rtts, 1)):
                    for payload in parts:
                        k = payload.count("|") + 1
                        calls += 1
                        r = self.try_call("mcw_batch", payload, timeout=self.a.timeout + 30)
                        v = r.ints("batch") if r and r.ok else None
                        if v and v[col] == k and v[2] == 0:
                            rtts.append(r.rtt)                   # type: ignore[union-attr]
                            good_ops += k
                            if col == 0:
                                game_ms.append(v[3])
                        else:
                            bad += 1
            wall = time.perf_counter() - t0
            emit(self.results, {"test": f"batch_{n}", "cmds": len(set_parts), "packet_bytes": max(pkts),
                                "bytes_per_op": round(sum(pkts) / n, 1), "calls": calls, "bad": bad,
                                "set": ms_stats(set_rtts), "clear": ms_stats(clr_rtts),
                                "game_set_ms_p50": pct(game_ms, 50),
                                "ops_per_s": round(good_ops / wall, 1) if wall > 0 else None})

    # -- драйвер куб-стресса: спавн N, держим --hold с, меряем пинг и шаг engineMs (прокси времени кадра)
    def t_stress(self) -> None:
        self.call("mcw_clearall", timeout=60)
        for n in [int(x) for x in self.a.levels.split(",")]:
            r = self.try_call("mcw_stress", n, self.a.spacing, "-", timeout=120)   # "-" - cont_crate; "" не разберётся
            v = r.ints("stress") if r and r.ok else None
            c0 = self.try_call("mcw_count", timeout=30)
            cnt = c0.ints("count") if c0 and c0.ok else None
            rtts, eng, ping_bad = [], [], 0
            end = time.monotonic() + self.a.hold
            while time.monotonic() < end:
                p = self.try_call("mcw_ping")
                e = pong(p) if p else None
                if e is None:
                    ping_bad += 1
                    continue
                rtts.append(p.rtt)                               # type: ignore[union-attr]
                eng.append(e)
            d = [b - a for a, b in zip(eng, eng[1:]) if b >= a]
            c = self.try_call("mcw_clearall", timeout=120)
            cl = c.ints("cleared") if c and c.ok else None
            emit(self.results, {"test": f"stress_{n}", "calls": 1, "bad": 0 if v else 1,
                                "spawned": v[0] if v else None, "game_spawn_ms": v[1] if v else None,
                                "rtt_spawn_ms": round(r.rtt * 1000, 1) if r else None,
                                "count_after": cnt[0] if cnt else None, "hold_s": self.a.hold,
                                "ping_with_blocks": ms_stats(rtts), "ping_bad": ping_bad,
                                "engine_ms_delta_p50": pct(d, 50), "cleared": cl[0] if cl else None,
                                "rtt_clear_ms": round(c.rtt * 1000, 1) if c else None})

    # -- проба @addField/map/@wrapMethod (modMCWProbe)
    def t_probe(self) -> None:
        st = self.try_call("mcwp_state")
        if st is None or not st.ok:
            if st is None:
                avail = "no_reply"                               # таймаут: ничего не известно
            elif st.failed:
                avail = False                                    # 'Warn: Failed' - функции нет: проба не скомпилирована
            elif st.silent:
                avail = "no_data_channel"                        # 'Spam' без маркера: Log() не в ответе (--via-log)
            else:
                avail = "incomplete"
            emit(self.results, {"test": "probe", "available": avail, "reply": st.text[:200] if st else None})
            return
        key = f"k{int(time.time()) % 100000}"
        rs = [st] + [self.try_call(f, *args) for f, args in (("mcwp_put", (key,)), ("mcwp_get", (key,)),
                                                              ("mcwp_del", (key,)), ("mcwp_get", (key,)),
                                                              ("mcwp_state", ()))]
        _, put, get, dele, get2, st2 = rs
        body = lambda r: r.body if r else None                   # noqa: E731
        g1 = get.tokens("get") if get and get.ok else None
        g2 = get2.tokens("get") if get2 and get2.ok else None
        emit(self.results, {"test": "probe", "available": True, "calls": len(rs),
                            "bad": sum(1 for r in rs if r is None or not r.ok),
                            "state": body(st), "put": body(put), "get": body(get), "del": body(dele),
                            "get_after_del": body(get2), "state_after": body(st2),
                            "ok": bool(g1 and g1[-1] == "1" and g2 and g2[-1] == "0")})

    # -- какие символы доживают до mcw_echo. Команда собирается руками, мимо fmt_arg (тот кавычки не пропустит).
    #    Замер 5.00c: '"' и "'" внутри строки ломают разбор всей команды - это ожидаемо (expected_fail, не bad);
    #    '\' доходит как есть; StrLen считает байты UTF-8 ('aжb' -> 4). Фейк разбирает так же
    def t_charset(self) -> None:
        bad, mangled, details, safe, expected = 0, [], [], [], []
        chars = string.printable.strip() + " " + "ж"
        for ch in chars:
            arg = "a" + ch + "b"
            seq = self.cli.next_seq()
            try:
                r = self.cli.exec(f'mcw_echo({seq}, "{arg}")', seq=seq)
            except ConnectionError:
                raise
            except Exception as e:                               # noqa: BLE001 - таймаут, ReplyLost
                bad += 1
                details.append({"char": ch, "got": type(e).__name__})
                continue
            got = r.text[:80]
            if r.ok and r.body[:1] == [f"echo {utf8_len(arg)} {arg}"]:
                safe.append(ch)
            elif ch in CHARSET_UNSAFE and r.failed:
                expected.append(ch)                              # Warn: Failed - как в замере
            elif r.ok and r.tokens("echo") is not None:
                mangled.append(ch)                               # ответ есть, строка испорчена
                details.append({"char": ch, "got": got})
            else:
                bad += 1                                         # годного ответа нет вовсе
                details.append({"char": ch, "got": got})
        alphabet = "".join(safe)
        print(f"безопасный алфавит exec-строки ({len(alphabet)} символов): {alphabet}", flush=True)
        emit(self.results, {"test": "charset", "calls": len(chars), "bad": bad, "safe_alphabet": alphabet,
                            "expected_fail": "".join(expected),
                            "unexpected_ok": "".join(c for c in CHARSET_UNSAFE if c in safe),
                            "bad_chars": "".join(d["char"] for d in details if d["char"] not in mangled),
                            "mangled": "".join(mangled), "details": details})

    # -- exec -> строки в scriptslog.txt: задержка '[MCW#seq] end' (Log) и '... via LogChannel' отдельно;
    #    заодно: что из них попадает в ответ exec по сети
    def t_loglat(self) -> None:
        if not self.log_path:
            emit(self.results, {"test": "loglat", "skipped": "нет пути к scriptslog.txt"})
            return
        seen_end: Dict[int, float] = {}
        seen_chan: Dict[int, float] = {}
        log_in_file = 0
        cond = threading.Condition()

        def on_line(line: str) -> None:
            nonlocal log_in_file
            m = w3net.line_seq(line)
            if not m:
                return
            now = time.perf_counter()
            with cond:
                if m[1] == "via LogChannel":
                    seen_chan.setdefault(m[0], now)
                elif m[1] == "end":
                    seen_end.setdefault(m[0], now)
                elif m[1] == "via Log":
                    log_in_file += 1
                cond.notify_all()

        tail = self.cli.listen_scripts_log(on_line, path=self.log_path, poll_interval=0.002)
        lat_end, lat_chan = [], []
        miss_end = miss_chan = both_missing = streak = calls = 0
        chan_in_reply = log_in_reply = 0
        aborted = None
        t0 = time.perf_counter()
        for _ in range(min(self.a.n, 200)):
            calls += 1
            seq = self.cli.next_seq()
            sent = time.perf_counter()
            fut = self.cli.exec_async(w3net.build_call("mcw_logtest", seq), seq=seq)
            with cond:
                cond.wait_for(lambda: seq in seen_end or seq in seen_chan, timeout=3.0)
                # вторая строка обычно в том же сбросе файла; если её канал не пишется в файл 5 раз подряд - не ждём
                other_missing = miss_chan if seq in seen_end else miss_end
                if (seq in seen_end) != (seq in seen_chan) and other_missing < 5:
                    cond.wait_for(lambda: seq in seen_end and seq in seen_chan, timeout=0.1)
                e, c = seen_end.get(seq), seen_chan.get(seq)
            if e is not None:
                lat_end.append(e - sent)
            else:
                miss_end += 1
            if c is not None:
                lat_chan.append(c - sent)
            else:
                miss_chan += 1
            if e is None and c is None:
                both_missing += 1
                streak += 1
            else:
                streak = 0
            try:
                r = fut.result(self.a.timeout + 2)
                if r.source == "net":
                    chan_in_reply += "via LogChannel" in r.body
                    log_in_reply += "via Log" in r.body
            except ConnectionError:
                tail.stop()
                raise
            except Exception:                                    # noqa: BLE001
                pass
            if streak >= 3:
                aborted = f"{streak} раза подряд ни одной строки mcw_logtest в {self.log_path} за 3 с"
                break
        tail.stop()
        d = {"test": "loglat", "calls": calls, "bad": both_missing, "log_end": ms_stats(lat_end),
             "logchannel": ms_stats(lat_chan), "missing_end": miss_end, "missing_chan": miss_chan,
             "wall_s": round(time.perf_counter() - t0, 2), "logchannel_in_reply": chan_in_reply,
             "log_in_reply": log_in_reply, "log_in_file": log_in_file, "path": self.log_path, "tail_polls": tail.polls}
        if aborted:
            d["error"] = aborted
        emit(self.results, d)

    # -- пределы exec (замер 5.00c: команда 2047, ответ 1999 байт). Двоичный поиск по двум пробам с seq из 10 цифр:
    #    предел команды - mcw_batch(seq, "x..x") (одна нераспознанная операция: ответ 'batch 0 0 1 ms' короткий при
    #    любой длине команды), предел ответа - mcw_echo(seq, "x"*n) (ответ на 48 байт длиннее аргумента). Выброшенная
    #    команда и потерянный ответ выглядят одинаково - ответа нет, поэтому таймаут пробы короткий (до 1 с), а
    #    найденные границы в конце перепроверяются с обычным. Длинные команды идут мимо проверки клиента (max_cmd).
    def t_maxlen(self) -> None:
        cli = self.cli
        t_short = min(self.a.timeout, 1.0)
        top = min(self.a.hi or MAXLEN_HI, w3net.MAX_STR)
        calls = timeouts = 0
        seqs = iter(range(MAXLEN_SEQ + 1, MAXLEN_SEQ + 10 ** 6))
        last = {"at": None}

        def probe(func: str, arg: str, timeout: float) -> Optional[w3net.ExecResult]:
            nonlocal calls, timeouts
            seq = next(seqs)
            cmd = w3net.build_call(func, seq, arg)
            last["at"] = (func, utf8_len(cmd))
            calls += 1
            try:
                return cli.exec(cmd, seq=seq, timeout=timeout)
            except ConnectionError:
                raise
            except TimeoutError:                                 # и ReplyLost: команда выброшена или ответ потерян
                timeouts += 1
                return None

        batch0 = call_len0("mcw_batch", MAXLEN_SEQ)
        echo0 = call_len0("mcw_echo", MAXLEN_SEQ)
        outcome: Dict[int, str] = {}                             # n аргумента echo -> ok | lost | truncated | bad
        got: Dict[int, int] = {}                                 # n -> длина пришедшего ответа, байт

        def cmd_ok(length: int, timeout: float = t_short) -> bool:
            r = probe("mcw_batch", "x" * (length - batch0), timeout)
            return r is not None and r.ok and r.ints("batch") is not None

        def echo_ok(n: int, timeout: float = t_short) -> bool:
            r = probe("mcw_echo", "x" * n, timeout)
            if r is None or r.source != "net":                   # из scriptslog (--via-log) - по сети ответа нет
                outcome[n] = "lost"
                return False
            t = r.tokens("echo")
            ok = r.ok and bool(t) and t[0] == str(n) and len(t) > 1 and t[1] == "x" * n
            outcome[n] = "ok" if ok else "truncated" if r.truncated else "bad"
            if ok:
                got[n] = utf8_len(r.text)
            return ok

        def search(ok_fn, lo: int, hi: int):
            """Наибольшее x в [lo, hi] с ok_fn(x) (сначала True, потом False) -> (наибольшее годное, первое плохое)."""
            best, first_bad = lo - 1, None
            while lo <= hi:
                mid = (lo + hi) // 2
                if ok_fn(mid):
                    best, lo = mid, mid + 1
                else:
                    first_bad, hi = mid, mid - 1
            return best, first_bad

        saved = cli.max_cmd
        cli.max_cmd = w3net.MAX_STR
        try:
            if not cmd_ok(64, self.a.timeout) or not echo_ok(16, self.a.timeout):
                emit(self.results, {"test": "maxlen", "calls": calls, "bad": calls, "timeouts": timeouts,
                                    "error": "короткие mcw_batch/mcw_echo без годного ответа: мод не скомпилирован "
                                             "или Log() не в ответе"})
                return
            cmd_max, cmd_bad = search(cmd_ok, 65, top)
            n_hi = cmd_max - echo0
            n_max, n_bad = search(echo_ok, 17, n_hi)
            long_line = None                                     # самая длинная echo, что влезает в команду
            if utf8_len(echo_reply(MAXLEN_SEQ, n_hi).split("\n")[0]) > w3net.EXEC_MAX_REPLY:
                if n_hi not in outcome:
                    echo_ok(n_hi, self.a.timeout)
                long_line = outcome.get(n_hi)
            # перепроверка границ с обычным таймаутом: короткий мог принять заминку игры за потерю
            stable = cmd_ok(cmd_max, self.a.timeout) and (cmd_bad is None or not cmd_ok(cmd_bad, self.a.timeout))
            if n_bad is not None and outcome.get(n_bad) == "lost":
                stable = stable and not echo_ok(n_bad, self.a.timeout)
        except ConnectionError as e:
            emit(self.results, {"test": "maxlen", "fatal_at": last["at"], "calls": calls, "bad": calls,
                                "error": f"{type(e).__name__}: {e}", "note": "соединение потеряно - игра упала?"})
            return
        finally:
            cli.max_cmd = saved
        reply_max = got.get(n_max, utf8_len(echo_reply(MAXLEN_SEQ, n_max)))
        reply_bad = utf8_len(echo_reply(MAXLEN_SEQ, n_bad)) if n_bad is not None else None
        emit(self.results, {"test": "maxlen", "calls": calls, "bad": 0, "timeouts": timeouts, "stable": stable,
                            "cmd_max_ok": cmd_max, "cmd_first_bad": cmd_bad, "cmd_search_hi": top,
                            "reply_max_ok": reply_max, "reply_first_bad": reply_bad,
                            "reply_first_bad_outcome": outcome.get(n_bad) if n_bad is not None else None,
                            "echo_max_ok_arg": n_max, "long_line": long_line,
                            "cmd_match": cmd_max == w3net.EXEC_MAX_CMD,
                            "reply_match": reply_max == w3net.EXEC_MAX_REPLY,
                            "packet_bytes_at_cmd_max": cmd_max + 36, **maxlen_limits()})


def main(argv: Optional[Sequence[str]] = None) -> int:
    w3net.utf8_stdio()
    ap = argparse.ArgumentParser(description="замеры отладочного канала Ведьмака",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("tests", nargs="+", choices=ALL + ["maxlen", "all"])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=None, help="по умолчанию 37001, затем 37000")
    ap.add_argument("--label", default="", help="состояние игры: 'мир 60fps', 'пауза', 'загрузка'...")
    ap.add_argument("--out", default=os.path.join(HERE, "results"), help="папка для JSON")
    ap.add_argument("--timeout", type=float, default=3.0)
    ap.add_argument("--bind-delay", type=float, default=0.0, help="пауза после BIND перед первым exec, с")
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--depths", default="1,2,4,8,16,32,64")
    ap.add_argument("--bursts", default="1,2,4,8,16,32")
    ap.add_argument("--sizes", default="256,512,1024,1900",
                    help="echo: байт аргумента; длиннее ~1950 ответ не влезает в 1999 байт - пропуск")
    ap.add_argument("--sizes-kb", default=None, help="echo: то же в КБ (дробные: 0.25,1,1.9), вместо --sizes")
    ap.add_argument("--ops", default="1,10,50",
                    help="операций в batch; больше ~70 - несколько команд по 2047 байт (300+ - отдельным прогоном)")
    ap.add_argument("--levels", default="100,1000", help="3000 - отдельно, после сохранения (bench_plan.md п. 4)")
    ap.add_argument("--spacing", type=int, default=150, help="шаг сетки mcw_stress, см")
    ap.add_argument("--hold", type=float, default=3.0, help="сколько держать сущности перед clearall, с")
    ap.add_argument("--hi", type=int, default=0, help=f"верх поиска длины команды в maxlen, байт (0 = {MAXLEN_HI})")
    ap.add_argument("--scriptslog", default=None, help="путь к scriptslog.txt (игра: Documents\\The Witcher 3)")
    ap.add_argument("--via-log", action="store_true",
                    help="Log() не попадает в ответ exec: завершать mcw-команды по строкам scriptslog.txt")
    ap.add_argument("--fake", action="store_true", help="поднять fake_w3 на свободном порту и мерить его")
    ap.add_argument("--fake-frame-ms", type=float, default=16.7)
    ap.add_argument("--fake-max-per-frame", type=int, default=0)
    ap.add_argument("--fake-per-line", action="store_true")
    ap.add_argument("--fake-leading-status", action="store_true", help="фейк шлёт 'Spam' до строк Log()")
    ap.add_argument("--fake-log-not-in-reply", action="store_true", help="фейк: Log() только в scriptslog")
    ap.add_argument("--fake-legacy-limits", action="store_true", help="фейк без пределов exec 5.00c (только i16)")
    a = ap.parse_args(argv)
    tests = ALL if "all" in a.tests else a.tests

    srv = None
    tmpdir = None
    log_path = a.scriptslog
    if a.fake:
        import fake_w3
        if log_path is None:
            tmpdir = tempfile.mkdtemp(prefix="w3bench_", dir=os.environ.get("W3NET_TMP"))
            log_path = os.path.join(tmpdir, "scriptslog.txt")
            open(log_path, "w").close()
        srv = fake_w3.FakeWitcher(port=0, frame_time=a.fake_frame_ms / 1000.0, max_per_frame=a.fake_max_per_frame,
                                  per_line=a.fake_per_line, leading_status=a.fake_leading_status,
                                  log_in_reply=not a.fake_log_not_in_reply, scriptslog_path=log_path,
                                  legacy_limits=a.fake_legacy_limits).start()
        a.port = srv.port
    elif log_path is None:
        log_path = w3net.default_scriptslog_path()
        if not os.path.exists(log_path):
            log_path = None
    if a.via_log and not log_path:
        print("--via-log: нет scriptslog.txt (укажите --scriptslog)", file=sys.stderr)
        return 2

    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    b = None
    try:
        with w3net.W3Client(a.host, a.port, timeout=a.timeout, bind_delay=a.bind_delay) as cli:
            if a.via_log:
                cli.attach_scriptslog(log_path)
            b = Bench(cli, a, log_path)
            for t in tests:
                try:
                    getattr(b, "t_" + t)()
                except Exception as e:                           # noqa: BLE001 - один тест не валит прогон
                    emit(b.results, {"test": t, "error": f"{type(e).__name__}: {e}"})
                    if not cli.connected:
                        break
            client = {"port": cli.port, "via_log": a.via_log, "bytes_sent": cli.bytes_sent,
                      "bytes_received": cli.bytes_received, "stream_resyncs": cli.stream.resyncs,
                      "bad_packets": cli.stream.bad_packets, "stats": dict(cli.stats)}
    except OSError as e:
        print(f"нет соединения: {e}", file=sys.stderr)
        return 2
    finally:
        if srv:
            srv.stop()
        if tmpdir:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
    print(json.dumps({"client": client}, ensure_ascii=False))
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, f"bench_{time.strftime('%Y%m%d-%H%M%S')}_{'fake' if a.fake else 'game'}.json")
    meta = {"started": started, "label": a.label, "fake": a.fake, "host": a.host, "tests": tests,
            "args": {k: v for k, v in vars(a).items() if k not in ("tests",)}, "python": sys.version.split()[0],
            "platform": platform.platform()}
    results = b.results if b else []
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "client": client, "results": results}, f, ensure_ascii=False, indent=1)
    print(f"результаты: {path}")
    failed = [r["test"] for r in results if "error" in r or "fatal" in r]
    dead = [r["test"] for r in results if all_bad(r)]
    if dead:
        print(f"ни одного годного ответа: {', '.join(dead)} - числа этих тестов не брать "
              f"(мод не скомпилирован? Log() не в ответе - тогда --via-log)", file=sys.stderr)
    if failed:
        print(f"ошибки: {', '.join(failed)}", file=sys.stderr)
    return 1 if failed or dead else 0


if __name__ == "__main__":
    sys.exit(main())
