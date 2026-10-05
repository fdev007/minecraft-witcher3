"""Статическая проверка WitcherScript (.ws) до первой компиляции в игре (The Witcher 3 5.0).

    python ws_lint.py mod\\modMCW\\content\\scripts\\local\\mcw_spike.ws ^
                      mod\\modMCWProbe\\content\\scripts\\local\\mcw_probe.ws
    python ws_lint.py --rules         # правила и откуда они взяты
    python ws_lint.py --selftest       # юнит-тесты на синтетических кусках .ws

Индекс объявлений строится по ванили <игра>\\content\\content0\\scripts (только чтение, ~16 МБ, несколько секунд),
глобальные объекты — из <игра>\\bin\\redscripts.ini [globals]. Все файлы из командной строки разбираются вместе:
моды видят функции друг друга. Файл внутри папки ванили проверяется теми же правилами, а дубликаты с другими
файлами ищутся только вне его (так меряем ложные срабатывания; на всех 1527 ванильных файлах 5.00c — 0 ERROR
и 4 WARN о нативах без объявления: ForceInteractionUpdate x3, OpenDebugWindows).
Это не компилятор: типы выражений не выводятся. Тип известен только у простого получателя (локальная переменная,
параметр, поле, this, глобальный объект вроде thePlayer): его метод ищется в цепочке его класса. У прочих
получателей (foo().Bar(), a[i].Bar()) — в любом классе с таким методом.
Код возврата 1, если есть ERROR.
"""
import argparse
import os
import re
import sys
import time
import unittest
from collections import defaultdict, namedtuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import w3paths  # noqa: E402

GAME = w3paths.game_dir() or r"C:\Program Files (x86)\Steam\steamapps\common\The Witcher 3"
SCRIPTS = os.path.join(GAME, r"content\content0\scripts")
EXE = os.path.join(GAME, r"bin\x64_dx12\witcher3.exe")
REDSCRIPTS_INI = os.path.join(GAME, r"bin\redscripts.ini")

# Код правила -> (что проверяем, откуда известно). Смещения — строки компилятора в witcher3.exe 5.0.0.1044392.
RULES = {
    "bom": ("файл в UTF-8 без BOM", "ваниль 5.0: 0 из 1527 .ws с BOM; требование модов 5.0 (PLAN)"),
    "encoding": ("файл декодируется как UTF-8", "ваниль 5.0 целиком UTF-8"),
    "literal": ("незакрытая строка/имя/комментарий", "лексер"),
    "balance": ("баланс () [] {}", "exe 0x3632368 'syntax error'"),
    "missing-semicolon": ("конец выражения (имя, литерал, ')' вызова, ']') и сразу начало следующего или '}' — "
                          "пропущена ';' или оператор. ')' условия if/while/for/switch и каста (T) концом не считается",
                          "exe 0x3632368 'syntax error'; ваниль: 0 таких мест на 1527 файлах"),
    "var-placement": ("var только в начале тела функции, до любого оператора",
                      "WIDE invalid-local-var-placement; ваниль: 0 нарушений на 1527 файлах (этот линтер)"),
    "local-dup": ("локальная переменная дважды или с именем параметра",
                  "exe 0x362b448 'Variable %s is defined more than once', 0x362b488"),
    "global-var": ("глобальных переменных нет, var на верхнем уровне только после @addField",
                   "WIDE global-scope-var-decl; глобальные объекты заданы движком в bin\\redscripts.ini [globals]"),
    "unknown-type": ("тип в var/параметре/возврате/касте (T)/new/array<>/map<> объявлен в ванили или моде либо "
                     "встречается в объявлениях ванили (нативные без .ws: EPersistanceMode, Float)",
                     "exe 0x36286f0 'Unknown type '%s' for property', 0x36300f8 '... used in casting', 0x3630220 "
                     "'Class '%s' is unknown'. WARN, если имя есть строкой в witcher3.exe"),
    "undeclared": ("идентификатор в теле — локальная, параметр, поле цепочки класса (с @addField), глобальный "
                   "объект из redscripts.ini, значение enum, тип или имя, которое ваниль употребляет без "
                   "объявления (нативные enum: PM_DontPersist в temp.ws:6921)",
                   "exe 0x36306e8 'I dont know any '%s''. WARN, если имя есть строкой в witcher3.exe"),
    "unknown-call": ("вызываемая функция/метод объявлены в ванили или в моде",
                     "exe 0x3630da8 'Could not find function', 0x36306e8 'I dont know any'. WARN, если имя есть "
                     "строкой в witcher3.exe: ваниль зовёт 3 таких натива без объявления (newDoor.ws:223)"),
    "not-member": ("у получателя известного класса метод есть в цепочке этого класса (SetScale есть у CComponent, "
                   "не у CEntity)", "exe 0x3630700 ''%s' is not a member of '%s''; ваниль: 0 таких мест, а по классу "
                   "получателя сужены 32783 из 70551 вызовов методов"),
    "method-as-global": ("метод класса нельзя звать без объекта из глобальной функции",
                         "exe 0x3630d28 'You cannot call nonstatic function from static scope'"),
    "arity": ("число аргументов в пределах объявления (optional учтены, пропуски ', ,' допустимы); у получателя "
              "известного класса — по методу из его цепочки (theGame.Pause() без аргумента)",
              "exe 0x36302e0 'Function does not take %lu param(s)', 0x3630310, 0x36302a0"),
    "string-to-name": ("строковый литерал \"..\" или переменная string в параметр типа name: каста string->name нет",
                       "exe 0x3630c30 'No implicit cast exists for converting'; в таблице опкодов exe есть "
                       "OP_NameToString, нет StringToName; name->string неявно есть (oilBarrelEntity.ws:79 "
                       "GetComponent('OnFireDamageArea') при параметре string)"),
    "exec-in-class": ("exec function только глобальная", "exe 0x3628e40"),
    "exec-latent": ("exec function не latent", "exe 0x36296b8"),
    "exec-call": ("exec-функцию нельзя вызвать из скрипта", "exe 0x3630760"),
    "exec-param": ("WARN: параметр exec — объект, структура или массив (консоль должна его разобрать)",
                   "874 ванильных exec: bool, name, int, float, string, enum и один Vector (temp.ws:10874)"),
    "latent-call": ("latent-функцию зовут только из latent/entry", "exe 0x36303e0"),
    "global-flags": ("у глобальной функции нет timer/entry/event/final/cleanup и модификаторов доступа",
                     "exe 0x36294d0..0x36296f0, 0x3629800"),
    "timer-params": ("timer function(dt : float, id : int)", "exe 0x36280e0, 0x36282e0"),
    "struct-prop-of-call": ("поле структуры у результата вызова (foo().X) запрещено, сначала в переменную",
                            "exe 0x36305b0 'Parent scope does not come from variable'; в ванили 0 случаев "
                            "GetWorldPosition().X"),
    "array-method-of-call": ("метод массива у результата вызова (foo().Size()) запрещён, сначала в переменную",
                             "компилятор 5.00c 'Only arrays coming from variables can be modified' на GetTagged().Size(), "
                             "2026-10-03"),
    "const-global-field": ("запись поля через глобальный объект (thePlayer.поле = ...) запрещена, через переменную",
                           "компилятор 5.00c 'Left hand side is const' на thePlayer.mcwSpawnMode = mode, 2026-10-03"),
    "empty-if": ("пустой оператор: if (..) ; / else ;", "exe 0x36304b0, 0x36304d8; пустой блок { } ваниль "
                 "допускает 121 раз (actor.ws:5275), значит ошибка про пустой оператор"),
    "array-literal": ("литералов массивов {..} нет, массив собирают PushBack",
                      "ваниль: photomodeMenu.ws:436-448, temp.ws:6921 (horseTag.PushBack)"),
    "reserved-name": ("ключевые слова лексера (в т.ч. map/set из 5.0) не годятся в имена параметров, переменных, "
                      "функций, полей", "таблица ключевых слов лексера в exe около 0x3632088; exe 0x3630818 "
                      "'Subscript requires array, map or set'; ingameMenu.ws:229 map<name, bool>"),
    "duplicate": ("имя глобальной функции/класса/структуры/enum не совпадает с ванилью, другими файлами и другим "
                  "объявлением в этом же файле; метод — с другим методом того же класса",
                  "exe 0x3628a70 'Function has already been defined in current compilation unit', 0x3628be0 "
                  "(метод), 0x3627b70 (класс), 0x36277b8 (структура), 0x36298d8 (state), 0x3627970; WIDE "
                  "symbol-name-taken-in-dependency"),
    "duplicate-case": ("совпадение без учёта регистра (WARN): в ванили есть и LogStats, и logstats", "temp.ws"),
    "annotation": ("аннотации: только @addField/@addMethod/@wrapMethod/@replaceMethod, вне классов, цель есть",
                   "exe 0x3629748..0x3629fd0; REDkit wiki 'WS: Script Compilation Errors overrides'"),
    "wrap": ("@wrapMethod: метод объявлен в самом классе, не import (натив), тело содержит wrappedMethod, число "
             "параметров то же; @replaceMethod: не import",
             "exe 0x3629278, 0x3629440, 0x3629400, 0x36293b0 'cannot wrap because it is native (only allowed in "
             "modio mods)', 0x3629d88 '@replaceMethod cannot replace an import', 0x362a0c0"),
    "top-level": ("на верхнем уровне только объявления", "грамматика"),
    "wrap-kind": ("WARN: event обёрнут как function (или наоборот). Моды 4.04 и w3-ac под 5.0 оборачивают "
                  "event OnSpawned ключевым словом function; в 5.0 «Events can be annotated properly now», "
                  "в exe есть 0x362ac20 «declared as event function ... Cannot override as normal function»",
                  "REDkit Changelog 5.0.1042178; github.com/p-marques/w3-ac ACIntegrations.ws"),
}

