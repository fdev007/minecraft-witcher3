"""w3cmd - агентский канал в Ведьмака (аналог cs2-gothic/spike/gcmd.py): exec по сети и хвост scriptslog.txt.

    python w3cmd.py "mcw_nope()"                     протокол жив? ждём fail с 'Warn: Failed to process command'
    python w3cmd.py --fifo "spawnbarrel()"           ванильная команда без seq: ждём ok и 'Spam: ...'
    python w3cmd.py "mcw_ping(1)"                    текст ответа, затем ok/fail
    python w3cmd.py "mcw_echo(#, \\"hi\\")"            '#' первым аргументом -> свежий seq
    python w3cmd.py "mcw_cube(#, \\"-\\")" "mcw_count(#)"  несколько команд по очереди ("-" - cont_crate; "" игра
                                                      не разбирает, кавычки внутри строки - тоже)
    python w3cmd.py -f cmds.txt                       команды из файла, по одной в строке (# - комментарий)
    python w3cmd.py --tail "mcw_logtest(#)"           то же + строки scriptslog.txt за --seconds (1 с)
    python w3cmd.py --tail --seconds 30               просто хвост лога (без --seconds - до Ctrl-C)
    python w3cmd.py --reload                          перекомпилировать скрипты, вывести warn/error
    python w3cmd.py --info                            root path, список модов, конфиг [Scripts]
    python w3cmd.py --listen 60                       пакеты, которые игра шлёт сама (сетевой лог?)
    --port P (по умолчанию 37001, затем 37000), --host, --timeout, --log ПУТЬ к scriptslog.txt,
    --bind-delay S (пауза после BIND перед первым exec; если первый exec даёт TimeoutError - повторить с 1.0)

Код выхода: 0 - всё ok, 1 - хоть одна команда fail, 2 - нет соединения.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from typing import List, Optional, Sequence

import w3net

_HASH = re.compile(r"^(\s*mcw[a-z]?_[A-Za-z0-9_]*\s*\(\s*)#")   # mcw_, mcwp_, mcws_, mcwf_, mcwa_, mcwx_ ...


def expand_seq(cmd: str, cli: w3net.W3Client) -> str:
    """mcw_ping(#) -> mcw_ping(<seq>); так же mcwf_state(#), mcws_ptp(#, ...) и другие mcw?_."""
    return _HASH.sub(lambda m: m.group(1) + str(cli.next_seq()), cmd, count=1)


def describe(r: w3net.ExecResult) -> str:
    tail = f"rtt={r.rtt * 1000:.1f}ms packets={r.packets}" + (f" seq={r.seq}" if r.seq is not None else "")
    if r.source != "net":
        tail += f" via={r.source}"
    if r.near_limit:
        tail += " NEAR_LIMIT"
    if r.truncated:
        tail += " TRUNCATED"
    if r.ok:
        return "ok  " + tail
    if r.failed:
        why = "функции нет или аргументы не разобрались (Warn: Failed to process command)"
    elif r.silent:
        why = "ответ 'Spam' без маркера: Log() не дошёл до ответа exec - смотрите --tail (бенч: --via-log)"
    else:
        why = "нет строки '[MCW#seq] end'"
    return f"fail {why}; {tail}"


def _print_items(prefix: str, items: Sequence[w3net.Item]) -> None:
    kind, parsed = w3net.classify(items)
    vals = [i.value if i.kind != "unknown" else bytes(i.value).hex() for i in items]   # type: ignore[arg-type]
    print(f"{prefix} {time.strftime('%H:%M:%S')} {kind}: {vals}", flush=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    w3net.utf8_stdio()
    ap = argparse.ArgumentParser(description="exec в Ведьмака по отладочному протоколу",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("cmds", nargs="*", help="консольные команды, например mcw_ping(1)")
    ap.add_argument("-f", "--file", help="файл с командами, по одной в строке")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=None, help="порт (по умолчанию 37001, затем 37000)")
    ap.add_argument("--timeout", type=float, default=5.0, help="ожидание ответа на команду, с")
    ap.add_argument("--bind-delay", type=float, default=0.0, help="пауза после BIND перед первым exec, с")
    ap.add_argument("--fifo", action="store_true", help="не искать seq в команде, ждать один пакет")
    ap.add_argument("--tail", action="store_true", help="печатать новые строки scriptslog.txt")
    ap.add_argument("--log", default=None, help="путь к scriptslog.txt (по умолчанию Documents\\The Witcher 3)")
    ap.add_argument("--seconds", type=float, default=None, help="сколько хвостить лог / слушать")
    ap.add_argument("--reload", action="store_true", help="перекомпилировать скрипты в игре")
    ap.add_argument("--info", action="store_true", help="root path, моды, конфиг [Scripts]")
    ap.add_argument("--listen", type=float, default=None, metavar="S", help="печатать пакеты без запроса S секунд")
    a = ap.parse_args(argv)

    cmds: List[str] = list(a.cmds)
    if a.file:
        with open(a.file, encoding="utf-8") as f:
            cmds += [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]

    tail = None
    if a.tail:
        path = a.log or w3net.default_scriptslog_path()
        if not os.path.exists(path):
            print(f"[tail] файла пока нет: {path} (жду появления)", file=sys.stderr)
        tail = w3net.ScriptsLogTail(lambda ln: print(f"[log] {ln}", flush=True), path=path, poll_interval=0.01)
        tail.start()
        tail.ready.wait(2.0)
        if not cmds and not a.reload and not a.info and a.listen is None:
            try:
                end = time.monotonic() + a.seconds if a.seconds is not None else None
                while end is None or time.monotonic() < end:
                    time.sleep(0.05)
            except KeyboardInterrupt:
                pass
            tail.stop()
            return 0

    if not cmds and not a.reload and not a.info and a.listen is None:
        ap.print_usage()
        return 2

    rc = 0
    cli = w3net.W3Client(a.host, a.port, timeout=a.timeout, bind_delay=a.bind_delay)
    try:
        cli.connect()
    except OSError as e:
        print(f"fail {e}", file=sys.stderr)
        if tail:
            tail.stop()
        return 2
    try:
        if a.listen is not None or a.reload:
            cli.on_unsolicited(lambda items: _print_items("[unsolicited]", items))
        if a.info:
            for label, fn in (("root", cli.root_path), ("mods", cli.mod_list), ("Scripts", lambda: cli.var_list("Scripts"))):
                try:
                    print(f"{label}: {fn()}")
                except Exception as e:                       # noqa: BLE001 - печатаем и идём дальше
                    print(f"{label}: fail {type(e).__name__}: {e}")
                    rc = 1
        if a.reload:
            try:
                res = cli.reload_scripts(on_progress=lambda ev: print(f"[reload] {ev}", flush=True),
                                         timeout=max(a.timeout, 300.0))
                if res.success:
                    print("ok  reload")
                else:
                    why = "finished без кода успеха" if res.success is None else "finished != 0"
                    print(f"fail reload: {why}; {len(res.errors)} ошибок, {len(res.warnings)} предупреждений"
                          + ("" if res.errors else " (текст ошибки - в строках [reload]/[unsolicited] выше)"))
                rc |= 0 if res.success else 1
            except Exception as e:                           # noqa: BLE001
                print(f"fail reload: {type(e).__name__}: {e}")
                rc = 1
        for raw in cmds:
            cmd = expand_seq(raw, cli)
            if cmd != raw:
                print(f"> {cmd}")
            try:
                r = cli.exec(cmd, timeout=a.timeout, fifo=a.fifo)
            except w3net.CommandTooLong as e:
                print(f"fail {e}")
                rc = 1
                continue
            except TimeoutError as e:
                print(f"fail {type(e).__name__}: {e}")
                rc = 1
                continue
            except ConnectionError as e:
                print(f"fail {e}")
                return 2
            if r.text:
                print(r.text)
            print(describe(r), flush=True)
            rc |= 0 if r.ok else 1
        wait = a.listen if a.listen is not None else (a.seconds if a.seconds is not None else (1.0 if tail else 0.0))
        if wait:
            try:
                time.sleep(wait)
            except KeyboardInterrupt:
                pass
    finally:
        cli.close()
        if tail:
            tail.stop()
    return rc


if __name__ == "__main__":
    sys.exit(main())
