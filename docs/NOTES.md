# Minecraft × The Witcher 3: что выяснили до спайка

План и этапы — в [PLAN.md](PLAN.md). Здесь справочник фактов о движках: исследование 2026-10-02 (шесть тем, каждую
перепроверял отдельный скептик), ключевые номера строк перепроверены 2026-10-03. Хронологию допишем после спайка.

Метки: **[проверено]** — на этой машине или в первоисточнике (код, официальная страница), **[по данным сообщества]** —
описания модов, форумы, community-doc, **[проверить]** — ответ даст только спайк. `<игра>` =
`C:\Program Files (x86)\Steam\steamapps\common\The Witcher 3`. Пути вида `engine\game.ws:129` — от
`<игра>\content\content0\scripts`. Смещения в exe — файловые.

## The Witcher 3 5.00c (REDengine 3, DX12)

### Версия и запуск
- **[проверено]** `bin\x64_dx12\witcher3.exe`: 90 674 640 байт, FileVersion 5.0.0.1044392, метка `v 5.00c`, строка
  `5.0.1044392  P4CL: 13360103`. Steam buildid 25646871, обновлён 2026-10-01 15:41 UTC. DX11-сборки нет.
- **[проверено]** На 5.00c игру не запускали: в `dx12user.settings` `LastLaunchVersion="v 5.00b"`,
  `LastSeenVersion=500`. Первый старт может показать лаунчер и Mod Hub.
- **[проверено]** 5.0 Remastered вышел 2026-09-29 (PC, PS5, Xbox Series, Switch 2); 2026-10-01 — 5.00b на консолях и
  Xbox PC и hotfix 5.00c на PC. Ветки Steam для отката: next-gen 4.04 (buildid 14504303), classic 1.32 (3280809).
  **[по данным сообщества]** Первая PC-сборка 29.09 — уже 5.00b, 5.0.0.1041720 (rw3d PR #12; Nexus 12945: «Scripts
  4.04b vs 5.00b - 29th September 2026», «5.00b vs 5.00c - 1st October»). С этим сходится локальный `v 5.00b` выше.
- **[проверено]** Поддержка CDPR: «Mods containing xml files, script files, or w3strings will not work upon the release
  of the update», Mod Hub работает на mod.io. Фраза оттуда же «script mods must be created via REDkit» относится к
  mod.io, локальные моды она не запрещает.
- **[проверено]** Steam запускает `REDprelauncher.exe`. `launcher-skip` — его опция (рядом `launcher-headless`,
  `launcher-verify`), в witcher3.exe такой строки нет. **[по данным сообщества]** `--launcher-skip -debugscripts`
  работает на 5.00b (AIWitcher). Проверка:
  `Get-CimInstance Win32_Process -Filter "Name='witcher3.exe'" | Select CommandLine`.
- **[проверено]** Движок читает `commandline.txt` (в exe есть флаг `-ignoreCommandLineTxt`). Из какой папки —
  **[проверить]**.
- **[проверено]** Конфиги: `bin\config\config_description.xml` задаёт порядок base → developer → r4game → platform\pc,
  поверх идут `Documents\The Witcher 3\*.settings`. `bin\config\base\general.ini` = только `[General] ConfigVersion=7`.
  `bin\gameconf.cfg`: gameClass CR4Game, playerClass CR4Player. Глобальные объекты скриптов (`theGame`, `thePlayer` :
  CR4Player, `theCamera` : CCamera, `theInput`) задаёт `bin\redscripts.ini`.
- **[проверено]** Импорты exe: DINPUT8, WINMM, DSOUND, WINHTTP, dbghelp, WS2_32. `dinput8.dll` в `bin\x64_dx12` нет.

