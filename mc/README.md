# mc — серверная половина: Fabric-мод MCW

Headless-сервер Minecraft 1.21.11 владеет миром блоков вокруг Геральта, а Ведьмак его только рисует. Мод MCW делает
три вещи:

- держит связь с мостом: TCP с JSON-строками на `127.0.0.1:25600`;
- зеркалит изменения блоков: миксин на `Level.setBlock`, сравнение с зеркалом в конце тика, сообщение `blocks`;
- ставит на место Геральта настоящий `ServerPlayer` по схеме Carpet. Двигает его мост, ломает и ставит он по
  правилам выживания, подбирает дроп с земли и крафтит через ванильные меню (этап 3);
- шлёт мосту мобов и предметы вокруг Геральта потоком `ent` (этап 5). Мобы живут на ванильном ИИ: крипер идёт к
  Геральту, шипит и взрывается;
- уступает место Геральта скрытому клиенту Майнкрафта (`..\mcclient`). Клиент входит под тем же именем — марионетка
  уходит (playerdata сохраняется), клиент выходит — марионетка возвращается из той же playerdata. Всё остальное
  (зеркало, act, крафт, мобы, inv, hp) работает с тем, кто сейчас Геральт.

Результаты прогона — в [SMOKE.md](SMOKE.md), формат сообщений — в [protocol_examples.jsonl](protocol_examples.jsonl).

## Версии

Minecraft 1.21.11, Fabric Loader 0.19.5, Fabric API 0.141.6+1.21.11, Mojang mappings (`loom.officialMojangMappings()`),
плагин `net.fabricmc.fabric-loom-remap` **1.17.21**, Gradle 9.7.1 (wrapper), Java 21.
Loom 1.18.x не подходит: ему нужна JVM 25 уже для запуска Gradle.

## Сборка

```powershell
$env:JAVA_HOME = "$env:APPDATA\.minecraft\runtime\java-runtime-delta\windows\java-runtime-delta"  # JDK 21; java в PATH тут 1.8
.\gradlew.bat build deployRun
```

`build` собирает `build\libs\mcw-0.1.0.jar` (intermediary, для обычного сервера). `deployRun` кладёт его вместе с
`fabric-api-0.141.6+1.21.11.jar` в `run\mods\`. Первая сборка качает около 1 ГБ в `%USERPROFILE%\.gradle` и идёт
несколько минут, повторные — секунды.

## Сервер в `run\`

Один раз:

1. `run\fabric-server-launch.jar` — серверный лаунчер Fabric:
   `https://meta.fabricmc.net/v2/versions/loader/1.21.11/0.19.5/1.1.2/server/jar`. При первом запуске он сам скачает
   `server.jar` и библиотеки.
2. `run\eula.txt` с `eula=true`. EULA Minecraft принимает пользователь, скрипт этот файл не создаёт.
3. `.\gradlew.bat build deployRun`.

```powershell
.\run_server.ps1           # копирует server.properties.mcw -> run\server.properties, ждёт Done, печатает {"pid","wall_s","done_s"}
python fake_bridge.py stop       # мягкая остановка с сохранением мира
.\run_server.ps1 -Stop     # жёстко, по PID из run\server.pid: несохранённое с последнего автосохранения теряется
```

Процесс запускается скрытым. Лог Minecraft пишется в `run\logs\latest.log`, вывод консоли — в
`run\logs\console.out.log`. Настройки сервера лежат в `server.properties.mcw`: только 127.0.0.1, offline-режим,
пустой мир, `pause-when-empty-seconds=0`, `spawn-protection=0`. При каждом старте мод выключает монстрам MC появление
сами собой (`spawn_phantoms`, `spawn_monsters`, `spawn_patrols`, `spawn_wandering_traders` = false: фантомы бессонницы
летали ящиками и убивали Геральта, ночные зомби лезли бы к его ногам; призыв и лошадь работают) и ставит три гейм-рула под
игрока клиента: `player_movement_check=false` (клиент идёт за Ведьмаком сам, быстрое перемещение — прыжок на сотни
блоков), `immediate_respawn=true` (экрана смерти в скрытом окне никто не увидит) и `keep_inventory=true` (смерть
в пустоте под миром Ведьмака не отнимает вещи). Геральт при первом входе получает стартовый набор (`Kit.java`: меч,
лук, инструменты, блоки, еда, щит в левой руке, железная броня, стрелы); отметка — тег игрока `mcw_kit_1`, второй
раз набор не выдаётся. Настройки мода — в `run\config\mcw.properties`
(host, port, radius, name, spawn, queue_limit; с этапа 5 ещё `ent_radius=32`, `ent_every=2`, `ent_max=64`), его
создаёт сам мод при первом старте. В старом файле новых ключей нет, тогда берутся значения по умолчанию. Любой ключ
можно перекрыть через `-Dmcw.<ключ>=`.

