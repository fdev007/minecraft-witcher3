"""Конфиг моста: значения по умолчанию здесь, поверх — bridge.toml (или свой --config), поверх — bridge.local.toml
(свои правки машины, не в git; лайт-режим без модов REDkit туда пишет mcw.ps1 install из корня), поверх — флаги CLI.

Неизвестный ключ в файле — ошибка (опечатка не должна молча оставить значение по умолчанию).
"""
from __future__ import annotations

import copy
import os
import tomllib

import spike_path  # noqa: F401
import coords

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FILE = os.path.join(HERE, "bridge.toml")
LOCAL_FILE = os.path.join(HERE, "bridge.local.toml")

# Значения по умолчанию. Комментарии к каждому ключу — в bridge.toml.
DEFAULTS: dict = {
    "w3": {
        "host": "127.0.0.1",
        "port": 37001,
        "connect_timeout_s": 3.0,
        "reconnect_min_s": 0.5,
        "reconnect_max_s": 10.0,
        "max_inflight": 8,
        "scriptslog": "",
    },
    "poll": {
        "hz": 15.0,
        "depth": 2,
        "timeout_s": 2.0,
        "pos_keepalive_s": 1.0,
    },
    "mc": {
        "host": "127.0.0.1",
        "port": 25600,
        "radius_chunks": 4,
        "connect_timeout_s": 3.0,
        "reconnect_min_s": 0.5,
        "reconnect_max_s": 10.0,
        "freeze_on_pause": True,
    },
    "coords": {
        "y_offset": coords.Y_OFFSET,
    },
    "display": {
        "radius_m": 32.0,
        "max_entities": 2000,
        "reselect_move_m": 1.0,
        "reselect_interval_s": 0.1,
        "default_template": 12,
        # 2..12 — блоки modMCWBlocks (spike/templates.json, source "modMCWBlocks"), 12 — _unknown
        "templates": {"minecraft:grass_block": 2, "minecraft:dirt": 3, "minecraft:coarse_dirt": 3, "minecraft:stone": 4,
                      "minecraft:cobblestone": 5, "minecraft:oak_log": 6, "minecraft:oak_planks": 7, "minecraft:sand": 8,
                      "minecraft:crafting_table": 9, "minecraft:oak_leaves": 10, "minecraft:glass": 11},
        "hide": ["minecraft:water", "minecraft:lava", "minecraft:bubble_column", "minecraft:fire", "minecraft:soul_fire",
                 "minecraft:light", "minecraft:structure_void", "minecraft:moving_piston"],
        "transparent": ["minecraft:water", "minecraft:lava", "minecraft:*glass*", "minecraft:*leaves", "minecraft:ice",
                        "minecraft:iron_bars", "minecraft:*_fence", "minecraft:*_fence_gate", "minecraft:*_wall",
                        "minecraft:*door", "minecraft:*_slab", "minecraft:*_stairs", "minecraft:ladder",
                        "minecraft:*torch", "minecraft:*rail", "minecraft:snow", "minecraft:*_carpet",
                        "minecraft:*sign", "minecraft:short_grass", "minecraft:tall_grass", "minecraft:*fern",
                        "minecraft:*_sapling", "minecraft:*flower*", "minecraft:dandelion", "minecraft:poppy",
                        "minecraft:*_tulip", "minecraft:*mushroom", "minecraft:sugar_cane", "minecraft:wheat",
                        "minecraft:chest", "minecraft:*_bed", "minecraft:cobweb", "minecraft:scaffolding"],
    },
    "batch": {
        "bytes": 1900,
        "inflight": 2,
        "timeout_s": 5.0,
    },
    "ground": {
        "enabled": True,
        "radius_m": 24.0,
        "rate_hz": 80.0,
        "inflight": 4,
        "depth": 3,
        "clear_dist_m": 96.0,
        "retry_none_s": 10.0,
        "send_interval_s": 0.25,
        "max_columns": 1024,
        "timeout_s": 2.0,
        "pause_rtt_ms": 250.0,
        "batch_cols": 48,
        "batch_hz": 12.0,
        "batch_inflight": 2,
        "lookahead_s": 1.0,
        "lookahead_max_m": 16.0,
    },
    "supervise": {
        "enabled": False,                    # включает bridge.toml: тесты и чужие конфиги процессы не трогают
        "server": True,
        "client": True,
        "server_down_s": 20.0,
        "client_down_s": 15.0,
        "min_interval_s": 90.0,
        "timeout_s": 300.0,
        "server_script": "../mc/run_server.ps1",
        "client_script": "../mcclient/run_client.ps1",
    },
    "skin": {
        "enabled": True,
    },
    "solid": {
        "enabled": True,
        "radius_m": 8.0,
        "rate_hz": 30.0,
        "inflight": 2,
        "per_call": 6,
        "rescan_dz_m": 4.0,
        "door_radius_m": 5.0,
        "door_every_s": 0.5,
        "keep_m": 40.0,
        "max_columns": 256,
        "timeout_s": 2.0,
    },
    "resync": {
        "count_every_s": 2.0,
        "min_interval_s": 5.0,
        "max_interval_s": 60.0,
        "clearall_timeout_s": 5.0,
    },
    "act": {
        "look_timeout_s": 1.0,
        "eps_m": 0.05,
    },
    "input": {
        "enabled": True,
        "process": "witcher3.exe",
        "poll_hz": 100.0,
        "settings": "",
        "bind": {
            "toggle": ["F2"],
            "break": ["MOUSE4", "8"],
            "place": ["MOUSE5", "9"],
            "slot_prev": ["LBRACKET"],
            "slot_next": ["RBRACKET"],
            **{f"slot{i}": [f"NUMPAD{i}"] for i in range(1, 10)},
            "craft_menu": ["F3"],
            "craft_prev": ["PAGEUP", "SUBTRACT"],
            "craft_next": ["PAGEDOWN", "ADD"],
            "craft_do": ["0", "NUMPAD0"],
            "summon": ["F6"],
            "fp": ["F4"],
            "inventory": ["E"],
            "horse": ["X"],
        },
    },
    "ents": {
        "enabled": True,
        "radius_m": 32.0,
        "max": 32,
        "hz": 10.0,
        "timeout_s": 2.0,
        "move_eps_m": 0.05,
        "yaw_eps_deg": 5.0,
        "default_template": 0,
        "templates": {"minecraft:creeper": 0, "minecraft:horse": -1},
        "items_notify": True,
        "items_radius_m": 8.0,
        "fuse_notify": True,
        "summon_type": "minecraft:creeper",
        "summon_dist_m": 6.0,
    },
    "combat": {
        "enabled": True,
        "radius_m": 24.0,
        "max": 16,
        "hz": 30.0,
        "timeout_s": 2.0,
        "npc_mc_hp": 20.0,
        "ref_mc_dmg": 6.0,
        "heavy_dmg": 7.0,
        "hurt_mult": 2.5,
        "min_hurt": 0.25,
        "impact_delay_s": 0.2,
    },
    "arrows": {
        "enabled": True,
        "inflight": 2,
        "walls": True,
    },
    "fp": {
        "sens": 30,
        "start_on": False,
        "fov": 0.0,
    },
    "client": {
        "enabled": True,
        "host": "127.0.0.1",
        "port": 25601,
        "connect_timeout_s": 3.0,
        "reconnect_min_s": 0.5,
        "reconnect_max_s": 5.0,
        "overlay": "../overlay/mcw_overlay.exe",
        "window": "MCW Client",
        "host_window": "The Witcher 3",
        "block_witcher": True,
        "pass": {"MOUSE1": "btn:0", "MOUSE2": "btn:1", "MOUSE3": "btn:2", "Q": "key:81", "1": "key:49", "2": "key:50", "3": "key:51", "4": "key:52", "5": "key:53", "6": "key:54", "7": "key:55", "8": "key:56", "9": "key:57"},
        "drive": True,
        "pass_drive": {"W": "key:87", "A": "key:65", "S": "key:83", "D": "key:68", "SPACE": "key:32",
                       "LSHIFT": "key:340", "LCTRL": "key:341"},
        "fov_axis": "v",
        "cursor_scale": 1.0,
    },
    "notify": {
        "enabled": True,
        "min_interval_s": 0.75,
        "queue": 4,
        "hp": True,
        "inv": True,
        "inv_window_s": 1.5,
    },
    "boom": {
        "enabled": True,
        "radius_m": 48.0,
        "min_interval_s": 1.0,
    },
    "log": {
        "file": "logs/bridge.log",
        "level": "INFO",
        "metrics_every_s": 10.0,
    },
}

