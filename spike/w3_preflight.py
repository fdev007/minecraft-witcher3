"""Проверка перед спайком и после него: что видит движок Witcher 3 5.0 и что запущено. Только чтение.

    python w3_preflight.py [--game <папка игры>] [--watch 1.5] [--json]

Ничего не пишет и игру не запускает. К портам не подключается: кто слушает 37001/37000 — по netstat
(подключение могло бы занять единственный слот отладочного сервера). Печатает факты и булевы значения, в конце —
список «внимание», если что-то расходится с ожидаемым (версия exe, кэш скриптов, флаги запуска, порт на 0.0.0.0).
Требуется Python 3.12+, Windows.
"""
import argparse
import configparser
import csv
import ctypes
import json
import mmap
import os
import re
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

import w3paths

GAME = Path(w3paths.game_dir() or r"C:\Program Files (x86)\Steam\steamapps\common\The Witcher 3")
APPID = "292030"
OUR_MODS = ("modMCW", "modMCWProbe")
PORTS = (37001, 37000)                              # 37001 игра, 37000 редактор (REDkit)
EXPECT_EXE = "5.0.0.1044392"                        # hotfix 5.00c
EXPECT_BUILDID = "25646871"
EXPECT_REDSCRIPTS = 6625059                         # cookedfinal.redscripts 5.00c, не трогать
FLAGS = ("-net", "-debugscripts", "-disablemods", "-disablemodio", "-forcescriptcompilation",
         "-verifyscriptsandexit", "--launcher-skip")
PROCS = ("witcher3.exe", "REDprelauncher.exe", "REDlauncher.exe")


# ---- мелкие помощники ---------------------------------------------------------------------------------------
def documents_dir() -> Path:
    """Папка «Документы» с учётом перенаправления (OneDrive): SHGetFolderPathW(CSIDL_PERSONAL)."""
    buf = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
        return Path(buf.value)
    return Path(os.environ["USERPROFILE"]) / "Documents"


def ini(path: Path) -> configparser.RawConfigParser:
    cp = configparser.RawConfigParser(strict=False, allow_no_value=True, interpolation=None)
    cp.optionxform = str
    if path.exists():
        try:
            cp.read(path, encoding="utf-8-sig")
        except (configparser.Error, UnicodeDecodeError) as e:
            print(f"  ! не разобрать {path}: {e}")
    return cp


def split_cmdline(cmd: str) -> list[str]:
    """Командная строка -> токены по правилам Windows (CommandLineToArgvW)."""
    if not cmd:
        return []
    f = ctypes.windll.shell32.CommandLineToArgvW
    f.restype = ctypes.POINTER(wintypes.LPWSTR)
    f.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    n = ctypes.c_int(0)
    argv = f(cmd, ctypes.byref(n))
    if not argv:
        return cmd.split()
    try:
        return [argv[i] for i in range(n.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(argv)


def file_version(path: Path) -> str | None:
    """FileVersion из ресурса VS_FIXEDFILEINFO, например '5.0.0.1044392'."""
    ver = ctypes.windll.version
    size = ver.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(str(path), 0, size, buf):
        return None
    ptr, ln = ctypes.c_void_p(), wintypes.UINT()
    # Строковая FileVersion: в 5.0 номер билда целиком в младшем DWORD ('5.0.0.1044392'), поэтому
    # четвёрка из VS_FIXEDFILEINFO (hi.lo.hi.lo) дала бы '5.0.15.61352'
    if ver.VerQueryValueW(buf, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(ln)) and ln.value >= 4:
        lang, cp = ctypes.cast(ptr, ctypes.POINTER(wintypes.WORD * 2)).contents
        key = f"\\StringFileInfo\\{lang:04x}{cp:04x}\\FileVersion"
        if ver.VerQueryValueW(buf, key, ctypes.byref(ptr), ctypes.byref(ln)) and ln.value:
            s = ctypes.wstring_at(ptr, ln.value)          # '5.0.0.1044392(Build Machine)'
            m = re.match(r"\s*(\d+(?:\.\d+)+)", s)
            return m.group(1) if m else s.rstrip("\x00").strip()
    if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(ln)) or not ln.value:
        return None
    ms, ls = ctypes.cast(ptr, ctypes.POINTER(wintypes.DWORD * 13)).contents[2:4]
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls}"


