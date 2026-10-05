"""Список крафта для горячих клавиш: ответ мода recipes{r:[[рецепт, предмет, штук, сколько раз, сетка], ...]} и курсор.

Только данные и текст для mcw_notify. Сам крафт решает мод (craft{recipe, n} -> ack{made, crafts, why}).
После обновления списка курсор остаётся на том же рецепте, а если его больше нет — на той же позиции.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Recipe:
    id: str          # minecraft:oak_planks
    item: str        # minecraft:oak_planks
    count: int       # штук за один крафт
    max: int         # сколько раз подряд хватит ингредиентов
    grid: int        # 2 — в инвентаре, 3 — нужен верстак


def short(name: str) -> str:
    return name.split(":", 1)[1] if name.startswith("minecraft:") else name


class CraftMenu:
    def __init__(self) -> None:
        self.open = False
        self.items: list[Recipe] = []
        self.i = 0
        self.table: Optional[list] = None
        self.req_id: Optional[int] = None        # recipes в полёте: его ответ и есть список
        self.notify_on_reply = False             # показать текущий пункт, когда придёт ответ
        self.stale = False                       # инвентарь поменялся: список перезапросить

    def set_list(self, m: dict) -> None:
        cur = self.current()
        items = []
        for r in m.get("r") or []:
            if isinstance(r, list) and len(r) >= 5:
                items.append(Recipe(str(r[0]), str(r[1]), int(r[2]), int(r[3]), int(r[4])))
        self.items = items
        self.table = m.get("table")
        if cur is not None:
            for k, r in enumerate(items):
                if r.id == cur.id:
                    self.i = k
                    break
            else:
                self.i = min(self.i, max(0, len(items) - 1))
        else:
            self.i = 0

    def current(self) -> Optional[Recipe]:
        return self.items[self.i] if 0 <= self.i < len(self.items) else None

    def move(self, d: int) -> None:
        if self.items:
            self.i = (self.i + d) % len(self.items)

    def text(self) -> str:
        r = self.current()
        if r is None:
            return "MCW craft: nothing to craft from the inventory"
        where = "" if r.grid == 2 else (" (table)" if self.table else " (needs table)")
        return f"MCW craft {self.i + 1}/{len(self.items)}: {short(r.item)} x{r.count}, can {r.max}{where}"
