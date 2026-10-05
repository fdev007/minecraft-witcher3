"""Соединения моста. Подключение — в отдельном потоке (на Windows отказ на localhost приходит только через ~2 с,
главный цикл ждать не должен), чтение — потоки-демоны. Всё входящее уходит в очередь событий моста с номером
поколения соединения: события старого соединения после переподключения мост узнаёт и выбрасывает.

    ("mc_sock", gen, sock) ("mc_fail", gen, err) ("mc", gen, msg) ("mc_down", gen, err)
    ("cl_sock", gen, sock) ("cl_fail", gen, err) ("cl", gen, msg) ("cl_down", gen, err)   (клиент MC, McLink tag="cl")
    ("w3_up", gen, client) ("w3_fail", gen, err) ("w3_res", gen, seq, future) ("w3_down", gen, err)
"""
from __future__ import annotations

import json
import queue
import socket
import threading
from typing import Any, Optional

import spike_path  # noqa: F401
import w3net


class LineFramer:
    """Байты -> JSON-объекты: строка может прийти частями, несколько строк — одним куском, UTF-8 символ — разрезанным
    между кусками (как mc/fake_bridge.LineFramer)."""

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


def encode(msg: dict[str, Any]) -> bytes:
    return (json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


class McLink:
    """Мост <-> Fabric-мод: JSON-строки по TCP. Отправка — из главного потока (sendall с таймаутом: недописанная строка
    рвёт поток, такое соединение закрываем, мост переподключится и получит снапшот). tag — префикс событий: "mc" —
    серверный мод, "cl" — скрытый клиент (mcclient), протокол тот же."""

    def __init__(self, host: str, port: int, q: queue.Queue, connect_timeout: float = 3.0,
                 send_timeout: float = 5.0, tag: str = "mc") -> None:
        self.host, self.port, self.q = host, port, q
        self.tag = tag
        self.connect_timeout = connect_timeout
        self.send_timeout = send_timeout
        self.gen = 0
        self.sock: Optional[socket.socket] = None
        self.connecting = False
        self.bytes_sent = 0
        self.msgs_sent = 0

    @property
    def up(self) -> bool:
        return self.sock is not None

    def start_connect(self) -> None:
        self.gen += 1
        self.connecting = True
        threading.Thread(target=self._connect, args=(self.gen,), name=self.tag + "-connect", daemon=True).start()

    def _connect(self, gen: int) -> None:
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(self.send_timeout)
        except OSError as e:
            self.q.put((self.tag + "_fail", gen, e))
            return
        self.q.put((self.tag + "_sock", gen, sock))

    def attach(self, gen: int, sock: socket.socket) -> bool:
        """Главный поток, на <tag>_sock. False — сокет от устаревшей попытки (закрыт)."""
        self.connecting = False
        if gen != self.gen or self.sock is not None:
            sock.close()
            return False
        self.sock = sock
        threading.Thread(target=self._read, args=(gen, sock), name=self.tag + "-read", daemon=True).start()
        return True

    def failed(self, gen: int) -> bool:
        if gen == self.gen:
            self.connecting = False
            return True
        return False

    def _read(self, gen: int, sock: socket.socket) -> None:
        fr = LineFramer()
        err: Optional[BaseException] = None
        try:
            while True:
                try:
                    data = sock.recv(1 << 16)
                except socket.timeout:
                    continue
                if not data:
                    err = ConnectionError("другая сторона закрыла соединение")
                    break
                for m in fr.feed(data):
                    self.q.put((self.tag, gen, m))
        except (OSError, ValueError) as e:        # ValueError: битый JSON — поток не разобрать
            err = e
        self.q.put((self.tag + "_down", gen, err))

    def send(self, msg: dict[str, Any]) -> bool:
        sock = self.sock
        if sock is None:
            return False
        data = encode(msg)
        try:
            sock.sendall(data)
        except OSError as e:
            self.close()
            self.q.put((self.tag + "_down", self.gen, e))
            return False
        self.bytes_sent += len(data)
        self.msgs_sent += 1
        return True

    def close(self) -> None:
        sock, self.sock = self.sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()


class W3Link:
    """Мост <-> Ведьмак поверх spike/w3net.W3Client: конвейер exec с сопоставлением по [MCW#seq]. Результат каждого
    вызова приходит в очередь как ("w3_res", gen, seq, future)."""

    def __init__(self, host: str, port: int, q: queue.Queue, *, timeout: float = 2.0, connect_timeout: float = 3.0,
                 scriptslog: str = "") -> None:
        self.host, self.port, self.q = host, port, q
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.scriptslog = scriptslog
        self.gen = 0
        self.client: Optional[w3net.W3Client] = None
        self.connecting = False

    @property
    def up(self) -> bool:
        return self.client is not None

    def start_connect(self) -> None:
        self.gen += 1
        self.connecting = True
        threading.Thread(target=self._connect, args=(self.gen,), name="w3-connect", daemon=True).start()

    def _connect(self, gen: int) -> None:
        # только явный порт: без него W3Client пробует 37001, затем 37000
        client = w3net.W3Client(self.host, port=self.port, timeout=self.timeout, connect_timeout=self.connect_timeout)
        client.on_disconnect(lambda exc, g=gen: self.q.put(("w3_down", g, exc)))
        try:
            client.connect()
            if self.scriptslog:
                client.attach_scriptslog(None if self.scriptslog == "auto" else self.scriptslog)
        except Exception as e:  # noqa: BLE001 — любая ошибка подключения = повторить позже
            client.close()
            self.q.put(("w3_fail", gen, e))
            return
        self.q.put(("w3_up", gen, client))

    def attach(self, gen: int, client: w3net.W3Client) -> bool:
        self.connecting = False
        if gen != self.gen or self.client is not None:
            client.close()
            return False
        self.client = client
        return True

    def failed(self, gen: int) -> bool:
        if gen == self.gen:
            self.connecting = False
            return True
        return False

    def next_seq(self) -> int:
        if self.client is None:
            raise ConnectionError("Ведьмак не подключён")
        return self.client.next_seq()

    def exec(self, seq: int, cmd: str, timeout: float) -> None:
        """Отправить готовую mcw-команду с этим seq. Ошибка отправки тоже придёт в очередь (future с исключением);
        ConnectionError бросается сразу, если соединение уже мертво."""
        client = self.client
        if client is None:
            raise ConnectionError("Ведьмак не подключён")
        gen = self.gen
        fut = client.exec_async(cmd, seq=seq, timeout=timeout)
        fut.add_done_callback(lambda f, g=gen, s=seq: self.q.put(("w3_res", g, s, f)))

    def call(self, func: str, *args: object, timeout: float) -> int:
        seq = self.next_seq()
        self.exec(seq, w3net.build_call(func, seq, *args), timeout)
        return seq

    def close(self) -> None:
        client, self.client = self.client, None
        if client is not None:
            client.close()