Tok = namedtuple("Tok", "kind val line")

_TOKEN = re.compile(r"""
    (?P<nl>\n)
  | (?P<ws>[ \t\r\f\v\u00a0]+)
  | (?P<lc>//[^\n]*)
  | (?P<bc>/\*.*?\*/)
  | (?P<str>"(?:[^"\\\n]|\\.)*")
  | (?P<name>'[^'\n]*')
  | (?P<num>0[xX][0-9a-fA-F]+|(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?[fF]?)
  | (?P<id>[A-Za-z_]\w*)
  | (?P<at>@[A-Za-z_]\w*)
  | (?P<op>==|!=|<=|>=|&&|\|\||\+=|-=|\*=|/=|\|=|&=|::|.)
""", re.S | re.X)

MODS = {"import", "final", "latent", "private", "protected", "public", "exec", "quest", "timer", "entry",
        "storyscene", "cleanup", "reward", "abstract", "statemachine", "saved", "editable", "const",
        "inlined", "autobind", "static", "optional", "out"}
ACCESS = {"private", "protected", "public"}
NOT_CALLS = {"if", "while", "for", "switch", "return", "case", "do", "new", "delete", "in", "default",
             "this", "super", "parent", "virtual_parent", "NULL", "true", "false", "else"}
# встроенные операции массивов и map с числом аргументов: опкоды OP_Array*/OP_Map* в exe (0x3634260..0x36348e0),
# проверка «Function '%s' does not take %i params» там же (0x3635940..)
BUILTIN_ARITY = {"PushBack": 1, "PopBack": 0, "Size": 0, "Clear": 0, "Resize": 1, "Grow": 1, "Insert": 2,
                 "Erase": 1, "EraseFast": 1, "Remove": 1, "RemoveFast": 1, "Contains": 1, "ContainsFast": 1,
                 "FindFirst": 1, "FindFirstFast": 1, "FindLast": 1, "FindLastFast": 1, "Last": 0, "Key": 1,
                 "TryGet": 2}
STRUCT_PROPS = {"X", "Y", "Z", "W", "Pitch", "Yaw", "Roll", "Min", "Max"}
# Методы array<T>: у результата вызова компилятор 5.00c их не принимает — «Only arrays coming from variables can be
# modified» даже на .Size() (замер 2026-10-03, mcw_spike.ws). Сначала результат в переменную.
ARRAY_METHODS = {"Size", "PushBack", "PopBack", "Clear", "Erase", "EraseFast", "Remove", "Grow", "Resize", "Insert",
                 "FindFirst", "FindLast", "Contains", "Last"}
# Глобальные объекты: поле через них не записать — «Left hand side is const» (замер 2026-10-03: thePlayer.поле = ...).
CONST_GLOBALS = {"thePlayer", "theGame", "theCamera", "theInput", "theSound", "theDebug", "theTelemetry", "theServer",
                 "theTimer"}
ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "|=", "&="}
EXEC_TYPES = {"bool", "name", "int", "int32", "float", "string", "byte", "vector"}
ANNOTATIONS = {"addField", "addMethod", "wrapMethod", "replaceMethod"}
# ключевые слова лексера WitcherScript 5.0: таблица строк в witcher3.exe около 0x3632088 (рядом TOKEN_MAP, TOKEN_SET)
KEYWORDS = set("""
theTimer theInput theSound theDebug thePlayer theCamera theGame theTelemetry virtual_parent super parent this
continue return break while do for switch case default if else delete new in endif ifdef ifndef define undefine
wrapMethod addField replaceMethod addMethod savepoint reward saved quest exec storyscene latent var array map set
timer cleanup public event private protected local inlined any single optional autobind final out entry enter
false true NULL hint defaults name void int float byte bool string def editable struct function state enum class
statemachine extends abstract const import""".split())
# ключевые слова, которые сами являются значением (могут закончить выражение)
VALUE_KW = {"true", "false", "NULL", "this", "theTimer", "theInput", "theSound", "theDebug", "thePlayer",
            "theCamera", "theGame", "theTelemetry"}
BUILTIN_TYPES = {"int", "float", "bool", "string", "name", "byte", "void", "array", "map", "set"}
# bin\redscripts.ini [globals] игры 5.00c; читается из файла, это — запасной вариант (самопроверка, нет игры)
GLOBALS_DEFAULT = {"theGame": "CR4Game", "theServer": "CServerInterface", "thePlayer": "CR4Player",
                   "theCamera": "CCamera", "theUI": "CGuiWitcher", "theSound": "CScriptSoundSystem",
                   "theDebug": "CDebugAttributesManager", "theTimer": "CTimerScriptKeyword",
                   "theInput": "CInputManager"}
# Поправки к ini по ванили: в ini theCamera=CCamera, но ваниль зовёт у theCamera методы, объявленные только в
# CCameraDirector (temp.ws:11178 GetTopmostCameraObject — cameraDirector.ws:30; btTaskTeleport.ws:501
# GetCameraRight — :23), 59 мест. Значит, компилятор типизирует theCamera как CCameraDirector.
GLOBALS_FIX = {"theCamera": "CCameraDirector"}
# перед '(' каста (T)x может стоять оператор или одно из этих слов; после if/while/for/switch это условие
CAST_PREV = {"return", "case", "else", "in"}
OPEN, CLOSE = "([{", ")]}"


class Func:
    __slots__ = ("name", "mods", "kind", "params", "body", "line", "path", "owner", "ann", "ann_cls",
                 "ret", "locals", "vend", "casts", "news")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def nmin(self):
        return sum(1 for p in self.params if not p[2])

    @property
    def nmax(self):
        return len(self.params)

    @property
    def latent(self):
        return "latent" in self.mods

    @property
    def exec(self):
        return "exec" in self.mods

    def sig(self):
        ps = ", ".join(("optional " if p[2] else "") + p[0] + " : " + p[1] for p in self.params)
        where = f"{self.owner}." if self.owner else ""
        return f"{where}{self.name}({ps})  [{os.path.basename(self.path)}:{self.line}]"


class TypeInfo:
    __slots__ = ("name", "kind", "base", "methods", "fields", "path", "line", "mods", "values", "sm")

    def __init__(self, name, kind, base, path, line, mods=(), sm=None):
        self.name, self.kind, self.base, self.path, self.line = name, kind, base, path, line
        self.mods, self.sm = set(mods), sm   # sm — класс-владелец state
        self.methods = defaultdict(list)
        self.fields = {}  # имя -> (тип, файл, строка)
        self.values = []  # значения enum


class Finding(namedtuple("Finding", "path line level code msg")):
    def __str__(self):
        return f"{self.path}:{self.line}: {self.level} {self.code}: {self.msg}"


def tokenize(text):
    """-> (токены без пробелов/комментариев, [(строка, сообщение)] ошибок лексера)."""
    toks, errs, line = [], [], 1
    for m in _TOKEN.finditer(text):
        k, v = m.lastgroup, m.group()
        if k == "nl":
            line += 1
            continue
        if k in ("ws", "lc"):
            continue
        if k == "bc":
            line += v.count("\n")
            continue
        if k == "op" and v in "\"'":
            errs.append((line, "незакрытый литерал " + v))
        if k == "op" and v == "/" and text.startswith("/*", m.start()):
            errs.append((line, "незакрытый /* комментарий"))
        toks.append(Tok(k, v, line))
    return toks, errs


def bracket_pairs(toks):
    """-> (пары индексов открывающей/закрывающей в обе стороны, [(строка, сообщение)])."""
    pairs, stack, errs = {}, [], []
    for i, t in enumerate(toks):
        if t.kind != "op":
            continue
        if t.val in OPEN:
            stack.append(i)
        elif t.val in CLOSE:
            if not stack:
                errs.append((t.line, f"лишняя '{t.val}'"))
                continue
            j = stack.pop()
            if OPEN.index(toks[j].val) != CLOSE.index(t.val):
                errs.append((t.line, f"'{t.val}' закрывает '{toks[j].val}' со строки {toks[j].line}"))
            pairs[j], pairs[i] = i, j
    for j in stack:
        errs.append((toks[j].line, f"незакрытая '{toks[j].val}'"))
    return pairs, errs