## Проверка

```powershell
python -m unittest test_fake_bridge -v          # оффлайн: фрейминг, зеркало, protocol_examples, TCP-петля
python smoke_0prime.py --start --stop           # этап 0': сервер + мод + фейковый мост, ~3 мин (с рестартом), итог — таблица
python smoke_0prime.py --idle-only --start --stop --pause 60   # Геральт держит сервер при ванильной паузе
python fake_bridge.py watch 60                  # руками: стать мостом, снапшот и поток событий
python fake_bridge.py act place 2 70 0 up 0     # только ack/dig; блоки видно в watch
python fake_bridge.py recipes                   # что Геральт может скрафтить сейчас
python fake_bridge.py craft minecraft:oak_planks 2
python fake_bridge.py spawn minecraft:creeper 6 # крипер в 6 блоках перед Геральтом; его самого видно в watch (ent)
```

Сквозной прогон этапов 3 и 5 (сервер, мост и фейковая игра) — `..\bridge\smoke_stage35.py`, итог — в
[SMOKE.md](SMOKE.md), раздел «Этапы 3 и 5».

Мостом мод считает соединение, приславшее `hello`: `fake_bridge.py hello` и `watch` вытесняют живой `bridge.py`.
Остальные команды (`cmd`, `pos`, `act`, `raw` без hello, `stop`) работают рядом с ним: ответы приходят им, события
мира — мосту.

## Протокол v1, коротко

Мост → мод: `hello{want.radius}`, `pos{p,yaw,pitch}`,
`act{id,a:break|place|use|cancel|select,b,face,hit,slot,sneak?,p?,yaw?,pitch?}`, `give{item,n}`, `cmd{c}`,
`ground{c:[x,z,y0,y1…]}`, `ground_clear{box?,any?}`, `snap{r}` (только мост), `ping{ts}`; с этапов 3 и 5 —
`recipes{id}`, `craft{id,recipe,n}`, `spawn{id,type,d}`; режим Стива — `horse{}` (лошадь Геральта, ответ
`horse{ok,id,new,p}`).

Мод → мост (у каждого сообщения есть `tick`):
- `hello`, затем `chunk{c,pal,b:[x,y,z,i…]}`×N и `snap_end` — всё за один тик. Радиус не больше view-distance
  сервера, фактический — в `hello.radius`, настройки потока `ent` — в `hello.ent{r,every,max}`;
- `blocks{pal,set:[x,y,z,i…],clear:[x,y,z…]}` раз в тик и сразу после `act`;
- `unchunk`, `inv`, `hp`, `ent`, `explosion`, `pteleport{p,dim}`, `death{msg}`, `respawn{p,dim}`,
  `geralt{client,p,dim}` (Геральт сменился: клиент вошёл или вышел) — события, только мосту;
- `dig{id,stage}`, `ack{id,ok,why}`, `cmd_out`, `ground_ok`, `recipes`, `pong`, `error` — ответы тому, кто спросил.

Воздух и `barrier` в списках не бывает: `barrier` — это рельеф Ведьмака. Новый `chunk` может прийти в любой момент:
чанк догрузился или Геральт ушёл. `snap` с меньшим радиусом и смена измерения (`pteleport`/`respawn` с другим `dim`)
сначала снимают лишние чанки через `unchunk`, потом шлют снапшот.

`place` работает как правый клик ванильного клиента: `useItemOn`, а если тот ответил PASS — `useItem`. Так работают
вёдра, лодки и жемчуг; они целятся лучом из глаз, поэтому в `act` нужны `yaw`/`pitch`. `use` — клик в воздух.
`ok` ставится, только если поменялись блоки или инвентарь (или началось использование предмета, например еда).
Если клик перехватил сам блок (верстак, сундук), ответ `why:used_block`; со `sneak:true` блок ставится на него.

