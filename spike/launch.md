# Запуск спайка: шпаргалка

Игра: `C:\Program Files (x86)\Steam\steamapps\common\The Witcher 3`, `bin\x64_dx12\witcher3.exe` 5.0.0.1044392
(hotfix 5.00c, Steam buildid 25646871). С 2026-10-03 игра с модом запускается на 5.00c (spike/RESULTS.md).

## Команды

| Что | Как |
|---|---|
| Запуск | `Start-Process "C:\Program Files (x86)\Steam\steam.exe" -ArgumentList '-applaunch','292030','--launcher-skip','-net','-debugscripts'`. Вызов через `&` при закрытом Steam игру не запускал (2026-10-04) |
| Первый запуск с новым или изменённым модом | добавить `-forcescriptcompilation` |
| Постоянно | Steam → Witcher 3 → Свойства → Параметры запуска: `--launcher-skip -net -debugscripts` |
| Проверить флаги | `Get-CimInstance Win32_Process -Filter "Name='witcher3.exe'" \| Select-Object ProcessId, CommandLine` или `python w3_preflight.py` |
| Порт отладки | 37001 (игра; 37000 у редактора): `netstat -ano \| findstr :3700` |
| Лог скриптов | `Documents\The Witcher 3\scriptslog.txt` (пишется при `-debugscripts`; `DebugScriptsForceFlush=true` уже стоит) |
| Поставить или снять мод | `.\install_mod.ps1 [-Probe]` / `.\uninstall_mod.ps1 [-Probe \| -ProbeOnly]`, сначала с `-WhatIf` |
| Бэкап настроек и сейвов | `.\backup_userdata.ps1` → `%LOCALAPPDATA%\mcw\backups\<время>` |

- Флаги `-net`, `-debugscripts`, `-disablemods`, `-disableModio`, `-forcescriptcompilation`, `-verifyscriptsandexit`
  есть в строках exe 5.00c. `--launcher-skip` — флаг REDprelauncher, в exe его нет (reported).
- Запуск из командной строки с аргументами Steam может остановить окном подтверждения. 2026-10-05 через
  `Start-Process` при открытом Steam игра стартовала сама, окна не было. Появится — удобнее прописать параметры
  запуска в свойствах игры.
- Если в cmdline есть `-disablemods` (лаунчер после обновления), моды не грузятся: см. `w3_preflight.py`.
- **`content0\cookedfinal.redscripts` (6 625 059 Б) не удалять.** Когда скрипты «не те», запускать с
  `-forcescriptcompilation`. Совет удалять `x64.final.redscripts` к 5.0 не относится: такого файла нет.

## Ошибка компиляции скриптов

При старте игра показывает окно `Script compilation error` / `There were errors compiling scripts. Try again?` со
списком `файл(строка): ошибка`. Сфотографировать список, ответить «Нет», игра закроется. Если сломалась проба:
`.\uninstall_mod.ps1 -ProbeOnly`. Если сломался базовый мод, чинить `.ws`. Стартовую компиляцию видно только по
этому окну: клиент в этот момент ещё не подключён. По сети компиляцию проверяет `python w3cmd.py --reload`
в запущенной игре (сообщение ScriptCompiler `finished`, 0 = успех).

`-verifyscriptsandexit` (строка в exe рядом с загрузкой скриптов) может оказаться быстрой проверкой компиляции
без запуска мира **[проверить]**: `witcher3.exe -verifyscriptsandexit`, затем смотреть код выхода и лог.

## Консоль (по желанию)

`DBGConsoleOn=true` в секции `[General]`. Варианты: `Documents\The Witcher 3\dx12user.settings` (не трогает
папку игры, мод 13072) или `bin\config\base\general.ini` (AIWitcher). Какой работает на 5.00c,
**[проверить]**. Клавиша консоли тоже **[проверить]**. Правку вносишь сам, скрипты спайка этих файлов не пишут.

## Шаги спайка

Без игры клиент и замеры проверяются на фейке: `python bench_w3.py --fake all`. Команды с `\"` ниже записаны
для cmd и Git Bash. PowerShell 5.1 теряет кавычки внутри аргументов внешних программ, поэтому такие команды
лучше положить в файл по одной в строке и запустить `python w3cmd.py -f cmds.txt`. Аргументы типа name пишутся
в одинарных кавычках, с ними PowerShell справляется. `#` первым аргументом mcw-функции w3cmd заменяет свежим seq.

