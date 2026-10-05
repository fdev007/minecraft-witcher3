"""w3net - клиент отладочного сетевого протокола The Witcher 3 (игра запущена с ``-net -debugscripts``).

Только stdlib. Формат провода взят из rusty_witcher_debugger (rw3d) v0.8.0 @5a0cf7d
(crates/net/src/protocol/{packet,encoding,packet_data}.rs, crates/net/src/messages/*.rs) и сверен
байт-в-байт с собранным rw3d_net (13 видов пакетов, 0 расхождений). Изменение патча 5.0 (rw3d PR #12):
строки в ответах игры приходят в UTF-8 там, где раньше был UTF-16, поэтому разбор принимает оба вида везде.

    packet := DE AD | total_len:u16be | item* | BE EF     total_len считает ВСЕ байты (голова..хвост)
    item   := tag:2 value
        81 08  i8        81 16  i16be     81 32  i32be     71 32  u32be     81 64  i64be
        AC 08  81 16 len:i16be  utf8[len]           len = байты
        9C 16  81 16 len:i16be  utf16be[len * 2]    len = кодовые единицы UTF-16

exec:  запрос  [utf8 Remote][i32 0x12345678][i32 0x81160008][utf8 cmd]
       ответ   [i32 0x12345678][i32 0x81160008][utf8 text]       id запроса НЕТ.

Пределы exec на 5.00c (замер, EXEC_MAX_CMD / EXEC_MAX_REPLY): команда до 2047 байт UTF-8 (длиннее игра молча
выбрасывает - клиент такую не шлёт, CommandTooLong), ответ до 1999 байт (длиннее теряется целиком, хотя функция
отработала), строка Log() длиннее ~1999 символов приходит как '......(truncated).......' (ExecResult.truncated).
Длины i16/u16 в формате провода - только предел протокола.

Сопоставление ответов exec (id нет, поэтому так):
* mcw-команды несут seq первым аргументом (``mcw_ping(17)``), а каждая их строка вывода начинается
  с ``[MCW#17]``; последняя строка ``[MCW#17] end``. Клиент собирает строки по seq до маркера end,
  неважно, пришёл ответ одним пакетом или пакетом на строку.
* Игра исполняет exec по порядку, поэтому пришедшая по сети строка с seq N доказывает, что всё,
  отправленное раньше N, уже отработало: такие ожидания помечаются потерянными (ReplyLost).
* Статус 'Spam: Command executed without errors' может прийти и после строк Log(), и ДО них (rw3d PR #12:
  «exec ... streams its script log output»; порядок и число пакетов на игре не проверены). Пока маркеры по сети не видели ни разу, 'Spam' для seq-команды не завершает её сразу:
  клиент ждёт маркер ещё ``status_grace`` (0,25 с) и только потом отдаёт «тихий» ответ (Log() не в ответе).
  После первого маркера 'Spam' для seq-команды - всегда чужой хвостовой статус и выбрасывается.
* Команды без seq (ванильные ``spawnbarrel()``) сопоставляются FIFO, по одному пакету на команду. После
  таймаута запись удаляется из очереди (становится «сиротой»), так что один потерянный ответ не ломает клиент
  навсегда. Пакет без маркера, пришедший, пока ждёт не начатая seq-команда, а перед ней есть сирота, - поздний
  ответ сироты, seq-команде он не достаётся. Ограничение: ответ, опоздавший дольше таймаута, достанется
  следующей FIFO-команде, если она уже ждёт (съедать его нельзя: если ответ сироты потерян совсем, каждая
  следующая FIFO-команда теряла бы свой). Для всего важного - seq-команды; после потерянного FIFO-ответа
  надёжнее переподключиться.
* Поздние ответы (seq уже снят по таймауту) логируются и выбрасываются; пакеты, не подходящие ни к одному
  ожиданию, уходят в ``on_unsolicited``.

Лог скриптов: rw3d хвостит файл ``Documents/The Witcher 3/scriptslog.txt`` (ScriptsLogTail ниже).
Если Log() внутри exec не попадает в ответ на 5.0, ``W3Client.attach_scriptslog()`` подаёт строки файла
в то же сопоставление по seq, и mcw-команды завершаются по маркеру из файла.
"""
from __future__ import annotations

import codecs
import collections
import itertools
import logging
import os
import random
import re
import select
import socket
import struct
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "GAME_PORT", "EDITOR_PORT", "DEFAULT_PORTS", "ALL_NAMESPACES", "MAX_STR", "MAX_PACKET", "NEAR_LIMIT",
    "EXEC_MAX_CMD", "EXEC_MAX_REPLY", "EXEC_TRUNCATED", "EXEC_STR_FORBIDDEN",
    "Item", "I8", "I16", "I32", "U32", "I64", "U8", "U16", "Name",
    "encode_item", "encode_packet", "decode_items", "decode_packet", "PacketStream", "ProtocolError",
    "CommandTooLong", "ReplyLost", "SendTimeout",
    "msg_bind", "msg_reload", "msg_root_path", "msg_exec", "msg_mod_list", "msg_opcodes", "msg_var_list",
    "msg_var_set", "classify", "fmt_arg", "build_call", "line_seq", "cmd_seq", "check_exec_cmd", "exec_packet_size",
    "ExecResult", "ReloadEvent", "ReloadResult", "W3Client", "ScriptsLogTail", "default_scriptslog_path",
    "utf8_stdio",
]

log = logging.getLogger("w3net")

GAME_PORT = 37001     # отдельно запущенная игра (rw3d connection.rs WitcherPort::Game)
EDITOR_PORT = 37000   # игра внутри REDkit (WitcherPort::Editor)
DEFAULT_PORTS = (GAME_PORT, EDITOR_PORT)

HEAD = b"\xDE\xAD"
TAIL = b"\xBE\xEF"
MIN_PACKET = 6        # голова 2 + длина 2 + хвост 2
MAX_PACKET = 0xFFFF   # поле длины u16
MAX_STR = 0x7FFF      # длина строки - помеченный i16: предел формата провода; у exec практический предел ниже
# Пределы exec, замер на 5.00c 2026-10-03 (байты UTF-8, как идут по проводу):
# * команда (текст cmd, например 'mcw_echo(91027094, "x...")') до 2047 байт исполняется; от 2048 игра молча
#   выбрасывает её: ответа нет совсем, соединение живо, следующие команды работают;
# * ответ (все строки Log() через '\n', как его получает клиент) до 1999 байт приходит; от ~2001 теряется целиком
#   и молча, хотя функция в игре отработала (её строки есть в scriptslog.txt);
# * одна строка Log() длиннее ~1999 символов приходит в ответе как EXEC_TRUNCATED (ответ снова мал и доходит).
EXEC_MAX_CMD = 2047
EXEC_MAX_REPLY = 1999
EXEC_TRUNCATED = "......(truncated)......."
NEAR_LIMIT = EXEC_MAX_REPLY * 9 // 10   # ответ длиннее - предупреждение: вывод дробить

TAG_I8 = b"\x81\x08"
TAG_I16 = b"\x81\x16"
TAG_I32 = b"\x81\x32"
TAG_U32 = b"\x71\x32"
TAG_I64 = b"\x81\x64"
TAG_UTF8 = b"\xAC\x08"
TAG_UTF16 = b"\x9C\x16"

NS_SCRIPT_DEBUGGER = "ScriptDebugger"
NS_SCRIPT_PROFILER = "ScriptProfiler"
NS_SCRIPT_COMPILER = "ScriptCompiler"
NS_SCRIPTS = "scripts"            # на проводе строчными
NS_REMOTE = "Remote"
NS_UTILITY = "Utility"
NS_CONFIG = "Config"
# порядок rw3d v0.8.0 client.rs listen_to_all_namespaces(); WolvenKit-7 и W3MP шлют другой порядок
ALL_NAMESPACES = (NS_CONFIG, NS_REMOTE, NS_SCRIPT_COMPILER, NS_SCRIPT_DEBUGGER, NS_SCRIPT_PROFILER,
                  NS_SCRIPTS, NS_UTILITY)


def _s32(x: int) -> int:
    """Беззнаковый 32-битный литерал -> знаковый i32, который уходит в провод."""
    x &= 0xFFFFFFFF
    return x - (1 << 32) if x >= (1 << 31) else x


