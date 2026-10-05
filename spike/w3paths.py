"""Где стоят Ведьмак 3, REDkit, Minecraft и JDK: переменные окружения, библиотеки Steam, GOG, рантаймы лаунчера
Minecraft. Только читает реестр и файлы.

Порядок поиска папки игры: MCW_W3_DIR -> библиотеки Steam (appmanifest_292030.acf) -> GOG (реестр) -> None.
REDkit: MCW_REDKIT_DIR -> библиотеки Steam (appmanifest_2684660.acf) -> None. Найденная папка проверяется по exe
(witcher3.exe, wcc_lite.exe), иначе считается, что её нет. JDK 21+ (Gradle и Loom, сервер и клиент MC): MCW_JAVA_HOME
-> JAVA_HOME -> рантайм лаунчера Minecraft (java-runtime-delta — полный JDK 21 с компилятором) -> Program Files ->
java в PATH.

    python w3paths.py            # JSON (только ASCII — PowerShell 5.1 читает его без порчи кириллицы):
                                 # game, store, redkit, documents, steam, mc_jar, java
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import sys

W3_APPID = "292030"
REDKIT_APPID = "2684660"
MC_VERSION = "1.21.11"
# GOG: Witcher 3 GOTY и обычное издание (HKLM\SOFTWARE\WOW6432Node\GOG.com\Games\<id>, значение path)
GOG_IDS = ("1495134320", "1207664663", "1207664643")
STEAM_DEFAULT = r"C:\Program Files (x86)\Steam"

W3_EXE = os.path.join("bin", "x64_dx12", "witcher3.exe")
WCC_EXE = os.path.join("bin", "x64_RedKit", "wcc_lite.exe")


def _reg(root: str, key: str, value: str) -> str | None:
    if sys.platform != "win32":
        return None
    import winreg
    hive = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}[root]
    try:
        with winreg.OpenKey(hive, key) as k:
            v, _ = winreg.QueryValueEx(k, value)
            return str(v)
    except OSError:
        return None


def steam_root() -> str | None:
    for p in (_reg("HKCU", r"Software\Valve\Steam", "SteamPath"),
              _reg("HKLM", r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
              STEAM_DEFAULT):
        if p and os.path.isdir(p):
            return os.path.realpath(p)             # реестр хранит путь в нижнем регистре; realpath — как на диске
    return None


def vdf_values(text: str, key: str) -> list[str]:
    """Все значения "key" "value" из vdf/acf (вложенность не важна: путь библиотеки, installdir)."""
    return [m.group(1).replace("\\\\", "\\") for m in re.finditer(r'"' + re.escape(key) + r'"\s+"((?:[^"\\]|\\.)*)"', text)]


def steam_libraries(root: str | None = None) -> list[str]:
    root = root or steam_root()
    if not root:
        return []
    libs = [root]
    vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
    try:
        with open(vdf, encoding="utf-8", errors="replace") as f:
            libs += vdf_values(f.read(), "path")
    except OSError:
        pass
    out: list[str] = []
    for p in libs:
        n = os.path.normcase(os.path.normpath(p))
        if os.path.isdir(p) and n not in (os.path.normcase(x) for x in out):
            out.append(os.path.realpath(p))
    return out


def steam_app_dir(appid: str, libraries: list[str] | None = None) -> str | None:
    for lib in libraries if libraries is not None else steam_libraries():
        acf = os.path.join(lib, "steamapps", f"appmanifest_{appid}.acf")
        try:
            with open(acf, encoding="utf-8", errors="replace") as f:
                inst = vdf_values(f.read(), "installdir")
        except OSError:
            continue
        if inst:
            d = os.path.join(lib, "steamapps", "common", inst[0])
            if os.path.isdir(d):
                return d
    return None


def gog_dir() -> str | None:
    for gid in GOG_IDS:
        p = _reg("HKLM", rf"SOFTWARE\WOW6432Node\GOG.com\Games\{gid}", "path")
        if p and os.path.isdir(p):
            return os.path.normpath(p)
    return None


def _valid(d: str | None, exe: str) -> str | None:
    return d if d and os.path.isfile(os.path.join(d, exe)) else None


def game_dir() -> str | None:
    env = os.environ.get("MCW_W3_DIR")
    if env:
        return _valid(env, W3_EXE)
    return _valid(steam_app_dir(W3_APPID), W3_EXE) or _valid(gog_dir(), W3_EXE)


def game_store(d: str | None = None) -> str:
    """'steam', 'gog' или 'other' — как запускать игру."""
    d = d or game_dir()
    if not d:
        return "other"
    if os.path.isfile(os.path.join(os.path.dirname(os.path.dirname(d)), f"appmanifest_{W3_APPID}.acf")):
        return "steam"
    if os.path.isfile(os.path.join(d, "goggame-1495134320.info")) or os.path.isfile(os.path.join(d, "goggame-1207664663.info")):
        return "gog"
    return "other"


def redkit_dir() -> str | None:
    env = os.environ.get("MCW_REDKIT_DIR")
    if env:
        return _valid(env, WCC_EXE)
    return _valid(steam_app_dir(REDKIT_APPID), WCC_EXE)


def documents_dir() -> str:
    """Папка «Документы» (у OneDrive она бывает не в профиле): реестр User Shell Folders, иначе ~/Documents."""
    p = _reg("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders", "Personal")
    if p:
        p = os.path.expandvars(p)
        if os.path.isdir(p):
            return p
    return os.path.join(os.path.expanduser("~"), "Documents")


def mc_jar(version: str = MC_VERSION) -> str | None:
    """Клиентский jar Minecraft: MCW_MC_JAR -> лаунчер (%APPDATA%\\.minecraft\\versions) -> кэш Loom (~/.gradle)."""
    cands = [os.environ.get("MCW_MC_JAR", ""),
             os.path.join(os.environ.get("APPDATA", ""), ".minecraft", "versions", version, f"{version}.jar"),
             os.path.join(os.path.expanduser("~"), ".gradle", "caches", "fabric-loom", version, "minecraft-client.jar")]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def java_version(home: str) -> tuple[int, bool] | None:
    """(старшая версия, есть ли компилятор) по файлу release; None — не JDK/JRE или файла нет."""
    try:
        with open(os.path.join(home, "release"), encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    m = re.search(r'^JAVA_VERSION="(\d+)', text, re.M)
    if not m or not os.path.isfile(os.path.join(home, "bin", "java.exe" if sys.platform == "win32" else "java")):
        return None
    mods = re.search(r'^MODULES="([^"]*)"', text, re.M)
    return int(m.group(1)), (mods is None or "jdk.compiler" in mods.group(1).split())


def java_candidates() -> list[str]:
    out = [os.environ.get("JAVA_HOME", "")]
    appdata, local = os.environ.get("APPDATA", ""), os.environ.get("LOCALAPPDATA", "")
    for base in (os.path.join(appdata, ".minecraft", "runtime"),
                 os.path.join(local, "Packages", "Microsoft.4297127D64EC6_8wekyb3d8bbwe", "LocalCache", "Local", "runtime")):
        out += sorted(glob.glob(os.path.join(base, "java-runtime-*", "*", "java-runtime-*")), reverse=True)
    for pf in {os.environ.get("ProgramFiles", r"C:\Program Files"), r"C:\Program Files"}:
        for vendor in ("Eclipse Adoptium", "Java", "Microsoft", "Zulu", "BellSoft", "Amazon Corretto", "Semeru"):
            out += sorted(glob.glob(os.path.join(pf, vendor, "*")), reverse=True)
    exe = shutil.which("java")
    if exe:
        out.append(os.path.dirname(os.path.dirname(os.path.realpath(exe))))
    return [c for c in out if c]


def java_home(min_major: int = 21) -> str | None:
    """JDK для Gradle: MCW_JAVA_HOME как есть (если там bin\\java), иначе первый с компилятором и версией >= min_major,
    21 — раньше более новых (под ним проверено)."""
    env = os.environ.get("MCW_JAVA_HOME")
    if env:
        return env if os.path.isfile(os.path.join(env, "bin", "java.exe" if sys.platform == "win32" else "java")) else None
    good = []
    for i, c in enumerate(java_candidates()):
        v = java_version(c)
        if v and v[0] >= min_major and v[1]:
            good.append((v[0] != 21, i, os.path.normpath(c)))
    return min(good)[2] if good else None


def main() -> None:
    game = game_dir()
    print(json.dumps({"game": game, "store": game_store(game) if game else None, "redkit": redkit_dir(),
                      "documents": documents_dir(), "steam": steam_root(), "mc_jar": mc_jar(), "java": java_home()},
                     ensure_ascii=True, indent=1))


if __name__ == "__main__":
    main()