# Таблицы, где ключи задаёт пользователь (имя блока, действие): их не сверяем со списком DEFAULTS.
FREE_TABLES = {("display", "templates"), ("input", "bind"), ("ents", "templates"), ("client", "pass"),
               ("client", "pass_drive")}


class Section(dict):
    """dict с доступом через точку: cfg.w3.port."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(name) from e


def _wrap(d: dict, path: tuple = ()) -> Section:
    out = Section()
    for k, v in d.items():
        out[k] = _wrap(v, path + (k,)) if isinstance(v, dict) and (path + (k,)) not in FREE_TABLES else v
    return out


def _merge(base: dict, over: dict, path: tuple = ()) -> None:
    for k, v in over.items():
        where = ".".join(path + (k,))
        if path + (k,) in FREE_TABLES:
            if not isinstance(v, dict):
                raise ValueError(f"{where}: ждали таблицу")
            base[k] = dict(v) if k in ("templates", "pass", "pass_drive") else {**base[k], **v}
            continue
        if k not in base:
            raise ValueError(f"неизвестный ключ конфига: {where}")
        if isinstance(base[k], dict):
            if not isinstance(v, dict):
                raise ValueError(f"{where}: ждали таблицу")
            _merge(base[k], v, path + (k,))
        else:
            if isinstance(base[k], float) and isinstance(v, int) and not isinstance(v, bool):
                v = float(v)
            if type(v) is not type(base[k]):
                raise ValueError(f"{where}: ждали {type(base[k]).__name__}, а не {type(v).__name__}")
            base[k] = v


def validate(cfg: Section) -> None:
    """Пределы, которые нельзя нарушать независимо от файла."""
    if not 256 <= cfg.batch.bytes <= coords.MAX_CMD:
        raise ValueError(f"batch.bytes {cfg.batch.bytes} вне [256, {coords.MAX_CMD}] "
                         f"(команду exec длиннее {coords.MAX_CMD} байт игра молча выбрасывает)")
    if cfg.w3.port in (0,) or cfg.mc.port in (0,) or cfg.client.port in (0,):
        raise ValueError("порт 0")
    for table in ("pass", "pass_drive"):
        for k, v in cfg.client[table].items():
            if not isinstance(v, str):
                raise ValueError(f"client.{table}[{k!r}]: ждали строку 'btn:<n>' или 'key:<GLFW_KEY_*>'")
    if cfg.client.fov_axis not in ("h", "v"):
        raise ValueError("client.fov_axis: 'h' (FOV камеры Ведьмака горизонтальный) или 'v' (вертикальный)")
    if not 0.0 <= cfg.fp.fov <= 150.0:
        raise ValueError("fp.fov в [0, 150] градусов (0 — не трогать камеру)")
    for key, minimum in (("hz", 0.1), ("depth", 1)):
        if cfg.poll[key] < minimum:
            raise ValueError(f"poll.{key} < {minimum}")
    if cfg.batch.inflight < 1 or cfg.w3.max_inflight < 2:
        raise ValueError("batch.inflight >= 1, w3.max_inflight >= 2")
    if cfg.display.max_entities < 0 or cfg.display.radius_m <= 0:
        raise ValueError("display.max_entities >= 0, display.radius_m > 0")
    if cfg.ground.depth < 1:
        raise ValueError("ground.depth >= 1")
    if not isinstance(cfg.fp.sens, int) or not 1 <= cfg.fp.sens <= 1000:
        raise ValueError("fp.sens — целое в [1, 1000] (сотые градуса на единицу оси мыши)")
    for k, v in cfg.display.templates.items():
        if not isinstance(v, int) or isinstance(v, bool):
            raise ValueError(f"display.templates[{k!r}]: ждали целый индекс шаблона mcw_batch")
    for k, v in cfg.ents.templates.items():
        if not isinstance(v, int) or isinstance(v, bool):
            raise ValueError(f"ents.templates[{k!r}]: ждали целый индекс шаблона (-1 — не показывать)")
    cb = cfg.combat
    if not 1 <= cb.max <= 30 or cb.hz <= 0 or cb.radius_m <= 0 or cb.npc_mc_hp <= 0 or cb.hurt_mult < 0:
        raise ValueError("combat.max в [1, 30] (ответ mcw_npcs <= 1999 байт), combat.hz, radius_m, npc_mc_hp > 0, "
                         "hurt_mult >= 0")
    if not 1 <= cfg.ground.batch_cols <= 64 or cfg.ground.batch_hz <= 0 or cfg.ground.batch_inflight < 1:
        raise ValueError("ground.batch_cols в [1, 64] (mcw_grounds), ground.batch_hz > 0, ground.batch_inflight >= 1")
    if cfg.arrows.inflight < 1:
        raise ValueError("arrows.inflight >= 1")
    if not 0 <= cfg.ents.max <= coords.ENT_SLOTS or cfg.ents.hz <= 0 or cfg.ents.radius_m <= 0:
        raise ValueError(f"ents.max в [0, {coords.ENT_SLOTS}] (слоты mcw_ents), ents.hz > 0, ents.radius_m > 0")


def load(path: str | None = None, overrides: dict | None = None, local: bool = True) -> Section:
    """path=None — bridge.toml рядом, если есть, и поверх него bridge.local.toml, если есть (local=False — без него).
    overrides — {"w3": {"port": 37101}, ...} из CLI или тестов."""
    data = copy.deepcopy(DEFAULTS)
    file = path if path is not None else (DEFAULT_FILE if os.path.exists(DEFAULT_FILE) else None)
    files = [file] if file else []
    if path is None and local and os.path.exists(LOCAL_FILE):
        files.append(LOCAL_FILE)
    for name in files:
        with open(name, "rb") as f:
            try:
                _merge(data, tomllib.load(f))
            except ValueError as e:
                raise ValueError(f"{name}: {e}") from e
    if overrides:
        _merge(data, overrides)
    cfg = _wrap(data)
    cfg["_file"] = " + ".join(files)
    validate(cfg)
    return cfg


def resolve(path: str) -> str:
    """Относительный путь из конфига — от папки моста."""
    return path if os.path.isabs(path) else os.path.join(HERE, path)