EXEC_MAGIC1 = 0x12345678             # requests.rs ExecuteCommandId
EXEC_MAGIC2 = _s32(0x81160008)       # байты 81 16 00 08
CONFIG_MAGIC = _s32(0xCC00CC00)      # requests.rs ConfigVarsId (rw3d)
CONFIG_MAGIC_WK7 = _s32(0x00CC00CC)  # WolvenKit-7: BitConverter.ToInt32([CC,00,CC,00]) на little-endian

EXEC_OK_SILENT = "Spam: Command executed without errors"   # успех, вывода нет
EXEC_FAIL = "Warn: Failed to process command"               # функции нет / аргументы не разобрались
_STATUSES = (EXEC_OK_SILENT, EXEC_FAIL)


class ProtocolError(Exception):
    pass


class CommandTooLong(ValueError):
    """Команда exec длиннее предела игры (EXEC_MAX_CMD) или не влезает в строку протокола (i16) / пакет (u16).
    Ничего не отправлено."""


class ReplyLost(TimeoutError):
    """Ответ уже не придёт: по сети пришёл ответ на более позднюю команду. ``partial`` - что успело прийти."""

    def __init__(self, msg: str, partial: Optional["ExecResult"] = None) -> None:
        super().__init__(msg)
        self.partial = partial


class SendTimeout(TimeoutError):
    """Игра не читала сокет дольше send_timeout; ни один байт пакета не ушёл, соединение цело."""


# --------------------------------------------------------------------------------------------- items

@dataclass(frozen=True)
class Item:
    kind: str      # 'i8' 'i16' 'i32' 'u32' 'i64' 'utf8' 'utf16' 'unknown'
    value: object

    @property
    def text(self) -> Optional[str]:
        return self.value if self.kind in ("utf8", "utf16") else None  # type: ignore[return-value]


def I8(v: int) -> Item: return Item("i8", int(v))
def I16(v: int) -> Item: return Item("i16", int(v))
def I32(v: int) -> Item: return Item("i32", _s32(v))
def U32(v: int) -> Item: return Item("u32", int(v) & 0xFFFFFFFF)
def I64(v: int) -> Item: return Item("i64", int(v))
def U8(s: str) -> Item: return Item("utf8", s)
def U16(s: str) -> Item: return Item("utf16", s)


_INT = {
    "i8": (TAG_I8, ">b", 1),
    "i16": (TAG_I16, ">h", 2),
    "i32": (TAG_I32, ">i", 4),
    "u32": (TAG_U32, ">I", 4),
    "i64": (TAG_I64, ">q", 8),
}
_TAG2INT = {tag: (kind, fmt, size) for kind, (tag, fmt, size) in _INT.items()}


def encode_item(it: Item) -> bytes:
    if it.kind in _INT:
        tag, fmt, _ = _INT[it.kind]
        return tag + struct.pack(fmt, it.value)
    if it.kind == "utf8":
        raw = str(it.value).encode("utf-8")
        n = len(raw)
        if n > MAX_STR:
            raise CommandTooLong(f"строка UTF-8 {n} байт, предел протокола {MAX_STR} (длина i16)")
        return TAG_UTF8 + TAG_I16 + struct.pack(">h", n) + raw
    if it.kind == "utf16":
        raw = str(it.value).encode("utf-16-be")
        n = len(raw) // 2
        if n > MAX_STR:
            raise CommandTooLong(f"строка UTF-16 {n} кодовых единиц, предел {MAX_STR}")
        return TAG_UTF16 + TAG_I16 + struct.pack(">h", n) + raw
    if it.kind == "unknown":
        return bytes(it.value)  # type: ignore[arg-type]
    raise ValueError(f"неизвестный вид поля {it.kind!r}")


def encode_packet(items: Iterable[Item]) -> bytes:
    body = b"".join(encode_item(i) for i in items)
    total = len(body) + MIN_PACKET
    if total > MAX_PACKET:
        raise CommandTooLong(f"пакет {total} байт, предел протокола {MAX_PACKET} (длина u16)")
    return HEAD + struct.pack(">H", total) + body + TAIL


def decode_items(body: bytes) -> List[Item]:
    """Разбор полей между длиной и хвостом.

    Отличие от rw3d: неизвестный тег у rw3d даёт 2-байтовый Unknown и разбор идёт дальше (и врёт);
    здесь весь остаток становится одним полем 'unknown'."""
    out: List[Item] = []
    off, n = 0, len(body)
    while off < n:
        if n - off < 2:
            raise ProtocolError(f"висячий байт на смещении {off}")
        tag = bytes(body[off:off + 2])
        off += 2
        if tag in _TAG2INT:
            kind, fmt, size = _TAG2INT[tag]
            if n - off < size:
                raise ProtocolError(f"обрезанное {kind} на смещении {off}")
            out.append(Item(kind, struct.unpack_from(fmt, body, off)[0]))
            off += size
        elif tag in (TAG_UTF8, TAG_UTF16):
            if n - off < 4 or bytes(body[off:off + 2]) != TAG_I16:
                raise ProtocolError(f"строка без длины i16 на смещении {off}")
            length = struct.unpack_from(">H", body, off + 2)[0]   # длина не бывает отрицательной
            off += 4
            nbytes = length if tag == TAG_UTF8 else length * 2
            if n - off < nbytes:
                raise ProtocolError(f"обрезанная строка ({nbytes} байт) на смещении {off}")
            raw = bytes(body[off:off + nbytes])
            off += nbytes
            if tag == TAG_UTF8:
                out.append(Item("utf8", raw.decode("utf-8", errors="replace")))
            else:
                out.append(Item("utf16", raw.decode("utf-16-be", errors="replace")))
        else:
            out.append(Item("unknown", bytes(body[off - 2:])))
            break
    return out


def decode_packet(data: bytes) -> List[Item]:
    """Разбор ровно одного целого пакета."""
    if len(data) < MIN_PACKET or data[:2] != HEAD:
        raise ProtocolError("плохая голова пакета")
    total = struct.unpack_from(">H", data, 2)[0]
    if total != len(data):
        raise ProtocolError(f"поле длины {total} != {len(data)} байт")
    if data[-2:] != TAIL:
        raise ProtocolError("плохой хвост пакета")
    return decode_items(data[4:-2])