def _type_names(typ):
    return re.findall(r"[A-Za-z_]\w*", typ or "")


def _operand_start(tk):
    return tk.kind in ("id", "num", "str", "name") or tk.val == "("


class FileUnit:
    """Один .ws: токены, пары скобок, объявления верхнего уровня."""

    def __init__(self, path, text, is_vanilla):
        self.path, self.is_vanilla = path, is_vanilla
        self.toks, self.lex_errs = tokenize(text)
        self.pairs, self.bal_errs = bracket_pairs(self.toks)
        self.funcs, self.types, self.gvars, self.parse_errs = [], [], [], []
        self.ann_fields = []  # (класс, имя, тип, строка)
        self.type_uses = []   # (тип, строка, где) — объявления вне тел функций
        if not self.bal_errs:
            self._parse()

    def _match(self, i):
        return self.pairs.get(i, len(self.toks) - 1)

    def _skip_stmt(self, i, end):
        """Пропуск до ';' (включительно) или через сбалансированный {...}."""
        t = self.toks
        while i < end:
            if t[i].val == ";" and t[i].kind == "op":
                return i + 1
            if t[i].val in OPEN and t[i].kind == "op":
                j = self._match(i)
                if t[i].val == "{":
                    return j + 1
                i = j
            i += 1
        return end

    def _type_until(self, i, stops):
        """Тип с учётом <...> до одного из stops на нулевой глубине угловых скобок."""
        t, depth, parts = self.toks, 0, []
        while i < len(t):
            v = t[i].val
            if v == "<":
                depth += 1
            elif v == ">":
                depth -= 1
            elif depth <= 0 and v in stops:
                break
            parts.append(v)
            i += 1
        return "".join(parts).replace(",", ", "), i

    def _parse_params(self, i, end):
        t, params, mods, names = self.toks, [], set(), []
        while i < end:
            v = t[i].val
            if v in ("optional", "out"):
                mods.add(v)
            elif v == ",":
                pass
            elif v == ":":
                typ, i = self._type_until(i + 1, {",", ")"})
                for n in names:
                    params.append((n, typ, "optional" in mods, "out" in mods))
                mods, names = set(), []
                continue
            elif t[i].kind == "id":
                names.append(v)
            i += 1
        return params

    def _parse_var(self, i, end):
        """t[i] == 'var' (или autobind) -> (имена, тип, строка, индекс после ';', (начало, конец) инициализатора)."""
        t, names, line = self.toks, [], self.toks[i].line
        i += 1
        while i < end and t[i].val != ":":
            if t[i].kind == "id":
                names.append(t[i].val)
            i += 1
        typ, i = self._type_until(i + 1, {";", "="})
        nxt = self._skip_stmt(i, end)
        init = (i + 1, nxt - 1) if i < end and t[i].val == "=" else None
        return names, typ, line, nxt, init

    def _parse_func(self, i, mods, owner, ann=None, ann_cls=None):
        t = self.toks
        kind, line = t[i].val, t[i].line
        name = t[i + 1].val if i + 1 < len(t) else "?"
        f = Func(name=name, mods=set(mods), kind=kind, params=[], body=None, line=line, path=self.path,
                 owner=owner, ann=ann, ann_cls=ann_cls, ret=None, locals=[], vend=None, casts=set(), news=set())
        i += 2
        if i < len(t) and t[i].val == "(":
            j = self._match(i)
            f.params = self._parse_params(i + 1, j)
            i = j + 1
        if i < len(t) and t[i].val == ":":
            f.ret, i = self._type_until(i + 1, {"{", ";"})
        for p in f.params:
            self.type_uses.append((p[1], line, f"{name}: параметр {p[0]}"))
        if f.ret:
            self.type_uses.append((f.ret, line, f"{name}: тип возврата"))
        while i < len(t) and t[i].val not in ("{", ";"):
            i += 1
        if i < len(t) and t[i].val == "{":
            j = self._match(i)
            f.body = (i, j)
            self._parse_body(f)
            return f, j + 1
        return f, i + 1

    def _parse_body(self, f):
        """Локальные var в начале тела, касты (T)x и new T — индексы токенов с именем типа."""
        t = self.toks
        a, b = f.body
        k = a + 1
        while k < b and t[k].val == "var":
            names, typ, line, k, init = self._parse_var(k, b)
            f.locals.append((names, typ, line, init))
        f.vend = k
        for p in range(a + 1, b):
            tk = t[p]
            if tk.kind == "op" and tk.val == "(" and p + 3 < b and t[p + 1].kind == "id" and t[p + 2].val == ")" \
                    and _operand_start(t[p + 3]):
                prev = t[p - 1]
                chained = prev.val == ")" and (p - 2) in f.casts      # (float)(int)x — ротация кастов
                if chained or not ((prev.kind == "id" and prev.val not in CAST_PREV) or prev.val in (")", "]")):
                    f.casts.add(p + 1)
            elif tk.kind == "id" and tk.val == "new" and p + 1 < b and t[p + 1].kind == "id":
                f.news.add(p + 1)

    def _parse_container(self, i, mods):
        t = self.toks
        kind, line = t[i].val, t[i].line
        name, base, parent = t[i + 1].val, None, None
        i += 2
        while i < len(t) and t[i].val != "{":
            if t[i].val == "extends":
                base = t[i + 1].val
            elif t[i].val == "in":
                parent = t[i + 1].val
            i += 1
        if i >= len(t):
            return None, i
        j = self._match(i)
        if kind == "state" and parent:
            name = f"{parent}State{name}"
            base = f"{parent}State{base}" if base else None
        ti = TypeInfo(name, kind, base, self.path, line, mods, parent)
        if kind in ("class", "state"):
            self._parse_members(ti, i + 1, j)
        elif kind == "struct":
            k = i + 1
            while k < j:
                if t[k].val == "var":
                    names, typ, vline, k, _ = self._parse_var(k, j)
                    self.type_uses.append((typ, vline, f"{name}.{', '.join(names)}"))
                    for n in names:
                        ti.fields[n] = (typ, self.path, vline)
                else:
                    k += 1
        elif kind == "enum":
            for k in range(i + 1, j):
                if t[k].kind == "id" and t[k - 1].val in ("{", ","):
                    ti.values.append(t[k].val)
        return ti, j + 1

    def _parse_members(self, ti, i, end):
        t = self.toks
        while i < end:
            if t[i].kind == "at":
                self.parse_errs.append(Finding(self.path, t[i].line, "ERROR", "annotation",
                                               f"{t[i].val} внутри класса {ti.name} (exe 0x3629fd0)"))
                i += 1
                if i < end and t[i].val == "(":
                    i = self._match(i) + 1
                continue
            mods = []
            while i < end and t[i].kind == "id" and t[i].val in MODS:
                mods.append(t[i].val)
                i += 1
            if i >= end:
                break
            v = t[i].val
            if v in ("function", "event"):
                f, i = self._parse_func(i, mods, ti.name)
                ti.methods[f.name].append(f)
                self.funcs.append(f)
            elif v == "var" or ("autobind" in mods and t[i].kind == "id"):
                # autobind имя : Тип = single; — поле без слова var (container.ws:28)
                names, typ, vline, i, _ = self._parse_var(i if v == "var" else i - 1, end)
                self.type_uses.append((typ, vline, f"{ti.name}.{', '.join(names)}"))
                for n in names:
                    ti.fields[n] = (typ, self.path, vline)
            else:
                i = self._skip_stmt(i, end)

    def _parse(self):
        t, i, n = self.toks, 0, len(self.toks)
        while i < n:
            ann = ann_cls = None
            ann_line = t[i].line
            if t[i].kind == "at":
                ann = t[i].val[1:]
                i += 1
                if i < n and t[i].val == "(":
                    j = self._match(i)
                    ann_cls = "".join(x.val for x in t[i + 1:j]) or None
                    i = j + 1
                if ann not in ANNOTATIONS:
                    self.parse_errs.append(Finding(self.path, ann_line, "ERROR", "annotation",
                                                   f"неизвестная аннотация @{ann}"))
            mods = []
            while i < n and t[i].kind == "id" and t[i].val in MODS:
                mods.append(t[i].val)
                i += 1
            if i >= n:
                break
            v = t[i].val
            if v in ("class", "state", "struct", "enum") and not ann:
                ti, i = self._parse_container(i, mods)
                if ti:
                    self.types.append(ti)
            elif v in ("function", "event"):
                f, i = self._parse_func(i, mods, None, ann, ann_cls)
                self.funcs.append(f)
                if ann == "addField":
                    self.parse_errs.append(Finding(self.path, f.line, "ERROR", "annotation",
                                                   "@addField должна стоять перед var"))
            elif v == "var":
                names, typ, line, i, _ = self._parse_var(i, n)
                self.type_uses.append((typ, line, f"var {', '.join(names)}"))
                if ann == "addField":
                    for nm in names:
                        self.ann_fields.append((ann_cls, nm, typ, line))
                else:
                    self.gvars.append((names, line))
            elif v == ";":
                i += 1
            else:
                self.parse_errs.append(Finding(self.path, t[i].line, "ERROR", "top-level",
                                               f"неожиданный '{v}' на верхнем уровне"))
                i = self._skip_stmt(i + 1, n)