def exe_label(path: Path) -> str | None:
    """Метка вида 'v 5.00c' из строк exe (так же её пишет LastLaunchVersion)."""
    if path.stat().st_size == 0:
        return None
    with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
        hit = re.search(rb"\x00(v \d\.\d\d[a-z]?)\x00", m)
        return hit.group(1).decode() if hit else None


def acf_values(path: Path, keys: tuple[str, ...]) -> dict:
    if not path.exists():
        return {}
    txt = path.read_text(encoding="utf-8", errors="replace")
    return {k: m.group(1) for k in keys if (m := re.search(rf'"{k}"\s+"([^"]*)"', txt))}


def ws_encoding(p: Path) -> str:
    head = p.read_bytes()
    if head[:3] == b"\xef\xbb\xbf":
        return "utf8-bom"
    if head[:2] in (b"\xff\xfe", b"\xfe\xff") or (len(head) > 3 and head[1:4:2] == b"\x00\x00"):
        return "utf16"
    try:
        head.decode("utf-8")
        return "utf8"
    except UnicodeDecodeError:
        return "not-utf8"


def run(cmd: list[str], timeout=20) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return r.stdout.decode("utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"<error {e}>"


def processes() -> list[dict]:
    flt = " or ".join(f"Name='{p}'" for p in PROCS)
    ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
          f"@(Get-CimInstance Win32_Process -Filter \"{flt}\" | "
          "Select-Object ProcessId,Name,CommandLine) | ConvertTo-Json -Compress")
    out = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps]).strip()
    if not out or out.startswith("<error"):
        return []
    data = json.loads(out)
    return data if isinstance(data, list) else [data]


def listeners(ports=PORTS) -> list[dict]:
    """Слушающие и подключённые сокеты на портах по `netstat -ano`. Состояние не читаем (оно локализуется):
    слушающий сокет — тот, у кого внешний адрес 0.0.0.0:0 / [::]:0."""
    out = run(["netstat", "-ano", "-p", "TCP"]) + run(["netstat", "-ano", "-p", "TCPv6"])
    rows = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] != "TCP":
            continue
        local, remote, pid = parts[1], parts[2], parts[-1]
        addr, _, port = local.rpartition(":")
        if not port.isdigit() or int(port) not in ports:
            continue
        listening = remote in ("0.0.0.0:0", "[::]:0")
        rows.append(dict(port=int(port), addr=addr, remote=remote, pid=int(pid), listening=listening))
    return rows


def pid_names() -> dict[int, str]:
    out = run(["tasklist", "/FO", "CSV", "/NH"])
    return {int(r[1]): r[0] for r in csv.reader(out.splitlines()) if len(r) >= 2 and r[1].isdigit()}