**Игрок клиента.** `hello.player.client = true`, когда Геральт — игрок скрытого клиента. Его позицию ведёт сам
клиент (идёт за Ведьмаком или, в режиме Стива, ходит сам). Из `pos` и `act.p` сервер берёт только прыжки дальше
8 блоков: тогда телепорт с сервера. Поставил или сломал блок клиент — `blocks` уходит мосту сразу после его пакета
(`GamePacketListenerMixin`: `handleUseItemOn`, `handlePlayerAction`, `handleUseItem`), а не в конце тика.
`act select` для него ещё и шлёт клиенту `ClientboundSetHeldSlotPacket`, иначе следующий его клик
пришёл бы со старым слотом. Пока Геральт меняется (1–2 тика между уходом марионетки и входом клиента), сообщения
моста, кроме `ping` и `cmd`, получают `error{why:no_geralt}`, а `hello` ждёт. Не вошёл за 10 с — марионетка
возвращается.
Числа от моста должны быть конечными и внутри мира, иначе `bad_args`.

Свой рельеф (`ground`) мод хранит в `SavedData` уровня (`world/data/mcw_ground.dat`), поэтому `ground_clear`
находит его и после рестарта. Колонны в незагруженных чанках `ground` пропускает и возвращает в `skip`.
`ground_clear{box, any:true}` убирает ещё и чужие barrier в загруженных чанках box.

### Инвентарь (этап 3)
`inv{sel, s:[[slot,id,n]…], dmg:[[slot,повреждение,предел]…]}` — весь `Inventory` 1.21.11 (43 слота): 0–8 хотбар,
9–35 рюкзак, 36–39 броня (ноги…голова), 40 левая рука, 41–42 тело и седло (у игрока пусты); `sel` — выбранный
слот хотбара. Сервер — источник правды. Мод шлёт `inv` в том же тике, где инвентарь поменялся: `act`, подбор с земли
(`GeraltPlayer.take`), крафт, `/give` (по `Inventory.getTimesChanged`); раз в 10 тиков — сверка подписью (износ
инструмента `timesChanged` не трогает). `act select` повторяет ванильный пакет выбора слота: смена слота прерывает
использование предмета правой рукой.

Подбор с земли работает как у обычного игрока: `Player.aiStep` из `doTick` трогает предметы в рамке +1/+0,5/+1 блока
вокруг Геральта. `noPhysics` и удержание позиции этому не мешают. Предмет, упавший на barrier-рельеф, лежит на
высоте ног Геральта.

### Крафт (этап 3)
- `recipes{id}` → `recipes{id, table, r:[[рецепт, предмет, штук, сколько раз, сетка]…]}` — рецепты верстака из
  `RecipeManager` сервера, которые можно скрафтить из инвентаря хотя бы раз (`StackedItemContents.canCraft`).
  Сетка 2 или 3 считается как в клиентской книге рецептов: ширина и высота shaped, число ингредиентов shapeless.
  Рецепты 3×3 попадают в список и без верстака, `table` — ближайший верстак в досягаемости или `null`.
- `craft{id, recipe, n}` → `ack{id, ok, why?, made, crafts, item, table}`. Крафт идёт через ванильные меню, как у
  клиента с книгой рецептов. 2×2 — `InventoryMenu` самого игрока; 3×3 — `CraftingMenu` верстака, до которого
  Геральт дотягивается: дальность + 1 блок, как клик по блоку, плюс `mayInteract`. Меню открывает сам блок
  (`getMenuProvider`). На каждый крафт: `handlePlacement` (`ServerPlaceRecipe` раскладывает ингредиенты, результат
  считает `RecipeManager`), затем shift-клик по результату (`onCraftedBy`, расход сетки, остатки вроде пустых вёдер).
  В конце меню закрывается, остатки сетки возвращаются в инвентарь.
- `why`: `bad_recipe`, `impossible` (особый рецепт без раскладки), `no_table`, `missing`, `inv_full`, `rejected`
  (`doLimitedCrafting`, выключенный предмет).

### Мобы (этап 5)
- `ent{spawn, move, hp, fuse, item, remove}` раз в `ent_every` тиков, только если что-то поменялось. Мобы и
  предметы не дальше `ent_radius` от Геральта, не больше `ent_max` ближних. `spawn` — `[id,тип,x,y,z,yaw,hp]` (ноги,
  yaw Майнкрафта, у предметов `hp` = `null`); `move` — сдвиг больше 2 см или поворот больше 2°; `fuse` — крипер
  шипит или подожжён; `item` — стопка предмета; `remove` — умер, подобран, взорвался, ушёл из радиуса или уровня.
  После `hello` все приходят заново как `spawn`. `id` — `Entity.getId()`, после рестарта сервера ids новые, но мост
  после рестарта всё равно шлёт `hello`.