class Index:
    """Все объявления: глобальные функции, методы, типы, значения enum. Ключи с учётом регистра.
    По ванили ещё собирается, какие имена типов и свободные идентификаторы она употребляет: что ваниль
    употребляет без объявления в .ws, то компилятор знает из C++ (EPersistanceMode, PM_DontPersist)."""

    def __init__(self, globals_map=None):
        self.funcs = defaultdict(list)
        self.methods = defaultdict(list)
        self.types = {}
        self.lower = defaultdict(set)  # имя в нижнем регистре -> настоящие имена глобальных символов
        self.enum_vals = {}            # значение -> enum
        self.globals = dict(globals_map or GLOBALS_DEFAULT)
        self.globals.update(GLOBALS_FIX)
        self.van_types = set()         # имена типов из объявлений, кастов и new в ванили
        self.van_free = set()          # идентификаторы в телах ванили не после '.' и не перед '('
        self.declared = set()          # имена параметров, локальных и полей в ванили
        self._natives = None

    def add(self, unit):
        for ti in unit.types:
            self.types.setdefault(ti.name, ti)
            self.lower[ti.name.lower()].add(ti.name)
            for name, fs in ti.methods.items():
                self.methods[name].extend(fs)
            for v in ti.values:
                self.enum_vals.setdefault(v, ti.name)
        for f in unit.funcs:
            if f.owner:
                continue
            if f.ann in ("addMethod", "wrapMethod", "replaceMethod") and f.ann_cls:
                self.methods[f.name].append(f)
                ti = self.types.get(f.ann_cls)
                if ti is not None and f.ann == "addMethod":
                    ti.methods[f.name].append(f)
            elif f.ann == "replaceMethod":
                self.funcs[f.name].append(f)
            elif not f.ann:
                self.funcs[f.name].append(f)
                self.lower[f.name.lower()].add(f.name)
        for cls, name, typ, line in unit.ann_fields:
            ti = self.types.get(cls)
            if ti is not None:
                ti.fields.setdefault(name, (typ, unit.path, line))
        if unit.is_vanilla:
            self._scan_vanilla(unit)
        self._natives = None

    def _scan_vanilla(self, u):
        toks = u.toks
        for typ, _, _ in u.type_uses:
            self.van_types.update(_type_names(typ))
        for ti in u.types:
            self.declared.update(ti.fields)
        for f in u.funcs:
            self.declared.update(p[0] for p in f.params)
            if not f.body:
                continue
            for names, typ, _, _ in f.locals:
                self.declared.update(names)
                self.van_types.update(_type_names(typ))
            self.van_types.update(toks[c].val for c in f.casts | f.news)
            a, b = f.body
            for m in range(f.vend, b):
                tk = toks[m]
                if tk.kind == "id" and toks[m - 1].val != "." and toks[m + 1].val != "(" and tk.val not in KEYWORDS:
                    self.van_free.add(tk.val)

    @property
    def natives(self):
        """Свободные имена из тел ванили, не объявленные в .ws нигде: нативные enum и константы."""
        if self._natives is None:
            self._natives = (self.van_free - self.declared - set(self.enum_vals) - set(self.types)
                             - set(self.funcs) - set(self.globals))
        return self._natives

    def base_of(self, ti):
        """База с неявными: класс без extends — IScriptable (playerAiming.ws:12 зовёт GetCurrentStateName из
        object.ws:30), state без extends — CScriptableState (states.ws:6). База state ищется и в предках
        класса-владельца: state AardConeCast in W3AardEntity extends NormalCast, а NormalCast объявлен
        in W3SignEntity (aardEntity.ws:391, signEntity.ws:852)."""
        b = ti.base
        if ti.kind == "state":
            if b and b not in self.types and ti.sm:
                short = b[len(ti.sm) + len("State"):]
                for c in self.chain(ti.sm)[1:]:
                    if f"{c.name}State{short}" in self.types:
                        return f"{c.name}State{short}"
            return b or "CScriptableState"
        if ti.kind == "class" and not b and ti.name not in ("IScriptable", "ISerializable"):
            return "IScriptable"
        return b

    def chain(self, cls):
        seen = []
        while cls and cls in self.types and cls not in seen:
            seen.append(cls)
            cls = self.base_of(self.types[cls])
        return [self.types[c] for c in seen]

    def chain_complete(self, cls):
        """Цепочка дошла до корня: ISerializable (в .ws не объявлен, база IScriptable, object.ws:6) или класс
        без базы. Если база где-то не объявлена (IGameSystem, CGuiObject) — цепочка неполная, метод не сужаем."""
        ch = self.chain(cls)
        if not ch:
            return False
        last = self.base_of(ch[-1])
        return last is None or last == "ISerializable"


def _norm_type(typ):
    typ = typ.strip().lower()
    return "name" if typ in ("name", "cname") else typ


def _split_args(toks, pairs, a, b):
    """Аргументы между скобками (a, b): список списков токенов, пустой аргумент — []."""
    if a + 1 >= b:
        return []
    args, cur, i = [], [], a + 1
    while i < b:
        tk = toks[i]
        if tk.kind == "op" and tk.val in OPEN:
            j = pairs.get(i, i)
            cur.extend(toks[i:j + 1])
            i = j + 1
            continue
        if tk.kind == "op" and tk.val == ",":
            args.append(cur)
            cur = []
        else:
            cur.append(tk)
        i += 1
    args.append(cur)
    return args