# ---- проверка ----------------------------------------------------------------------------------------------
def collect(game: Path, watch: float) -> tuple[dict, list[str]]:
    docs = documents_dir() / "The Witcher 3"
    steamapps = game.parent.parent                  # ...\steamapps
    f, warn = {}, []

    exe = game / "bin" / "x64_dx12" / "witcher3.exe"
    f["game_dir"] = str(game)
    f["exe_exists"] = exe.exists()
    if exe.exists():
        st = exe.stat()
        f["exe_version"] = file_version(exe)
        f["exe_label"] = exe_label(exe)
        f["exe_size"] = st.st_size
        f["exe_mtime"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))
        if f["exe_version"] != EXPECT_EXE:
            warn.append(f"witcher3.exe {f['exe_version']} != {EXPECT_EXE}: игра обновилась, сверить сигнатуры и протокол")
    man = acf_values(steamapps / f"appmanifest_{APPID}.acf", ("buildid", "LastUpdated", "StateFlags"))
    f["steam_buildid"] = man.get("buildid")
    f["steam_last_updated"] = (time.strftime("%Y-%m-%d %H:%M", time.localtime(int(man["LastUpdated"])))
                               if man.get("LastUpdated", "").isdigit() else None)
    f["steam_state_flags"] = man.get("StateFlags")  # 4 = установлено и актуально
    if man.get("buildid") and man["buildid"] != EXPECT_BUILDID:
        warn.append(f"Steam buildid {man['buildid']} != {EXPECT_BUILDID}")

    # Workshop: движок компилирует скрипты подписок вместе с modMCW (EnabledWorkshop)
    ws_dir = steamapps / "workshop" / "content" / APPID
    f["workshop_content_dir"] = ws_dir.exists()
    f["workshop_items"] = len(list(ws_dir.iterdir())) if ws_dir.exists() else 0
    f["workshop_acf"] = (steamapps / "workshop" / f"appworkshop_{APPID}.acf").exists()
    if f["workshop_items"]:
        warn.append(f"есть подписки Workshop ({f['workshop_items']}): их скрипты компилируются вместе с модом")

    # ASI: dinput8-прокси Ultimate ASI Loader и *.asi рядом с exe
    bin_dir = game / "bin" / "x64_dx12"
    f["dinput8_dll"] = (bin_dir / "dinput8.dll").exists()
    asi = [str(p.relative_to(bin_dir)) for d in (bin_dir, bin_dir / "scripts", bin_dir / "plugins")
           if d.exists() for p in d.glob("*.asi")]
    f["asi_files"] = asi
    if f["dinput8_dll"] or asi:
        warn.append("в bin\\x64_dx12 есть dinput8.dll или *.asi: нативный код в процессе игры")

    # mods\ и наши папки
    mods = game / "mods"
    f["mods_dir"] = mods.exists()
    f["mods"] = {}
    if mods.exists():
        for d in sorted(p for p in mods.iterdir() if p.is_dir()):
            ws = list(d.rglob("*.ws"))
            local = d / "content" / "scripts" / "local"
            enc = {}
            for p in ws:
                enc.setdefault(ws_encoding(p), []).append(p.name)
            f["mods"][d.name] = dict(
                prefix_mod=d.name.lower().startswith("mod"), ws=len(ws),
                ws_outside_local=[str(p.relative_to(d)) for p in ws if local not in p.parents],
                encodings={k: len(v) for k, v in enc.items()},
                bad_encoding=sorted(n for k, v in enc.items() if k != "utf8" for n in v),
                rsblob=len(list(d.rglob("*.rsblob"))) + len(list(d.rglob("*.redscriptsblob"))))
            if f["mods"][d.name]["bad_encoding"]:
                warn.append(f"{d.name}: .ws не в UTF-8 без BOM: {f['mods'][d.name]['bad_encoding']}")
            if f["mods"][d.name]["ws_outside_local"]:
                warn.append(f"{d.name}: .ws вне content\\scripts\\local (подмена ванили?)")
    for m in OUR_MODS:
        f[f"{m}_installed"] = (mods / m / "content" / "scripts" / "local").exists()
    f["dlc_dir"] = (game / "dlc").exists()

    # Кэш скриптов: cookedfinal.redscripts не удалять и не менять
    c0 = game / "content" / "content0"
    rs = c0 / "cookedfinal.redscripts"
    f["cookedfinal_redscripts_size"] = rs.stat().st_size if rs.exists() else None
    f["cookedfinal_redscripts_ok"] = f["cookedfinal_redscripts_size"] == EXPECT_REDSCRIPTS
    f["other_script_caches"] = sorted(p.name for p in c0.glob("*.redscripts*") if p.name != rs.name)
    if not f["cookedfinal_redscripts_ok"]:
        warn.append(f"cookedfinal.redscripts {f['cookedfinal_redscripts_size']} байт, ждали {EXPECT_REDSCRIPTS}")

    # Настройки пользователя (Documents\The Witcher 3)
    us = ini(docs / "dx12user.settings")
    f["docs_dir"] = str(docs)
    for k in ("Enabled", "EnabledLocal", "EnabledWorkshop"):
        v = us.get("ContentManager/Mods", k, fallback=None)
        f[f"mods_{k}"] = v
        if k != "EnabledWorkshop" and v is not None and v.lower() != "true":
            warn.append(f"[ContentManager/Mods] {k}={v}: локальные моды выключены")
    f["DBGConsoleOn_dx12user"] = us.get("General", "DBGConsoleOn", fallback=None)
    f["DBGConsoleOn_general_ini"] = ini(game / "bin" / "config" / "base" / "general.ini").get(
        "General", "DBGConsoleOn", fallback=None)
    f["scripts_section"] = dict(us.items("Scripts")) if us.has_section("Scripts") else {}
    f["LastLaunchVersion"] = (us.get("Marketing", "LastLaunchVersion", fallback=None) or "").strip('"') or None
    f["LastSeenVersion"] = us.get("StartupFlow", "LastSeenVersion", fallback=None)
    f["ModioEnabled"] = us.get("Gameplay", "ModioEnabled", fallback=None)
    if f.get("exe_label") and f["LastLaunchVersion"] and f["LastLaunchVersion"] != f["exe_label"]:
        warn.append(f"LastLaunchVersion {f['LastLaunchVersion']} != {f['exe_label']}: на этой версии игру ещё не "
                    "запускали, возможны окна лаунчера/соглашений")
    ms = docs / "mods.settings"
    msi = ini(ms)
    f["mods_settings"] = {sec: dict(msi.items(sec)) for sec in msi.sections()} if ms.exists() else None
    inp = docs / "input.settings"
    f["input_settings_mcw_actions"] = (inp.read_text(encoding="utf-8", errors="replace").count("Action=MCW")
                                       if inp.exists() else None)

    # Лог скриптов
    log_dir = f["scripts_section"].get("DebugScriptsLogDir") or str(docs)
    log = Path(log_dir) / "scriptslog.txt"
    f["scriptslog"] = str(log)
    f["scriptslog_exists"] = log.exists()
    if log.exists():
        s0 = log.stat().st_size
        f["scriptslog_size"] = s0
        f["scriptslog_mtime"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(log.stat().st_mtime))
        if watch > 0:
            time.sleep(watch)
            f["scriptslog_growth_bytes"] = log.stat().st_size - s0

    # Процессы и флаги запуска
    procs = processes()
    f["processes"] = []
    for p in procs:
        cmd = p.get("CommandLine")
        toks = [t.lower() for t in split_cmdline(cmd or "")[1:]]
        f["processes"].append(dict(pid=p["ProcessId"], name=p["Name"], cmdline_read=cmd is not None, args=toks,
                                   flags={fl: fl in toks for fl in FLAGS}))
    game_procs = [p for p in f["processes"] if p["name"].lower() == "witcher3.exe"]
    f["game_running"] = bool(game_procs)
    for p in game_procs:
        if not p["cmdline_read"]:
            warn.append(f"cmdline witcher3.exe pid {p['pid']} не прочитан (игра запущена с другими правами?)")
        elif not (p["flags"]["-net"] and p["flags"]["-debugscripts"]):
            warn.append(f"witcher3.exe pid {p['pid']} запущен без -net/-debugscripts")
        if p["flags"]["-disablemods"]:
            warn.append(f"witcher3.exe pid {p['pid']} запущен с -disablemods: моды не грузятся")

    # Порты отладочного сервера
    names = pid_names()
    socks = listeners()
    for s in socks:
        s["process"] = names.get(s["pid"])
    f["debug_ports"] = socks
    for s in socks:
        if s["listening"] and s["addr"] in ("0.0.0.0", "[::]"):
            warn.append(f"порт {s['port']} слушает {s['addr']} ({s['process']}): exec доступен из локальной сети, "
                        "закрыть входящие брандмауэром")
    if f["game_running"] and not any(s["listening"] for s in socks):
        warn.append("игра запущена, но 37001/37000 никто не слушает (нет -net или ещё грузится)")
    return f, warn


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--game", type=Path, default=GAME)
    ap.add_argument("--watch", type=float, default=1.5, help="секунд следить за ростом scriptslog.txt (0 — нет)")
    ap.add_argument("--json", action="store_true", help="вывести факты одним JSON")
    a = ap.parse_args()
    if sys.platform != "win32":
        sys.exit("только Windows")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # в пайпе по умолчанию cp1252
    facts, warn = collect(a.game, a.watch)
    if a.json:
        print(json.dumps(dict(facts=facts, warnings=warn), ensure_ascii=False, indent=1))
        return
    for k, v in facts.items():
        if isinstance(v, (dict, list)) and v:
            print(f"{k}:")
            for item in (v.items() if isinstance(v, dict) else enumerate(v)):
                print(f"  {item[0]}: {item[1]}")
        else:
            print(f"{k}: {v}")
    print(f"ВНИМАНИЕ ({len(warn)}):" if warn else "ВНИМАНИЕ: нет")
    for w in warn:
        print(f"  - {w}")


if __name__ == "__main__":
    main()