### Раскладка контента
- **[проверено]** `content\` = `content0\` + `metadata.store` (VTM v7, 45 629 011 байт) + `metadata.censored.store` +
  `metadata.store.stamp`. metadata.store перечисляет все 31 бандл, включая `bob.bundle`, поэтому шаблон из BaW должен
  резолвиться по пути в любом мире — **[проверить]** в игре.
- **[проверено]** В `content0\` кроме `bundles\` лежат рыхлые `engine\`, `hdr\`, `levels\`, `movies\`, `scripts\` (1527
  `.ws`), а также `collision.cache`, `texture.cache` (31 ГБ), `cookedfinal.redscripts`, `soundspc.cache`, шейдерные кэши
  и w3strings.
- **[проверено]** DLC слиты в основной депо: `bundles\ep1.bundle` (261 456 560 байт), `bob.bundle` (536 801 984),
  `dlc0.bundle` (34 810 816). `<игра>\dlc-tombstones\` — 18 папок (bob, ep1, dlc1–16) и 22 файла `*.tombstone` по
  0 байт, в appmanifest ровно 22 депо с size 0, строки `tombstone` в exe нет. Депо-пути DLC прежние (`dlc\bob\data\…`).
- **[проверено]** 31 бандл, 365 866 записей, 292 505 уникальных путей, из них 26 496 `.w2mesh` и 24 098 `.w2ent`. Сжатие
  только none (177 104) и zlib (188 762), lz4/snappy/doboz нет.
- **[проверено]** Движок сканирует `mods\`, `dlc\` и `..\..\workshop\content\292030\` (рядом строки `%s%s\content\`,
  `modio_`, `redkit_`, `info.json`, `Priority`, `Enabled`). Папок `mods\` и `dlc\` сейчас нет, подписок Workshop нет.

### Моды и тумблеры
- **[проверено]** Раскладка скриптового мода по wiki REDkit: `<игра>\mods\modX\content\scripts\local\*.ws`, имя папки
  начинается с `mod`. **[по данным сообщества]** На PC 5.0 так работают No Level Requirements (Nexus 13138), Geralt
  Cheats (13153), modSaveNames, AIWitcher (5.00b) и Spawn Menu 2.0: игра компилирует скрипты при первом старте.
- **[проверено]** Тумблеры — `Documents\The Witcher 3\dx12user.settings`, секция `[ContentManager/Mods]`: `Enabled`,
  `EnabledLocal`, `EnabledWorkshop` = true, `MaxNameLength=64`. В меню игры это
  `bin\config\r4game\user_config_matrix\pc\gameplay.xml:117-124`, там же `DisableModVerification`, `ModioEnabled`,
  `DisableRestartPopups`.
- **[проверено]** У REDlauncher 5.5.0.5 свой тумблер модов (`setModsEnabled`, `openModsErrorLog` в GUI), в
  REDlauncher.exe есть `-disablemods`, в REDprelauncher — `UserGameModsEnabled`. В `launcher.ini` ключа для W3 пока нет.
  **[по данным сообщества]** «With mods off there, the game skips the mods folder», «The launcher can turn mods off by
  itself after a game update» (Spawn Menu 2.0). Поэтому проверяем и cmdline, и settings.
- **[проверено]** `theGame.AreModsEnabled()` (`engine\game.ws:303`) и `CModHandlerSystem`
  (`game\systems\modHandlerSystem.ws`, новый в 5.0): подписки mod.io, `IsModdedSave` (`:122`),
  `CheckGameVersionModsInSave` (`:125`). Сейвы получают `modsMetadata`/`modName`/`modGameVersion`. Что отключает пометка
  «модифицированный», **[проверить]**.
- **[проверено]** `mods.settings` (`[modName] Enabled=1`, `Priority=1..9999`; без файла моды идут по алфавиту) описан в
  wiki REDkit 2024. Цельной строки `mods.settings` в exe 5.00c нет — **[проверить]**.
- **[проверено]** `dx11filelist.txt`/`dx12filelist.txt` в 5.0 нет; в `user_config_matrix\pc` 10 ванильных xml, игра
  берёт любой xml оттуда. **[по данным сообщества]** XP Boost (13106) и RefinedPT (13075) так добавляют пункты в
  Settings → Mods.
- **[по данным сообщества]** Моды на mod.io не могут иметь своё меню, свои клавиши, правки ini и dll, чужие бренды
  запрещены. Script Merger 0.6.4 для 5.0 не годится (есть Script Merger Remastered 1.2, Nexus 13076); нам он не нужен.

### Компиляция скриптов
- **[проверено]** `content0\cookedfinal.redscripts` (6 625 059 байт, заголовок `u32 7`, `x64`, `1044392`) — байткод
  ванили под этот билд. Пересборку движок решает по платформе, версии компилятора и CRC каталога скриптов («Scripts were
  compiled for '%s' expected '%s'», «The scripts directory has changed», «Calculating scripts CRC»). Пересобранный кэш
  называется по шаблону `%s.%s.redscripts` (на 4.04 — `x64.final.redscripts`), его имя на 5.0 **[проверить]**. Есть ещё
  формат `scripts.redscriptsblob`. Строки `x64.final.redscripts` в exe нет: совет «удали *.redscripts» к 5.0 не
  относится.
- **[проверено]** Флаги в exe: `-forcescriptcompilation`, `-noscriptcrc`, `-verifyscriptsandexit`, `-dumpscripts=`,
  `-compile_nonstrict`, `-scriptdefines=`, `-disablemods`, `-disableModio`. Что именно делает каждый, **[проверить]**.
- **[проверено]** Ошибки компиляции показывает модальное окно «Script compilation error» / «There were errors compiling
  scripts. Try again?» с файлом и строкой. По сети: `[scripts][reload]` → уведомления ScriptCompiler, признак успеха —
  `finished` с i8 = 0. Текст «Script compilation finished successfully» печатает клиент rw3d, в exe его нет. Файла с
  логом компиляции не нашли. Офлайн проверяет commandlet `compile scripts` у wcc_lite из REDkit 5.0.
- **[по данным сообщества]** Старые моды падают на новых ключевых словах: «unexpected TOKEN_MAP»
  (inventorycomponent.ws), «unexpected TOKEN_SET» (r4player.ws). Идентификаторы `map` и `set` не используем.
- **[проверено]** REDkit 5.0 добавил `precompiled.rsblob` (script blobs, так скриптовые моды работают на консолях),
  scope-based overrides и обёртку нативов. В exe: «@wrapMethod cannot wrap '%s' because it is native (only allowed in
  modio mods)». Нам не нужно: нативы не оборачиваем.

### Отладочный протокол
- **[проверено]** Сервер в exe 5.00c: `-net`, `no-netlog`, `-debugscripts`, `Listen for incoming connections...`,
  `DebugServer`, `RawNetworkThread`, `127.0.0.1`, `BIND`, `Ping`/`Pong`, `Remote` и остальные пространства имён,
  `RootPathConfirm`, `pkgSyncListing`, `OpcodeBreakdownRequest/Response`, ответы exec, сообщения отладчика
  (`DebugSessionStart`, `BreakpointToggle`, `EvalChildren`…).
- **[проверено]** Порт выбирается в машинном коде по смещениям 0x227c9e0 и 0x2281bd0:
  `cmp byte [rip+…],0; mov eax,37001; mov edx,37000; cmovne dx,ax`. 37001 — игра сама по себе, 37000 — игра внутри
  REDkit (как `WitcherPort::Game/Editor` в rw3d). Какой включён, решает флаг, поэтому клиент пробует 37001, потом 37000.
- **[по данным сообщества]** rw3d PR #12 (2026-09-30, не влит), Steam 5.0.0.1041720: exec, reload, rootpath и modlist
  работают. Все строки в ответах игры стали UTF-8 (раньше rootpath, modlist и сообщения компилятора шли в UTF-16),
  поэтому rw3d_cli 0.8.0 падает на rootpath/modlist («Waited too long for the response») и reload («log::message: Type
  mismatch»). Запросы клиента не менялись. **[проверено]** Ответ exec был UTF-8 и до 5.0, так что мосту, который делает
  только exec, смена не мешает. **[проверить]** Наша сборка 1044392.
- **[проверено]** Канал как рантайм-транспорт уже применяли: Chaos Tricks (1.32, хватало `-net`), Witcher3-Multiplayer
  (4.04+), Witcher Online (4.04, январь–июль 2026).

Таблица **[проверено]** по исходникам rw3d @5a0cf7d и побайтной сверке с собранным rw3d_net (13 видов пакетов, 0
расхождений с нашим кодировщиком).

| Что | Байты (всё big-endian) |
|---|---|
| Пакет | `DE AD` · u16 длина всего пакета (с головой, длиной и хвостом; минимум 6, максимум 65 535) · поля · `BE EF` |
| Поле | 2-байтный тег + значение: `81 08` i8, `81 16` i16, `81 32` i32, `71 32` u32, `81 64` i64, `AC 08` UTF-8, `9C 16` UTF-16BE |
| Строка | тег · `81 16` · i16 длина · данные. У UTF-8 длина в байтах (≤32 767), у UTF-16 — в кодовых единицах |
| BIND | `[utf8 "BIND"][utf8 ns]` на 7 ns: Config, Remote, ScriptCompiler, ScriptDebugger, ScriptProfiler, `scripts` (строчными), Utility. BIND Remote = `de ad 00 1c ac 08 81 16 00 04 'BIND' ac 08 81 16 00 06 'Remote' be ef` |
| exec | `[utf8 "Remote"][i32 0x12345678][i32 0x81160008][utf8 cmd]`. После `'Remote'` на проводе `81 32 12 34 56 78 81 32 81 16 00 08 ac 08 81 16 LL LL cmd`. Пакет = 36 байт + длина cmd (27 байт → 63) |
| Ответ exec | `[i32 0x12345678][i32 0x81160008][utf8 text]`, без namespace и без id |
| reload | `[utf8 "scripts"][utf8 "reload"]` → ScriptCompiler: started (i8, i8), log (str), warn/error (u32 строка, str путь, str текст), finished (i8, 0 = успех) |
| rootpath | `[ScriptCompiler][RootPath]` → `[ScriptCompiler][RootPathConfirm][путь]` |
| modlist | `[scripts][pkgSync]` → `[scripts][pkgSyncListing][i32 n]` + (имя, путь)×n |
| opcodes | `[ScriptDebugger][OpcodeBreakdownRequest][utf16 функция][i8 есть класс][utf16 класс?]` → OpcodeBreakdownResponse |
| var list | `[Config][i32 0xCC00CC00][list][раздел][имя]` → `[i32 0xCC00CC00][vars]` + (i8 тип, i8 ?, имя, раздел, значение)… + i8 0 |
| var set | `[Config][magic][set][раздел][имя][utf16 значение]`. В rw3d 0.5.1 помечен «FIXME not working»; у WolvenKit-7 магия `00 CC 00 CC` |

- **[проверено]** Текст ответа exec: `Spam: Command executed without errors` — успех без вывода;
  `Warn: Failed to process command` — ошибка; иначе строки Log(), выведенные во время вызова, через `\n` (rw3d
  `ExecuteCommandResult`). Текст — тоже строка i16, поэтому всё, что exec-функция выводит за вызов, ≤32 767 байт.
  У строк может быть префикс движка: Witcher Online искал тег после `'[Script] '` и `'OUT: '`
  (`DebugExecClient.cpp:222-296`), поэтому маркер `[MCW#seq]` ищем в любом месте строки (`spike\w3net.py`, `_MARK`).
  Точный вид строк на 5.00c **[проверить]**.
- **[проверено]** id запроса нет: rw3d сопоставляет ответы FIFO по типу сообщения, потерянный ответ сдвигает все
  следующие. Один ответ без маркера навсегда ломает FIFO-клиент (каждый следующий ответ достаётся чужому запросу),
  отсюда маркер `[MCW#seq] end`.
- **[проверено]** Нарезка потока: rw3d декодирует поля и сверяет хвост; WolvenKit-7 и W3MP разбирают один `Receive` как
  пакет и поле длины не используют. Надёжнее резать по длине и собирать разрезанные и склеенные пакеты.
- **[проверено]** UTF-16-строки от клиента WolvenKit-7 и W3MP меряют в байтах, rw3d — в кодовых единицах. Для exec не
  важно.
- **[проверено]** На BIND ответа нет, порядок неважен (WolvenKit-7 и W3MP шлют в другом). Что без BIND ответы не
  приходят, сказано только в комментарии rw3d — **[проверить]**.
- **[проверено]** Таймауты rw3d: ответ 2000 мс, чтение 2000, подключение 5000. Без `--no-delay` CLI добавляет паузы
  500/750 мс, их не путать с задержкой игры. witcherscript-ide своего протокола не имеет и вызывает rw3d_cli 0.8.0.
- **[проверено]** Witcher Online @8d608096: ASI в процессе игры → 127.0.0.1:37001, BIND Remote и scripts, exec
  `wo_update` (76 параметров), ответ через `Log("wo "+…)` с поиском тега, один тегированный запрос в полёте, таймаут
  500 мс, «последнее значение побеждает» по ключу, TCP_NODELAY и SO_RCVTIMEO/SNDTIMEO 50 мс, опрос без пауз. 2026-08-09
  (коммит 4432ca1b) проект ушёл на нативные импорты: автор форка прямо пишет, что «log-driven network architecture» даёт
  слишком большую задержку.
- **[проверено]** Witcher3-Multiplayer (закрыт 2026-01-15): `Log("W3MP:!TAG!{json}")`, в JSON вместо `"` стоит `@`, exec
  повторяется, пока не придёт тег. Похоже на трудности с кавычками — **[проверить]** charset-тестом.
- **[проверить]** Слушает ли игра 37001 на 127.0.0.1 или на 0.0.0.0 (у rw3d есть `--ip` для игры на другом ПК); пускает
  ли двух клиентов; один пакет на exec или по пакету на строку; шлёт ли пакеты без запроса (строка `no-netlog` сразу за
  `-net`: если они идут с тем же префиксом, FIFO ломается); сколько exec исполняется за кадр; что на паузе, в меню, на
  загрузке; что отвечает exec, пока `thePlayer == NULL` (главное меню); попадает ли LogChannel в ответ; нужен ли
  `-debugscripts`, чтобы Log() был в ответе; предел длины и экранирование строк у разборщика консоли.

### Лог скриптов и консоль
- **[проверено]** rw3d `scriptslog` хвостит `Documents\The Witcher 3\scriptslog.txt` (по умолчанию раз в 1000 мс) и к
  сокету не подключается. В exe рядом с именем файла ключи раздела `[Scripts]`: `LogDebugScripts`, `DebugScriptsLogDir`,
  `DebugScriptsForceFlush`, а также `-show_script_log_channels`. У нас `DebugScriptsForceFlush=true`,
  `DebugScriptsLogDir` пуст. Файла пока нет.
- **[по данным сообщества]** Файл пишется только при `-debugscripts`; `LogChannel('X', "t")` даёт строку `[X] t`;
  консоль вывод LogChannel не показывает.
- **[проверить]** Включает ли `LogDebugScripts` лог без `-debugscripts`; сбрасывается ли файл по строке или пачками;
  нужен ли `-show_script_log_channels`.
- **[проверено]** На Windows `threading.Event.wait` округляет ожидание до ~15,4 мс, `time.sleep` даёт ~2,4 мс: хвост
  лога ждём через `time.sleep`.
- **[проверено]** Консоль по wiki REDkit: `DBGConsoleOn=true` в `[General]` файла `bin\config\base\general.ini`, клавиша
  `~`. В exe `DBGConsoleOn` стоит в контексте `Debug|DBGConsoleOn|General`. **[по данным сообщества]** На 5.00b работает
  (AIWitcher), Ctrl+V работает (modSaveNames). Вариант без правки папки игры — `[General] DBGConsoleOn=true` в
  `dx12user.settings` (мод 13072), на 5.00c **[проверить]**.

### WitcherScript API 5.00c
Все строки таблицы **[проверено]** по `content0\scripts` 5.00c.

| Задача | Как |
|---|---|
| Спавн | `theGame.CreateEntity(entityTemplate : CEntityTemplate, pos : Vector, optional rot : EulerAngles, optional useAppearancesFromIncludes : bool, optional forceBehaviorPose : bool, optional doNotAdjustPlacement : bool, optional persistanceMode : EPersistanceMode, optional tagList : array<name>) : CEntity` — `engine\game.ws:129`. Пропуск аргументов валиден: `CreateEntity(template, pos, rot, , , true)` (`game\npc\npc.ws:1128`). В 5.0 сигнатура не менялась |
| Асинхронный спавн | `CreateEntityAsync(createEntityHelper : CCreateEntityHelper, entityTemplate, pos, …) : int` — `game\commonGame.ws:78` |
| Шаблон | `LoadResource(resource : string, optional isDepotPath : bool) : CResource` — `core\resource.ws:6`. Без isDepotPath строка — алиас из `gameplay\globals\resources\gameplay.xml` (1542 записи, xml.bundle; у bob и ep1 свои). `LoadResourceAsync` latent (`:9`), из exec не вызвать. Параметр exec типа name подходит: `LoadResource(nam)` (`game\temp.ws:6786`) |
| Удаление, перенос | `Destroy()` `engine\entity.ws:34`; `Teleport` `:44`; `TeleportWithRotation` `:47` (78 вызовов в ванили, на `CEntity` — `btTaskBruxaSpawn.ws:109`); `SetRawPlacement(position, rotation)` `:91` (новое в 5.0, в ванили ни одного вызова); `SetHideInGame(hide, optional immediate)` `:241` |
| Плавное движение актёра | Только у `CActor` (`game\actor.ws:6`). Неблокирующие, из exec можно: `ActionSlideToAsync(target, duration)` `game\actor.ws:804` (ваниль `:6211`, `:6225`), `ActionSlideToWithHeadingAsync(target, heading, duration, optional rotation)` `:814` (`btTaskDodge.ws:558`), `ActionMoveToAsync(target, …)` `:777` и `ActionMoveToChangeTargetAsync` `:784` (`btHorseRidingManager.ws:520`, `:525`). Латентные `ActionMoveTo` `:775`, `ActionSlideTo` `:802` из exec не вызвать. У `CEntity` — только телепорт |
| Имя из числа | `string→name` нет, ваниль строит name из int switch'ем по литералам: `GetAuxNameForInt` (`game\explorations\exploration_movement_system\explorationStateManager.ws:744-760`), `GetRandomName` (`core\string.ws:129-150`); `default: return ''` — `game\characterStats.ws:24-40`; `IsNameValid(n)` = `n != '' && n != 'None'` — `core\misc.ws:53`. Так modMCW делает теги слотов `MCW_E0`…`MCW_E63` |
| Масштаб | Только у компонента: `CComponent.SetScale(scale : Vector)` `engine\components.ws:28`. У CNode есть лишь геттер `GetLocalScale` (`engine\node.ws:18`). Ваниль берёт компоненты через `GetComponentsByClassName('CMeshComponent')` |
| Видимость, коллизия | `CComponent.SetEnabled(flag)` `components.ws:19` (так ваниль включает коллизию CRigidMeshComponent, `quest_function.ws:6768-6786`); `CDrawableComponent.SetVisible` `:473`, `SetCastingShadows` `:479`. CMeshComponent и CStaticMeshComponent — нативные, в скриптах не объявлены |
| Теги | `AddTag` `engine\node.ws:56` (обёртка над Get/SetTags), `HasTag` `:45`, `RemoveTag` `engine\entity.ws:412`; поиск `theGame.GetEntityByTag`, `GetEntitiesByTag(tag, out array<CEntity>)` (`engine\game.ws:137`, `:140`) |
| Трейсы | `CWorld.StaticTrace(pointA, pointB, out position, out normal, optional collisionGroupsNames : array<name>) : bool` `engine\world.ws:18`; `StaticTraceWithAdditionalInfo` (+ `out material : name`, `out component : CComponent`) `:20`; `SweepTest` `:24`; `SphereOverlapTest(out entities, position, radius, optional groups) : int` `:27`; `NavigationComputeZ` `:74`; `PhysicsCorrectZ` `:78`; `GetTraceManager().RayCastSync(start, end, out array<SRaycastHitResult>, optional groups)` `engine\physicsTraceManager.ws:34`, у результата есть `component` (`:15`) и `normal` (`:13`); нормаль из `RayCastSync` ваниль читает так же (`game\player\playerAiming.ws:567`, `:598`, `:605`). Мировая ли она и единичная — **[проверить]** на спайке (строка `normal` у `mcw_look`) |
| Высота земли | `StaticTrace(p + Vector(0,0,5), p − Vector(0,0,5), …)` (`game\temp.ws:6903`); `NavigationComputeZ` + `PhysicsCorrectZ` (`btTaskDeath.ws:605-616`) |
| Игрок, камера | `GetWorldPosition` `engine\node.ws:24`, `GetHeading` `:39`; `theCamera.GetCameraDirection` `engine\camera.ws:47`, `GetCameraPosition` `:50` |
| Мир, регион | `theGame.GetWorld().GetDepotPath()` `engine\world.ws:80` (`levels\novigrad\novigrad.w2w`, `levels\skellige\skellige.w2w`, …); `theGame.GetCommonMapManager().GetCurrentArea() : name` `game\commonMapManager.ws:148` (в 5.0 name, раньше int): `AN_NMLandNovigrad`, `AN_Skellige_ArdSkellig`, `AN_Kaer_Morhen`, `AN_Prologue_Village(_Winter)`. Велен — `X>970 \|\| Y<1600` внутри AN_NMLandNovigrad (`:346-357`) |
| Время | `theGame.GetEngineTimeAsSeconds() : Float` `engine\game.ws:58`; `GetLocalTimeAsMilliseconds() : int` `:79`; `IsPaused()` `:22` |
| Лог | `Log(text : string)` `core\misc.ws:6`; `LogChannel(channel : name, text : string)` `:9`; `LogAssert` → канал `ASSERT` (`:59`) |
| Строки | `StrSplit(str, subStr) : array<string>` `core\string.ws:201` (новое в 5.0); `StrSplitFirst(str, divider, out left, right) : bool` `:18`; `StrBeforeFirst` `:39`; `StrAfterFirst` `:45`; `IntToString` `:75`; `StringToInt(value, optional defValue)` `:78`; `StringToFloat` `:81`; `VectorToString` `:84`, `StringToVector` `:90` (новые в 5.0) |
| Ввод | `theInput.RegisterListener(listener : IScriptable, eventName : name, actionName : name)` `game\inputManager.ws:39`; колбэк `event OnX(action : SInputAction)` + `IsPressed(action)` (`:293`, порог 0.7); `GetLastActivationTime(actionName) : float` `:28`. Образец — `game\photomodeManager.ws:27-29,60-66` |
| Таймеры | `CEntity.AddTimer(timerName : name, period : float, optional repeats, …) : int` `engine\entity.ws:19`; `timer function X(dt : float, id : int)` — метод класса (`:402`) |
| События | `event OnSpawned(spawnData : SEntitySpawnData)`: `CEntity` `engine\entity.ws:325`, `CR4Player` `game\player\r4Player.ws:489`; `W3PlayerWitcher.OnSpawned` зовёт super (`playerWitcher.ws:282`), так что обёртка на CR4Player сработает для Геральта. CR4Game: `OnGameStarted` `game\r4Game.ws:774`, `OnBeforeWorldChange` `:822` |
| Appearance | `CAppearanceComponent.ApplyAppearance(appearanceName : string)` `engine\appearanceComponent.ws:12`, обёртка `CEntity.ApplyAppearance` `engine\entity.ws:382` (без CAppearanceComponent молча ничего не делает); `IncludeAppearanceTemplate`/`ExcludeAppearanceTemplate(template)` `:8-9`; `theGame.ChangePlayer(playerTemplate : String, optional appearance : name)` `game\commonGame.ws:240`. `GetAppearanceNames` (`:24`) объявлен без `out` — список не вернёт |
| Уведомление | `theGame.GetGuiManager().ShowNotification(messageText, optional duration, optional queue)` `game\gui\r4guimanager.ws:1405`; ванильный exec `HudConsoleMsg(msgText : string)` `game\gui\hud\hud.ws:1953` |
| Facts | `FactsAdd(ID : string, optional value : int, …)` `game\facts.ws:14`, `FactsQueryLatestValue` `:24`, `FactsRemove` `:30` — словарь string→int в сейве |
| Инвентарь | `thePlayer.inv.AddAnItem(item : name, optional quantity, …)` `game\components\inventoryComponent.ws:932`, `RemoveItemByName` `:1017` |
| Взрыв | алиас `barrel` → `COilBarrelEntity.SetOnFire(explTime : float, randomize : bool)` `game\gameplay\interactive\oilBarrel\oilBarrelEntity.ws:75`: эффекты `explosion` и `fire_ground`, GCameraShake, урон огнём с нокдауном (`:164-221`). `explTime` уходит прямо в `AddTimer('Explosion', explTime)` (`:84-87`), так что короткий запал по коду допустим (`mcw_boomat` — 0,2 с; вживую **[проверить]**) |
| Ванильные exec спавна | `spawnbarrel()` `game\temp.ws:7602`; `spawnPath(path : name, optional isHostile : bool)` `:6790` (LoadResource(path, true), спавн в 0–3 м перед игроком). Обе есть в `cookedfinal.redscripts` |
| Фоторежим | спавн реквизита: `(CEntityTemplate)LoadResource(propToSpawn)` + `theGame.CreateEntity(…)` (`game\gui\menus\photomodeMenu.ws:3237,3247`), уборка `SetHideInGame(true, true); Destroy()` (`:3492-3493`) |
| Эффекты | `SetEffectIntensity(effectName, intensity, optional specificComponentName, optional effectParameterName)` `engine\entity.ws:272` — единственный «параметр» внешнего вида из скрипта |

### Ограничения языка
- **[проверено]** exec-функции: «Exec function '%s::%s' cannot be declared inside a class», «Global exec function '%s'
  cannot also be 'latent'», «Cannot call exec function '%s' from scripts instead of the console» (строки компилятора в
  exe). Логика живёт в обычных функциях, exec — тонкая обёртка. В ванили 874 exec-функции с начала строки (877 с любым
  отступом), 575 из них в `game\temp.ws`. Типы аргументов: bool 162, name 143, int 129, float 82, string 71; `optional`
  у exec допустим.
- **[проверено]** Глобальных переменных в ванили нет (две строки `var` без отступа стоят внутри функций), глобальные
  объекты задаёт движок через `bin\redscripts.ini`. Состояние мода живёт только в полях объектов.
- **[проверено]** `timer function` бывает только методом класса: «Global function '%s' is specified as 'timer' but it
  has not meaning in global scope». Минимальный период в ванили — 0 (каждый тик). Разрешён ли
  `@addMethod(CR4Player) timer function`, **[проверить]**.
- **[проверено]** `string→name` нет: в таблице опкодов VM есть `NameToString` и `StringToInt/Float/Bool/Byte`, но не
  StringToName; ванильные «string→name» — switch по литералам. Единственный путь — `C2dArray.GetValueAsName`
  (`core\2DArray.ws:18`) из CSV. Тег из координат не построить, но `ApplyAppearance(string)` и `LoadResource(string)`
  принимают строки, собранные в рантайме.
- **[проверено]** Литерала массива `{…}` в ванили нет ни разу, теги собирают `tags.PushBack('…')` (`game\temp.ws:1230`).
- **[проверено]** `map<K,V>` появился в 5.0 (в 4.04b-версиях тех же файлов его нет). Ваниль: `map<name,bool>`,
  `map<int,float>`, `map<string,string>`, `map<int,SSpawnedProp>` (`ingameMenu.ws:229`, `photomodeMenu.ws:216-292`,
  `playerWitcher.ws:12,65-66`). Операции: `m[k]`, `Contains`, `Erase`, `Size`, `Key(i)`, `Clear`, `TryGet` (рядом с
  «Unknown map function '%s'» в exe, опкоды `OP_Map*`). `map<string,CEntity>` и map в `@addField` — **[проверить]**.
- **[проверено]** Аннотации `@addField`, `@addMethod`, `@wrapMethod` (с токеном `wrappedMethod`) и `@replaceMethod`
  поддерживает компилятор (ключевые слова и тексты ошибок в exe). Появились в патче 06.06.2024 (4.04a); в 5.0 их можно
  вешать на события и состояния. `@wrapMethod` оборачивает только метод, определённый в этом классе («definition
  inherited from»); аннотированные функции стоят на глобальном уровне файла. Синтаксис wiki:
  `@wrapMethod(CPlayer) function CheatGod2(on : bool) { wrappedMethod(on); }`,
  `@addField(CPlayer) public var dummy : int;`. **[по данным сообщества]** `@wrapMethod(W3GuiShopInventoryComponent)`
  работает на 5.00b (AIWitcher). Обёртка `event OnSpawned` — **[проверить]**.
- **[проверено]** `EPersistanceMode` нативный (в exe `PM_SaveStateOnly`, `PM_DontPersist`, `PM_Persist`). Числовые
  значения и умолчание в `CreateEntity` неизвестны, поэтому передаём явно.
- **[проверено]** Все 1527 `.ws` в 5.0 — UTF-8 без BOM (в 4.04 были UTF-16 LE BOM), комментарии CDPR вырезаны. Пути в
  строковых литералах ваниль пишет с одиночными `\` (`"gameplay\templates\signs\pc_igni_proj_cone.w2ent"`),
  экранирование видно только у `\"`. Как консоль разбирает строковый аргумент exec, **[проверить]**.
- **[проверено]** `FloatToString` может дать локальную запятую (`NoTrailZeros` проверяет `,`, `core\string.ws:114`):
  числа в логах только целые. Как консоль разбирает float-аргумент exec (`mcw_alias(…, 0.4)`: точка или запятая),
  **[проверить]** charset-тестом.
- **[проверено]** Ни строить меш, ни менять материал или текстуру из скрипта нельзя: `CMesh` и `CMaterialInstance` видны
  только как `import var`, в exe нет `SetMaterialParam` и `CreateComponent`.
- **[проверено]** Что нового в 5.0 по диффу 4.04b→5.00b (ElementaryLewis): `map`, `StrSplit`, `StringToVector`/
  `VectorToString`, `SetRawPlacement`, `SetHideInGame(…, immediate)`, `SweepTest(…, treeSizeThreshold)`,
  `engine\CEntityTemplate.ws`, `modHandlerSystem.ws`, `AreModsEnabled`; `GetCurrentArea` сменил тип с int на name.
  Локальные скрипты соответствуют 5.00c.

### Шаблоны и коллизия

Таблица **[проверено]** извлечением из бандлов и разбором CR2W, кроме мест с пометкой **[проверить]**.

| Шаблон | Что внутри | Для чего |
|---|---|---|
| `gameplay\containers\_container_definitions\_mesh_entities\cont_crate.w2ent` (bob.bundle, world_bob_startup.bundle) | CEntity без геймплея, один CStaticMeshComponent `DF_IsVisible\|DF_CastShadows`, меш `environment\decorations\containers\crate\crate.w2mesh` 1,031×1,03×1,024 м, bbox min (−0.479, −0.516, −0.521), pivot примерно в центре. Коллизия box wood_solid, полуразмеры 0.517/0.515/0.513. Меш в streamingDataBuffer, streamingDistance=10, autoHideDistance 80 | Основной куб спайка, ставим в центр клетки |
| алиас `denied_area` → `gameplay\explorations_reference_entiti\box_250cm.w2ent` (`gameplay.xml:1819`) | CEntity + CStaticMeshComponent `box_250cm` (PLC_StaticMetaobstacle, `drawableFlags={}` в обоих вложенных CR2W — невидим, как box_05_05_1m) + 4 CExplorationComponent (ET_Ledge) + тег `climb`. Меш `gameplay\explorations_reference_objects\box_250cm.w2mesh` 2,5 м, pivot снизу (bbox z 0…2,5), редакторский материал `checker_d.xbm`; physmat carpet, запись в collision.cache есть, группа коллизии неизвестна. Компонент стримится, streamingDistance 35 | Невидимый коллайдер, не запасной куб. Опыт: `SetScale` 0.4 → 1 м (масштаб коллизии) и `SetVisible(true)` — станет ли видимым **[проверить]**; компонент может появиться только к `OnStreamIn` |
| алиас `barrel` → `gameplay\interactive_objects\oil_barrel\oil_barrel.w2ent` (`gameplay.xml:1806`) | COilBarrelEntity, меш стримится (streamingDistance 9) | Взрыв: `SetOnFire` |
| `quests\sidequests\skellige\quest_files\sq204_forest_spirit\entities\box_05_05_1m.w2ent` | CEntity + нестримящийся CStaticMeshComponent (`isStreamed=0`) с `engine\meshes\editor\box.w2mesh`, pos z=0.5, scale (0.5, 0.5, 1), drawableFlags пусто, PLC_StaticMetaobstacle | Невидимый коллайдер. `SetScale(1,1,1)` + `SetVisible(true)` → серый куб **[проверить]** |
| `engine\meshes\editor\box.w2mesh` | 1×1×1 по центру, `grey.xbm`, `pbr_std`, коллизия wood_solid half 0.5 (collision.cache, смещение 424, zsize 48), autoHideDistance 60 | Коллизия своих блоков вторым невидимым компонентом **[проверить]** |
| `environment\blockout_base\box_{stone,wood,grass,metal,cloth,black,red,volume}.w2mesh`, `dlc\bob\data\environment\blockout_base\box_{glass,carpet,stone,wood_black,metal}.w2mesh` | 1,003×1,04×1 м, pivot в углу (min −0.012, −1.015, 0). physmat stone_solid и wood_solid подтверждены, grass_long/metal/glass/carpet **[проверить]** | Заготовки мешей этапа 2 |
| `game\photomode_props.csv` (61 строка: Name;LocKey;Entity;Icon;Categories) | Алиасы реквизита фоторежима; на роль куба годятся 10 позиций Furniture, коллизия неизвестна | Запасные заглушки |

- **[проверено]** Видимого ванильного «серого куба 1 м» нет: из 74 мешей 1 м ±12 % у 53 есть запись в collision.cache, и
  все они используются как невидимые коллайдеры (drawableFlags пусто или `DF_ClimbBlock`).
- **[проверено]** Коллизия cooked-мешей лежит не в `.w2mesh` (у CMesh нет свойства collisionMesh), а в
  `content0\collision.cache`, и ищется по депо-пути меша.
- **[проверено]** Шаблон с несколькими appearance обязан иметь CAppearanceComponent. Неакторные шаблоны с ним в ванили
  есть (181 в `environment\` и `gameplay\containers\`). Ваниль переключает appearance, а с ним и коллизию
  (`game\gameplay\interactive\rootsMazeWall.ws:84-160`).
- **[проверено]** `bin\config\base\resources.ini:45`: `StreamingOffset = 2` («stream in Xm before autohide distance
  kicks in»), то есть подгрузка стримящихся компонентов привязана к autoHideDistance меша. Что делает
  streamingDistance=10 у заспавненной сущности, **[проверить]**. В своих шаблонах ставим `isStreamed=false`.
- **[проверено]** Группы коллизий — `gameplay\globals\collision_matrix.csv` (startup.bundle): None + 30 именованных
  (Character, Static, Terrain, RigidBody, Destructible, Water, Foliage, Door, Platforms, Fence, Boat, DeniedArea,
  Camera, Weapon, Projectile…). Ваниль ещё использует `Dynamic` и `Phantom`. Луч камеры фоторежима 5.0 берёт 13 групп:
  RigidBody, Static, Dynamic, Destructible, Terrain, Phantom, Water, Boat, BoatDocking, Door, Platforms, Fence, Debris
  (`game\gui\menus\photomodeMenu.ws:436-448`). Группа cont_crate и box_250cm неизвестна, поэтому трейсим без списка
  групп или полным списком.
- **[проверить]** Видит ли `SphereOverlapTest` статические меши и сущности, созданные в этом же кадре (нужно запасному
  индексу без `@addField`).
- **[проверено]** Ваниль спавнит эффекты с `doNotAdjustPlacement=true`. Что движок делает при false, **[проверить]**.
- **[проверить]** Даёт ли physmat заспавненного блока правильный звук шагов Геральта (stone_solid → камень, wood_solid →
  доски и брёвна, grass_long → трава и земля, glass, carpet → шерсть). Если да, звук не требует кода.
- **[проверено]** В exe есть `CRenderMeshBatcher::DrawInstances` (в виде RTTI-манглинга), `RenderMergedMeshes_Meshlets`,
  `RTDataUseInstanceBatching`, `instancedRenderMeshAssets`, `batchedMeshInstance`, `CMergedWorldGeometry`. Инстансит ли
  батчер одинаковые меши разных сущностей, **[проверить]**. **[по данным сообщества]** (80.lv, 2015) тени статики
  рисуются одним draw call за счёт офлайн-слияния; заспавненные блоки туда не попадут, а каскадов теней 4
  (`rendering.ini` `MaxCascadeCount=4`), так что вдали тени блоков выключаем.

### Форматы файлов 5.0
- **[проверено]** `.bundle` версии 5: `0x00 'POTATO70'`, `0x08` u64 размер файла, `0x10` u32 размер таблицы, `0x14` u16
  version=5, `0x16` начало данных (= таблица + 0x20). Запись 0x130 байт: name[0x100], hash[16], `0x110` u64 абсолютное
  смещение, `0x118` u32 size, `0x11C` u32 zsize, `0x120` u32 crc32 несжатых данных, `0x124` compression (0 none, 1 zlib,
  байт + паддинг). В `*_startup.bundle` смещения не выровнены. Старая раскладка (1.32/4.0x, version 3) — запись 0x140 с
  u32-смещениями. Совпадает с Witcher3-Blender-Tools (`toc_entry_size {3: 320, 5: 304}`) и w3edit.
- **[проверено]** CR2W — версия 164 (вложенные flatCompiledData/streamingDataBuffer — v156…v164). В мешах новые поля
  `SMeshCookedData.blasBuffer`, `blasLODsOffsets` (BLAS для RT).
- **[проверено]** `collision.cache` — `CC3W` v11: `0x10` u64 смещение таблицы записей, `0x18` u32 число записей
  (13 363), `0x1C` u32 смещение таблицы имён, `0x24` u32 её размер. Запись 64 байта: u32 смещение имени, u64 смещение
  данных (`0x0C`), u32 zsize, u32 size; данные zlib, внутри имя physmat и полуразмеры box. Смысл 4 float по смещению 44
  записи (похоже на полуразмеры AABB) **[проверить]**.
- **[проверено]** `texture.cache` — футер 32 байта с `HCXT`, версия 7; записи по 52 байта, pageOffset в 4К-страницах,
  чанки zlib (описание w3edit, хвост файла сверен). `.xbm` в бандле обычно заглушка CBitmapTexture (width, height,
  compression, textureGroup, residentMipIndex, textureCacheKey): `hhpl_crate01.xbm` весит 1066 байт и описывает
  4096×2048. Но 1298 `.xbm` больше 20 КБ несут пиксели прямо в бандле (в выборке blob/bob/ep1 — 575 DiffuseNoMips, 52
  PostFxMap).
- **[по данным сообщества]** (community-doc) в 5.0 сменились форматы collision.cache, soundspc.cache и SRT. В
  официальном changelog этого нет.
- **[проверено]** Инструменты: WolvenKit-7 на 5.0 не работает (бросает исключение при CR2W > 163 и читает только записи
  0x140; последний релиз 7.2.0 от 2023-02-10). Witcher3-Blender-Tools 1.1.0 (2026-09-29) по release notes поддерживает
  Remastered: `.w2mesh` с коллайдерами и `.xbm` — импорт и экспорт, `.w2ent` — только импорт. w3edit (Systemcluster,
  коммит 2026-09-30 «Add support for Remastered file formats») читает и пишет bundle v3/v5, metadata.store v6/v7,
  texture.cache v6/v7, конвертирует legacy↔remastered, пакует cooked-файлы в `content\blob0.bundle` + metadata.store +
  texture.cache. Кука и конвертации картинок в нём нет, в игре результат никто не проверял.
- **[проверено]** Наши read-only инструменты: `tools\w3bundle.py` (листинг и извлечение, v5 и legacy, проверка CRC),
  `tools\cr2w.py` (CR2W v156–164), `tools\collcache.py` (CC3W v11), `tools\bundle_extract.py`. Извлечённые игровые файлы
  в проект не кладём.

### REDkit
- **[проверено]** Steam app 2684660, бесплатный, с 21.05.2024. Ветка public (buildid 25651183, 2026-10-01): 5.0.1042178
  от 29.09.2026 + hotfix 5.0.1044630 («Fixed localized strings editor crash»). Ветка next-gen 16641804 — для 4.04. Депо
  2684661 — 59,7 ГБ загрузки и 108,3 ГБ на диске, 2684662 — 0,64 и 2,42 ГБ (магазин пишет 50 ГБ). Запуск
  `bin\x64_redkit\editor.exe -forceshadercacheusage`; рекомендовано 32 ГБ RAM и RTX 3080; для звука Wwise 2023 не новее
  2023.1.17. При запуске сверяет версию игры. Сейчас качается.
- **[проверено]** Changelog 5.0.1042178: scope-based overrides, script blobs, XML-оверрайды с `onConflict`, DLC
  Mounters, `map`, обёртка нативов, состояний и событий, commandlet `compile scripts`, «Now scripts are part of REDKit,
  please make sure that no vanilla scripts are part of the depot».
- **[проверено]** После cook REDkit выдаёт папку packed (бандлы, кэши texture и collision, metadata, скрипты), дальше —
  Install project, zip или Workshop. Для mod.io нужен JSON и пути в нижнем регистре (страница Publishing mods, 2024).
- **[проверено]** EULA: «only as part of The Witcher 3: Wild Hunt game (and not with other games or on a standalone
  basis) and only for non-commercial purposes».

### ASI и нативный код на 5.0
- **[проверено]** Ultimate ASI Loader v9.7.4 (2026-08-16). **[по данным сообщества]** На 5.0 x64_dx12 ASI грузится:
  Spawn Menu 2.0 (Nexus 12898: UAL, MinHook, ImGui; «Script compilation error at start-up: W3SpawnMenu.asi or
  dinput8.dll is missing», то есть ASI регистрирует нативы для скриптов; исходники закрыты; ставит свою `dlc\dlcw3sm`),
  13072 (dinput8 + NoNearCameraCulling.asi), RefinedPT (13075, свой dinput8-прокси, исходники в Files). Свежие UAL могут
  проседать по FPS (Nexus 7171, февраль 2026).
- **[проверено]** Witcher Online (коммит 4432ca1b): `import function WO_Send(payload : string) : bool` и др., ASI
  находит регистрацию нативов сигнатурой `BA 10 00 00 00 B9 C0 00 00 00 E8 ?? ?? ?? ?? 48 8B F8 …` (не меньше 32
  совпадений) и хукает RegisterGlobal (пролог `48 89 6C 24 18 48 89 74 24 20 57 48 83 EC 20`, CFunction 0xC0); потом
  логика ушла в закрытый WitcherNativeBridge (API v1). В `.text` exe 5.00c полная сигнатура и 10-байтный префикс дают 0
  совпадений, пролог — 281 (не уникален). Перенос под 5.0 = реверс с нуля.
- **[проверено]** Hyjarion/Witcher3-OnlineModule (classic) держится на жёстких смещениях (`GGame = base+0x2AA0A70`), для
  5.0 бесполезен. RED3ext мёртв: последний коммит 2023-04-18.
- **[проверено]** Движок грузит DDI-плагины `..\ddi\*.dll` (CreateInterface/DestroyInterface, флаг `-ddi=`), там лежит
  `bin\ddi\Steam.dll`. Это слой платформы, для модов не годится (оценка).

### Ввод
- **[проверено]** `Documents\The Witcher 3\input.settings` (110 989 байт, `[InputSettings] Version=60`): контексты
  `[Exploration]`, `[Combat]`, `[Horse]`, `[Swimming]`, `[Boat]`, `[JumpClimb]`, `[Photomode]`, BASE_*; строки вида
  `IK_F1=(Action=OnShowControlsHelp)`. Шаблон по умолчанию — `bin\config\r4game\legacy\base\input_qwerty.ini`. Пункт в
  меню переназначения — `<Var builder="Input" … displayType="INPUTPC" actions="…"/>` в `user_config_matrix\pc\input.xml`
  (пример в комментарии файла). **[по данным сообщества]** Мод 13072 на 5.0 дописывает `IK_F8=(Action=FPR_Toggle)`. Не
  перезапишет ли игра `input.settings` при смене версии, **[проверить]**.

### Прецеденты
- **[по данным сообщества]** Блоков Minecraft в Ведьмаке нет. Ближайшее: мод-предмет A Minecraft Sword (Nexus 5020),
  Chill Out (6402, 4.04): build mode по лучу камеры (Attack ставит, Parry переключает snap/free), destroy mode, рубка и
  добыча; исходников нет. SkyCraft (Skyrim) — passthrough через shared memory.
- **[проверено]** Подмена модели игрока: Custom Player Characters (nikich340, 4.04, GPL-3.0) —
  `ChangePlayer("nr_replacer_witcher")` и `Include/ExcludeAppearanceTemplate((CEntityTemplate)LoadResource(path, true))`
  по частям тела (`NR_PlayerManager.ws:1483-1531, 1980-2010`). Под 5.0 не пересобран.
- **[по данным сообщества]** Вид от первого лица на 5.0 — мод 13072 v1.5 (обновлён 2026-10-02): идёт с
  NoNearCameraCulling.asi и dinput8.dll, в ближнем бою тело скрыто, виден только меч. **[проверено]** Официально первое
  лицо есть только в фоторежиме.
- **[проверить]** Публичных замеров FPS на сотни и тысячи заспавненных сущностей и задержки exec не нашли, цифры даст
  только спайк. **[по данным сообщества]** Пример GTA держит не больше 400 пропов: по его README GTA падает примерно на
  1500 скриптовых объектах.
- **[проверено]** В базе знаний universal-modder про Witcher 3 и RED Engine ничего нет, строка в `big-frameworks.md`
  («script mods merged with Script Merger») для 5.0 устарела.

## Minecraft 1.21.11 (серверный мод на Fabric)

### Версии и сборка
- **[проверено]** Пины по ветке 1.21.11 fabric-example-mod и meta.fabricmc.net: Minecraft 1.21.11, Fabric Loader 0.19.5
  (installer 1.1.2), Fabric API 0.141.6+1.21.11 (2026-07-28), Gradle 9.7.1, `release = 21`,
  `loom.officialMojangMappings()`, `modImplementation`. Для серверного мода — `loom { serverOnlyMinecraftJar() }`,
  `environment=server`.
- **[проверено]** Loom — плагин `net.fabricmc.fabric-loom-remap` **1.17.21**: последний релиз под JVM 21
  (`fabric-loom-1.17.21.module`: `org.gradle.jvm.version` 21, plugin-api 9.5.0). 1.18.x, в том числе 1.18-SNAPSHOT из
  примера и релиз 1.18.2, требует, чтобы сам Gradle работал на JVM 25 (`fabric-loom-1.18.2.module`:
  `org.gradle.jvm.version` 25, plugin-api 9.7.0); на JDK 21 сборка `mc\` с 1.18.2 падала на конфигурации с «Dependency
  requires at least JVM runtime version 25». Пин и причина — в `mc\gradle.properties`.
- **[проверено]** 1.21.11 — последняя обфусцированная версия (26.1 — первая без обфускации). Yarn заморожен на
  1.21.11+build.6, после 1.21.11 Yarn и Intermediary не выпускаются. Для 26.x — плагин `net.fabricmc.fabric-loom` и
  Java 25. Текущий релиз — 26.3 (снапшот 26.4-snapshot-2), JDK 25 на машине нет.
- **[проверено]** В Mojang-именах 1.21.11 `ResourceLocation` стал `net.minecraft.resources.Identifier`. Gamerule — в
  snake_case: `minecraft:advance_time` (бывший doDaylightCycle), `advance_weather`, `spawn_mobs`, `spawn_monsters`,
  `mob_griefing`, `block_drops`, `keep_inventory`, `tnt_explodes`, `random_tick_speed`, `immediate_respawn`.
- **[проверено]** `server.jar` 1.21.11:
  `https://piston-data.mojang.com/v1/objects/64bb6d763bed0a9f1d632ec347938594144943ed/server.jar`, 56 327 581 байт (из
  `versions\1.21.11\1.21.11.json`); server_mappings sha1 `5621e925…`, client_mappings `031a68be…`.
- **[проверено]** Серверный лаунчер Fabric
  `https://meta.fabricmc.net/v2/versions/loader/1.21.11/0.19.5/1.1.2/server/jar` (181 841 байт) с JDK 21 и `nogui`
  скачал server.jar (sha1 совпал) и библиотеки (asm 9.10.1, sponge-mixin 0.17.4+mixin.0.8.7), дошёл до EULA и сам вышел
  за 21,5 с; на диске 139 МБ.
- **[проверено]** Java: `%APPDATA%\.minecraft\runtime\java-runtime-delta\windows\java-runtime-delta` — Microsoft OpenJDK
  21.0.7+6-LTS с javac, jar, javap, jlink. `java` в PATH — 1.8.0_471, `JAVA_HOME` пуст. Gradle 9.7.1 запускается на JVM
  17–26 (сам wrapper совместим и с 8), поэтому с Loom 1.17.21 — `$env:JAVA_HOME='…'; .\gradlew.bat build`. Первая
  сборка качает всё (`gradle-9.7.1-bin.zip` 151 433 392 байта), первые прогоны трека mc шли около 4 мин.
- **[проверено]** `serverOnlyMinecraftJar()` вместе с полным fabric-api компилируется и ремапится: `mc\` собран в
  `mc\build\libs\mcw-0.1.0.jar` (у Loom были баги #1129 и #1326, на 1.17.21 не проявились). Работает ли мод на живом
  сервере, **[проверить]** — итог в `mc\SMOKE.md` после прогона `smoke_0prime.py`.
- **[проверено]** Профиль лаунчера «Fabric 1.21.11»: loader 0.18.2, sponge-mixin 0.16.5. В общей `.minecraft\mods`
  только моды под 1.21.4 (fabric-api 0.111.0+1.21.4, spectatorplus), поэтому отладочный клиент — ванильный 1.21.11.

### Пауза сервера и тикеты
- **[проверено]** `pause-when-empty-seconds` (по умолчанию 60): если `PlayerList.getPlayerCount()==0` дольше N×20 тиков,
  `MinecraftServer.tickServer` выполняет только `tickConnection`. Fabric вешает `START_SERVER_TICK` на INVOKE
  `tickChildren`, а `END_SERVER_TICK` на TAIL `tickServer`, поэтому во время паузы оба события молчат (задачи
  `server.execute()` при этом выполняются).
- **[проверено]** `ServerLevel.tick` перестаёт тикать сущности и block entities, если 300 тиков подряд в измерении нет
  тикета с флагом `KEEP_DIMENSION_ACTIVE` (8). blockTicks и fluidTicks идут дальше: вода течёт, а падающий песок и
  подожжённый TNT висят.
- **[проверено]** TicketType в 1.21.11 — `record(long timeout, int flags)` в `BuiltInRegistries.TICKET_TYPE`. Флаги:
  PERSIST=1, LOADING=2, SIMULATION=4, KEEP_DIMENSION_ACTIVE=8, CAN_EXPIRE_IF_UNLOADED=16. Тикеты с флагом 8:
  PLAYER_SIMULATION(0, 12) — его ставит `DistanceManager.addPlayer` каждому ServerPlayer, FORCED(0, 15), PORTAL(300,
  15), ENDER_PEARL(40, 14). Уровни: FULL=33, BLOCK_TICKING=32, ENTITY_TICKING=31, MAX=44; `addTicketWithRadius` ставит
  33−r. `setChunkForced` — это `/forceload` (тикет FORCED, сохраняется на диск). Естественный спавн мобов требует игрока
  рядом.
- **[проверить]** Пустит ли сервер ванильный клиент, если мод зарегистрирует свой TicketType (синхронизация реестров
  Fabric). Безопаснее обходиться тикетами самого ServerPlayer.

### Геральт = ServerPlayer (схема Carpet 1.4.194)
- **[проверено]** Fabric API `FakePlayer` (fabric-events-interaction-v0): `tick()` пустой, `isInvulnerableTo()` всегда
  true, `openMenu`/`startRiding` отключены, в мир и в PlayerList не добавляется. Годится только как «руки» для вызовов
  gameMode: чанки не грузит, паузе сервера не мешает, мобы его не видят, урона нет.
- **[проверено]** Carpet 1.4.194 (minecraft_version=1.21.11, Mojang-имена) создаёт игрока так, как в блоке ниже.
  `FakeClientConnection extends Connection`: EmbeddedChannel через аксессор на поле `Connection.channel`, no-op
  `setReadOnly`, `handleDisconnection`, `setupInboundProtocol`, `setListenerForServerboundHandshake`. В `tick()` раз в
  10 тиков `connection.resetPosition()` и `level().getChunkSource().move(this)`, затем `super.tick()` и `doTick()`
  (соединения нет в ServerConnectionListener).

```java
var profile = new GameProfile(UUIDUtil.createOfflinePlayerUUID("Geralt"), "Geralt");
var p = new ServerPlayer(server, level, profile, ClientInformation.createDefault());
server.getPlayerList().placeNewPlayer(new FakeClientConnection(PacketFlow.SERVERBOUND), p,
        new CommonListenerCookie(profile, 0, p.clientInformation(), false));
```

- **[проверено]** Этого мало. Нужен миксин `PlayerList` (`PlayerList_fakePlayersMixin`): `@Redirect` NEW
  `ServerGamePacketListenerImpl` в `placeNewPlayer` → `NetHandlerPlayServerFake` (`send()` пустой, `disconnect()`
  пропускает только idling и duplicate_login) и `@Redirect` NEW `ServerPlayer` в `respawn`. Иначе ванильный listener
  пишет пакеты в EmbeddedChannel (`Connection.isConnected() = channel.isOpen()`), и они копятся (рост памяти — вывод из
  семантики Netty, не замер), а `respawn` создаёт обычный ServerPlayer без своего tick. `placeNewPlayer` в 1.21.11
  playerdata не грузит: Carpet вызывает `loadPlayerData` + `player.load(TagValueInput)` вручную, иначе инвентарь не
  переживёт перезапуск. Смерть Геральта: hp → 0 → мод сам зовёт respawn и обновляет ссылку на игрока.
- **[проверено]** `Player.tick` каждый тик ставит `noPhysics = isSpectator()` (`Player.java:249`), поэтому нужен миксин
  в духе `PlayerMixin` примера GTA. При noPhysics `Entity.isInWall()` = false: Геральт не задохнётся в barrier-рельефе.
  Позиция — `absSnapTo(x, y, z, yaw, pitch)` (Yarn `updatePositionAndAngles`) + `getChunkSource().move(player)`. Блок в
  собственное тело не поставить: проверка `isUnobstructed`, как в ванилле.
- **[проверить]** Хватит ли FakeClientConnection по схеме Carpet с Fabric API 0.141.6 без других миксинов Carpet.

### Изменения блоков
- **[проверено]** Единая точка — `@Inject(at = RETURN)` в
  `Level.setBlock(BlockPos, BlockState, int flags, int recursionLeft)Z` (Yarn `World.setBlockState`, intermediary
  `method_30092`, obf `dwo.a(Lis;Leoh;II)Z`). `LevelChunk.setBlockState` вызывают только Level, WorldGenRegion и классы
  чанков. Через setBlock идут песок (FallingBlockEntity), вода (`FlowingFluid.spreadTo`), взрывы (`onExplosionHit` →
  `setBlock(AIR, 3)`) и ломка игроком (`removeBlock` → `setBlock`).
- **[проверено]** setBlock возвращает true, даже если `onPlace` тут же поменял состояние. Поэтому в миксине копим только
  позиции (dedupe), а итоговое `level.getBlockState(pos)` читаем на `END_SERVER_TICK` и сравниваем с отправленным
  зеркалом.
- **[проверено]** Остальные кандидаты хуже: `ServerLevel.sendBlockUpdated` срабатывает только при `flags & 2` и чанке не
  ниже BLOCK_TICKING; `ServerChunkCache.blockChanged`/`ChunkHolder.blockChanged` теряют изменения без ticking-чанка;
  `updatePOIOnBlockStateChange` зовётся только при совпадении итогового состояния с запрошенным.
- **[проверено]** Блоки из загрузки или генерации чанка setBlock не проходят: их отдаём снапшотом по
  `ServerChunkEvents.CHUNK_LOAD` (есть ещё `CHUNK_GENERATE`, `CHUNK_UNLOAD`, `CHUNK_LEVEL_TYPE_CHANGE`).

### Действия игрока
- **[проверено]** Ломка: `ServerPlayerGameMode.handleBlockBreakAction(pos, action, face, maxY, seq)` с action
  START_DESTROY_BLOCK или STOP_DESTROY_BLOCK проверяет дальность (`isWithinBlockInteractionRange(pos, 1.0)`), высоту,
  `mayInteract`, `blockActionRestricted` и время копания (progress×(ticks+1) ≥ 0.7). Дальше `destroyBlock`:
  `canDestroyBlock` → `playerWillDestroy` → `removeBlock` → `mineBlock` (износ) → `playerDestroy`; дроп только при
  `hasCorrectToolForDrops`.
- **[проверено]** Установка: `useItemOn(player, level, stack, hand, BlockHitResult)`. Дальность, высоту и `mayInteract`
  проверяет обработчик пакета (`handleUseItemOn`, допуск 1.0000001), а не этот метод, — повторяем в моде.

### server.properties и мир
- **[проверено]** Нужно: `pause-when-empty-seconds=0`, `online-mode=false`, `server-ip=127.0.0.1`. Void:
  `level-type=minecraft:flat` +
  `generator-settings={"layers":[],"biome":"minecraft:the_void","features":false,"lakes":false}` (разбор через
  `FlatLevelGeneratorSettings.CODEC`; вживую void не проверяли).
- **[проверено]** `enforce-secure-profile` при offline-mode и так выключен; `spawn-protection` (по умолчанию 16)
  действует, только если `ops.json` не пуст. Обе строки безвредны, но не обязательны.
- **[проверено]** `management-server-*` — встроенный JSON-RPC (Allowlist, Player, Banlist, Operator, GameRules,
  ServerSettings, ServerState, Discovery); API для блоков там нет. Сгенерированный `management-server-secret` в git не
  кладём.
- **[проверено]** Предел высоты: `DimensionType.MIN_Y = −2032`, `Y_SIZE = 4064`, `MIN_HEIGHT = 16` (12 бит на Y);
  overworld — −64…319. Свой `dimension_type` через датапак (min_y и height кратны 16) **[проверить]**.

### Текстуры для конвертера
- **[проверено]** Клиентский `versions\1.21.11\1.21.11.jar`, `assets/minecraft/textures/block/`: 1210 записей: 1111 PNG
  и 99 `.png.mcmeta`. 16×16 из них 1041; остальные 70 — анимационные полосы и редкие размеры (fire_1 16×512, lava_flow
  32×512, prismarine 16×64, magma 16×48, полки 32×32). Моделей блоков 2390, blockstates 1168, colormap — grass, foliage,
  dry_foliage. В server.jar текстур нет (есть 1083 loot_table/blocks).
- **[проверено]** Признак тинта — `tintindex` в `models/block/*.json`, а не серый PNG: `grass_block_top` и
  `grass_block_side_overlay` серые (PNG color type 0), но серый и `stone.png`, который не тонируется. `glass.png` с
  альфой (color type 6) — для стекла и листвы нужен alpha-test.

### Пример minecraft-gta5-passthrough (плагин universal-modder)
- **[проверено]** Собран под MC 26.3, Fabric API 0.161.0+26.3, Loom 1.18-SNAPSHOT (`fabric-loom`, без mappings), Gradle
  9.7.1, Java 25. Связь — WebSocket (Java-WebSocket 1.6.0) на 127.0.0.1:25599, но `HostLink extends WebSocketServer`
  лежит в клиентском source set, а main-часть рассчитана на встроенный сервер в одной JVM с клиентом. В `blocks` идёт
  только твёрдость без типа блока: в GTA это невидимые пропы.
- **[проверено]** На серверную сторону переносятся `LevelMixin` (RETURN `Level.setBlock`), `ServerExplosionMixin` (HEAD
  `explode`: center, radius, getDirectSourceEntity), `PlayerMixin` (noPhysics), `ServerPlayerMixin` (`teleport` →
  pteleport) и `WorldBridge.solid/clearSolid` (рельеф хоста как barrier с флагом placingGround). Цели есть в 1.21.11 с
  теми же Mojang-именами; отличия — `Entity.entityTags()` → `getTags()` и `EntityTypes` → `EntityType` (MobWar).

### Протокол мост ↔ мод (v1)
- **[проверено]** Источник истины — `mc\protocol_examples.jsonl` (по примеру на тип) и код `mc\src\main\java\mcw\`: TCP
  `127.0.0.1:25600`, UTF-8, одна JSON-строка на сообщение, поле `t`, координаты в системе Майнкрафта, списки блоков
  плоские (`set`/`b` — `[x,y,z,pal,…]`, `clear` — `[x,y,z,…]`), air и barrier в `set`/`b` не попадают. Мост → MC:
  `hello`, `pos`, `act` (break, place, cancel, select; с позой на момент действия), `ground`, `ground_clear`, `snap`,
  `give`, `cmd`, `ping`. MC → мост: `hello`, `chunk` + `snap_end` (снапшот), `blocks{tick, pal, set, clear}` раз в тик,
  `unchunk`, `ack{id, ok, why}`, `dig`, `inv`, `hp`, `death`, `respawn`, `explosion` (только эффект), `pteleport`,
  `ground_ok`, `cmd_out`, `pong`, `error` (в том числе `unknown_type`). Поведение на живом сервере — `mc\SMOKE.md`
  (этап 0′, 33/33).
- **[проверено]** Этапы 3 и 5 (2026-10-03, только добавления): мост → MC `recipes{id}`, `craft{id, recipe, n}`,
  `spawn{id, type, d}`; MC → мост `recipes{id, table, r:[[рецепт, предмет, штук, раз, сетка]…]}`, `ack` крафта
  (`made`, `crafts`, `item`, `table`), `ack` призыва (`ent`, `p`), поток `ent{spawn, move, hp, fuse, item, remove}`
  (мобы и предметы в `ent_radius` от Геральта раз в `ent_every` тиков, дельтами). `inv` дополнен `dmg`, `hello` —
  `ent{r, every, max}`, `pong` — `ents`. Мод ↔ Ведьмак: `mcw_ents(seq, "spawn:id:x:y:z:yaw:t|move:id:x:y:z:yaw|
  kill:id|wipe")` → `ents <spawned> <moved> <killed> <errors> <ms>`. Сквозной прогон с настоящим сервером и фейковой
  игрой — `bridge\SMOKE.md`, «Этапы 3 и 5» (17/17).
- **[проверено]** Всё ещё нет: мост → MC `boom`, действие `drop` (`ack` с `unknown_action`), урон по мобу из
  Ведьмака (вариант — `bridge\README.md`, «Мобы»). `use` есть с этапа 1.

### Крафт и мобы на сервере (этапы 3 и 5)
- **[проверено]** Inventory 1.21.11 — 43 слота: 36 своих (`items`) и 7 экипировки через `EQUIPMENT_SLOT_MAPPING`
  (36–39 броня, 40 левая рука, 41 BODY, 42 SADDLE); `getContainerSize()` = 43, поэтому `inv` всегда видел всё.
  `fillStackedContents` берёт только 36 своих слотов.
- **[проверено]** Подбор: `Player.aiStep` (его зовёт `ServerPlayer.doTick`, а Геральт тикает `doTick` сам) трогает
  сущности в `getBoundingBox().inflate(1.0, 0.5, 1.0)`, `ItemEntity.playerTouch` кладёт стопку при `pickupDelay == 0`
  и зовёт `take`. noPhysics и удержание позиции не мешают; на смоуке 3 бревна подобраны за 0,44 с после шага.
- **[проверено]** Крафт сервером без клиента: `AbstractCraftingMenu.handlePlacement(useMax, creative, holder,
  level, inventory)` → `ServerPlaceRecipe.placeRecipe` (проверку книги рецептов делает только
  `handlePlaceRecipe` пакета). Сетка в рецепт не проверяется: 3×3 в сетке 2×2 просто не совпадёт, поэтому размер
  берём из `RecipeDisplay` (`ShapedCraftingRecipeDisplay.width/height`, `Shapeless….ingredients().size()`), как
  клиентская книга. `CraftingMenu.slotsChanged` считает результат только через `ContainerLevelAccess` реального
  блока (с `NULL` — никогда), поэтому меню 3×3 открывается `getMenuProvider` верстака. Результат забирается
  `clicked(0, 0, QUICK_MOVE)`; `InventoryMenu.removed` и `closeContainer` возвращают остатки сетки.
- **[проверено]** Крипер: цели — `NearestAttackableTargetGoal<Player>`, Геральт-ServerPlayer в выживании ей
  подходит. `SwellGoal` включается ближе 3 блоков, гаснет дальше 7. На смоуке шипящий крипер шёл за
  отступающим Геральтом и взорвался в 4,2 м от него (hp 20 → 10,9). Ходит по barrier-рельефу; путь по нему ищется.

### Клиент Майнкрафта 1.21.11 (скрытый, mcclient)
Сверено по исходникам `gradlew genSources` (Vineflower, Mojang-имена) и в живом прогоне 2026-10-03.
- **[проверено]** `Window.<init>`: `glfwDefaultWindowHints` → свои хинты → `glfwCreateWindow(IILjava/lang/CharSequence;JJ)J`.
  Сам Minecraft `glfwShowWindow`/`glfwFocusWindow` не зовёт: окно, созданное с `GLFW_VISIBLE = false`, остаётся
  скрытым, пока его не покажешь сам. LWJGL 3.3.3, JNA 5.17.0 и jna-platform — в библиотеках клиента.
- **[проверено]** Кадр: `Minecraft.runTick` очищает главный буфер `clearColorAndDepthTextures(color, 0, depth, 1.0)`
  (цвет ARGB), `GameRenderer.renderLevel` → `LevelRenderer.renderLevel(...)` (10 аргументов), затем очистка глубины
  и `renderItemInHand` с отдельной проекцией. Без вызова `LevelRenderer.renderLevel` рука и HUD рисуются как обычно.
- **[проверено]** Экран «Loading terrain» (`LevelLoadTracker.WaitingForPlayerChunk`) ждёт
  `LevelRenderer.isSectionCompiledAndVisible(позиция игрока)`, таймаут 30 с. Без рендера мира вход всегда шёл по
  таймауту.
- **[проверено]** `Options.load` без `options.txt` (первый запуск) выходит сразу: инъекция в `TAIL` не срабатывает,
  нужна `RETURN`. Иначе первым экраном — онбординг доступности, `--quickPlayMultiplayer` его не пропускает.
- **[проверено]** `MouseHandler.grabMouse` ставит `missTime = 10000`; сбрасывает его `continueAttack(false)` на первом
  тике без зажатой ЛКМ. Удар держится только при `isMouseGrabbed()`. Обработчики GLFW (`MouseHandler.onButton(long,
  MouseButtonInfo, int)`, `onScroll(long, double, double)`, `KeyboardHandler.keyPress(long, int, KeyEvent)`,
  `charTyped(long, CharacterEvent)`) приватные, вызываются через `@Invoker` и проверяют, что окно — своё.
- **[проверено]** `FramerateLimitTracker`: 10 FPS только при свёрнутом окне или долгом AFK (`inactivityFpsLimit = AFK`);
  с `MINIMIZED` и окном под другими клиент держит 42–60 FPS.
- **[проверено]** Ванильный прицел — пайплайн `RenderPipelines.CROSSHAIR` (инверсия цвета под собой): на magenta
  выходит зелёным. Фон ячеек хотбара (`textures/gui/sprites/hud/hotbar.png`) — (39, 37, 5) с альфой 186, не серый.
- **[проверено]** Сервер: `PlayerList.disconnectAllPlayersWithProfile(UUID)` при входе дубля зовёт
  `connection.disconnect`, а вход ждёт (`WAITING_FOR_DUPE_DISCONNECT`), пока старый игрок не уйдёт из мира. Марионетка
  отключений не принимает, поэтому её убираем сами в `HEAD` этого метода. Fabric API 0.141.6: `ServerPlayerEvents.JOIN`
  (после загрузки в мир), `LEAVE` (до сохранения и удаления), `AFTER_RESPAWN`; `ServerLivingEntityEvents.AFTER_DEATH`.
- **[проверено]** `ServerGamePacketListenerImpl.handleMovePlayer`: при `player.noPhysics` ход клиента принимается без
  проверки коллизий; «moved too quickly» выключает гейм-рул `player_movement_check` (`GameRules.PLAYER_MOVEMENT_CHECK`).
- **[проверено]** Fabric API `fabric-rendering-v1` 16.2.10 встраивается в `LevelRenderer.renderBlockOutline`
  (`BEFORE_BLOCK_OUTLINE`) и берёт контекст, который готовит в начале `LevelRenderer.renderLevel`. Без рендера мира
  вызов `renderBlockOutline` — NPE (`worldState()` = null), клиент падает. `extractBlockOutline` и
  `ShapeRenderer.renderShape` (`RenderTypes.lines()`, POSITION_COLOR_NORMAL + толщина линии) так не падают.
- **[проверено]** `Entity.collide`: список «столкновений с сущностями» (`Level.getEntityCollisions`) уходит и в
  `collideBoundingBox`, и в подъём на ступеньку (`collectColliders`). Подмешанные туда формы работают как земля, с
  подъёмом до `maxUpStep` (0,6). С формой, в которую сущность уже вошла, MC её не сталкивает: она проваливается.
- **[проверено]** Barrier-рельеф и прицел: рамка MC обводит barrier (не воздух), а установка блока на землю целится в
  него же — для клиента barrier нужен в форме (`getShape`), но не в столкновениях (`getCollisionShape` по контексту
  сущности).

### Witcher 3 5.00c: лучи, коллизия домов, двери (2026-10-03, пробы `mcwx_*` в `mcw_collide.ws`)
- **[проверено]** `theGame.GetWorld().GetTraceManager().RayCastSync( a, b, out res, groups )`
  (`engine\physicsTraceManager.ws:34`) отдаёт **все** попадания вдоль луча, без сортировки по расстоянию; только по
  граням, повёрнутым к лучу (луч вниз — нормали вверх, луч вверх — вниз); `distance` — доля длины луча;
  `component` у статики и земли — NULL. Одна и та же грань бывает дважды (две формы).
- **[проверено]** Твёрдое по колонне — чётностью: луч сверху даёт верхние грани, луч снизу — нижние; идём сверху из
  воздуха. Работает для домов Белого Сада (стены до крыши, пол, перекрытие, шкафы, балки), разрез — `mcwx_slice`.
  Земля (heightfield) снизу не мешает: её попадание при луче вверх приходит с нормалью вверх.
- **[проверено]** `SweepTest( p, p - (0,0,0.01), r, ... )` — «точка внутри твёрдого»: внутри формы статики — попадание
  в самой точке; внутри земли (heightfield) — нет.
- **[проверено]** Многие дома деревни Белого Сада — цельные: коллизия комнаты сплошная от пола до перекрытия (войти
  нельзя и в Ведьмаке). Входные — с толкаемыми дверями `gameplay\interactive_objects\doors\pushable\
  push_village_door_a.w2ent`: `CDoorComponent` не интерактивный (`IsInteractive` 0, подсказки «Открыть» нет), не
  заперт. Игрока ваниль только записывает в пользователи двери (`AddDoorUser`, `newDoor.ws:106`), открывает её
  физика капсулы. `AddForceImpulse( p, 3000 )` (как Аард) и `Open( false, false )` из exec в упор `IsOpen` не
  меняют [проверить глазами, двигается ли створка].
- **[проверено]** `SphereOverlapTest( out ents, p, r, ['Door'] )` находит сущности дверей (у них `CDoorComponent`).
- **[проверено]** Кости Геральта (`GetBoneIndex`): pelvis 9, torso 10, torso2 11, torso3 12, neck 13, head 14, l_thigh
  15, l_shin 16, l_foot 17, r_thigh 18, r_shin 19, r_foot 20, l_shoulder 21, l_bicep 22, l_forearm 23, l_hand 24,
  r_shoulder 27, r_bicep 28, r_forearm 29, r_hand 30. Голова на 1,70 м над ногами.
- **[проверено]** `mcw_cols`: блок со статикой (16 подколонн × 2 луча) — ~1 мс игрового кадра (`cms`), строка ответа
  до ~650 байт.
- **[проверено]** Состояние Геральта-куклы в режиме Стива: не в воздухе, `CanInteract` 1, состояние `Exploration`,
  `EIAB_Interactions` разрешён — взаимодействия (подсказки E) работают, если они есть у объекта.

### Witcher 3 5.00c: модели из коробок на Геральте (2026-10-03, `mcw_models.ws`)
- **[проверено]** Новый мод (папка в `<игра>\mods\`) игра подхватывает только при старте: `LoadResource` его шаблона
  до перезапуска — NULL; `w3cmd.py --reload` перекомпилирует только скрипты.
- **[проверено]** `CreateAttachmentAtBoneWS( thePlayer, 'кость', pos, rot )` крепит сущность к кости с её текущим
  мировым положением: дальше она следует за анимацией и за телепортом Геральта. `HasAttachment()` у такой сущности —
  0 (он про её собственных детей).
- **[проверено]** `SetVisibility( false )` у Геральта прячет и всё, что прикреплено к его костям. Тело, голова,
  волосы и броня Геральта — предметы (прячет `MCWF_HideItems`), поэтому для тела Стива актёр остаётся видимым, а
  прячутся только предметы.
- **[проверено]** `Matrix` (`core\math.ws:18`): строки `X`, `Y`, `Z` = `RotRight`, `RotForward`, `RotUp`;
  `MatrixGetRotation` по таким строкам возвращает исходные углы; положительный `Pitch` поднимает нос.
- **[проверено]** Меш из `box.w2mesh` с переписанными позициями вершин, рамкой и `collisionMesh` = NULL кукается и
  рисуется (`modMCWModels`), `collision.cache` у мода тогда не создаётся.
- **[проверено]** Гейм-рулы 1.21.11 — snake_case в `world\level\gamerules\GameRules.java`: `spawn_phantoms`,
  `spawn_monsters`, `spawn_patrols`, `spawn_wandering_traders`, `spawn_mobs`. Без `spawn_phantoms=false` Геральта (игрок
  клиента не спит) ночью убивают фантомы. `tick freeze` / `tick unfreeze` — заморозка мира сервера, игроки ходят.
- **[проверено]** Клиент MC 1.21.11: `BlockState.getCollisionShape` зовётся и для частиц (`Particle.move` ->
  `Entity.collideBoundingBox`) с `EntityCollisionContext` без сущности: `getEntity()` — null (вылет клиента 22:27).
- **[проверено]** Сервер MC 1.21.11 у транспорта (`handleMoveVehicle`) проверяет «moved wrongly» (> 0,25 м по
  горизонтали после своего `move`) и «столкнулся с новым»; `player_movement_check` на транспорт не действует.

### Witcher 3 5.00c: появление сущностей и компиляция (2026-10-03)
- **[проверено]** `CreateEntity` в кадре: сущность растворяется (узор dither) ~0,5 с — так и блоки modMCWBlocks.
  Сущность, созданная скрытой (`SetHideInGame( true, true )`) и показанная позже `SetHideInGame( false, true )`, видна
  целиком в первом же кадре, в том числе после `TeleportWithRotation` из другой точки (первый кадр смазан размытием
  движения). `SetHideInGame( false, true )` сразу после `CreateEntity` растворения не отменяет. Пробы —
  `mcw_fadetest`/`mcw_fadeshow` в `mcw_steve.ws`.
- **[проверено]** Компилятор 5.00c, ошибки, которых раньше не ловил `ws_lint` (теперь ловит): `thePlayer.поле = x` —
  «Left hand side is const» (глобальные объекты — константы, писать через локальную переменную);
  `Функция().Size()` — «Only arrays coming from variables can be modified» (любой метод массива у результата
  вызова). Неудачная перекомпиляция (`w3cmd.py --reload` → `FAILED`) игру не роняет, старые скрипты продолжают работать.
- **[проверено]** `CCamera.SetFov/GetFov` (`engine\camera.ws:56`, `:59`): у `poster_camera.w2ent` FOV 60, вертикальный
  (сверено рамкой MC по блоку); `theCamera.GetFov()` камеры геймплея — 67.
- **[проверено]** `mcw_ground` (луч сверху по Terrain + Static) под крышей отдаёт высоту крыши: для физики MC дом —
  сплошная колонна.
- **[проверено]** Предметы на Геральте (мечи на спине, арбалет, волосы, голова) — отдельные `CItemEntity`:
  `SetVisibility( false )` у `CActor` их не прячет. Прячет `GetInventory().GetItemEntityUnsafe( id ).SetHideInGame( true )`
  для `IsItemMounted || IsItemHeld`.

### Бой, стрелы, трещины (2026-10-04, `mcw_combat.ws`, `Npcs`, `combat.py`)
- **[проверено]** `mcw_npcs` в Белом Саду: 13 жителей в 40 м (`CNewNPC`, не лошади), все `IsHuman`, не враждебны;
  капсула взрослого — радиус 0,40 м, рост 1,80 м, ребёнка — 0,35 / 1,40. `GetCapsuleHeight/Radius` есть только у
  `CMovingPhysicalAgentComponent` (`movingAgentComponent.ws:275-276`), не у `CMovingAgentComponent` — поймал `ws_lint`.
- **[проверено]** Fabric `ServerEntityEvents.ENTITY_LOAD` приходит изнутри `ServerLevel.addFreshEntity`: сущность,
  которую регистрируешь в своей таблице после `addFreshEntity`, уборщик по тегу видит «чужой» и сносит сразу (прокси
  NPC пропадали в тот же тик). Регистрировать до.
- **[проверено]** Прокси NPC на сервере: 7 стоек для брони с тегом `mcw_npc`, позиции — NPC Ведьмака в координатах
  MC. Первая версия брала размер атрибутом `scale` (0,911 = 1,80 / 1,975, ширина выходила 0,46 м вместо 0,8); теперь
  размер — ровно капсула через миксин `LivingEntity.getDimensions` на сервере и клиенте, `scale` 1. Миксины `LivingEntity.hurtServer`, `ArmorStand.hurtServer`/`interactAt`,
  `Mob.interact` (1.21.11) применились без ошибок.
- MC 1.21.11 по исходникам: `Player.attack` считает урон (атрибут, зачарования, сила замаха, крит x1,5) и зовёт
  `entity.hurtOrSimulate(src, урон)` -> `hurtServer`; ответ `true` — «попал» (отбрасывание, взмах, звуки).
  `ArmorStand.hurtServer` переопределён целиком, без `super`; `Animal.hurtServer` доходит до `LivingEntity`.
  `ServerEntity.sendChanges` (`:118`) шлёт позицию раз в `updateInterval` тиков или при `entity.needsSync` (публичное
  поле). `AbstractArrow.isInGround()` защищённый, флаг `IN_GROUND` синхронизируется с сервера; поворот снаряда —
  курс `atan2(vx, vz)`, наклон `atan2(vy, гориз.)` (`Projectile.updateRotation`).
- Ведьмак по скриптам: стрела лучников — `items\weapons\projectiles\arrows\arrow_01.w2ent` (`btTaskShoot.ws:33`),
  `W3ArrowProjectile`: таймеры и столкновения — только после выстрела (`OnProjectileShot`, `OnProjectileCollision`),
  без выстрела это просто модель. След — эффект `arrow_trail`, звуки `cmb_arrow_swoosh`, `cmb_arrow_impact_dirt`
  (`arrowProjectile.ws:8, :35, :124`). Как пул стрел выглядит в игре — **[проверить]**.
- `W3PlayerWitcher.ReduceDamage` (`playerWitcher.ws:2385`) уже после брони: уклонение, Квен, навыки; урон по
  жизненной силе после него — то, что уходит в MC (`MCWN_OnHurt`). Падение (`FallingDamage`), окружение и урон со
  временем в MC не идут. Доходят ли до `ReduceDamage` удары NPC по невидимой кукле — **[проверить]** с волками.
- **[проверено]** Телепорт Геральта (`TeleportWithRotation`), пока игра в меню паузы, не применяется: позиция та же.
- Ванильные трещины (`destroy_stage_N.png`, 16x16): прозрачный фон и пиксели (61, 61, 61) и (155, 155, 155), ваниль
  кладёт их умножением x2 (`crumbling`): тёмные затемняют блок до ~48 %, светлые чуть осветляют.
- **[проверено]** 2026-10-04 01:04: лошадь MC на 14 м/с выбежала за известный рельеф (мост знает ~80 колонн/с) и
  упала в пустоту; кукла Ведьмака ушла за ней на z = -19 м, а `mcwfFar` (3D, > 10 м) и `CLIENT_JUMP` сервера
  тянули игрока MC обратно под мир каждую секунду. Отсюда страховка лошади в клиенте, горизонтальный `mcwfFar` и
  отказ сервера тянуть отвесно вниз.

- **[проверено]** `CEntity.CalcBoundingBox` у жителей Белого Сада — 2,2–3,1 м в ширину и ~2,2 м в высоту, от 0,4–0,7 м
  ниже ног (статичные габариты меша), при капсуле 0,40 / 1,80 м: как хитбокс не годится, прокси — по капсуле
  (проба `mcwx_npcbox`).
- **[проверено]** `mcw_grounds`: 48 колонн — 1–2 мс кадра (`gms`); `mcw_path`: луч вниз находит землю на той же высоте,
  что `mcw_ground`, горизонтальный на 3 м — стену в 8 м.
- Парирование и уклонение NPC в 5.00c (по скриптам, разведка агентом): решаются до `ProcessAction`, у атакующего —
  `CActor.TestParryAndCounter` (`actor.ws:5409-5505`) на конце замаха; NPC узнаёт о замахе в его начале
  (`SetAttackData`, `actor.ws:5374-5403`: `Time2Dodge`, `swingType`/`swingDir`, `ParryStart` при `IsGuarded` или щите,
  `IsAttacked`). Парирует сам NPC: `CNewNPC.PerformParryCheck` (`npc.ws:1028`, защищённый) — `bParryEnabled &&
  IsGuarded`, угол < 90, выносливость; `bParryEnabled` поднимает задача `btTaskPerformParry` после `ParryStart`,
  анимация — по `ParryPerform`. Уклонение — `IsCurrentlyDodging` (0,4 с после `Time2Dodge`, `btTaskDodge.ws`).
  Прямой урон (`DAMAGE_NAME_DIRECT`) добавляется после `ReduceDamage` (`damageManagerProcessor.ws:479, 517-545`) и
  парированием не обнуляется — его не добавляет сам мод. `W3Action_Attack.Init` (`attackAction.ws:27`), не
  `Initialize`; после — `ClearDamage`/`ClearEffects`. Как часто NPC успевают парировать при задержке удара 0,2 с —
  **[проверить]**.
- Парирование игроком (`CR4Player.PerformParryCheck`, `r4Player.ws:9333`) требует `IsActionAllowed(EIAB_Parry)` и
  `RaiseForceEvent('PerformParry')` на кукле — мод вместо этого сам ставит `SetIsParried` в `ReduceDamage` (щит MC,
  удар спереди), ванильный `CActor.ReduceDamage` обнуляет урон (`actor.ws:5991-6013`); NPC отшатывается
  `ReactToReflectedAttack` (`actor.ws:2736`). Отдельной реакции «меня парировали» у NPC в скриптах нет.

- **[проверено]** 2026-10-04: стрела `arrow_01.w2ent` сразу после `CreateEntity` — один `CTriggerActivatorComponent0`,
  меш подгружается стримингом. Созданная у камеры видна (и после `SetHideInGame( true, true )` + `( false, true )` в том
  же кадре), а созданная на парковке в 800 м под миром меша так и не получила: после переноса к камере невидима
  (снимки окна, пробы `mcwx_arrows`, `mcwx_arrowtest`). Пул стрел теперь ждёт в 10 м под Геральтом без пряток —
  видна. Блоки `modMCWBlocks` (наши шаблоны без стриминга) на дальней парковке работают.
- **[проверено]** Здоровье гуся в Белом Саду — около 1 (`GetHealth`), удар долей максимума (0,3 за удар) убивал его с
  4 ударов; теперь удар MC — удар Геральта его мечом x урон MC / 6.

- `CActor.SetVisibility( false )` прячет актёра и для ИИ: условие деревьев поведения `CBTCondIsVisible` (meshVisibility,
  target) проверяет `GetCombatTarget().GetVisibility()` (`game\behavior_tree\conditions\btCondIsVisible.ws:61`). С
  невидимой куклой враги в режиме Стива ходили рядом и не нападали (жалоба 2026-10-04). Тело Геральта — предметы,
  прятать их (`MCWF_HideItems`), актёр — видимый.

- **[проверено]** 2026-10-04: лошадь MC после спешивания стояла на y 1,0 при земле Ведьмака 1,63 — клиент вёл её по
  непрерывной земле, она оказалась ногами в barrier-блоке (верх barrier — округлённая высота, до 0,5 м выше), а MC не
  выталкивает сущность из формы, в которой она стоит, — провалилась на блок. `Horses.tick` ставит её на верх столбика
  (y 2,0); Ведьмак рисует на земле колонны (мост, разница до 0,6 м).
- Земля Ведьмака у клиента — ступени (колонны 1×1, подколонны 25 см): лошадь MC на скаку с каждой ступени летит тик-два,
  а в воздухе `LivingEntity.travel` разгоняет её только `flyingSpeed` — скорость падала, картинка тряслась. Прижим к
  опоре до 0,55 м (`Control.snapDown`, ванильный `move`) — **[проверить]** езду.

## Где источники расходились и как решили
Решения опираются на факты выше, метки — там.
- **Куб для спайка.** denied_area, cont_crate, реквизит фоторежима, box_05_05_1m → основной cont_crate по депо-пути
  (сначала ванильным `spawnPath`). box_250cm (denied_area) и box_05_05_1m — невидимые коллайдеры (`drawableFlags={}`):
  для них только опыт, станут ли видимыми через `SetVisible`. Видимой замены cont_crate нет, кроме реквизита фоторежима.
- **Предел exec.** «~60 КБ» опровергнуто: строка ограничена i16, поэтому ≤32 767 байт и для команды, и для ответа;
  рабочий размер — замером с 1–2 КБ.
- **Log или LogChannel.** В ответе exec подтверждён только `Log()`. Что делает LogChannel, покажет `mcw_logtest`.
- **«Лог только файлом» против `no-netlog` в exe.** Не решено: на спайке минуту слушаем сырые пакеты.
- **REDkit для этапа 2.** «Обязателен из-за смены collision.cache» (community-doc) против w3edit, коллизии от
  `box.w2mesh` и xbm с пикселями → похоже, REDkit нужен только для `.w2ent`/`.w2mi`, но обе посылки **[проверить]**:
  примет ли игра упаковку w3edit и даст ли `box.w2mesh` коллизию вторым компонентом.
- **Loom.** Пример 1.21.11 — 1.18-SNAPSHOT, релиз — 1.18.2, но оба требуют Gradle на JVM 25 → пин 1.17.21 под JDK 21.
- **Новизна аннотаций.** Не 5.0: они есть с 4.04a. Scope-based overrides REDkit 5.0 — отдельный механизм (rsblob).
- **Номера строк.** `CreateEntity` — `game.ws:129` (не 125), `GetEngineTimeAsSeconds` — `:58` (не 21, там `IsPaused` на
  `:22`), `CreateEntityAsync` — `commonGame.ws:78`. Перепроверено grep 2026-10-03.
- **Где включаются моды.** Тумблер REDlauncher и `[ContentManager/Mods]` в settings — верны оба, проверяем и cmdline, и
  settings.
- **«script mods must be created via REDkit»** — про mod.io, не про локальные моды.
- **«content\ содержит только content0»** — неверно: там же `metadata.store*`.
- **«Удали *.redscripts».** На 5.0 есть только поставочный cookedfinal, удалять нельзя; вместо этого
  `-forcescriptcompilation`.
- **DBGConsoleOn.** `general.ini` (wiki 2024) или `dx12user.settings` (мод 13072) — на 5.00c не проверен ни один.
- **Мод 13072 «только скрипты».** Неверно: идёт с NoNearCameraCulling.asi и dinput8.dll.
- **`GetAppearanceNames` «out array<name>».** Параметр объявлен без out, для перечисления appearance не годится.
- **Фейковый игрок.** Fabric FakePlayer → ServerPlayer по полной схеме Carpet (миксин PlayerList, ручной
  loadPlayerData).
- **Канал мост ↔ Fabric.** WebSocket примера → обычный TCP, порт 25600 вместо 25599.
- **Текстуры MC «все 16×16».** Неверно: 1041 из 1111. 1210 записей в `textures/block` — это 1111 PNG + 99 mcmeta
  (пересчитано 2026-10-03).
- **texture.cache «только через REDkit».** Неверно: w3edit пишет v7, а часть xbm несёт пиксели в бандле.
- **874 или 877 exec-функций.** Разные шаблоны grep, на выводы не влияет.

## Источники
- Протокол: [rw3d](https://github.com/SpontanCombust/rusty_witcher_debugger) (коммит 5a0cf7d, `crates/net`),
  [rw3d PR #12](https://github.com/SpontanCombust/rusty_witcher_debugger/pull/12),
  [WolvenKit-7](https://github.com/WolvenKit/WolvenKit-7) (`WolvenKit.Net/Commands.cs`),
  [Witcher3-Multiplayer](https://github.com/werasik2aa/Witcher3-Multiplayer),
  [Witcher Online](https://github.com/rejuvenate7/WitcherOnline) (коммиты 8d608096, 4432ca1b),
  [witcherscript-ide](https://github.com/SpontanCombust/witcherscript-ide).
- CDPR: [REDkit Changelog](https://cdprojektred.atlassian.net/wiki/spaces/W3REDkit/pages/12058625/Changelog),
  [WS: overrides](https://cdprojektred.atlassian.net/wiki/spaces/W3REDkit/pages/36241598/WS:+Script+Compilation+Errors+overrides),
  [первый скриптовый мод](https://cdprojektred.atlassian.net/wiki/spaces/W3REDkit/pages/36241465/WS+Create+your+first+script+mod),
  [консоль](https://cdprojektred.atlassian.net/wiki/spaces/W3REDkit/pages/36208717/WS+Enable+debug+console+in-game),
  [Managing mods](https://cdprojektred.atlassian.net/wiki/spaces/W3REDkit/pages/36339714/Managing+mods),
  [Publishing mods](https://cdprojektred.atlassian.net/wiki/spaces/W3REDkit/pages/6328254/Publishing+mods),
  [поддержка: mod.io](https://support.cdprojektred.com/en/witcher-3/pc/gameplay/issue/3001/cross-platform-mod-support-how-to),
  [EULA REDkit](https://store.steampowered.com/eula/2684660_eula_1),
  [новости 5.0](https://www.thewitcher.com/us/en/news/52067/patch-5-00b-for-the-witcher-3-wild-hunt-remastered-is-now-live).
- Скрипты и моды 5.0: [диффы 4.04b→5.00b→5.00c](https://github.com/ElementaryLewis/The-Witcher-3-Remastered-Changelog),
  [AIWitcher](https://github.com/DaniilKvasnikov/AIWitcher), [modSaveNames](https://github.com/MajaAgent/modSaveNames),
  [community-doc «What does the remaster mean for modding?»](https://docs.google.com/document/d/1-9A97cBUIfOIEXJiid-vkFIAEdZVWipAkyKid9AhWeU),
  Nexus [12898](https://www.nexusmods.com/witcher3/mods/12898), [13072](https://www.nexusmods.com/witcher3/mods/13072),
  [13075](https://www.nexusmods.com/witcher3/mods/13075), [13076](https://www.nexusmods.com/witcher3/mods/13076),
  [13106](https://www.nexusmods.com/witcher3/mods/13106), [13138](https://www.nexusmods.com/witcher3/mods/13138),
  [6402](https://www.nexusmods.com/witcher3/mods/6402), [5940](https://www.nexusmods.com/witcher3/mods/5940).
- Форматы и ассеты: [w3edit](https://github.com/Systemcluster/w3edit) (`w3edit-formats.md`),
  [Witcher3-Blender-Tools](https://github.com/dingdio/Witcher3-Blender-Tools),
  [w3.NewPlayerCharacters](https://github.com/nikich340/w3.NewPlayerCharacters),
  [интервью 80.lv о REDengine 3](https://80.lv/articles/the-witcher-3-interview-a-deeper-look-into-redengine-3),
  [steamcmd 292030](https://api.steamcmd.net/v1/info/292030),
  [steamcmd 2684660](https://api.steamcmd.net/v1/info/2684660).
- ASI: [Ultimate ASI Loader](https://github.com/ThirteenAG/Ultimate-ASI-Loader/releases),
  [RED3ext](https://github.com/WopsS/RED3ext),
  [Witcher3-OnlineModule](https://github.com/Hyjarion/Witcher3-OnlineModule).
- Minecraft: [fabric-example-mod 1.21.11](https://github.com/FabricMC/fabric-example-mod/tree/1.21.11),
  [meta.fabricmc.net](https://meta.fabricmc.net/v2/versions/loader/1.21.11),
  [Fabric 1.21.11](https://fabricmc.net/2025/12/05/12111.html), [fabric-docs](https://github.com/FabricMC/fabric-docs)
  (`develop/loom/options.md`), [Fabric API 1.21.11](https://github.com/FabricMC/fabric-api/tree/1.21.11),
  [fabric-carpet 1.4.194](https://github.com/gnembon/fabric-carpet/tree/1.4.194) (`EntityPlayerMPFake.java`,
  `FakeClientConnection.java`, `PlayerList_fakePlayersMixin.java`, `NetHandlerPlayServerFake.java`), Loom
  [1.17.21](https://maven.fabricmc.net/net/fabricmc/fabric-loom/1.17.21/fabric-loom-1.17.21.module) и
  [1.18.2](https://maven.fabricmc.net/net/fabricmc/fabric-loom/1.18.2/fabric-loom-1.18.2.module) (Gradle module),
  [#1129](https://github.com/FabricMC/fabric-loom/issues/1129),
  [#1326](https://github.com/FabricMC/fabric-loom/issues/1326),
  [Gradle 9.7.1: совместимость](https://docs.gradle.org/9.7.1/userguide/compatibility.html),
  [манифест версий Mojang](https://piston-meta.mojang.com/mc/game/version_manifest_v2.json).