class Linter:
    def __init__(self, index, units, exe_path=None):
        self.ix, self.units, self.out = index, units, []
        self.exe_path, self._exe, self._exe_cache = exe_path, None, {}

    def err(self, unit, line, code, msg, level="ERROR"):
        self.out.append(Finding(unit.path, line, level, code, msg))

    def run(self):
        for u in self.units:
            self.lint_unit(u)
        return self.out

    def exe_has(self, name):
        if name in self._exe_cache:
            return self._exe_cache[name]
        found = False
        if self.exe_path is not None and os.path.exists(self.exe_path):
            if self._exe is None:
                with open(self.exe_path, "rb") as fh:
                    self._exe = fh.read()
            found = re.search(rb"(?<![\x21-\x7e])" + re.escape(name.encode()) + rb"\x00", self._exe) is not None
        self._exe_cache[name] = found
        return found

    def err_or_native(self, u, line, code, msg, name):
        """Имя есть строкой в exe — WARN (компилятор может знать его из C++), иначе ERROR."""
        if self.exe_has(name):
            self.err(u, line, code, msg + "; строка есть в witcher3.exe — вероятно, нативное", "WARN")
        else:
            self.err(u, line, code, msg)

    # ---------------------------------------------------------------- уровень файла
    def lint_unit(self, u):
        for line, msg in u.lex_errs:
            self.err(u, line, "literal", msg)
        for line, msg in u.bal_errs:
            self.err(u, line, "balance", msg)
        if u.bal_errs:
            return
        self.out.extend(u.parse_errs)
        for names, line in u.gvars:
            self.err(u, line, "global-var", f"глобальная переменная {', '.join(names)}: в WitcherScript их нет")
        for cls, name, typ, line in u.ann_fields:
            self.check_add_field(u, cls, name, typ, line)
            if name in KEYWORDS:
                self.err(u, line, "reserved-name", f"поле '{name}' — ключевое слово")
        for typ, line, what in u.type_uses:
            self.check_type(u, typ, line, what)
        for f in u.funcs:
            self.check_decl(u, f)
            if f.body:
                self.check_body(u, f)
        for ti in u.types:
            self.check_dup(u, ti.name, ti.line, ti.path)
        self.check_dup_in_file(u)
        for i, tk in enumerate(u.toks):
            if tk.kind == "id" and tk.val in ("map", "set"):
                nxt = u.toks[i + 1].val if i + 1 < len(u.toks) else ""
                if nxt != "<":
                    self.err(u, tk.line, "reserved-name", f"'{tk.val}' — ключевое слово 5.0, не имя")

    def check_type(self, u, typ, line, what):
        for nm in _type_names(typ):
            if nm in BUILTIN_TYPES or nm in self.ix.types or nm in self.ix.van_types:
                continue
            self.err_or_native(u, line, "unknown-type", f"{what}: неизвестный тип '{nm}' (exe 0x36286f0)", nm)

    def check_dup(self, u, name, line, path):
        others = [f for f in self.ix.funcs.get(name, []) if f.path != path and not f.ann]
        ti = self.ix.types.get(name)
        if ti is not None and ti.path != path:
            others.append(ti)
        for o in others[:1]:
            self.err(u, line, "duplicate", f"'{name}' уже объявлено в {os.path.relpath(o.path, SCRIPTS)}:{o.line}")
        if u.is_vanilla:
            return  # 11 таких пар ваниль компилирует (LogStats/logstats) — для неё это не находка
        for other in sorted(self.ix.lower.get(name.lower(), set()) - {name}):
            self.err(u, line, "duplicate-case", f"'{name}' отличается от '{other}' только регистром", "WARN")

    def check_dup_in_file(self, u):
        """Повтор объявления в одном файле: индекс помнит первое, check_dup сверяет только с чужими файлами."""
        exe = {"function": "0x3628a70", "class": "0x3627b70", "struct": "0x36277b8", "state": "0x36298d8",
               "enum": "0x3627608"}
        first = {}
        decls = [(f.kind if f.kind == "function" else "function", f.name, f.line) for f in u.funcs
                 if f.owner is None and not f.ann]
        decls += [(ti.kind, ti.name, ti.line) for ti in u.types]
        for kind, name, line in decls:
            key = (kind == "function", name)
            if key in first:
                self.err(u, line, "duplicate", f"{kind} {name} уже объявлен в этом файле на строке {first[key]} "
                                               f"(exe {exe.get(kind, '0x3628a70')})")
            else:
                first[key] = line
        for ti in u.types:
            for name, fs in ti.methods.items():
                own = [g for g in fs if g.owner == ti.name]
                for g in own[1:]:
                    self.err(u, g.line, "duplicate", f"метод {ti.name}.{name} уже объявлен на строке {own[0].line} "
                                                     f"(exe 0x3628be0)")

    def check_add_field(self, u, cls, name, typ, line):
        ti = self.ix.types.get(cls or "")
        if ti is None or ti.kind not in ("class", "state"):
            self.err(u, line, "annotation", f"@addField: класс '{cls}' не найден (exe 0x3629890)")
            return
        for t in self.ix.chain(cls):
            src = t.fields.get(name)
            if src and (src[1], src[2]) != (u.path, line):  # само это поле индекс уже знает — его пропускаем
                self.err(u, line, "duplicate", f"@addField: поле {name} уже есть в {t.name} "
                                               f"({os.path.basename(src[1])}:{src[2]})")

    def check_decl(self, u, f):
        if f.owner is None and not f.ann:
            bad = sorted(f.mods & ({"timer", "entry", "final", "cleanup"} | ACCESS))
            if bad:
                self.err(u, f.line, "global-flags", f"глобальная функция {f.name}: {' '.join(bad)} не имеет смысла")
            if f.kind == "event":
                self.err(u, f.line, "global-flags", f"event {f.name} вне класса")
            self.check_dup(u, f.name, f.line, f.path)
        if f.exec:
            for p in f.params:
                ti = self.ix.types.get(p[1].strip())
                handle = ti is not None and ti.kind in ("class", "state", "struct")
                if _norm_type(p[1]) not in EXEC_TYPES and (handle or "<" in p[1]):
                    # нативные enum (EDifficultyMode, temp.ws:7936) в .ws не объявлены — их не трогаем
                    self.err(u, f.line, "exec-param", f"exec {f.name}: параметр {p[0]} : {p[1]}", "WARN")
            if f.owner or f.ann:
                self.err(u, f.line, "exec-in-class", f"exec function {f.name} внутри класса (exe 0x3628e40)")
            if f.latent:
                self.err(u, f.line, "exec-latent", f"exec function {f.name} не может быть latent (exe 0x36296b8)")
        if "timer" in f.mods:
            types = [_norm_type(p[1]) for p in f.params]
            if len(types) != 2 or types[0] not in ("float", "gametime") or types[1] not in ("int", "int32"):
                self.err(u, f.line, "timer-params", f"timer {f.name} должен принимать (dt : float, id : int)")
        for p in f.params:
            if p[0] in KEYWORDS:
                self.err(u, f.line, "reserved-name", f"параметр '{p[0]}' — ключевое слово")
        if f.name in KEYWORDS:
            self.err(u, f.line, "reserved-name", f"функция '{f.name}' — ключевое слово")
        if f.ann:
            self.check_annotated(u, f)

    def check_annotated(self, u, f):
        if f.ann == "replaceMethod" and not f.ann_cls:
            orig = [g for g in self.ix.funcs.get(f.name, []) if g is not f]
            if not orig:
                self.err(u, f.line, "annotation", f"@replaceMethod: глобальной {f.name} нет")
            elif all("import" in g.mods for g in orig):
                self.err(u, f.line, "wrap", f"@replaceMethod {f.name}: глобальная функция нативная (import, "
                                            f"exe 0x362a0c0): {orig[0].sig()}")
            return
        ti = self.ix.types.get(f.ann_cls or "")
        if ti is None or ti.kind not in ("class", "state"):
            self.err(u, f.line, "annotation", f"@{f.ann}: класс '{f.ann_cls}' не найден")
            return
        own = [g for g in ti.methods.get(f.name, []) if g is not f]
        if f.ann == "addMethod":
            inherited = [g for t in self.ix.chain(f.ann_cls)[1:] for g in t.methods.get(f.name, [])]
            if own or inherited:
                where = (own or inherited)[0]
                self.err(u, f.line, "duplicate", f"@addMethod: {f.name} уже есть: {where.sig()}")
        elif f.ann in ("wrapMethod", "replaceMethod"):
            if not own:
                inh = [t.name for t in self.ix.chain(f.ann_cls)[1:] if f.name in t.methods]
                why = f"объявлен в предке {inh[0]} (exe 0x3629440)" if inh else "не объявлен (exe 0x3629400)"
                self.err(u, f.line, "wrap", f"@{f.ann}({f.ann_cls}) {f.name}: метод {why}")
            elif "import" in own[0].mods:
                code = "0x36293b0, можно только в modio-модах" if f.ann == "wrapMethod" else "0x3629d88"
                self.err(u, f.line, "wrap", f"@{f.ann}({f.ann_cls}) {f.name}: метод нативный (import, exe {code}): "
                                            f"{own[0].sig()}")
            elif own[0].nmax != f.nmax:
                self.err(u, f.line, "wrap", f"@{f.ann} {f.name}: {f.nmax} параметров, в оригинале "
                                            f"{own[0].nmax}: {own[0].sig()}")
            elif own[0].kind != f.kind:
                self.err(u, f.line, "wrap-kind", f"@{f.ann} {f.name}: в оригинале {own[0].kind}, в обёртке "
                                                 f"{f.kind}", "WARN")
            if f.ann == "wrapMethod" and f.body:
                a, b = f.body
                if not any(t.val == "wrappedMethod" for t in u.toks[a:b]):
                    self.err(u, f.line, "wrap", f"@wrapMethod {f.name}: нет вызова wrappedMethod (exe 0x3629278)")

    # ---------------------------------------------------------------- тело функции
    @staticmethod
    def owner_of(f):
        if f.owner:
            return f.owner
        return f.ann_cls if f.ann in ("addMethod", "wrapMethod", "replaceMethod") else None

    def scope_of(self, f):
        """Имя -> тип: глобальные объекты < поля цепочки класса < параметры < локальные."""
        scope = dict(self.ix.globals)
        for t in reversed(self.ix.chain(self.owner_of(f))):
            scope.update({n: v[0] for n, v in t.fields.items()})
        scope.update({p[0]: p[1] for p in f.params})
        for names, typ, _, _ in f.locals:
            scope.update({n: typ for n in names})
        return scope

    def check_body(self, u, f):
        toks, pairs = u.toks, u.pairs
        a, b = f.body
        locs = {}
        pnames = {p[0] for p in f.params}
        for names, typ, line, _ in f.locals:
            self.check_type(u, typ, line, f"{f.name}: var {', '.join(names)}")
            for nm in names:
                if nm in locs:
                    self.err(u, line, "local-dup", f"{f.name}: '{nm}' объявлена дважды")
                if nm in pnames:
                    self.err(u, line, "local-dup", f"{f.name}: '{nm}' совпадает с параметром")
                if nm in KEYWORDS:
                    self.err(u, line, "reserved-name", f"{f.name}: переменная '{nm}' — ключевое слово")
                locs[nm] = typ
        scope = self.scope_of(f)
        late = set()  # заголовки var не на месте: их имена в область видимости, сами токены не проверяем
        for m in range(f.vend, b):
            if toks[m].kind == "id" and toks[m].val == "var":
                self.err(u, toks[m].line, "var-placement", f"{f.name}: var после операторов")
                names, typ, _, nxt, init = u._parse_var(m, b)
                scope.update({n: typ for n in names})
                late.update(range(m, init[0] if init else nxt))
        for c in sorted(f.casts):
            self.check_type(u, toks[c].val, toks[c].line, f"{f.name}: каст ({toks[c].val})")
        for c in sorted(f.news):
            self.check_type(u, toks[c].val, toks[c].line, f"{f.name}: new {toks[c].val}")
        in_class = bool(f.owner or (f.ann and f.ann_cls))
        caller_latent = f.latent or "entry" in f.mods
        for m in range(a + 1, b):
            tk = toks[m]
            if tk.kind == "op" and tk.val == "{":
                prev = toks[m - 1]
                if prev.val in ("=", "(", ",", "return"):
                    self.err(u, tk.line, "array-literal", "литерал {..}: массивы собирают через PushBack")
            # пустой оператор сразу после if (...) / else. Пустой блок { } ваниль допускает (actor.ws:5275).
            if tk.kind == "op" and tk.val == ";":
                prev = toks[m - 1]
                if prev.val == "else":
                    self.err(u, tk.line, "empty-if", "else ;")
                elif prev.val == ")" and pairs.get(m - 1, 0) > 0 and toks[pairs[m - 1] - 1].val == "if":
                    self.err(u, tk.line, "empty-if", "if (..) ;")
            if tk.kind == "op" and tk.val == ")" and m + 2 < b and toks[m + 1].val == ".":
                prop, after = toks[m + 2], toks[m + 3] if m + 3 < len(toks) else None
                o = pairs.get(m)
                if (prop.val in STRUCT_PROPS and (after is None or after.val != "(") and o is not None
                        and o > 0 and toks[o - 1].kind == "id" and toks[o - 1].val not in NOT_CALLS):
                    self.err(u, tk.line, "struct-prop-of-call",
                             f"{toks[o - 1].val}(..).{prop.val}: сначала присвой результат переменной")
            if (tk.kind == "op" and tk.val == ")" and m + 3 < b and toks[m + 1].val == "."
                    and toks[m + 2].val in ARRAY_METHODS and toks[m + 3].val == "("):
                o = pairs.get(m)
                if o is not None and o > 0 and toks[o - 1].kind == "id" and toks[o - 1].val not in NOT_CALLS:
                    self.err(u, tk.line, "array-method-of-call",
                             f"{toks[o - 1].val}(..).{toks[m + 2].val}(): массив сначала присвой переменной")
            if (tk.kind == "id" and tk.val in CONST_GLOBALS and toks[m - 1].val != "." and m + 3 < b
                    and toks[m + 1].val == "." and toks[m + 2].kind == "id" and toks[m + 3].val in ASSIGN_OPS):
                self.err(u, tk.line, "const-global-field",
                         f"{tk.val}.{toks[m + 2].val} {toks[m + 3].val} ...: {tk.val} константа, запиши через переменную")
            if tk.kind == "id" and m + 1 < b and toks[m + 1].val == "(" and tk.val not in NOT_CALLS:
                self.check_call(u, f, m, in_class, caller_latent, scope)
        self.check_idents(u, f, scope, late)
        self.check_semicolons(u, f, late)

    def check_idents(self, u, f, scope, late=frozenset()):
        """Свободные идентификаторы (не после '.', не вызов) должны разрешаться."""
        toks, ix = u.toks, self.ix
        a, b = f.body
        spans = [range(f.vend, b)] + [range(*init) for _, _, _, init in f.locals if init]
        skip = f.casts | f.news | late
        seen = set()
        for span in spans:
            for m in span:
                tk = toks[m]
                if tk.kind != "id" or tk.val in KEYWORDS or m in skip:
                    continue
                if toks[m - 1].val == "." or toks[m + 1].val == "(":
                    continue
                v = tk.val
                if v in scope or v in ix.enum_vals or v in ix.types or v in ix.natives or v in seen:
                    continue
                seen.add(v)
                owner = self.owner_of(f)
                where = f", не поле {owner} и предков" if owner else ""
                self.err_or_native(u, tk.line, "undeclared", f"{f.name}: '{v}' не объявлен (не локальная, "
                                   f"не параметр{where}, не enum; exe 0x36306e8)", v)

    def _expr_end(self, u, f, m):
        toks = u.toks
        tk = toks[m]
        if tk.kind in ("num", "str", "name"):
            return True
        if tk.kind == "id":
            return tk.val not in KEYWORDS or tk.val in VALUE_KW
        if tk.val == "]":
            return True
        if tk.val == ")":
            o = u.pairs.get(m)
            if o is None or o == 0 or toks[o - 1].val in ("if", "while", "for", "switch"):
                return False
            return (m - 1) not in f.casts
        return False

    def check_semicolons(self, u, f, late=frozenset()):
        toks = u.toks
        a, b = f.body
        for m in range(f.vend, b):
            if m in late:
                continue
            t2 = toks[m + 1]   # до '}' тела включительно: «a = 1 }» в ванили 0 раз
            starts = t2.kind in ("num", "str", "name") or (t2.kind == "id" and t2.val != "in")
            if t2.val == "}" and t2.kind == "op":
                o = u.pairs.get(m + 1, 0)
                starts = o > 0 and toks[o - 1].val not in ("=", "(", ",", "return")   # литерал { .. } — array-literal
            if starts and self._expr_end(u, f, m):
                t1 = toks[m]
                self.err(u, t1.line, "missing-semicolon", f"{f.name}: после '{t1.val}' сразу '{t2.val}' "
                                                          f"(строка {t2.line}) — пропущена ';' или оператор")

    def recv_type(self, u, f, m, scope):
        """Класс простого получателя x.Method(): локальная, параметр, поле, this, глобальный объект."""
        toks = u.toks
        if m < 2 or toks[m - 1].val != ".":
            return None
        r = toks[m - 2]
        if r.kind != "id" or (m >= 3 and toks[m - 3].val == "."):
            return None
        if r.val == "this":
            typ = self.owner_of(f)
        elif r.val in ("super", "parent", "virtual_parent"):
            return None
        else:
            typ = scope.get(r.val)
        if not typ or "<" in typ:
            return None
        ti = self.ix.types.get(typ.strip())
        if ti is None or ti.kind not in ("class", "state") or not self.ix.chain_complete(ti.name):
            return None
        return ti.name

    def check_call(self, u, f, m, in_class, caller_latent, scope=None):
        toks, ix = u.toks, self.ix
        scope = scope or {}
        name, line = toks[m].val, toks[m].line
        is_method = toks[m - 1].val == "."
        close = u.pairs.get(m + 1, m + 1)
        args = _split_args(toks, u.pairs, m + 1, close)
        if name == "wrappedMethod":
            if f.ann != "wrapMethod":
                self.err(u, line, "wrap", "wrappedMethod вне @wrapMethod")
            return
        if is_method:
            cands = ix.methods.get(name, [])
            rt = self.recv_type(u, f, m, scope)
            if rt:
                own = [g for t in ix.chain(rt) for g in t.methods.get(name, [])]
                if own:
                    cands = own
                elif cands and name not in BUILTIN_ARITY:
                    chain = " > ".join(t.name for t in ix.chain(rt))
                    self.err(u, line, "not-member", f"{toks[m - 2].val}.{name}(): {name} не член {rt} ({chain}), "
                                                    f"есть только у {cands[0].sig()} (exe 0x3630700)")
                    return
            if name in BUILTIN_ARITY:
                nb = len(args) - (1 if args and not args[-1] else 0)
                if nb == BUILTIN_ARITY[name]:
                    return  # массив/map; одноимённые методы классов проверяются ниже, если число не совпало
                if not cands:
                    self.err(u, line, "arity", f".{name}: {nb} аргумент(ов), встроенная операция берёт "
                                               f"{BUILTIN_ARITY[name]}")
                    return
            if not cands:
                if name in ix.funcs:
                    self.err(u, line, "unknown-call", f".{name}(): это глобальная функция, не метод")
                else:
                    self.err_or_native(u, line, "unknown-call", f"метод {name} не объявлен ни в одном классе", name)
                return
        else:
            cands = list(ix.funcs.get(name, []))
            ti = ix.types.get(name)
            if ti is not None and ti.kind == "struct":
                return  # конструктор структуры: Vector(0,0,5)
            if in_class:
                cands += ix.methods.get(name, [])
            if not cands:
                if name in ix.methods:
                    self.err(u, line, "method-as-global", f"{name}() — метод класса, а вызов из глобальной функции")
                else:
                    self.err_or_native(u, line, "unknown-call", f"функция {name} не объявлена ни в ванили, ни в моде",
                                       name)
                return
            if all(c.exec for c in cands):
                self.err(u, line, "exec-call", f"{name} — exec function, из скрипта её вызвать нельзя")
        if not caller_latent and all(c.latent for c in cands):
            self.err(u, line, "latent-call", f"{name} latent, а {f.name} нет")
        while args and not args[-1]:
            args.pop()  # хвостовая запятая f(a, b, ) в ванили компилируется (btTicket.ws:12)
        n = len(args)
        fit = [c for c in cands if c.nmin <= n <= c.nmax]
        if not fit:
            self.err(u, line, "arity", f"{name}: {n} аргумент(ов), а объявлено: " +
                     "; ".join(c.sig() for c in cands[:2]))
            return
        for pos, arg in enumerate(args):
            if len(arg) != 1 or arg[0].kind not in ("str", "id"):
                continue
            if arg[0].kind == "id" and _norm_type(scope.get(arg[0].val, "")) != "string":
                continue
            types = {_norm_type(c.params[pos][1]) for c in fit if pos < len(c.params)}
            if types == {"name"}:
                what = f"строка {arg[0].val}" if arg[0].kind == "str" else f"переменная string {arg[0].val}"
                self.err(u, line, "string-to-name",
                         f"{name}: аргумент {pos + 1} — {what}, а параметр name: нужны '..' (exe 0x3630c30)")