- `spawn{id, type, d}` — тестовый призыв: моб в `d` блоках перед Геральтом, на первой опоре от +3 до −8 блоков, как
  `/summon` (`EntityType.spawn`, `finalizeSpawn`). Ответ — `ack{ent, p}`, `why`: `bad_type`, `bad_args`,
  `no_ground`, `rejected`.
- ИИ ванильный. Крипер выбирает целью Геральта-`ServerPlayer`, идёт к нему по barrier-рельефу и шипит ближе 3 блоков.
  Пока шипит, он идёт следом за отступающим игроком и взрывается через 30 тиков. Взрыв даёт `explosion{src:creeper}`,
  снесённое приходит в `blocks` того же тика, урон Геральту — `hp` (или `death`), сам крипер — `ent remove`.

## Файлы

| Файл | Что |
|---|---|
| `src/main/java/mcw/Mcw.java` | entrypoint, события Fabric, `later()` — задача в очередь сервера |
| `Link.java` | TCP-сервер, мост и отладочные соединения, потоки чтения и записи, очередь отправки с лимитом |
| `Bridge.java` | разбор сообщений и маршрут ответов, `inv`/`hp`/`explosion`/`death`, смена измерения, `spawn` |
| `BlockSync.java` | зеркало по чанкам, снапшоты, `flush()` грязных позиций |
| `Actions.java` | `act`: ломка через `handleBlockBreakAction` (с `dig`), установка через `useItemOn`/`useItem`, выбор слота |
| `Craft.java` | `recipes` и `craft` через `RecipeManager` и ванильные меню (2×2 инвентаря, 3×3 верстака) |
| `EntSync.java` | поток `ent`: мобы и предметы у Геральта, дельтами |
| `Ground.java` | рельеф Ведьмака как barrier, позиции в `SavedData` |
| `Kit.java` | стартовый набор Геральта (один раз, тег `mcw_kit_1`) |
| `Horses.java` | лошадь Геральта по `horse{}` от моста: ручная, под седлом, скорость 0,3375, прыжок 1,0; одна (тег `mcw_horse`), найденная в 256 блоках подходит снова. Ответ `horse{ok, id, new, p}` |
| `GeraltPlayer.java`, `FakeConnection.java`, `FakeGameListener.java` | `ServerPlayer` без клиента (марионетка); смена Геральта на игрока клиента и обратно — `Mcw.java` (`dropPuppet`, `joined`, `left`) |
| `mixin/` | `GamePacketListenerMixin` (blocks сразу после пакета клиента; `handleMoveVehicle` без отката лошади под Геральтом: barrier-рельеф сервера округлён, клиент ездит по коллизии Ведьмака), `LevelMixin` (setBlock), `PlayerListMixin` (listener, respawn, уход марионетки при входе клиента), `PlayerMixin` (noPhysics любому Геральту), `ServerExplosionMixin`, `ConnectionAccessor` |
| `fake_bridge.py`, `test_fake_bridge.py` | клиент протокола и его оффлайн-тесты |
| `smoke_0prime.py`, `run_server.ps1`, `server.properties.mcw` | этап 0′ |

## Чужое

Схема `ServerPlayer` без клиента (FakeConnection с `EmbeddedChannel`, listener с пустым `send`, подмена в
`PlayerList.placeNewPlayer` и `respawn`, ручной `loadPlayerData`, `doTick` из `tick`, снятие флага смены измерения в
`teleport`) взята из
[fabric-carpet 1.4.194](https://github.com/gnembon/fabric-carpet/tree/1.4.194), MIT, Copyright (c) 2020 gnembon.
Код написан заново, атрибуция стоит в комментариях классов. `PlayerMixin` (noPhysics), `LevelMixin` (`Level.setBlock`)
и `ServerExplosionMixin` сделаны по образцу примера `minecraft-gta5-passthrough` из плагина universal-modder (MIT,
Copyright (c) 2026 Rehan and universal-modder contributors), атрибуция — в комментариях миксинов. Gradle wrapper
взят оттуда же. Декомпилированный код Mojang в проект не копировался.

## Не сделано

Монстры MC сами не появляются (гейм-рулы, раздел «Сервер в `run\`»), поэтому ночь MC безопасна; время суток с
Ведьмаком не связано. `boom` от моста, урон по мобу из Ведьмака (вариант — в `..\bridge\README.md`, «Мобы»), отдельные измерения под
миры Ведьмака (смену измерения мод уже переживает: `pteleport`/`respawn` с `dim` и новый снапшот), синхронизация
времени суток. Печь, наковальня и прочие станции, кроме верстака, в `craft` не входят.