| Шаг | Что сделать | Признак успеха |
|---|---|---|
| 0a | `python w3_preflight.py`, затем `.\backup_userdata.ps1`. Записать `scriptslog_exists` и `mods_settings` (для отката) и правила брандмауэра игры: `Get-NetFirewallApplicationFilter -Program "<игра>\bin\x64_dx12\witcher3.exe" \| Get-NetFirewallRule \| Select-Object Name, DisplayName, Direction, Action` (ошибка `No MSFT_NetApplicationFilter objects found` = правил нет; 2026-10-03 их не было) | в preflight нет неожиданных «внимание», бэкап с manifest.txt |
| 0b | Первый запуск 5.00c через REDlauncher (Steam «Играть» без параметров). В шестерёнке включить Mods, дойти до меню, выйти. Потом параметры Steam `--launcher-skip -net -debugscripts`. Первый запуск с `-net` — в окне (Настройки → Графика → оконный режим): если всплывёт запрос брандмауэра Windows, в полноэкранной игре он уводит фокус. Ответить «Отмена» (доступ из сети мосту не нужен; «Отмена» создаёт блокирующие входящие правила для witcher3.exe) и записать в RESULTS.md, что спросили | preflight: `LastLaunchVersion: v 5.00c`, у witcher3.exe флаги `-net` и `-debugscripts`, 37001 слушается. Запрос брандмауэра — признак, что порт открыт не только на 127.0.0.1 (сверить на 0h) |
| 0c | Без своего мода. `python w3cmd.py --info`. Затем **до первого exec** `python w3cmd.py --listen 60`: минуту стоять в мире, открыть и закрыть меню. Потом `python w3cmd.py --fifo "spawnbarrel()"` (ваниль, temp.ws:7602) и `python w3cmd.py --fifo "spawnPath('gameplay\containers\_container_definitions\_mesh_entities\cont_crate.w2ent')"` (temp.ws:6790, аргумент типа name). Если ответ `Warn: Failed to process command`, пробовать `/` вместо `\` и `\\` **[проверить]** | root и моды читаются. `--listen` ничего не напечатал: пакетов без запроса нет, FIFO-сопоставление ответов годится. Если пакеты пришли, записать какие (строка «Строки сетевого лога без запроса») и сопоставлять ответы только по `[MCW#seq]`. На оба exec ответ `Spam: Command executed without errors` одним пакетом, у ног Геральта бочка, в пределах 3 м ящик |
| 0d | До игры: `python ws_lint.py mod\modMCW\content\scripts\local\mcw_spike.ws mod\modMCWProbe\content\scripts\local\mcw_probe.ws`. Потом `.\install_mod.ps1 -WhatIf`, `.\install_mod.ps1`, запуск с `-forcescriptcompilation`. В мире: `python w3cmd.py --reload`, затем `python w3cmd.py "mcw_ping(#)" "mcw_echo(#, \"abc\")"` и `python w3cmd.py --tail "mcw_logtest(#)"` | ws_lint: 0 ERROR. Окна ошибок компиляции нет, `ok  reload` (finished = 0). `[MCW#<seq>] pong <ms>`, `[MCW#<seq>] echo 3 abc`, у каждой команды `[MCW#<seq>] end`. По `mcw_logtest` записать, что из `via Log` и `via LogChannel` пришло в ответ exec (`ok`), а что только в `[log]` |
| 0e | `python bench_w3.py rtt rate frame poll echo batch charset loglat --label "мир"` и то же с `--label` «пауза», «меню», «загрузка» (запускать прямо перед загрузкой сейва) и «катсцена» (запускать перед сценой, например у NPC с диалогом-роликом). `python bench_w3.py maxlen` последним и отдельно (команды до 4 КБ; выброшенная команда и потерянный ответ — тишина, проба ждёт её до 1 с, весь тест ~15–30 с) | метрики в RESULTS.md: Log или LogChannel, charset, maxlen, RTT, calls/s, batch (JSON в `results\`) |
| 0f | **Видимость.** `python w3cmd.py "mcw_cube(#, \"-\")"`, отойти на 15–30 м: на каком расстоянии ящик пропал (в templates.json у cont_crate `streamingDistance` 10, единицы **[проверить]**) и пропадает ли тень. Запрыгнуть на ящик: стоит ли Геральт. **Шаблоны.** `python w3cmd.py "mcw_alias(#, 'denied_area', 0.4)" "mcw_alias(#, 'barrel', 0.0)"`; упереться в невидимый denied_area. `python w3cmd.py "mcw_look(#)"` (смотреть на ящик), `python w3cmd.py "mcw_poll(#)"`, `python w3cmd.py "mcw_ground(#, <x>, <y>)"` (x, y в см из строки `pos`), `python w3cmd.py "mcw_boom(#)"`. **Расширения для моста.** В ответе `mcw_look` на ящик — строка `normal`: смотреть на ящик с разных сторон, единичная ли она (|n| ≈ 10000) и в мировых ли осях (грань к камере). `python w3cmd.py "mcw_boomat(#, <x>, <y>, <z>)"` (точка в 5 м от `pos`, z на 1 м выше): бочка встала на землю под точкой и взорвалась почти сразу (запал 0,2 с). `python w3cmd.py "mcw_countblocks(#)"` после `mcw_cube` и `mcw_boom`: пробы во втором числе, блоки — в первом. **Мобы (mcw_ents, этап 5).** `python w3cmd.py "mcw_ents(#, \"spawn:0:<x>:<y>:<z+52>:0:0\")"` (точка в 3 м от `pos`), затем несколько раз `"mcw_ents(#, \"move:0:<x+50>:<y>:<z+52>:9000\")"` со сдвигом по 50 см, потом `"mcw_ents(#, \"kill:0\")"`; в одном пакете `"mcw_ents(#, \"spawn:1:…:0|move:1:…\")"` (видит ли GetEntityByTag сущность того же кадра); `"mcw_ents(#, \"wipe\")"`; `mcw_count` и `mcw_countblocks` между ними не меняются. Смотреть: ящик стоит низом на земле, прыгает на каждый move и поворачивается (курс 90 — смотрит на запад?), коллизия едет вместе с ним, `mcw_ground` и `mcw_look` по ящику. **Нагрузка.** `python w3cmd.py "mcw_clearall(#)"`, затем `python bench_w3.py stress --levels 100,1000,3000 --hold 5`; FPS и RAM смотреть оверлеем и в диспетчере задач, глазами — сколько кубов видно. Если ящик пропадает к ~10 м: из ответа `mcw_poll` взять строку `pos` и `python coords.py disk --pos "<строка pos>" > disk.txt` (≈1080 блоков в кольце 2–9.5 м, 4 слоя), `python w3cmd.py -f disk.txt`, `python w3cmd.py "mcw_count(#)"`, замерить FPS, `python w3cmd.py "mcw_clearall(#)"` | `spawned 1` на каждом спавне, `look 1 … 1` и `normal …` на ящике, `ground 1 <z>`, бочка взорвалась (и от `mcw_boomat` — в своей точке), `countblocks` не считает пробы, `mcw_ents` отвечает `ents 1 0 0 0`, `ents 0 1 0 0`, `ents 0 0 1 0` без ошибок (четвёртое число). Таблицы «Шаблоны» и «Кубы» в RESULTS.md заполнены, включая «видно кубов из N». Если видна лишь часть кубов, FPS для 1000 берётся из плотной кладки, а цифры stress помечаются «только загруженные меши» |
| 0g | `.\install_mod.ps1 -Probe`, запуск с `-forcescriptcompilation`; `python w3cmd.py "mcwp_state(#)" "mcwp_put(#, \"a\")" "mcwp_get(#, \"a\")" "mcwp_del(#, \"a\")"`; загрузить сейв, снова `mcwp_state` | проба скомпилировалась, map и @addField работают, токен после загрузки сменился. При ошибке компиляции `.\uninstall_mod.ps1 -ProbeOnly`, базовый мод должен остаться рабочим |
| 0h | `netstat -ano \| findstr :37001` (смотреть уже на 0b) | слушает `127.0.0.1`. Если `0.0.0.0`, нужно правило брандмауэра (ниже) |

Каждый вылет записывать в журнал RESULTS.md: время, шаг, что делали, причина.

### Если 37001 слушает 0.0.0.0

Тогда exec доступен всей локальной сети. Правило вносишь сам, нужен PowerShell от администратора:

```powershell
New-NetFirewallRule -DisplayName "MCW - block W3 debug port" -Direction Inbound -Protocol TCP `
  -LocalPort 37000,37001 -Program "C:\Program Files (x86)\Steam\steamapps\common\The Witcher 3\bin\x64_dx12\witcher3.exe" -Action Block
```

Локальный мост ходит через 127.0.0.1. Брандмауэр Windows loopback не фильтрует **[проверить]**: после правила
`mcw_ping` должен по-прежнему отвечать. Снять правило: `Remove-NetFirewallRule -DisplayName "MCW - block W3 debug port"`.

## Откат

1. Закрыть игру. Если висит `crashreporter`, завершить его по PID.
2. Удалить вручную `mods\modMCWBlocks` и `mods\modMCWModels` (их собирают `tools\build_blocks.py` и
   `tools\build_models.py`, скрипт их не трогает), затем `.\uninstall_mod.ps1 -Probe`: удалит `mods\modMCW`,
   `mods\modMCWProbe` и опустевшую `mods`. Про
   `Documents\The Witcher 3\mods.settings` скрипт скажет, что делать: если файла нет в manifest.txt последнего
   бэкапа (его создала игра), удалить, иначе вернуть из бэкапа. Сам он файл не трогает.
3. Убрать параметры запуска в Steam.
4. Вернуть настройки командой, которую напечатал `backup_userdata.ps1`:
   `robocopy "<бэкап>" "Documents\The Witcher 3" /E /XF manifest.txt`. robocopy без `/PURGE` ничего не удаляет:
   сейвы, сделанные с модом, останутся помеченными как модифицированные, а файлы, которых не было в бэкапе,
   убираются руками (п. 2 и 5).
5. `Documents\The Witcher 3\scriptslog.txt`: если на 0a было `scriptslog_exists: False`, удалить (его создал
   `-debugscripts`).
6. Брандмауэр (от администратора): `Remove-NetFirewallRule -DisplayName "MCW - block W3 debug port"`, если ставил;
   правила witcher3.exe, которых не было в списке с шага 0a (их создаёт «Отмена» на 0b), — найти той же командой
   `Get-NetFirewallApplicationFilter … | Get-NetFirewallRule` и снять `Remove-NetFirewallRule -Name <Name>`.
7. Проверить через `python w3_preflight.py`: `mods_dir: False`, порты не слушаются, `cookedfinal_redscripts_ok: True`.