class PacketStream:
    """Сборка пакетов из TCP-потока: разрезанные и склеенные чтения, ресинхронизация на мусоре."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.resyncs = 0
        self.bad_packets = 0
        self.salvaged = 0

    def _valid_at(self, i: int) -> bool:
        """С позиции i в буфере лежит целый пакет: голова, длина, хвост на месте, поля разбираются."""
        buf = self._buf
        if len(buf) - i < MIN_PACKET or buf[i:i + 2] != HEAD:
            return False
        total = struct.unpack_from(">H", buf, i + 2)[0]
        if total < MIN_PACKET or i + total > len(buf) or buf[i + total - 2:i + total] != TAIL:
            return False
        try:
            items = decode_items(bytes(buf[i + 4:i + total - 2]))
        except ProtocolError:
            return False
        return bool(items) and items[0].kind != "unknown"

    def salvage(self) -> List[List[Item]]:
        """Недособранный пакет обещает больше байт, чем пришло, а за его головой уже лежит целый валидный пакет -
        похоже на битую длину (обрезанный ответ 0xFFFF и т.п.). Звать, когда поток замолчал: голова выбрасывается,
        разбор идёт с найденного пакета. Иначе следующие честные ответы ждали бы, пока придут 64 КБ."""
        buf = self._buf
        if len(buf) < 4 or buf[0:2] != HEAD or self._valid_at(0):
            return []
        idx = buf.find(HEAD, 2)
        while idx >= 0:
            if self._valid_at(idx):
                self.resyncs += 1
                self.salvaged += 1
                del buf[:idx]
                return self.feed(b"")
            idx = buf.find(HEAD, idx + 1)
        return []

    def feed(self, data: bytes) -> List[List[Item]]:
        buf = self._buf
        buf += data
        out: List[List[Item]] = []
        while len(buf) >= 2:
            if buf[0:2] != HEAD:
                self.resyncs += 1
                idx = buf.find(HEAD, 1)
                if idx < 0:
                    keep = 1 if buf[-1:] == HEAD[:1] else 0
                    del buf[:len(buf) - keep]
                    break
                del buf[:idx]
                continue
            if len(buf) < 4:
                break
            total = struct.unpack_from(">H", buf, 2)[0]
            if total < MIN_PACKET:
                self.resyncs += 1
                del buf[:2]
                continue
            if len(buf) < total:
                break
            if buf[total - 2:total] != TAIL:
                self.resyncs += 1
                del buf[:2]
                continue
            body = bytes(buf[4:total - 2])
            del buf[:total]
            try:
                out.append(decode_items(body))
            except ProtocolError:
                self.bad_packets += 1
        return out

    @property
    def pending(self) -> int:
        return len(self._buf)


# ------------------------------------------------------------------------------------------ messages

def msg_bind(namespace: str) -> List[Item]:
    return [U8("BIND"), U8(namespace)]                                  # notifications.rs ListenToNamespace


def msg_reload() -> List[Item]:
    return [U8(NS_SCRIPTS), U8("reload")]                               # notifications.rs ReloadScripts


def msg_root_path() -> List[Item]:
    return [U8(NS_SCRIPT_COMPILER), U8("RootPath")]                     # requests.rs ScriptsRootPath


def msg_exec(cmd: str) -> List[Item]:
    return [U8(NS_REMOTE), I32(EXEC_MAGIC1), I32(EXEC_MAGIC2), U8(cmd)]  # requests.rs ExecuteCommand


def msg_mod_list() -> List[Item]:
    return [U8(NS_SCRIPTS), U8("pkgSync")]                              # requests.rs ScriptPackages


def msg_opcodes(func_name: str, class_name: Optional[str] = None) -> List[Item]:
    items = [U8(NS_SCRIPT_DEBUGGER), U8("OpcodeBreakdownRequest"), U16(func_name)]
    return items + ([I8(1), U16(class_name)] if class_name else [I8(0)])


def msg_var_list(section: str = "", name: str = "") -> List[Item]:
    return [U8(NS_CONFIG), I32(CONFIG_MAGIC), U8("list"), U8(section), U8(name)]


def msg_var_set(section: str, name: str, value: str, wk7_magic: bool = False) -> List[Item]:
    """ЭКСПЕРИМЕНТ: в rw3d v0.5.1 помечено 'FIXME not working'. wk7_magic - вариант WolvenKit-7 (00 CC 00 CC)."""
    magic = CONFIG_MAGIC_WK7 if wk7_magic else CONFIG_MAGIC
    return [U8(NS_CONFIG), I32(magic), U8("set"), U8(section), U8(name), U16(value)]


def check_exec_cmd(cmd: str, limit: int = EXEC_MAX_CMD) -> int:
    """Длина команды exec в байтах UTF-8; длиннее limit - CommandTooLong (по умолчанию EXEC_MAX_CMD: длиннее игра
    молча выбрасывает команду)."""
    n = len(cmd.encode("utf-8"))
    if n > limit:
        raise CommandTooLong(f"команда exec {n} байт UTF-8, предел {limit} "
                             f"(игра 5.00c молча выбрасывает команды длиннее {EXEC_MAX_CMD})")
    return n


def exec_packet_size(cmd: str, limit: int = EXEC_MAX_CMD) -> int:
    """Размер пакета exec = 36 байт + длина команды в UTF-8. Команда длиннее limit - CommandTooLong (как у
    exec_async); limit=MAX_STR - только предел протокола."""
    check_exec_cmd(cmd, limit)
    return len(encode_packet(msg_exec(cmd)))


@dataclass
class ReloadEvent:
    kind: str                     # started | log | warn | error | finished | raw (ScriptCompiler незнакомой формы)
    message: str = ""
    line: int = 0
    path: str = ""
    success: Optional[bool] = None
    raw: List[object] = field(default_factory=list)   # поля после подвида, если форма нестандартная

    def __str__(self) -> str:
        tail = f"  raw={self.raw!r}" if self.raw else ""
        if self.kind in ("warn", "error"):
            return f"{self.kind}: {self.path}:{self.line}: {self.message}{tail}"
        if self.kind == "finished":
            ok = {True: "ok", False: "FAILED", None: "без кода"}[self.success]
            return f"finished: {ok}{tail}"
        return f"{self.kind}: {self.message}".rstrip(": ") + tail


def _txt(items: Sequence[Item], i: int) -> Optional[str]:
    return items[i].text if i < len(items) else None


def _is_i32(items: Sequence[Item], i: int, v: int) -> bool:
    return i < len(items) and items[i].kind == "i32" and items[i].value == v


def classify(items: Sequence[Item]) -> Tuple[str, object]:
    """Опознать пакет игра -> клиент. Возвращает (вид, разобранное)."""
    if _is_i32(items, 0, EXEC_MAGIC1) and _is_i32(items, 1, EXEC_MAGIC2) and _txt(items, 2) is not None:
        return "exec", _txt(items, 2)
    ns, sub = _txt(items, 0), _txt(items, 1)
    if ns == NS_SCRIPT_COMPILER and sub is not None:
        if sub == "RootPathConfirm":
            return "root_path", _txt(items, 2)
        rest = list(items[2:])
        raw = [i.value if i.kind != "unknown" else bytes(i.value).hex() for i in rest]  # type: ignore[arg-type]
        if sub == "started":
            return "reload", ReloadEvent("started")
        if sub == "log":
            return "reload", ReloadEvent("log", message=_txt(items, 2) or "")
        if sub in ("warn", "error"):
            if len(items) >= 5 and items[2].kind in _INT and items[3].text is not None and items[4].text is not None:
                return "reload", ReloadEvent(sub, line=int(items[2].value), path=items[3].text,  # type: ignore[arg-type]
                                             message=items[4].text)
            # форма 5.0 могла поменяться: лучшее, что можно, плюс сырые поля - ошибка не должна пропасть из отчёта
            ints = [int(i.value) for i in rest if i.kind in _INT]  # type: ignore[arg-type]
            texts = [i.text for i in rest if i.text is not None]
            path, msg = (texts[0], " ".join(texts[1:])) if len(texts) >= 2 else ("", texts[0] if texts else "")
            return "reload", ReloadEvent(sub, message=msg, line=ints[0] if ints else 0, path=path, raw=raw)
        if sub == "finished":
            # finished + int8: 0 = успех; без поля - неизвестно
            ok = (items[2].value == 0) if len(items) > 2 and items[2].kind in _INT else None
            return "reload", ReloadEvent("finished", success=ok)
        return "reload", ReloadEvent("raw", message=sub, raw=raw)
    if ns == NS_SCRIPTS and sub == "pkgSyncListing" and len(items) >= 3 and items[2].kind == "i32":
        count = int(items[2].value)  # type: ignore[arg-type]
        pkgs = [(_txt(items, 3 + 2 * k) or "", _txt(items, 4 + 2 * k) or "") for k in range(count)]
        return "mod_list", pkgs
    if ns == NS_SCRIPT_DEBUGGER and sub == "OpcodeBreakdownResponse" and len(items) >= 5:
        count = int(items[4].value)  # type: ignore[arg-type]
        rows = []
        for k in range(count):
            line = items[5 + 2 * k].value
            rows.append((int(line), (_txt(items, 6 + 2 * k) or "").split("\n")))  # type: ignore[arg-type]
        return "opcodes", rows
    if _is_i32(items, 0, CONFIG_MAGIC) and _txt(items, 1) == "vars":
        out, i = [], 2
        while i < len(items) and items[i].kind == "i8" and items[i].value != 0:
            dtype = items[i].value
            # порядок на проводе: тип, неизвестное, имя, раздел, значение
            out.append({"type": dtype, "name": _txt(items, i + 2), "section": _txt(items, i + 3),
                        "value": _txt(items, i + 4)})
            i += 5
        return "vars", out
    return "unknown", list(items)


# ----------------------------------------------------------------------------- команды и маркеры

_MARK = re.compile(r"\[MCW#(\d+)\]")
_SEQ_CMD = re.compile(r"^\s*mcwp?_[A-Za-z0-9_]*\s*\(\s*(\d+)\s*[,)]")


def line_seq(line: str) -> Optional[Tuple[int, str]]:
    """'[MCW#17] pong 5' -> (17, 'pong 5'). Берётся ПЕРВЫЙ маркер в строке: LogChannel может
    добавить свой префикс ('[MCW] [MCW#17] ...'), а mcw_echo может нести маркер в самом тексте."""
    m = _MARK.search(line)
    if not m:
        return None
    return int(m.group(1)), line[m.end():].strip()


def cmd_seq(cmd: str) -> Optional[int]:
    """seq из mcw-команды: первый аргумент-целое у функций mcw_* и mcwp_*."""
    m = _SEQ_CMD.match(cmd)
    return int(m.group(1)) if m else None


_TRUNC = re.compile(r"^\.*\(truncated\)\.*$")    # EXEC_TRUNCATED; число точек вокруг не держим за точное


def _truncated(lines: Iterable[str]) -> int:
    """Сколько строк ответа игра заменила на EXEC_TRUNCATED."""
    return sum(1 for ln in lines if _TRUNC.match(ln.strip()))


class Name(str):
    """Аргумент типа name: уходит в одинарных кавычках, как в консоли ('barrel')."""


EXEC_STR_FORBIDDEN = "\"'\r\n"   # символы, с которыми строковый аргумент exec не отправить (см. fmt_arg)


def fmt_arg(v: object) -> str:
    """Python -> аргумент консольной команды. Парсер exec 5.00c (charset-замер 2026-10-03): экранирования нет -
    обратная косая доходит как есть ('a\\\\b' -> StrLen 4), а кавычка " или ' внутри строки и пустая строка ""
    ломают разбор всей команды ('Warn: Failed to process command'; строка из пробела проходит). Кириллица проходит,
    StrLen считает байты UTF-8. Поэтому строка или имя с кавычкой, переводом строки или пустые - ValueError до
    отправки: чистить текст - дело вызывающего."""
    if isinstance(v, Name):
        if not v or any(c in EXEC_STR_FORBIDDEN for c in v):
            raise ValueError(f"недопустимое имя {v!r}: пустое, кавычка или перевод строки")
        return f"'{v}'"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        s = f"{v:.6f}".rstrip("0")
        return s + "0" if s.endswith(".") else s
    if isinstance(v, str):
        if not v:
            raise ValueError('пустой строковый аргумент exec: "" игра не разбирает (вся команда - Warn: Failed); '
                             'для пути по умолчанию у mcw_cube/mcw_stress - "-"')
        if any(c in EXEC_STR_FORBIDDEN for c in v):
            raise ValueError(f"строковый аргумент exec {v[:40]!r}: кавычка или перевод строки - игра не разберёт "
                             f"команду (экранирования у парсера exec нет)")
        return '"' + v + '"'
    raise TypeError(f"аргумент exec типа {type(v).__name__} не поддержан")


def build_call(func: str, *args: object) -> str:
    return f"{func}({', '.join(fmt_arg(a) for a in args)})"


# ------------------------------------------------------------------------------------------- client

@dataclass
class ExecResult:
    cmd: str
    text: str                      # все строки ответа через '\n' (из сети или из лога, см. source)
    rtt: float                     # секунды от отправки до завершения
    seq: Optional[int] = None
    complete: bool = True          # для seq: пришёл '[MCW#seq] end'; для FIFO всегда True
    packets: int = 1
    source: str = "net"            # net | log
    near_limit: bool = False       # какой-то пакет ответа длиннее NEAR_LIMIT
    truncated: bool = False        # игра заменила строку ответа на EXEC_TRUNCATED (Log() длиннее ~1999 символов)

    @property
    def failed(self) -> bool:
        return any(ln.strip() == EXEC_FAIL for ln in self.text.split("\n"))

    @property
    def silent(self) -> bool:
        return self.text.strip() == EXEC_OK_SILENT

    @property
    def ok(self) -> bool:
        return not self.failed and (self.seq is None or self.complete)

    @property
    def lines(self) -> List[str]:
        """Строки вывода без служебных статусов и пустых."""
        out = []
        for ln in self.text.split("\n"):
            s = ln.rstrip("\r")
            if s.strip() and s.strip() not in _STATUSES:
                out.append(s)
        return out

    @property
    def body(self) -> List[str]:
        """Для seq: свои строки без маркера и без 'end' ('pong 123'); без seq - то же, что lines."""
        if self.seq is None:
            return self.lines
        out = []
        for ln in self.lines:
            m = line_seq(ln)
            if m and m[0] == self.seq and m[1] != "end":
                out.append(m[1])
        return out

    def tokens(self, word: str) -> Optional[List[str]]:
        """Первая строка body, начинающаяся со слова word, без него: tokens('pong') -> ['123']."""
        for b in self.body:
            parts = b.split()
            if parts and parts[0] == word:
                return parts[1:]
        return None

    def ints(self, word: str) -> Optional[List[int]]:
        """Только когда после слова одни целые: ints('pong') -> [123], ints('batch') -> [3, 0, 4, 1].
        Для строк вперемешку ('spawned <ok> comps <n> scaled <k>') вернёт None - там kv()."""
        t = self.tokens(word)
        if t is None:
            return None
        try:
            return [int(x) for x in t]
        except ValueError:
            return None

    def kv(self, word: str) -> Optional[Dict[str, object]]:
        """Строка из пар «ключ значение»: kv('spawned') для 'spawned 1 comps 3 scaled 3' ->
        {'spawned': 1, 'comps': 3, 'scaled': 3}. Значение - int, если разбирается, иначе строка;
        у непарного последнего ключа значение None."""
        t = self.tokens(word)
        if t is None:
            return None
        words = [word] + t
        out: Dict[str, object] = {}
        for i in range(0, len(words), 2):
            v: object = words[i + 1] if i + 1 < len(words) else None
            if isinstance(v, str):
                try:
                    v = int(v)
                except ValueError:
                    pass
            out[words[i]] = v
        return out


@dataclass
class ReloadResult:
    success: Optional[bool]
    events: List[ReloadEvent] = field(default_factory=list)

    @property
    def errors(self) -> List[ReloadEvent]:
        return [e for e in self.events if e.kind == "error"]

    @property
    def warnings(self) -> List[ReloadEvent]:
        return [e for e in self.events if e.kind == "warn"]


class _Req:
    """Ожидание ответа. Порядок order = порядок отправки."""
    __slots__ = ("order", "kind", "seq", "cmd", "fut", "t0", "deadline", "timeout", "lines", "log_lines",
                 "packets", "started", "awaiting_log", "near_limit", "res", "cb", "got_status", "grace_deadline",
                 "status_lines", "truncated")

    def __init__(self, order: int, kind: str, cmd: str, seq: Optional[int], timeout: float) -> None:
        self.order, self.kind, self.cmd, self.seq = order, kind, cmd, seq
        self.fut: Future = Future()
        self.t0 = time.perf_counter()
        self.timeout = timeout
        self.deadline = time.monotonic() + timeout
        self.lines: List[str] = []        # из сети
        self.log_lines: List[str] = []    # из scriptslog.txt
        self.packets = 0
        self.started = False              # пришла хоть одна строка со своим маркером по сети
        self.awaiting_log = False         # по сети пришёл 'Spam', ждём маркер из файла
        self.got_status = False           # 'Spam' пришёл раньше строк: ждём маркер до grace_deadline
        self.grace_deadline = 0.0
        self.status_lines: List[str] = []
        self.near_limit = False
        self.truncated = False            # в сетевом ответе была строка EXEC_TRUNCATED
        self.res: Optional[ReloadResult] = None
        self.cb: Optional[Callable[[ReloadEvent], None]] = None


class W3Client:
    """Потокобезопасный клиент: один фоновый поток чтения, отправка из любых потоков, конвейер.

    >>> with W3Client() as w3:                 # 37001, затем 37000
    ...     r = w3.call("mcw_ping")             # seq подставляется сам: mcw_ping(<seq>)
    ...     print(r.ok, r.ints("pong"), r.rtt)
    """

    def __init__(self, host: str = "127.0.0.1", port: Optional[int] = None, *,
                 ports: Sequence[int] = DEFAULT_PORTS, timeout: float = 3.0, connect_timeout: float = 3.0,
                 send_timeout: float = 10.0, namespaces: Sequence[str] = ALL_NAMESPACES,
                 seq_start: Optional[int] = None, bind_delay: float = 0.0, status_grace: float = 0.25,
                 salvage_after: float = 0.25, max_cmd: int = EXEC_MAX_CMD) -> None:
        """connect_timeout 3 с: на Windows отказ на localhost приходит только через ~2 с (повторы SYN).
        bind_delay - пауза после 7 BIND перед первым exec (rw3d без --no-delay ждёт 500+750 мс; нужна ли она игре,
        неизвестно). status_grace - сколько ждать маркер после 'Spam', пока маркеров по сети ещё не видели.
        salvage_after - сколько молчания потока терпеть, прежде чем искать целый пакет за битой длиной.
        max_cmd - команда exec длиннее (байт UTF-8) не отправляется, CommandTooLong; выше EXEC_MAX_CMD - только
        для замера предела (bench_w3 maxlen): игра такую команду молча выбросит."""
        self.host = host
        self.ports = (port,) if port else tuple(ports)
        self.port: Optional[int] = None
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.send_timeout = max(0.05, send_timeout)
        self.namespaces = tuple(namespaces)
        self.bind_delay = max(0.0, bind_delay)
        self.status_grace = max(0.0, status_grace)
        self.salvage_after = salvage_after
        self.max_cmd = max_cmd
        self._seq = itertools.count(seq_start if seq_start is not None else random.randrange(1, 1 << 30))
        self._order = itertools.count(1)
        self._sock: Optional[socket.socket] = None
        self._send_lock = threading.Lock()       # порядок отправок (и регистрации ожиданий)
        self._lock = threading.RLock()           # структуры ожиданий; поток чтения берёт только его
        self._execs: "collections.OrderedDict[int, _Req]" = collections.OrderedDict()
        self._by_seq: Dict[int, _Req] = {}
        self._kinds: Dict[str, Deque[_Req]] = collections.defaultdict(collections.deque)
        self._reloads: List[_Req] = []
        self._expired_seqs: "collections.OrderedDict[int, int]" = collections.OrderedDict()   # seq -> order
        self._fifo_orphans: List[int] = []       # order FIFO-команд, снятых по таймауту
        self._net_markers_seen = False           # Log() доходит до ответа exec
        self._log_fed = False                    # к клиенту подключён хвост scriptslog.txt
        self._tails: List["ScriptsLogTail"] = []
        self._raw_cbs: List[Callable[[List[Item]], None]] = []
        self._unsolicited_cbs: List[Callable[[List[Item]], None]] = []
        self._disconnect_cbs: List[Callable[[Optional[BaseException]], None]] = []
        self._reader: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._abort_exc: Optional[BaseException] = None
        self._dead: Optional[BaseException] = None   # соединение было и умерло (или закрыто): новые запросы - сразу ошибка
        self._next_expire = 0.0
        self.connected = False
        self.stream = PacketStream()
        self.bytes_sent = 0
        self.bytes_received = 0
        self.stats: collections.Counter = collections.Counter()

    # -- жизненный цикл
    def connect(self) -> "W3Client":
        errors = []
        sock = None
        for p in self.ports:
            try:
                sock = socket.create_connection((self.host, p), timeout=self.connect_timeout)
                self.port = p
                break
            except ConnectionRefusedError as e:
                errors.append(f"{p}: отказ ({e})")
            except (socket.timeout, TimeoutError):
                errors.append(f"{p}: нет ответа за {self.connect_timeout:.1f} с")
            except OSError as e:
                errors.append(f"{p}: {e}")
        if sock is None:
            raise ConnectionError(f"игра не слушает {self.host} ({'; '.join(errors)}): нет -net -debugscripts, "
                                  f"не тот порт или брандмауэр")
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setblocking(False)
        self._sock = sock
        self._stop.clear()
        self._abort_exc = None
        self._dead = None
        self.stream = PacketStream()
        self.connected = True
        self._reader = threading.Thread(target=self._read_loop, args=(sock,), name="w3net-reader", daemon=True)
        self._reader.start()
        for ns in self.namespaces:
            self.send(msg_bind(ns))
        if self.bind_delay:
            time.sleep(self.bind_delay)
        return self

    def close(self) -> None:
        self._stop.set()
        if self._dead is None:
            self._dead = ConnectionError("клиент закрыт")
        for t in self._tails:
            t.stop()
        self._tails.clear()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        if self._reader is not None and self._reader is not threading.current_thread():
            self._reader.join(timeout=2)
        self._fail_all(ConnectionError("клиент закрыт"))
        self.connected = False

    def __enter__(self) -> "W3Client":
        return self.connect() if not self.connected else self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- обратные вызовы
    def on_raw_packet(self, cb: Callable[[List[Item]], None]) -> None:
        """Каждый разобранный пакет от игры, до сопоставления."""
        self._raw_cbs.append(cb)

    def on_unsolicited(self, cb: Callable[[List[Item]], None]) -> None:
        """Пакеты, не подошедшие ни к одному ожиданию (то, что игра шлёт сама, чужие seq)."""
        self._unsolicited_cbs.append(cb)

    def on_disconnect(self, cb: Callable[[Optional[BaseException]], None]) -> None:
        self._disconnect_cbs.append(cb)

    # -- отправка
    def _check_alive(self) -> None:
        """После обрыва (on_disconnect уже вызван) новый запрос сразу получает ConnectionError, а не TimeoutError
        через timeout+1 с: по нему мост переподключается."""
        if self._dead is not None:
            raise ConnectionError(f"соединение потеряно ({self._dead}); нужен новый connect()")

    def send(self, items: Sequence[Item]) -> int:
        data = encode_packet(items)
        self._check_alive()
        with self._send_lock:
            self._sendall(data)
        return len(data)

    def _sendall(self, data: bytes) -> None:
        """sendall со своим таймаутом через select: зависший приём у игры не роняет короткие таймауты чтения.
        Если пакет ушёл частично, поток порван: соединение закрывается (игру не пересинхронизировать)."""
        sock = self._sock
        if sock is None:
            raise ConnectionError("нет соединения")
        view = memoryview(data)
        sent = 0
        deadline = time.monotonic() + self.send_timeout
        while sent < len(data):
            left = deadline - time.monotonic()
            if left <= 0:
                if sent:
                    self.stats["torn"] += 1
                    exc = ConnectionError(f"таймаут отправки {self.send_timeout:.1f} с посреди пакета "
                                          f"({sent}/{len(data)} байт): соединение закрыто")
                    self._abort(exc)
                    raise exc
                self.stats["send_timeout"] += 1
                raise SendTimeout(f"игра не читает сокет {self.send_timeout:.1f} с; пакет не отправлен")
            try:
                _, w, _ = select.select([], [sock], [], min(left, 0.25))
            except (OSError, ValueError) as e:
                raise ConnectionError(f"сокет закрыт: {e}") from e
            if not w:
                continue
            try:
                sent += sock.send(view[sent:])
            except (BlockingIOError, InterruptedError):
                continue
            except OSError as e:
                raise ConnectionError(f"отправка не удалась: {e}") from e
        self.bytes_sent += len(data)

    def _abort(self, exc: BaseException) -> None:
        self._abort_exc = exc
        sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    # -- exec
    def next_seq(self) -> int:
        return next(self._seq) & 0x3FFFFFFF or 1

    def exec_async(self, cmd: str, seq: Optional[int] = None, timeout: Optional[float] = None,
                   fifo: bool = False) -> Future:
        """Отправить exec; Future -> ExecResult. seq=None: взять из mcw-команды (первый аргумент),
        иначе FIFO. fifo=True - принудительно FIFO. Таймаут снимает ожидание из очереди.
        Сразу бросает CommandTooLong (длиннее max_cmd = EXEC_MAX_CMD байт: игра молча выбросила бы её; или не влезает
        в i16/u16) и ConnectionError (соединение уже потеряно)."""
        check_exec_cmd(cmd, self.max_cmd)        # CommandTooLong до какой-либо регистрации
        data = encode_packet(msg_exec(cmd))
        self._check_alive()
        if not fifo and seq is None:
            seq = cmd_seq(cmd)
        if fifo:
            seq = None
        t = self.timeout if timeout is None else timeout
        with self._send_lock:
            with self._lock:
                if seq is not None and seq in self._by_seq:
                    raise ValueError(f"seq {seq} уже ждёт ответа")
                req = _Req(next(self._order), "exec", cmd, seq, t)
                self._register(req)
            try:
                self._sendall(data)
            except BaseException as e:
                with self._lock:
                    self._drop(req)
                if not req.fut.done():
                    req.fut.set_exception(e)
                return req.fut
        self.stats["exec_sent"] += 1
        return req.fut

    def exec(self, cmd: str, seq: Optional[int] = None, timeout: Optional[float] = None,
             fifo: bool = False) -> ExecResult:
        t = self.timeout if timeout is None else timeout
        fut = self.exec_async(cmd, seq=seq, timeout=t, fifo=fifo)
        return self._wait(fut, t)

    def call_async(self, func: str, *args: object, timeout: Optional[float] = None) -> Future:
        """mcw-вызов с автоматическим seq: call_async('mcw_echo', 'abc') -> mcw_echo(<seq>, "abc")."""
        seq = self.next_seq()
        return self.exec_async(build_call(func, seq, *args), seq=seq, timeout=timeout)

    def call(self, func: str, *args: object, timeout: Optional[float] = None) -> ExecResult:
        t = self.timeout if timeout is None else timeout
        return self._wait(self.call_async(func, *args, timeout=t), t)

    def _wait(self, fut: Future, t: float):
        try:
            return fut.result(t + 1.0)       # таймаут снимает поток чтения; запас на случай, если он умер
        except TimeoutError:
            if fut.done():
                raise
            with self._lock:
                for req in list(self._execs.values()) + [r for q in self._kinds.values() for r in q] + self._reloads:
                    if req.fut is fut:
                        self._drop(req)
            raise

    def _register(self, req: _Req) -> None:
        """Под self._lock. Если поток чтения уже умер (его _fail_all прошёл раньше), ожидание никто не снимет:
        ConnectionError сразу. Поток чтения ставит _dead до _fail_all, так что щели между ними нет."""
        if self._dead is not None:
            raise ConnectionError(f"соединение потеряно ({self._dead}); нужен новый connect()")
        if req.kind == "exec":
            self._execs[req.order] = req
            if req.seq is not None:
                self._by_seq[req.seq] = req
                self._expired_seqs.pop(req.seq, None)
        elif req.kind == "reload":
            self._reloads.append(req)
        else:
            self._kinds[req.kind].append(req)

    # -- прочие запросы (FIFO по виду ответа, как в rw3d Router)
    def _request(self, kind: str, items: Sequence[Item], timeout: Optional[float]) -> object:
        data = encode_packet(items)
        self._check_alive()
        t = self.timeout if timeout is None else timeout
        with self._send_lock:
            with self._lock:
                req = _Req(next(self._order), kind, kind, None, t)
                self._register(req)
            try:
                self._sendall(data)
            except BaseException:
                with self._lock:
                    self._drop(req)
                raise
        return self._wait(req.fut, t)

    def root_path(self, timeout: Optional[float] = None) -> str:
        return self._request("root_path", msg_root_path(), timeout)  # type: ignore[return-value]

    def mod_list(self, timeout: Optional[float] = None) -> List[Tuple[str, str]]:
        return self._request("mod_list", msg_mod_list(), timeout)  # type: ignore[return-value]

    def opcodes(self, func_name: str, class_name: Optional[str] = None, timeout: Optional[float] = None):
        return self._request("opcodes", msg_opcodes(func_name, class_name), timeout)

    def var_list(self, section: str = "", name: str = "", timeout: Optional[float] = None):
        return self._request("vars", msg_var_list(section, name), timeout)

    def var_set(self, section: str, name: str, value: str, wk7_magic: bool = False) -> None:
        """ЭКСПЕРИМЕНТ, ответа не ждём (см. msg_var_set)."""
        self.send(msg_var_set(section, name, value, wk7_magic))

    def reload_scripts(self, on_progress: Optional[Callable[[ReloadEvent], None]] = None,
                       timeout: float = 300.0) -> ReloadResult:
        """Перекомпиляция скриптов в запущенной игре; ждёт ScriptCompiler 'finished' (int8: 0 = успех)."""
        data = encode_packet(msg_reload())
        self._check_alive()
        with self._send_lock:
            with self._lock:
                req = _Req(next(self._order), "reload", "reload", None, timeout)
                req.res, req.cb = ReloadResult(success=None), on_progress
                self._register(req)
            try:
                self._sendall(data)
            except BaseException:
                with self._lock:
                    self._drop(req)
                raise
        return self._wait(req.fut, timeout)

    # -- лог скриптов
    def listen_scripts_log(self, callback: Callable[[str], None], path: Optional[str] = None,
                           poll_interval: float = 0.02, from_end: bool = True) -> "ScriptsLogTail":
        tail = ScriptsLogTail(callback, path=path, poll_interval=poll_interval, from_end=from_end)
        tail.start()
        tail.ready.wait(2.0)
        return tail

    def attach_scriptslog(self, path: Optional[str] = None, poll_interval: float = 0.005) -> "ScriptsLogTail":
        """Подать строки scriptslog.txt в сопоставление по seq (запасной канал, если Log() не в ответе)."""
        self._log_fed = True
        tail = self.listen_scripts_log(self.feed_log_line, path=path, poll_interval=poll_interval)
        self._tails.append(tail)
        return tail

    def feed_log_line(self, line: str) -> None:
        m = line_seq(line)
        if m is None:
            return
        seq, rest = m
        actions: List[tuple] = []
        with self._lock:
            req = self._by_seq.get(seq)
            if req is None:
                return                       # чужое или уже завершённое по сети - тихо
            req.log_lines.append(line)
            if rest == "end":
                self._complete(req, "log", actions)
        self._run(actions)

    # -- чтение
    def _read_loop(self, sock: socket.socket) -> None:
        err: Optional[BaseException] = None
        last_rx = time.monotonic()
        salvage_armed = False                    # новые байты пришли, а пакет так и не собрался
        try:
            while not self._stop.is_set():
                try:
                    r, _, _ = select.select([sock], [], [], 0.05)
                except (OSError, ValueError) as e:
                    err = e
                    break
                if r:
                    try:
                        chunk = sock.recv(65536)
                    except (BlockingIOError, InterruptedError):
                        chunk = None
                    except OSError as e:
                        err = e
                        break
                    if chunk == b"":
                        err = ConnectionError("игра закрыла соединение")
                        break
                    if chunk:
                        self.bytes_received += len(chunk)
                        last_rx = time.monotonic()
                        self._dispatch_all(self.stream.feed(chunk))
                        salvage_armed = self.stream.pending > 0
                elif salvage_armed and time.monotonic() - last_rx >= self.salvage_after:
                    salvage_armed = False                # поток молчит посреди пакета: битая длина?
                    got = self.stream.salvage()
                    if got:
                        self.stats["salvaged"] += 1
                        log.warning("w3net: пакет с битой длиной выброшен, разбор продолжен со следующего")
                        self._dispatch_all(got)
                self._expire_due()
        finally:
            self.connected = False
            if not self._stop.is_set():
                exc = self._abort_exc or err or ConnectionError("соединение потеряно")
                cexc = exc if isinstance(exc, ConnectionError) else ConnectionError(str(exc))
                self._dead = cexc                        # до _fail_all: см. _register
                if self._sock is sock:
                    self._sock = None
                try:
                    sock.close()
                except OSError:
                    pass
                self._fail_all(cexc)
                for cb in list(self._disconnect_cbs):
                    try:
                        cb(exc)
                    except Exception:
                        pass

    def _dispatch_all(self, packets: List[List[Item]]) -> None:
        for items in packets:
            try:
                self._dispatch(items)
            except Exception:                            # чужая ошибка не должна убить поток чтения
                log.exception("w3net: ошибка разбора пакета")

    def _dispatch(self, items: List[Item]) -> None:
        for cb in list(self._raw_cbs):
            try:
                cb(items)
            except Exception:
                pass
        kind, parsed = classify(items)
        actions: List[tuple] = []
        with self._lock:
            if kind == "exec":
                self._on_exec(items, str(parsed), actions)
            elif kind == "reload":
                self._on_reload(items, parsed, actions)  # type: ignore[arg-type]
            else:
                q = self._kinds.get(kind)
                req = q.popleft() if q else None
                if req is None:
                    actions.append(("unsolicited", items))
                else:
                    actions.append(("result", req.fut, parsed))
        self._run(actions)

    def _on_reload(self, items: List[Item], ev: ReloadEvent, actions: List[tuple]) -> None:
        if not self._reloads:
            actions.append(("unsolicited", items))
            return
        for req in list(self._reloads):
            req.res.events.append(ev)  # type: ignore[union-attr]
            if req.cb is not None:
                actions.append(("call", req.cb, ev))
            if ev.kind == "finished":
                req.res.success = ev.success  # type: ignore[union-attr]
                actions.append(("result", req.fut, req.res))
        if ev.kind == "finished":
            self._reloads.clear()

    def _on_exec(self, items: List[Item], text: str, actions: List[tuple]) -> None:
        it = items[2]
        nbytes = len(text.encode("utf-8")) if it.kind == "utf8" else len(text.encode("utf-16-le")) // 2
        near = nbytes >= NEAR_LIMIT
        if near:
            self.stats["near_limit"] += 1
            log.warning("w3net: ответ exec %d байт, ответ длиннее %d игра теряет целиком: дробите вывод",
                        nbytes, EXEC_MAX_REPLY)
        lines = [ln.rstrip("\r") for ln in text.split("\n")]
        ntrunc = _truncated(lines)
        if ntrunc:
            self.stats["truncated"] += ntrunc
            log.warning("w3net: игра заменила %d строк(и) ответа на %r: строка Log() длиннее ~%d символов",
                        ntrunc, EXEC_TRUNCATED, EXEC_MAX_REPLY)
        tagged = [(line_seq(ln), ln) for ln in lines]
        first = next((m[0] for m, _ in tagged if m is not None), None)
        if first is None:
            self._route_plain(items, text, lines, near, actions)
            return
        # строки без маркера прилипают к предыдущему маркеру (ведущие - к первому)
        groups: "collections.OrderedDict[int, List[str]]" = collections.OrderedDict()
        cur = first
        for m, ln in tagged:
            if m is not None:
                cur = m[0]
            groups.setdefault(cur, []).append(ln)
        if all(s not in self._by_seq and s not in self._expired_seqs for s in groups):
            # ни один seq не ждём: если первым в очереди стоит FIFO-ожидание (exec(..., fifo=True)) - пакет его
            head = self._plain_head()
            if head is not None and head.seq is None:
                self._route_plain(items, text, lines, near, actions)
            else:
                actions.append(("unsolicited", items))
            return
        unsolicited = False
        for seq, ls in groups.items():
            unsolicited |= not self._route_seq(seq, ls, near, actions)
        if unsolicited:
            actions.append(("unsolicited", items))

    def _route_seq(self, seq: int, lines: List[str], near: bool, actions: List[tuple]) -> bool:
        """Строки с маркером seq пришли по сети. False - не подошли ни к чему (чужой seq)."""
        req = self._by_seq.get(seq)
        if req is None:
            order = self._expired_seqs.get(seq)
            if order is None:
                return False
            self.stats["late"] += 1
            log.info("w3net: поздний ответ seq %d выброшен: %r", seq, lines[:2])
            self._net_markers_seen = True          # Log() доходит до ответа, просто поздно: 'Spam' дальше - чужой
            self._lose_older(order, actions)
            return True
        self._net_markers_seen = True
        self._lose_older(req.order, actions)
        req.lines.extend(lines)
        req.packets += 1
        req.started = True
        req.near_limit |= near
        req.truncated |= _truncated(lines) > 0
        if any((m := line_seq(ln)) is not None and m[0] == seq and m[1] == "end" for ln in lines):
            self._complete(req, "net", actions)
        return True

    def _plain_head(self) -> Optional[_Req]:
        """Кому достаётся пакет без маркера: первое ожидание exec, кроме ждущих маркер из файла и тех seq-команд,
        у которых статус уже пришёл раньше строк (got_status) - следующий статус уже не их."""
        for r in self._execs.values():
            if r.awaiting_log or (r.got_status and not r.started):
                continue
            return r
        return None

    def _route_plain(self, items: List[Item], text: str, lines: List[str], near: bool,
                     actions: List[tuple]) -> None:
        """Пакет без маркеров: статус 'Spam:'/'Warn:', вывод ванильной команды или то, что игра шлёт сама."""
        head = self._plain_head()
        s = text.strip()
        if head is None:
            if self._fifo_orphans:
                self._fifo_orphans.pop(0)
                self.stats["late"] += 1
                log.info("w3net: поздний FIFO-ответ выброшен: %r", text[:80])
            elif s == EXEC_OK_SILENT and self._net_markers_seen:
                self.stats["stray_status"] += 1
            else:
                actions.append(("unsolicited", items))
            return
        if head.seq is None:                       # FIFO: один пакет = один ответ
            head.lines = lines
            head.packets += 1
            head.near_limit |= near
            head.truncated |= _truncated(lines) > 0
            self._fifo_orphans = [o for o in self._fifo_orphans if o > head.order]
            self._complete(head, "net", actions)
            return
        if head.started:                           # ответ seq-команды уже идёт: всё до 'end' - её
            head.lines.extend(lines)
            head.packets += 1
            head.truncated |= _truncated(lines) > 0
            return
        # seq-команда, своих строк ещё не было. Сирота FIFO, отправленная раньше неё, ещё должна ответ:
        # пакет без маркера - скорее её поздний ответ (свой ответ seq-команды несёт маркеры)
        older = next((o for o in self._fifo_orphans if o < head.order), None)
        if older is not None:
            self._fifo_orphans.remove(older)
            self.stats["late"] += 1
            log.info("w3net: поздний FIFO-ответ перед %r выброшен: %r", head.cmd, text[:80])
            return
        if s == EXEC_FAIL:                         # функции нет или аргументы не разобрались
            head.lines.extend(lines)
            head.packets += 1
            self._complete(head, "net", actions, complete=False)
            return
        if s == EXEC_OK_SILENT:
            if self._net_markers_seen:             # Log() доходит до ответа -> это хвостовой статус прошлой
                self.stats["stray_status"] += 1    # команды (режим «пакет на строку + статус»)
                return
            head.packets += 1
            if self._log_fed:                      # вывод идёт только в файл: ждём маркер оттуда
                head.awaiting_log = True
                return
            head.status_lines = lines
            if self.status_grace <= 0:
                self._complete_silent(head, actions)
                return
            # статус мог прийти раньше строк Log(): ждём маркер ещё status_grace, потом «тихий» ответ
            head.got_status = True
            head.grace_deadline = min(time.monotonic() + self.status_grace, head.deadline)
            return
        actions.append(("unsolicited", items))

    def _complete_silent(self, req: _Req, actions: List[tuple]) -> None:
        """Пришёл только 'Spam', маркера нет: Log() не попадает в ответ exec (или пришёл позже status_grace)."""
        req.lines = list(req.status_lines)
        self.stats["silent"] += 1
        self._complete(req, "net", actions, complete=False)

    def _complete(self, req: _Req, via: str, actions: List[tuple], complete: bool = True) -> None:
        self._drop(req)
        if req.seq is not None:
            self._remember_expired(req.seq, req.order)     # хвосты этого seq - «поздние»
        lines = req.log_lines if via == "log" else req.lines
        res = ExecResult(cmd=req.cmd, text="\n".join(lines), rtt=time.perf_counter() - req.t0, seq=req.seq,
                         complete=complete if req.seq is not None else True, packets=max(1, req.packets),
                         source=via, near_limit=req.near_limit, truncated=req.truncated and via == "net")
        self.stats["exec_done"] += 1
        actions.append(("result", req.fut, res))

    def _lose_older(self, order: int, actions: List[tuple]) -> None:
        """Всё, что отправлено раньше order и ещё ждёт ответа из сети, уже не получит его."""
        for o, req in list(self._execs.items()):
            if o >= order:
                break
            if req.awaiting_log:
                continue
            self._drop(req)
            if req.seq is not None:
                self._remember_expired(req.seq, req.order)
            self.stats["lost"] += 1
            partial = ExecResult(req.cmd, "\n".join(req.lines), time.perf_counter() - req.t0, req.seq,
                                 complete=False, packets=req.packets)
            actions.append(("error", req.fut, ReplyLost(f"ответ на {req.cmd!r} потерян: пришёл ответ на "
                                                        f"более позднюю команду", partial)))
        self._fifo_orphans = [o for o in self._fifo_orphans if o > order]

    def _remember_expired(self, seq: int, order: int) -> None:
        self._expired_seqs[seq] = order
        while len(self._expired_seqs) > 4096:
            self._expired_seqs.popitem(last=False)

    def _drop(self, req: _Req) -> None:
        if req.kind == "exec":
            self._execs.pop(req.order, None)
            if req.seq is not None and self._by_seq.get(req.seq) is req:
                del self._by_seq[req.seq]
        elif req.kind == "reload":
            if req in self._reloads:
                self._reloads.remove(req)
        else:
            q = self._kinds.get(req.kind)
            if q is not None and req in q:
                q.remove(req)

    def _expire_due(self) -> None:
        now = time.monotonic()
        if now < self._next_expire:
            return
        self._next_expire = now + 0.02
        actions: List[tuple] = []
        with self._lock:
            reqs = list(self._execs.values()) + [r for q in self._kinds.values() for r in q] + list(self._reloads)
            for req in reqs:
                if req.got_status and not req.started and not req.awaiting_log and now >= req.grace_deadline:
                    self._complete_silent(req, actions)
                    continue
                if now < req.deadline:
                    continue
                self._drop(req)
                self.stats["expired"] += 1
                if req.kind == "exec":
                    if req.seq is not None:
                        self._remember_expired(req.seq, req.order)
                    elif not req.packets:
                        self._fifo_orphans.append(req.order)
                actions.append(("error", req.fut, TimeoutError(f"нет ответа на {req.cmd!r} за {req.timeout:.1f} с")))
        self._run(actions)

    def _run(self, actions: List[tuple]) -> None:
        for a in actions:
            try:
                if a[0] == "result":
                    if not a[1].done():
                        a[1].set_result(a[2])
                elif a[0] == "error":
                    if not a[1].done():
                        a[1].set_exception(a[2])
                elif a[0] == "call":
                    a[1](a[2])
                elif a[0] == "unsolicited":
                    self.stats["unsolicited"] += 1
                    for cb in list(self._unsolicited_cbs):
                        cb(a[1])
            except Exception:
                log.exception("w3net: ошибка в обратном вызове")

    def _fail_all(self, exc: BaseException) -> None:
        with self._lock:
            reqs = list(self._execs.values()) + [r for q in self._kinds.values() for r in q] + list(self._reloads)
            self._execs.clear()
            self._by_seq.clear()
            self._kinds.clear()
            self._reloads.clear()
            self._fifo_orphans.clear()
        for req in reqs:
            if not req.fut.done():
                req.fut.set_exception(exc)

    @property
    def in_flight(self) -> int:
        with self._lock:
            return len(self._execs) + sum(len(q) for q in self._kinds.values()) + len(self._reloads)


def utf8_stdio() -> None:
    """Для CLI: в трубу (агент, лог) - UTF-8, в консоль - как есть, но без падения на непечатном."""
    import sys
    for st in (sys.stdout, sys.stderr):
        if not hasattr(st, "reconfigure"):
            continue
        try:
            if st.isatty():
                st.reconfigure(errors="replace")
            else:
                st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# -------------------------------------------------------------------------------------- scripts log

def _documents_dir() -> str:
    try:
        import winreg  # type: ignore
        key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            val, _ = winreg.QueryValueEx(k, "Personal")
        return os.path.expandvars(val)
    except Exception:
        return os.path.join(os.path.expanduser("~"), "Documents")


def default_scriptslog_path() -> str:
    """Documents/The Witcher 3/scriptslog.txt (rw3d scriptslog/src/lib.rs; под Proton - в префиксе)."""
    return os.path.join(_documents_dir(), "The Witcher 3", "scriptslog.txt")


if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _CreateFileW = _k32.CreateFileW
    _CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                             wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _CreateFileW.restype = wintypes.HANDLE
    _INVALID_HANDLE = wintypes.HANDLE(-1).value

    def _open_shared(path: str):
        """Только чтение, FILE_SHARE_READ|WRITE|DELETE: игра может писать, усекать и удалять файл."""
        h = _CreateFileW(path, 0x80000000, 0x7, None, 3, 0x80, None)  # GENERIC_READ, share all, OPEN_EXISTING
        if h is None or h == _INVALID_HANDLE:
            err = ctypes.get_last_error()
            if err in (2, 3):
                raise FileNotFoundError(path)
            raise OSError(err, ctypes.FormatError(err), path)
        fd = msvcrt.open_osfhandle(h, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        return os.fdopen(fd, "rb")
else:
    def _open_shared(path: str):
        return open(path, "rb")


class ScriptsLogTail(threading.Thread):
    """Фоновый ``tail -f`` scriptslog.txt. Только чтение: файл не создаётся, не блокируется, не усекается.

    Файл открывается заново на каждом опросе со всеми флагами разделения, хэндл между опросами не держится.
    ``callback(line)`` получает каждую целую строку без CR/LF. Переживает: появление файла позже (тогда читает
    с начала), усечение (перезапуск игры), пересоздание (другой file id) и перезапись с другим началом файла.
    Не заметит: усечение и перезапись того же начала длиннее старого размера между двумя опросами.
    Кодировка по BOM (UTF-16 LE/BE, UTF-8), без BOM - UTF-8 с заменой (как rw3d from_utf8_lossy)."""

    HEAD_CHECK = 64

    def __init__(self, callback: Callable[[str], None], path: Optional[str] = None,
                 poll_interval: float = 0.02, from_end: bool = True) -> None:
        super().__init__(name="w3net-scriptslog", daemon=True)
        self.callback = callback
        self.path = path or default_scriptslog_path()
        self.poll_interval = poll_interval
        self.from_end = from_end
        self.ready = threading.Event()        # первый опрос сделан: с этого момента строки не теряются
        self._stop_evt = threading.Event()
        self.lines_seen = 0
        self.reopens = 0
        self.polls = 0
        self.encoding: Optional[str] = None

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_evt.set()
        if self is not threading.current_thread() and self.is_alive():
            self.join(timeout)

    def _nap(self) -> None:
        # time.sleep на Windows точный (3.11+); Event.wait округляет до тика ~15,6 мс
        time.sleep(self.poll_interval)

    def _emit(self, text: str) -> str:
        *lines, rest = text.split("\n")
        for ln in lines:
            self.lines_seen += 1
            try:
                self.callback(ln.rstrip("\r"))
            except Exception:
                pass
        return rest

    @staticmethod
    def _detect(bom: bytes) -> Tuple[str, int]:
        if bom[:2] == b"\xff\xfe":
            return "utf-16-le", 2
        if bom[:2] == b"\xfe\xff":
            return "utf-16-be", 2
        if bom[:3] == b"\xef\xbb\xbf":
            return "utf-8", 3
        return "utf-8", 0

    def run(self) -> None:
        ident = None
        pos = 0
        pending = ""
        decoder = None
        head = b""
        skip_existing: Optional[bool] = None       # решается на первом опросе: был непустой файл -> пропустить
        positioned = False
        while not self._stop_evt.is_set():
            self.polls += 1
            try:
                f = _open_shared(self.path)
            except OSError:
                if skip_existing is None:
                    skip_existing = False
                self.ready.set()
                self._nap()
                continue
            got = False
            try:
                st = os.fstat(f.fileno())
                size = st.st_size
                if skip_existing is None:
                    skip_existing = self.from_end and size > 0
                now_ident = (st.st_dev, st.st_ino)
                reset = ident is None or now_ident != ident or size < pos
                if not reset and head:
                    n = min(len(head), size)
                    f.seek(0)
                    reset = f.read(n) != head[:n]
                if reset and size > 0:
                    f.seek(0)
                    enc, start = self._detect(f.read(3))
                    self.encoding = enc
                    decoder = codecs.getincrementaldecoder(enc)(errors="replace")
                    if not positioned and skip_existing:
                        pos = max(start, size - ((size - start) % 2 if enc.startswith("utf-16") else 0))
                    else:
                        pos = start
                    if positioned:
                        self.reopens += 1
                    positioned = True
                    pending = ""
                    ident = now_ident
                    head = b""
                elif reset:
                    ident, head, pos = None, b"", 0     # пустой файл: ждём первых байт (и BOM)
                if ident is not None and len(head) < self.HEAD_CHECK and size > len(head):
                    f.seek(0)
                    head = f.read(min(self.HEAD_CHECK, size))
                if ident is not None and size > pos:
                    f.seek(pos)
                    data = f.read(size - pos)
                    pos += len(data)
                    pending = self._emit(pending + decoder.decode(data))  # type: ignore[union-attr]
                    got = bool(data)
            except OSError:
                pass
            finally:
                f.close()
            self.ready.set()
            if not got:
                self._nap()
