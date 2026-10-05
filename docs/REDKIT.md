# REDkit без GUI: разведка и конвейер блоков (этап 2)

Метки как в [NOTES.md](NOTES.md): **[проверено]** — на этой машине, **[по данным сообщества]**, **[проверить]** —
выяснит только игра. `<REDkit>` = `C:\Program Files (x86)\Steam\steamapps\common\The Witcher 3 REDkit`,
`<work>` = `%LOCALAPPDATA%\mcw\redkit_work`. Источники: строки `wcc_lite.exe`/`editor.exe` (`strings`), линкер-мап
`editor.map`, логи прогонов (`<work>\logs\*.log`). Разведка 2026-10-03, REDkit 5.0.1044630, игра 5.00c (1044392).

## Коротко

- **[проверено]** Весь путь от PNG до готового мода проходит без GUI, одним `wcc_lite.exe`: import (PNG→xbm),
  cook, buildcache textures/physics, pack, metadatastore. Его оборачивает `tools\build_blocks.py`. Мод
  `modMCWBlocks` на 11 блоков собран и проверен нашими инструментами (`verify: OK`) за 4 минуты.
- **[проверено]** Готового uncooked-депо всех ассетов в REDkit нет. `r4data` (78 ГБ, 232 111 файлов) — депо
  редактора: там почти все `.w2ent`/`.w2mi`/`.w2mg`, а мешей всего 110 и xbm — 1161. Остальное редактор
  разворачивает из бандлов игры по требованию (uncook-депо). Наши шаблоны (`box.w2mesh`, `box_05_05_1m.w2ent`, w2mi на
  `pbr_std`) в `r4data` есть.
- **[проверено]** Как делаем `.w2ent`/`.w2mi`/`.w2mesh`: берём шаблон из `r4data` и правим его нашим писателем CR2W
  (`tools\cr2w.py`, `CR2WDoc`). Round-trip побайтовый на 2697 cooked и 711 uncooked файлах.
- **[проверено]** С форматами игры совпадает всё: bundle v5, CR2W v164, `metadata.store` VTM v7, `texture.cache`
  HCXT v7, `collision.cache` CC3W v11. Кук REDkit ванильного `box_05_05_1m.w2ent`, `pbr_std.w2mg`, `grey.xbm`,
  `normal.xbm` и `box.w2mesh.1.buffer` побайтово совпадает с файлами из бандлов 5.00c. В `box.w2mesh` разница одна —
  мусорный `boundingBox.Min.W`.