# -------------------------------------------------------------------- загрузка и запуск
def read_unit(path, is_vanilla):
    with open(path, "rb") as fh:
        raw = fh.read()
    pre = []
    if raw.startswith(b"\xef\xbb\xbf"):
        pre.append(Finding(path, 1, "ERROR", "bom", "UTF-8 BOM в начале файла"))
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        pre.append(Finding(path, raw[:e.start].count(b"\n") + 1, "ERROR", "encoding", f"не UTF-8: {e.reason}"))
        text = raw.decode("utf-8", errors="replace")
    return FileUnit(path, text, is_vanilla), pre


def read_globals(ini_path):
    """bin\\redscripts.ini [globals]: имя=Класс. Нет файла — GLOBALS_DEFAULT."""
    if not ini_path or not os.path.exists(ini_path):
        return dict(GLOBALS_DEFAULT)
    out, section = {}, None
    with open(ini_path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            s = raw.strip()
            if s.startswith("[") and s.endswith("]"):
                section = s[1:-1].strip().lower()
            elif section == "globals" and "=" in s and not s.startswith(";"):
                k, v = s.split("=", 1)
                out[k.strip()] = v.strip()
    return out or dict(GLOBALS_DEFAULT)


def vanilla_files(scripts):
    for dp, _, fn in os.walk(scripts):
        for f in sorted(fn):
            if f.endswith(".ws"):
                yield os.path.join(dp, f)


def build(scripts, extra_paths, ini_path=REDSCRIPTS_INI):
    """Индекс ванили + файлы из командной строки. -> (index, units к проверке, предварительные находки)."""
    ix, pre, units = Index(read_globals(ini_path)), [], []
    root = os.path.normcase(os.path.abspath(scripts))
    by_path = {}
    for p in vanilla_files(scripts):
        u, pf = read_unit(p, True)
        ix.add(u)
        by_path[os.path.normcase(os.path.abspath(p))] = (u, pf)
    mods = []
    for p in extra_paths:
        key = os.path.normcase(os.path.abspath(p))
        if key in by_path:
            u, pf = by_path[key]
        else:
            u, pf = read_unit(p, key.startswith(root))
            mods.append(u)
        units.append(u)
        pre.extend(pf)
    for u in mods:
        ix.add(u)
    return ix, units, pre


def lint_texts(vanilla, mods):
    """Для тестов: vanilla/mods — словари {путь: текст}. -> список Finding по модам."""
    ix = Index()
    for p, txt in vanilla.items():
        ix.add(FileUnit(p, txt, True))
    units = [FileUnit(p, txt, False) for p, txt in mods.items()]
    for u in units:
        ix.add(u)
    return Linter(ix, units).run()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--scripts", default=SCRIPTS, help="папка ванильных скриптов 5.0")
    ap.add_argument("--exe", default=EXE, help="witcher3.exe для поиска нативных имён (только чтение)")
    ap.add_argument("--ini", default=REDSCRIPTS_INI, help="bin\\redscripts.ini с [globals] (только чтение)")
    ap.add_argument("--rules", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.rules:
        for code, (what, src) in RULES.items():
            print(f"{code:20} {what}\n{'':20} источник: {src}")
        return 0
    if a.selftest:
        res = unittest.main(argv=["ws_lint"], module=__name__, exit=False, verbosity=1).result
        return 0 if res.wasSuccessful() else 1
    if not a.files:
        ap.error("укажи .ws файлы")
    t0 = time.time()
    ix, units, pre = build(a.scripts, a.files, a.ini)
    found = pre + Linter(ix, units, a.exe).run()
    by_file = defaultdict(list)
    for f in found:
        by_file[f.path].append(f)
    for u in units:
        fs = sorted(by_file.get(u.path, []), key=lambda x: x.line)
        e = sum(1 for x in fs if x.level == "ERROR")
        w = len(fs) - e
        for x in fs:
            print(x)
        nf = sum(1 for f in u.funcs if f.body)
        print(f"{u.path}: {'OK' if not e else 'FAIL'} errors={e} warnings={w} "
              f"functions={nf} tokens={len(u.toks)}{' (vanilla)' if u.is_vanilla else ''}")
    total_e = sum(1 for x in found if x.level == "ERROR")
    print(f"index: {len(ix.funcs)} global functions, {len(ix.methods)} method names, {len(ix.types)} types, "
          f"{len(ix.enum_vals)} enum values, {len(ix.natives)} native names, {len(ix.globals)} globals; "
          f"{time.time() - t0:.1f} s; errors total {total_e}")
    return 1 if total_e else 0


# -------------------------------------------------------------------- самопроверка
_VAN = {
    "v/core.ws": """
import function Log( text : string );
import function LogChannel( channel : name, text : string );
import function IntToString( value : int ) : string;
import latent function Sleep( time : float );
import function LoadResource( resource : string, optional isDepotPath : bool ) : CResource;
import struct Vector { import var X,Y,Z,W : float; }
enum EMCWTColor { MCWTC_Red, MCWTC_Green = 5 }
exec function logstats() { }
function LogStats() { }
import function NativeOnly( v : int );
function UsesNative() { NativeOnly( PM_DontPersist ); }
import class IScriptable extends ISerializable { }
import class CObject extends IScriptable { }
import class CResource extends CObject { }
import class CEntityTemplate extends CResource { }
import class CNode extends CObject
{
    import final function GetWorldPosition() : Vector;
    import final function HasTag( tag : name ) : bool;
}
import class CComponent extends CNode
{
    import final function SetScale( scale : Vector );
}
import class CEntity extends CNode
{
    import final function AddTimer( timerName : name, period : float, optional repeats : bool ) : int;
}
class CPlayer extends CEntity
{
    public var hp : int;
    event OnSpawned( spawnData : int ) { }
    function Heal( n : int ) { hp += n; }
}
class CR4Player extends CPlayer
{
    event OnSpawned( spawnData : int ) { }
    import final function IsEnemyVisible( enemy : CEntity ) : bool;
}
"""}


def _codes(mod_text):
    return sorted({f.code for f in lint_texts(_VAN, {"m/mod.ws": mod_text}) if f.level == "ERROR"})


class SelfTest(unittest.TestCase):
    def test_clean(self):
        src = """
function MCWT_Out( seq : int, s : string )
{
    var v : Vector;
    var p : Vector;
    var c : EMCWTColor;
    v = Vector( 0, 0, 1 );
    c = MCWTC_Green;
    Log( "[MCW#" + IntToString( seq ) + "] " + s );
    LogChannel( 'MCW', s );
    if( thePlayer )
        thePlayer.Heal( (int)v.Z );
}
exec function mcwt_ping( seq : int ) { MCWT_Out( seq, "pong" ); }
"""
        self.assertEqual(_codes(src), [])

    def test_var_after_statement(self):
        self.assertEqual(_codes("function F() { var a : int; a = 1; var b : int; }"), ["var-placement"])

    def test_var_in_nested_block(self):
        self.assertEqual(_codes("function F() { var a : int; if (a == 1) { var b : int; } }"), ["var-placement"])

    def test_global_var(self):
        self.assertEqual(_codes("var g : int;"), ["global-var"])

    def test_unknown_and_arity(self):
        self.assertEqual(_codes("function F() { Nope(); }"), ["unknown-call"])
        self.assertEqual(_codes("function F() { Log(); }"), ["arity"])
        self.assertEqual(_codes('function F() { var r : CResource; r = LoadResource("a", true, 1); }'), ["arity"])
        self.assertEqual(_codes('function F() { var r : CResource; r = LoadResource("a"); }'), [])

    def test_skipped_optional_args(self):
        self.assertEqual(_codes("function F(e : CEntity) { e.AddTimer( 'T', 1.0, ); }"), [])
        self.assertEqual(_codes("function F() { var a : array<int>; a.PushBack( 1 ); a.Erase( 0 ); }"), [])
        self.assertEqual(_codes("function F() { var a : array<int>; a.Erase( 0, 1 ); }"), ["arity"])

    def test_string_to_name(self):
        self.assertEqual(_codes('function F() { LogChannel( "MCW", "x" ); }'), ["string-to-name"])
        self.assertEqual(_codes("function F(n : CNode) { n.HasTag( \"t\" ); }"), ["string-to-name"])
        self.assertEqual(_codes('function F( s : string ) { LogChannel( s, "x" ); }'), ["string-to-name"])
        self.assertEqual(_codes("function F( n : name ) { LogChannel( n, \"x\" ); }"), [])
        self.assertEqual(_codes("function F( n : name ) { Log( n ); }"), [])   # name -> string неявно есть

    def test_exec_rules(self):
        self.assertEqual(_codes("latent exec function e1() { }"), ["exec-latent"])
        found = lint_texts(_VAN, {"m/mod.ws": "exec function e3( v : CNode ) { }"})
        self.assertEqual([(f.level, f.code) for f in found], [("WARN", "exec-param")])
        self.assertEqual(_codes("class K { exec function e2() { } }"), ["exec-in-class"])
        self.assertEqual(_codes("function F() { logstats(); }"), ["exec-call"])

    def test_latent_call(self):
        self.assertEqual(_codes("function F() { Sleep( 1.0 ); }"), ["latent-call"])
        self.assertEqual(_codes("latent function F() { Sleep( 1.0 ); }"), [])

    def test_array_method_of_call_and_const_global(self):
        # оба — ошибки компилятора 5.00c, которые раньше линтер пропускал (mcw_spike.ws, 2026-10-03)
        self.assertEqual(_codes("function G() : array<int> { var a : array<int>; return a; } "
                                "function F() { var n : int; n = G().Size(); }"), ["array-method-of-call"])
        self.assertEqual(_codes("function G() : array<int> { var a : array<int>; return a; } "
                                "function F() { var a : array<int>; var n : int; a = G(); n = a.Size(); }"), [])
        self.assertEqual(_codes("function F( n : name ) { thePlayer.Heal( 1 ); }"), [])
        self.assertEqual(_codes("@addField(CR4Player) var mcwX : int; "
                                "function F() { thePlayer.mcwX = 1; }"), ["const-global-field"])
        self.assertEqual(_codes("@addField(CR4Player) var mcwX : int; "
                                "function F() { var p : CR4Player; var n : int; p = thePlayer; p.mcwX = 1; "
                                "n = thePlayer.mcwX; }"), [])

    def test_method_from_global(self):
        self.assertEqual(_codes("function F() { Heal( 1 ); }"), ["method-as-global"])

    def test_struct_prop_of_call(self):
        self.assertEqual(_codes("function F(n : CNode) { var z : float; z = n.GetWorldPosition().Z; }"),
                         ["struct-prop-of-call"])

    def test_empty_if_and_array_literal(self):
        self.assertEqual(_codes("function F(a : int) { if (a == 1) ; }"), ["empty-if"])
        self.assertEqual(_codes("function F(a : int) { if (a == 1) { } else { } }"), [])  # как actor.ws:5275
        self.assertEqual(_codes("function F() { var a : array<int>; a = { 1, 2 }; }"), ["array-literal"])

    def test_reserved_names(self):
        self.assertEqual(_codes("function F() { var map : int; }"), ["reserved-name"])
        self.assertEqual(_codes("function F( local : int ) { }"), ["reserved-name"])
        self.assertEqual(_codes("function F() { var m : map<string, int>; }"), [])

    def test_duplicates(self):
        self.assertEqual(_codes("function Log( text : string ) { }"), ["duplicate"])
        found = lint_texts(_VAN, {"m/mod.ws": "function logStats() { }"})
        self.assertEqual({(f.level, f.code) for f in found}, {("WARN", "duplicate-case")})  # LogStats и logstats

    def test_duplicates_in_one_file(self):
        self.assertEqual(_codes("function MCWT_D() { }\nfunction MCWT_D() { }"), ["duplicate"])
        self.assertEqual(_codes("struct SMCWT { var a : int; }\nstruct SMCWT { var a : int; }"), ["duplicate"])
        self.assertEqual(_codes("class CMCWT { }\nclass CMCWT { }"), ["duplicate"])
        self.assertEqual(_codes("class CMCWT2 { function A() { } function A() { } }"), ["duplicate"])
        self.assertEqual(_codes("function MCWT_A() { }\nfunction MCWT_B() { }"), [])

    def test_unknown_type(self):
        self.assertEqual(_codes("function F() { var t : CEntityTemplat; }"), ["unknown-type"])
        self.assertEqual(_codes("function F( t : array<CEntit> ) { }"), ["unknown-type"])
        self.assertEqual(_codes("function F() : CEntit { return NULL; }"), ["unknown-type"])
        self.assertEqual(_codes('function F() { var r : CResource; r = (CEntityTemplat)LoadResource( "a" ); }'),
                         ["unknown-type"])
        self.assertEqual(_codes("function F() { var m : map<string, CEntityTemplate>; }"), [])
        self.assertEqual(_codes('function F() { var t : CEntityTemplate; t = (CEntityTemplate)LoadResource( "a" ); }'),
                         [])

    def test_undeclared(self):
        self.assertEqual(_codes("function F() { var nSet : int; nSte += 1; }"), ["undeclared"])
        self.assertEqual(_codes("function F() { var c : EMCWTColor; c = MCWTC_Gren; }"), ["undeclared"])
        self.assertEqual(_codes("function F() { NativeOnly( PM_DontPersistt ); }"), ["undeclared"])
        self.assertEqual(_codes("function F() { NativeOnly( PM_DontPersist ); }"), [])   # ваниль так пишет
        self.assertEqual(_codes("function F( a : int ) { var b : int = a; b = a; }"), [])
        self.assertEqual(_codes("function F() { var b : int = zz; }"), ["undeclared"])  # инициализатор var
        self.assertEqual(_codes("@addField(CR4Player) var mcwtN : int;\n"
                                "@addMethod(CR4Player) function MCWT_M() { mcwtN = hp; }"), [])

    def test_not_member(self):
        self.assertEqual(_codes("function F( e : CEntity ) { e.SetScale( Vector( 2, 2, 2 ) ); }"), ["not-member"])
        self.assertEqual(_codes("function F( c : CComponent ) { c.SetScale( Vector( 2, 2, 2 ) ); }"), [])
        self.assertEqual(_codes("function F() { thePlayer.Heal(); }"), ["arity"])     # по цепочке CR4Player
        self.assertEqual(_codes("function F() { thePlayer.Heal( 1 ); }"), [])

    def test_missing_semicolon(self):
        self.assertEqual(_codes("function F() { var a, b : int;\n a = 1\n b = 2; }"), ["missing-semicolon"])
        self.assertEqual(_codes('function F() { Log( "a" )\n Log( "b" ); }'), ["missing-semicolon"])
        self.assertEqual(_codes("function F() { var a : int; if( a == 1 ) { a = 2 } }"), ["missing-semicolon"])
        self.assertEqual(_codes("function F() { var a : int; a = 1 }"), ["missing-semicolon"])
        self.assertEqual(_codes("function F( a : int ) { var f : float; if( a == 1 ) f = (float)a; "
                                "while( a > 1 ) a -= 1; }"), [])

    def test_balance_and_bom(self):
        self.assertEqual(_codes("function F() { Log( \"x\" ; }"), ["balance"])
        self.assertTrue(read_unit(__file__, False)[1] == [])

    def test_annotations_ok(self):
        src = """
@addField(CR4Player)
public var mcwtSlots : array<CEntity>;

@addField(CR4Player)
public var mcwtIndex : map<string, CEntity>;

@wrapMethod(CR4Player)
function OnSpawned( spawnData : int )
{
    var result : bool;
    result = wrappedMethod( spawnData );
    this.mcwtSlots.Clear();
}

@addMethod(CR4Player)
timer function MCWT_Tick( dt : float, id : int ) { }
"""
        self.assertEqual(_codes(src), [])

    def test_wrap_kind(self):
        def warns(src):
            return [f.code for f in lint_texts(_VAN, {"m/mod.ws": src}) if f.level == "WARN"]
        body = "( spawnData : int ) { var r : bool; r = wrappedMethod( spawnData ); }"
        self.assertEqual(warns("@wrapMethod(CR4Player) function OnSpawned" + body), ["wrap-kind"])
        self.assertEqual(warns("@wrapMethod(CR4Player) event OnSpawned" + body), [])

    def test_annotation_errors(self):
        self.assertEqual(_codes("@addField(CNope) var x : int;"), ["annotation"])
        self.assertEqual(_codes("@wrapMethod(CR4Player) function Heal( n : int ) { wrappedMethod( n ); }"),
                         ["wrap"])  # Heal объявлен в предке CPlayer
        self.assertEqual(_codes("@wrapMethod(CR4Player) function OnSpawned( s : int ) { }"), ["wrap"])
        self.assertEqual(_codes("@addMethod(CR4Player) function OnSpawned( s : int ) { }"), ["duplicate"])
        self.assertEqual(_codes("@addField(CR4Player) var hp : int;"), ["duplicate"])
        self.assertEqual(_codes("class K { @addField(CR4Player) var q : int; }"), ["annotation"])

    def test_wrap_native(self):
        self.assertEqual(_codes("@wrapMethod(CR4Player) function IsEnemyVisible( e : CEntity ) : bool "
                                "{ return wrappedMethod( e ); }"), ["wrap"])
        self.assertEqual(_codes("@replaceMethod(CR4Player) function IsEnemyVisible( e : CEntity ) : bool "
                                "{ return false; }"), ["wrap"])
        self.assertEqual(_codes("@replaceMethod function Log( text : string ) { }"), ["wrap"])

    def test_global_flags_and_timer(self):
        self.assertEqual(_codes("timer function T( dt : float, id : int ) { }"), ["global-flags"])
        self.assertEqual(_codes("class K2 { timer function T( dt : float ) { } }"), ["timer-params"])


if __name__ == "__main__":
    sys.exit(main())