- **[проверено в игре]** (оркестратор, 2026-10-03, сборка `out\`): мод с LZ4HC-бандлами грузится, все 11 блоков
  спавнятся с текстурами MC, отбрасывают тень, коллизия работает, грани ориентированы верно (у травы зелёный верх
  сверху и бахрома вверху боков, инструменты верстака стоят прямо).
- **[проверено в игре]** Альфа-тест в `out\` не работал: стекло и листва — сплошные кубы. Причина найдена и исправлена
  офлайн (раздел «Альфа-тест»): `enableMask` надо ставить и на локальный материал меша. Сборка с правкой —
  `<work>\out_alpha\`.
- **[проверено в игре]** 2026-10-03, 16:21: `out_alpha` стоит в `<игра>\mods\modMCWBlocks`. У стекла видна только
  рамка и блики, сквозь него виден фон; у листвы дыры. Днём цвета досок, бревна и листвы такие же, как в MC: желтизна
  ночью была от освещения. Ещё ждёт игры звук шагов по physmat.

## Состав установки

- **[проверено]** `<REDkit>` = `assets\w3_audio` (Wwise), `bin`, `r4data`. Папок `bundles` и `dlc` нет, хотя wcc их
  ищет.
- **[проверено]** `bin\x64_RedKit\`: `wcc_lite.exe` (150 МБ), `bundlebuilder.exe`, `witcher3Release.exe`,
  `r4Launcher.exe` — консольные (PE subsystem 3); `editor.exe`, `scriptStudio.exe` — GUI. Рядом `libfbxsdk.dll`
  (импорт FBX), `DevIL.dll`/`ILU.dll` (картинки), PhysX3 и `editor.map` — линкер-мап редактора на 660 МБ с именами
  функций (`CGameConfiguration::GetUserPathSuffix`, `CSplitEditorDepot::GetWritableDepotPath`, `CModCooking*Step`).
- **[проверено]** `bin\tools\cooker\`: `W3CookerTool.exe` (.NET, полный кук игры) и батники внутреннего тулчейна CDPR
  (`toolchain\_step_*.bat`). Они зовут `wcc.exe`, которого в поставке нет, пути захардкожены (`Z:\cooked_builds`).
  Пригодились только синтаксисом: `buildcache textures|physics -db -out`, `metadatastore -path`, `exportbundles`,
  `split`, `splitcache`.
- **[проверено]** Мод в редакторе кукается внутри процесса `editor.exe` (`CModCooking{Clean,Analyze,Cook,
  CollisionCache,TextureCache,CompileScripts,CompileShaders,CreateBundles,CreateMetadataStores,Install,Package}Step`).
  Проект редактора — файл `*.w3edit` и папки `workspace\`, `cooked\`, `packed\content\`, `publish\`. В wcc есть те же
  шаги в виде командлетов.
- **[проверено]** Версии: `wcc_lite.exe` — `5.0.1044630  P4CL: 13360103  Stream: //Red_engine/Main.Lava.Release`,
  собран `Oct  1 2026`; игра — `5.0.1044392  P4CL: 13360103`. Changelist исходников один и тот же.

### r4data (депо редактора)

- **[проверено]** 232 111 файлов, 78,0 ГБ: 75 452 `.thmb` (8 ГБ), 34 627 `.w2l`, 27 450 `.w2ent`, 5705 `.w2ter`,
  1241 `.w2mi`, 1161 `.xbm` (17 ГБ, исходные пиксели внутри), 726 `.w2mg`, 110 `.w2mesh`, 1527 `.ws`. Для сравнения,
  в бандлах игры 56 474 `.w2mesh` и 35 855 `.xbm`. Например, `crate.w2mesh` в `r4data` нет.
- **[проверено]** Версии CR2W v125…v164. До v154 включительно — старая раскладка (длина строк префиксом, без
  таблиц): так лежат, например, `grey.xbm` (v152) и часть `.w2mi`/`.env`. С v155 — раскладка с 10 таблицами, как у
  cooked 5.0. `tools\cr2w.py` читает и пишет только её.
- **[проверено]** `pbr_std.w2mg`: параметры Diffuse (по умолчанию `engine\textures\editor\grey.xbm`), Normal
  (`...\normal.xbm`, плоская), RSpecScale, RSpecBase, SpecularColor (35,35,35), VarianceColor, VarianceOffset,
  Translucency, ShadowGradient, wetness_switch. Альфа-тест — свойство `enableMask=true` у `CMaterialInstance`: так
  сделана 21 ванильная w2mi на `pbr_std` (паутина, корни). Флаг действует только на тот экземпляр, который висит на
  чанке меша (раздел «Альфа-тест»).
- **[проверено]** Uncooked-файлы, в отличие от cooked, несут: у `.w2mesh` — локальные `CMaterialInstance`, коллизию
  (`CCollisionMesh` + `CCollisionShapeBox`), сырые вершины в буферах внутри файла и миниатюру после `bufferSize`;
  у `.w2ent` — компонент в `entityObject` и копию во `flatCompiledData` (вложенный CR2W).
- **[проверено]** В логе wcc: `!!! FILE CORRUPTION !!! Chunk CRC mismatch` у ванильных `dlc\dlc12`, `dlc15`,
  `ep1.reddlc` (несовпадение crc экспорта, как у `box.w2mesh`). Это предупреждение, загрузке оно не мешает.

## wcc_lite.exe

### Запуск

- **[проверено]** `gameconf.cfg` читается из `<cwd>\..\gameconf.cfg`: без него wcc сразу выходит с ошибкой. Лог
  пишется в `<cwd>\..\wcc.log`. Корень, `bin`, `r4data`, `bin\config` берутся от пути exe. Поэтому cwd =
  `<work>\wccroot\bin\x64_RedKit`, рядом лежат копии `gameconf.cfg`/`redscripts.ini`.
- **[проверено]** Командлет — первый аргумент. Ключи после него: `-a=b` или, для `-workspaceDir`/`-uncookDir`,
  `-key <значение>` через пробел.
- **[проверено]** Без `-agreetoterms` wcc показывает окно EULA (GUI), согласие хранится в
  `HKCU\Software\CD Projekt RED\Mod Tools\1.0\DoNotShowEula`. Первый запуск без флага висел 90 с, пока его не убили.
- **[проверено]** Глобальные ключи: `-verbose -silent -memdump -noassert -noninteractivecrash -agreetoterms
  -uncookDir -workspaceDir -wcclog -longpathaware`, плюс `-datapath=` (свой data path). Сам wcc добавляет
  `-profilecooker -useCookedLocale -disableModio`.
- **[проверено]** Любой вызов поднимает движок примерно за 13–15 с: депо с dep.cache (442 210 записей) — 5 с, затем
  компиляция скриптов, звуковые банки, Galaxy. Со всеми шагами 11 блоков собираются за 4 мин.

### Депо мода: workspace

- **[проверено]** Депо редактора составное (`CSplitEditorDepot`): база `r4data` + uncook-депо + workspace мода.
  Новые файлы пишутся в workspace (`GetWritableDepotPath`). Рабочий вариант:
  `-workspaceDir <work>\project\ -uncookDir <work>\uncook\` через **пробел**, файлы мода лежат в
  `<work>\project\workspace\` (подпапка `workspace\` обязательна). В корень `<work>\project\` wcc кладёт
  `LocalEditorStringDataBaseW3_UTF8_mod.db`.
- **[проверено]** Ошибки, на которые наткнулись по дороге:
  - с `-workspaceDir=<путь>` ключ молча игнорируется. Workspace тогда = `<REDkit>\bin\`: в логе assert «Depot
    directory path should end with \ or /» и 1000 строк «Missing dependency file entry» про файлы `bin`, а
    `import -depot=local -out=<относительный путь>` пишет прямо в `<REDkit>\bin\<путь>`. Так один раз появился
    `bin\mcw\blocks\stone\stone.xbm`; его перенесли в `<work>`, папку `bin\mcw` удалили;
  - если workspace = папке, где лежит `mcw\` (без подпапки `workspace\`), cook не видит файлы («Failed to load …
    is this a resource»);
  - `-depot=<путь>` у import и export падает: «Remap is not supported for SplitEditorDepot!» (STATUS_BREAKPOINT).
- **[проверено]** Если cook зовут с `-file=<ванильный путь>`, он тянет зависимости (`box.w2mesh`, `pbr_std.w2mg`,
  `grey.xbm`, `normal.xbm`). С `-mod=<workspace>\` кукаются только файлы мода, ванильные ссылки остаются ссылками.

### Побочные эффекты (5.0.1044630)

- **[проверено]** Пишет в `Documents\<title из gameconf.cfg>` (`CGameConfiguration::GetUserPathSuffix`). На выходе
  он создаёт `editoruser.settings` с `.bak` и переписывает `profile.settings`; на старте читает `mods.settings` и
  `input.settings`. Первые два прогона (05:25, 05:28) так и сделали в `Documents\The Witcher 3`: появились
  `editoruser.settings` и `.bak`, а в `profile.settings` `wasConsentPopupShown` стал `false` (было `true`).
  Оркестратор об этом знает.
- **[проверено]** Решение: в нашей копии `gameconf.cfg` поставлено `title "..\AppData\Local\mcw\redkit_work\userdir"`.
  Путь `Documents\..\AppData\...` резолвится в `<work>\userdir`. После этого Documents больше не менялся: примерно
  70 вызовов wcc, а файлы `Documents\The Witcher 3` с 05:28 никто не трогал. `build_blocks.py` сверяет снимок после
  каждого вызова.
- **[проверено]** В папку REDkit при каждом старте пишет `r4data\x64.release.redscripts` (скрипты, 7,9 МБ). Без
  workspace — ещё `bin\LocalEditorStringDataBaseW3_UTF8_mod.db` (32 КБ, остался от первых прогонов). `<REDkit>\temp\`
  не создаётся.
- **[проверено]** Сеть: `[Net] Listening for incoming connections on port 37000` и попытка открыть сервис-хендлер на
  37010. При запущенной игре 37001 и 37010 заняты `witcher3.exe`, поэтому 37010 у wcc не открывается. Пока wcc
  работает, на 37000 слушает он, а не игра: мост должен ходить строго на 37001.

### Командлеты

Полный список (`wcc_lite help`): analyze, astc, bulkTgaImport, metadatastore, buildcache, splitcache, reportchunks,
compilescripts, cook, validate, dependencies, get_txts, dumpbundleinfo, dumpscripts, exportbundles, export, factsdump,
dumpfile, dumpfilestrings, filever, gluefiles, gluefilesdlc, import, loadtest, cookmaterials, cookocclusion,
calculateRuntimeOcclusionMemory, findDuplicates, optimizecollisioncache, pack, package, patch, pathlib,
questlayoutdump, r4characters, r4charactersdlc, importMergedShadowMesh, resave, resourceusage, cooksounds, split,
splitRemasterPostprocess, srtimport, dumpcharset, cookstrings, splitstrings, swfdump, swfimport, testcollisioncache,
cookertest, testmem, unbundle, uncook, upgradeBundles, verifyseeds, venc, voconvert, cooksubs,
WorldSceneDependencyInfoFiles.

| Команда | Синтаксис, который работает | Заметки |
|---|---|---|
| import | `import -depot=local -file=<abs.png> -out=<abs путь>.xbm -texturegroup=WorldDiffuse` | **[проверено]** xbm из dds, bmp, jpg, tga, png, xbd; w2mesh только из fbx (OBJ нет). Пишет CR2W v164 `CBitmapTexture` + `CSourceTexture`. 40 групп текстур (`help import`), для нас WorldDiffuse / WorldDiffuseWithAlpha |
| cook | `cook -platform=pc -mod=<work>\project\workspace\ -outdir=<work>\cooked\` + workspace-ключи | **[проверено]** cooked-файлы v164 + `*.w2mesh.1.buffer` + `cook.db`. Ключи `-seed`, `-file`, `-noerrors`, `-ignore=<ext>` (на файлы из `-mod` не действует), `-trimdir`, `-excludedir` |
| buildcache | `buildcache textures\|physics -platform=pc -db=<cooked>\cook.db -out=<файл>` + workspace-ключи | **[проверено]** HCXT v7 и CC3W v11; также `shaders`, `-modulo/-offset` |
| pack | `pack -dir=<cooked>\ -outdir=<content>\ [-compression=LZ4HC\|LZ4\|ZLIB]` | **[проверено]** `blob0.bundle` + `buffers0.bundle` (буферы отдельно), v5, `cook.db` в бандл не попадает. В заголовке размер округлён вверх до 16, сам файл не дополнен (у ванили они равны) |
| metadatastore | `metadatastore -path=<content>\ [-out=] [-censorship]` | **[проверено]** VTM v7 |
| export | `export -depot=local -file=<депо-путь> -out=<abs> [-fbx=2016…]` | `-depot=<путь>` падает, как у import |
| uncook | `uncook -indir=<бандлы> -outdir=<dir> [-targetfile= -uncookext=w2mesh,xbm -imgfmt=png …]` | развернуть ванильный меш или текстуру |
| dumpfile | `dumpfile -file=<путь> -out=<файл>` | с депо-путём файла не нашёл (`CreateFile failed`), **[проверить]** с абсолютным |

## Запись CR2W (tools\cr2w.py, CR2WDoc)

- **[проверено]** Правила, сверенные на ванили:
  - строки — уникальные, в исходном порядке;
  - хэш имени — FNV-1a32 по байтам **вместе с NUL**, у пустого 0. У одного из 711 uncooked-файлов (польская реплика
    в `.w2scene`) хэш посчитан по не-ASCII исходнику, поэтому неизменённые имена сохраняют свой хэш;
  - crc таблиц и экспортов — crc32 их байтов; crc заголовка — crc32 первых 0xA0 байт с полем crc = 0xDEADBEEF;
  - данные экспортов идут подряд (`fileSize`), за ними внутренние буферы (`bufferSize`) и хвост-миниатюра;
  - у cooked-файлов буферы внешние (`.1.buffer`, offset 0, `bufferSize == fileSize`);
  - если данные экспорта не менялись, сохраняется исходный crc (у `box.w2mesh` он не совпадает с данными).
- **[проверено]** Запись свойства: `{u16 name, u16 type, u32 size (вместе с полем size), value}`, данные экспорта
  начинаются с 0 и кончаются `u16 0`. Параметры `CMaterialInstance` идут после свойств: `u32 count`, затем
  `{u32 size (вместе с шапкой), u16 name, u16 type, value}`. Handle: −(i+1) — импорт, +(i+1) — экспорт. `String`:
  `0x80 | длина` и ASCII. `EngineTransform`: байт флагов (1 pos, 2 rot, 4 scale) и float'ы, `00` = identity.
  `EDrawableFlags`: u16-имена с завершающим 0.
- **[проверено]** Сырая вершина uncooked `.w2mesh` (MVT_StaticMesh) — 152 байта: pos 3f @0, кости 4B @12, веса 4f
  @16, normal 3f @32, цвет @44, uv0 2f @48, uv1 @56, tangent @64, binormal @76. Вершины лежат дважды: в
  `rawVertices` и в `chunksBuffer` (после заголовка чанка). У `box.w2mesh` 24 вершины, 36 индексов u16, UV граней
  0.339…0.661.
- Тесты: `python -m unittest test_cr2w_write test_build_blocks -v` (из `tools`). Ванильные файлы читаются в память
  из бандлов и `r4data`.

## Конвейер блоков: `tools\build_blocks.py`

На каждый блок `<b>` (депо-пути в нижнем регистре):

1. `mcw\blocks\<b>\<b>.w2mi` — из `environment\textures_tileable\common_materials\novigrad_poor\floor_brick.w2mi`
   (v164): `pbr_std`, один параметр `Diffuse` → `<b>.xbm`. Для cutout-блоков `mc_textures` (листва, стекло)
   добавляется `enableMask=true`, текстура — WorldDiffuseWithAlpha.
2. `mcw\blocks\<b>\<b>.w2mesh` — из `engine\meshes\editor\box.w2mesh` (v162). Куб 1 м, pivot в центре, коллизия
   box 0,5 из шаблона с physmat по типу. UV 24 вершин в обоих буферах переложены в атлас 4x2 из
   `cube_mesh.face_quads("atlas")`: грань — по нормали, угол — по позиции. Материал — локальный
   `CMaterialInstance` с base = `<b>.w2mi`; у cutout-блоков у него тоже `enableMask=true` (с 2026-10-03, сборка
   `out_alpha`).
3. `mcw\blocks\<b>.w2ent` — из `quests\sidequests\skellige\quest_files\sq204_forest_spirit\entities\box_05_05_1m.w2ent`
   (v159): один нестримящийся `CStaticMeshComponent`. В entityObject и во flatCompiledData правятся mesh,
   `DF_IsVisible|DF_CastShadows`, transform = identity, bbox ±0,5, `isStreamed=0`, `name=mcw_<b>`, GUID = uuid5.
   После кука структура та же, что у ванильного cooked `box_05_05_1m.w2ent`.
4. import → cook → buildcache textures/physics → pack → metadatastore → verify.

Почему не FBX-импорт: он работает (`CFBXMeshImporter`, FBX ASCII даёт `cube_mesh.py`), но коллизию и материал после
него всё равно пришлось бы дописывать в CR2W. В шаблоне `box.w2mesh` коллизия уже есть, и `buildcache physics`
кладёт её в `collision.cache` (box 0,5, physmat наш — проверено `collcache.py`). FBX остаётся запасным путём для
не-кубов.

### Альфа-тест (листва, стекло)

**[проверено в игре]** В сборке `out\` (enableMask только в `<b>.w2mi`) стекло — сплошной светло-голубой куб, листва —
зелёный куб без дыр. Под прозрачными пикселями атласа `mc_textures` заливает RGB цветом соседей, этот цвет и виден.

Причина **[проверено офлайн]**: альфа-тест включается флагом материала, который висит на чанке меша, а у нас это
локальный `CMaterialInstance` в `.w2mesh` без `enableMask`. Флаг базового w2mi он не наследует.
- `CMaterialInstance::IsMasked` (`editor.exe` 0x142915600, адрес из `editor.map`, разобран по байтам) —
  `def = GetMaterialDefinition(); return def && def->IsMasked() && (!def->CanOverrideMasked() || this->m_enableMask)`,
  где `m_enableMask` — байт по смещению 0xA4 *своего* объекта. До `IsMasked` базового экземпляра код не доходит.
  `CMaterialGraph::IsMasked` возвращает поле графа.
- У `pbr_std.w2mg` (r4data и cooked в `startup.bundle`) `isMasked=1`, `canOverrideMasked=1`. Поэтому на pbr_std
  маска = `enableMask` экземпляра. Вход `Mask` выходного блока `CMaterialBlockOutputColorDeferred` берётся из Alpha
  сэмплера `Diffuse` — нужна альфа именно в диффузе.
- Ваниль (бандлы 5.00c, скан в памяти): 61 cooked w2mi на pbr_std с `enableMask`. Меши ссылаются на masked w2mi
  двумя способами: 502 раза — прямо из `CMesh.materials`, 138 раз — через локальный экземпляр, и у 97 из этих 138
  `enableMask=1` повторён в локальном. Локальных экземпляров с `enableMask=1` всего 5847 (2573 из них — прямо на
  `pbr_std.w2mg`).
- Текстура в порядке: импорт RGBA PNG с `-texturegroup=WorldDiffuseWithAlpha` даёт `TCM_DXTAlpha` (DXT5). Альфа
  0/255 в мипе 0 совпадает с PNG попиксельно (у стекла 391 168 / 133 120 пикселей, у листвы 172 032 / 352 256). В
  `texture.cache` у glass и oak_leaves формат 8 (DXT5), у остальных 7 (DXT1) — так же, как у ванильного
  `village_thatched_roof_old_alpha.xbm` и других диффузов. `mc_textures.py` альфу не теряет.

Правка: `make_mesh(..., masked=True)` ставит `enableMask=true` и в локальный материал (порядок свойств как в ванили:
`baseMaterial`, `enableMask`). `verify` теперь падает, если у cutout-блока нет `enableMask` в w2mi или в материале
меша, если у xbm не `TCM_DXTAlpha` или если в `texture.cache` формат не DXT5 (у opaque — не DXT1). На старом `out\` он
выдаёт ровно две проблемы: `glass.w2mesh` и `oak_leaves.w2mesh: у CMaterialInstance нет enableMask=true`.

**[проверить]** в игре на `out_alpha`: дыры у листвы, прозрачные пиксели стекла (видна только рамка). Чего ждать
дальше:
- Куб односторонний. Через дыру в передней грани видно не изнанку задней грани, а то, что за блоком. У MC (fancy
  leaves) внутренние грани листвы видны. Если надо так же — `isTwoSided` у меша, отдельная правка.
- Мипы. Если считать box-фильтром (как wcc строит мипы, не проверяли), доля пикселей с альфой ≥ 0,5 на грани
  такая: стекло — 0,25 на мипах 0–4, 0,12 на мипе 6, 0 дальше; листва — 0,67, а с мипа 6 — 1,0. То есть когда грань
  на экране мельче ~4 px, стекло исчезает, а листва становится сплошной. Порог альфа-теста движка не выясняли.
- Полупрозрачность (цветное стекло, лёд, вода) на pbr_std не сделать. Сейчас для них тоже альфа-тест.

### Цвета (светлее / желтее, чем в MC)

**[проверено офлайн]** Несовпадения sRGB/linear в нашем конвейере нет:
- PNG → `CSourceTexture` → DXT в мипе 0 — те же значения (oak_log (116, 90, 54) → DXT (115, 89, 58)). Порядок
  каналов RGBA, импорт ничего не конвертирует.
- cooked xbm устроен так же, как ванильный диффуз (`floor_brick_edge.xbm`): `textureGroup` WorldDiffuse,
  `TCM_DXTNoAlpha`, тот же набор свойств, формат в кэше 7/4. Флага sRGB нет ни в xbm, ни в `texturegroups.xml` (там
  у WorldDiffuse только compression и maxsize). Значит, движок читает наш диффуз так же, как ванильный, в гамма-
  пространстве.
- Граф pbr_std: Diffuse → `customColoring(VarianceOffset, albedo, VarianceColor)` → `wetnessColor` → выход. Умолчание
  `CMaterialParameterScalar.scalar` — 1,0: из 482 скаляров в графах `r4data\engine\materials\graphs` 148 без
  значения, 0,0 записан явно 124 раза, а 1,0 — ни разу. При `VarianceOffset = 1` customColoring возвращает albedo
  как есть. Мокрая поверхность только темнее (×0,6).
- Шероховатость и блеск берутся из альфы Normal. У нас Normal — по умолчанию (`engine\textures\editor\normal.xbm`,
  4×4, альфа 127), отсюда roughness ≈ 0,5 и specular = SpecularColor 35 × 0,5. Это средний диэлектрик, ничего
  экстремального, но без своей нормали блок слегка блестит, а MC вообще без бликов.

Вывод: сдвиг цвета — от освещения и тонмаппинга игры (смотрели ночью), не от текстур. **[проверить]** днём. Если
днём всё ещё светло, следующий шаг — свой Normal: плоский `(128, 128, 255)` с альфой 255 (матовый, без блика)
через параметр `Normal` w2mi. Отдельный импорт и одна строка в `make_material`.

### Что собрано (2026-10-03)

| Вариант | Где | Блоки | Бандлы | Кэши |
|---|---|---|---|---|
| основной | `<work>\out\modMCWBlocks\content\` | 11: grass_block, dirt, stone, cobblestone, oak_log, oak_planks, sand, crafting_table, oak_leaves, glass (masked), _unknown; атласы `mc_textures` 1024×512 | `blob0.bundle` 53 758 Б (44 записи), `buffers0.bundle` 6540 Б (11), LZ4HC | `texture.cache` 66 725 Б (11), `collision.cache` 1696 Б (11), `metadata.store` 6686 Б |
| тестовый | `<work>\test\out\modMCWBlocks\content\` | 9 (без листвы и стекла); у каждой грани свой цвет и тёмный квадрат в левом верхнем углу картинки | ZLIB | так же |

Проверено (`build_blocks.py verify`, тест `BuiltMod`): бандлы v5 читаются, crc всех записей сходится, извлечённое
побайтово равно `cooked\`. Все CR2W — v164, ссылки ведут в мод или в бандлы игры (`pbr_std.w2mg`). В texture.cache
все 11 xbm (1024×512, `residentMipIndex` 5), в collision.cache все 11 мешей с box 0,5 и нужным physmat. Наш
`w3bundle.py` распаковывает LZ4HC без ошибок: на реальных данных эта ветка проверена впервые.

### Как проверить в игре (делает оркестратор)

1. Скопировать `<work>\out\modMCWBlocks` в `<игра>\mods\modMCWBlocks` (внутри только `content\` с пятью файлами).
   Для ориентации граней — тестовый вариант из `<work>\test\out\`.
2. Спавн тем же способом, что ящик в спайке: `spawnPath('mcw\blocks\stone.w2ent')` или шаблон в `mcw_cube` вместо
   `cont_crate`.
3. Смотреть **[проверить]**:
   - куб 1 м виден с тенью, pivot в центре;
   - у травы верх сверху, у бревна торцы сверху и снизу;
   - на тестовом варианте у всех граней тёмный квадрат в левом верхнем углу, если смотреть снаружи; иначе V
     перевёрнут и правится `face_uv_for`;
   - Геральт стоит на блоке, коллизия 1 м;
   - у листвы и стекла видны дыры (enableMask) — только на `out_alpha` и новее, в `out\` их нет;
   - звук шагов: stone_solid → камень.

   Пункты про тень, ориентацию граней, коллизию и LZ4HC на `out\` уже проверены в игре (раздел «Коротко»).
4. Если игра не читает LZ4HC-бандлы: `python build_blocks.py pack --allow-wcc --compression ZLIB`, затем `verify` (~30 с, после разрешения на wcc).

## Запуски wcc_lite: история, разрешение, порядок

**Статус.** Можно ли запускать wcc_lite, решает пользователь, потому что wcc умеет писать в Documents. До его ответа
`build_blocks.py` wcc не зовёт: без `--allow-wcc` каждый шаг с wcc сразу выходит. `prepare` и `verify` wcc не
используют.

**История (2026-10-03).** Сначала запуски, потом ответ лида, поэтому так по порядку:
- 05:25 — `wcc_lite` без аргументов: окно EULA, через 90 с убит.
- 05:28 — `help -agreetoterms` с cwd в scratchpad (нет `gameconf.cfg`, сразу выход), затем с cwd в
  `<work>\wccroot`. Этот запуск записал в `Documents\The Witcher 3`: создал `editoruser.settings` и `.bak`, а в
  `profile.settings` сменил `wasConsentPopupShown=true` на `false`.
- 05:48 — проба с `title "..\AppData\Local\mcw\redkit_work\userdir"`: wcc стартовал, по `wcc.log` видно, что его
  пользовательская папка — `<work>\userdir`, и процесс убит до выхода (настройки он пишет на выходе). Снимок
  `Documents\The Witcher 3` (имя, размер, mtime) до и после совпал.
- 05:49–06:15 — около 70 вызовов wcc с этой копией `gameconf.cfg`: разведка ключей, import, cook, buildcache, pack,
  metadatastore, три полных сборки. Ответ лида «не запускать до решения пользователя» пришёл уже после них.
- Состояние Documents после всех вызовов **[проверено]**. Сам каталог `Documents\The Witcher 3` и все файлы в нём не
  менялись с 05:28:58 (последняя запись — тот самый прогон до подмены title). В сейвах и вне этой папки изменений
  нет. Хеши на конец разведки (SHA-256, первые 16 символов):

  | Файл | Размер | mtime | sha256 |
  |---|---|---|---|
  | dx12user.settings, .bak | 6935 | 05:19:47 (игра) | DB9AE6765F1BAE16 |
  | input.settings | 110 989 | 05:19:47 (игра) | 116AE23507437B04 |
  | editoruser.settings, .bak | 6352 | 05:28:58 (wcc, до подмены title) | C72E8AEB49A5C4F7 |
  | profile.settings | 130 | 05:28:58 (wcc, до подмены title) | 71F40466A367601B |
  | profile.settings.bak | 129 | 05:19:47 (игра) | 0C4266BDC4015A5C |
  | steam_autocloud.vdf | 52 | 05:15:36 | D71243CDA08A0BF6 |
  | scriptslog.txt | — | пишет игра, файл занят | — |

  Хешей «до» у первых запусков нет. Для них доказательство — mtime с точностью до миллисекунд и mtime самого
  каталога (появление или удаление файла его меняет).
- 16:06 — сборка `out_alpha` (`all --allow-wcc --out ...`) остановлена обвязкой после первого `import_grass_block`:
  изменился `Documents\The Witcher 3\scriptslog.txt` (89 932 Б, mtime 16:06:36). Больше в Documents не изменилось
  ничего. По логу вызова пользовательская папка wcc — `<work>\userdir\` (`Save directory set to`), свои settings он
  записал туда, scriptslog.txt там не появился. В это время шла игра (witcher3.exe), а scriptslog.txt пишет она.
  Подтверждение: в 16:09:50 файл изменился снова (89 932 → 90 157 Б), когда wcc не работал (оркестратор гонял
  `exec` в консоли игры). `out\` не тронут: шаги caches/pack до него не дошли. Дальше — исключение из п. 3 ниже,
  пересборка с ним.

**Что wcc создал или изменил вне `<work>` (всё):**
- `Documents\The Witcher 3\editoruser.settings`, `editoruser.settings.bak` — созданы в 05:28;
  `profile.settings` — `wasConsentPopupShown` `true` → `false` в 05:28. Не трогал и не откатывал.
- `<REDkit>\r4data\x64.release.redscripts` — перезаписывается при каждом старте wcc.
- `<REDkit>\bin\LocalEditorStringDataBaseW3_UTF8_mod.db` (32 КБ) — создан в 05:28. С workspace этот файл ложится в
  `<work>\project\`.
- `<REDkit>\bin\mcw\blocks\stone\stone.xbm` — создан в 05:51 (import без workspace). Перенесён в `<work>`, папка
  `bin\mcw` удалена.
- Реестр `HKCU\Software\CD Projekt RED\Mod Tools\1.0` не проверял. Окно EULA убито без нажатия кнопок, дальше везде
  стоял `-agreetoterms`.
- Временно, пока wcc работает: слушает TCP 37000, пробует 37010.

**Порядок команд после разрешения** (так их зовёт `python build_blocks.py all --allow-wcc`). Везде cwd =
`<work>\wccroot\bin\x64_RedKit`, рядом `..\gameconf.cfg` с `title`, ведущим в `<work>\userdir`, и в конце
`-agreetoterms`. `W` = `<work>`, `WS` = `-workspaceDir W\project\ -uncookDir W\uncook\`:
```
wcc_lite import -depot=local -file=W\src\<b>.png -out=W\project\workspace\mcw\blocks\<b>\<b>.xbm -texturegroup=WorldDiffuse   (на каждый блок; у масочных WorldDiffuseWithAlpha)
wcc_lite cook -platform=pc -mod=W\project\workspace\ -outdir=W\cooked\ WS
wcc_lite buildcache textures -platform=pc -db=W\cooked\cook.db -out=W\out\modMCWBlocks\content\texture.cache WS
wcc_lite buildcache physics  -platform=pc -db=W\cooked\cook.db -out=W\out\modMCWBlocks\content\collision.cache WS
wcc_lite pack -dir=W\cooked\ -outdir=W\out\modMCWBlocks\content\ -compression=LZ4HC
wcc_lite metadatastore -path=W\out\modMCWBlocks\content\
```

**Проверка Documents (план, уже встроен в обвязку):**
1. Перед первым запуском снять базовый снимок: верхний уровень «Документов» (имя и mtime) и каждый файл
   `Documents\The Witcher 3` без `gamesaves` (размер, mtime_ns, sha256). Так делает `docs_snapshot()`; путь к
   «Документам» берётся через `SHGetFolderPath(CSIDL_PERSONAL)`, как у движка.
2. Первым запуском после разрешения — один `wcc --allow-wcc -- help`. Снимки до и после должны совпасть, а в
   `logs\raw_help*.log` строка `Save directory set to` должна указывать на `<work>\userdir`. Обвязка проверяет это сама и
   печатает WARNING.
3. Дальше каждый вызов снимает снимок до и после. Если «Документы» изменились, обвязка печатает изменённые пути и
   останавливает конвейер (`sys.exit`). Изменения REDkit вне двух известных файлов — WARNING. Исключение одно
   (решение оркестратора, 2026-10-03): `Documents\The Witcher 3\scriptslog.txt` не сравнивается, если
   `witcher3.exe` запущен и при снимке до, и при снимке после (`tasklist`). Если игры нет хотя бы при одном снимке,
   файл сравнивается как все. Изменение тогда печатается как `note`.
4. После сборки сравнить итоговые хеши с таблицей выше. Ожидание: совпадают все файлы, кроме тех, что пишет сама игра
   (`scriptslog.txt`, `*.settings`, если она перезапускалась).

## Что требует GUI

Для конвейера блоков — ничего: ни один шаг не открывает окно (EULA снимается `-agreetoterms`). Глазами в редакторе
можно разве что посмотреть превью меша или материала, но в игре это проверяется так же. GUI понадобится только там,
где у wcc нет командлета или нет шаблона:
- новый граф материала (полупрозрачность воды и цветного стекла: в `r4data` есть `transparent_lit.w2mg`,
  `transparent_reflective.w2mg`, но параметры их экземпляров не разбирали);
- свои звуки (Wwise-проект `assets\w3_audio`);
- сцены и квесты.

Если всё же понадобится, шаги для автоматизации: `editor.exe` (по README Steam — `-forceshadercacheusage`) → открыть
или создать проект `*.w3edit` → Asset Browser → импорт → правка свойств → Cook / Pack из меню мода. Конкретные
пункты меню на этой сборке не проверяли **[проверить]**.

## Совместимость с игрой 5.00c

- **[проверено]** Один P4CL 13360103. Форматы на выходе совпадают с ванилью: bundle v5 (запись 0x130), CR2W v164,
  VTM v7, HCXT v7, CC3W v11.
- **[проверено]** REDkit кукает ванильные исходники побайтово так же, как в бандлах 5.00c (5 файлов из 6). В шестом,
  `box.w2mesh`, отличается только `boundingBox.Min.W` (мусорный float), буфер совпадает.
- **[проверено в игре]** Сжатие LZ4HC (умолчание `wcc pack`; в бандлах игры только zlib и none) игра 5.00c читает:
  сборка `out\` грузится. Запасной вариант `--compression ZLIB` собран и проверен офлайн.
- **[проверено в игре]** `texture.cache` и `collision.cache` из `mods\<мод>\content\` игра поднимает: текстуры MC
  видны, коллизия работает.

## Открытые вопросы

- **[проверено в игре]** Направление V у сырых вершин — вниз (DirectX, как в картинке): у травы зелёный верх сверху
  и бахрома вверху боков, инструменты верстака стоят прямо.
- **[проверить]** Касательные шаблона после смены UV не пересчитываются. Пока Normal плоский, это не важно.
- **[проверить]** physmat для травы, земли и песка: пока стоит `wood_solid`.
- **[проверить]** Для translucent-блоков сейчас тоже ставится альфа-тест.
- **[проверить]** Альфа-тест на `out_alpha` (раздел «Альфа-тест»): дыры у листвы, стекло — одна рамка; дальние мипы;
  нужен ли `isTwoSided` для листвы.
- **[проверить]** Цвета днём (раздел «Цвета»): офлайн несовпадения гаммы нет.
