// =====================================================================================================
// modMCW — базовый спайк-мод «Minecraft внутри The Witcher 3». Не компилировался: игру не запускали.
// Цель: The Witcher 3 5.0.0.1044392 (5.00c), запуск с -net -debugscripts. Только глобальные функции:
// без аннотаций и без map, чтобы компиляция прошла наверняка (рискованное — в modMCWProbe).
// Установка: <игра>\mods\modMCW\content\scripts\local\mcw_spike.ws, UTF-8 без BOM.
// Проверка до игры: python spike\ws_lint.py <этот файл>.
//
// Ссылки V\<файл>:<строка> — ванильные скрипты 5.00c (<игра>\content\content0\scripts); exe@0x.. — строки
// компилятора в bin\x64_dx12\witcher3.exe.
//
// КОНТРАКТ (его же эмулирует фейковая игра в треке net). Каждая строка вывода — Log("[MCW#<seq>] ..."),
// последняя строка любой mcw-функции — "[MCW#<seq>] end". Числа целые: координаты в см, углы в сотых
// градуса, направления x10000, время в мс.
//   mcw_ping(seq : int)                          pong <engineMs>
//   mcw_echo(seq : int, s : string)              echo <len> <s>
//   mcw_logtest(seq : int)                       Log "via Log" + LogChannel('MCW', "[MCW#seq] via LogChannel")
//   mcw_poll(seq : int)                          pos <x> <y> <z> <yaw> | cam <x> <y> <z> <dx> <dy> <dz> |
//                                                area <name> | world <depotPath>
//   mcw_cube(seq : int, path : string)           spawned <ok> comps <n>          ("" = cont_crate, 2 м перед Геральтом)
//   mcw_alias(seq : int, alias : name, scale : float)   spawned <ok> comps <n> scaled <k>
//   mcw_stress(seq : int, n : int, spacing : int, path : string)   stress <spawned> <ms>
//   mcw_clearall(seq : int)                      cleared <n>
//   mcw_count(seq : int)                         count <n>
//   mcw_countblocks(seq : int)                   countblocks <blocks> <tests>   (блоки моста без проб MCW_TEST; пробы)
//   mcw_batch(seq : int, cmds : string)          batch <set> <cleared> <errors> <ms>   (семантика — у функции)
//   mcw_look(seq : int)                          look <hit> <x> <y> <z> <isBlock>
//                                                normal <nx> <ny> <nz>   (только при hit 1; x10000)
//   mcw_ground(seq : int, x : int, y : int)      ground <ok> <z>
//   mcw_grounds(seq : int, cmds : string)        gs <ok>:<z> ... (по 16) | gms <ms>   (пакет mcw_ground, "x:y|...", до 64)
//   mcw_boom(seq : int)                          boom <ok>
//   mcw_boomat(seq : int, x : int, y : int, z : int)   boom <ok>   (бочка в точке x, y, z мировых см)
//   mcw_notify(seq : int, s : string)            notified
//   mcw_ents(seq : int, ops : string)            ents <spawned> <moved> <killed> <errors> <ms>   (сущности моста — мобы)
// Вне мира (главное меню, нет thePlayer/камеры/мира) строки pos, cam, area, world не печатаются, look отвечает
// "look 0 0 0 0 0" (без normal), ground — "ground 0 0"; end — всегда.
// countblocks, строка normal и boomat добавлены для моста (bridge\README.md): старые строки и сигнатуры не менялись,
// мод без них мост узнаёт по "Warn: Failed to process command" и по отсутствию строки normal. mcw_ents (этап 5) —
// так же: только добавлена.
//
// Теги сущностей: 'MCW_BLOCK' — у всех сущностей мода (count, clearall, clear в mcw_batch); 'MCW_TEST' — ещё и у
// одиночных проб (cube, alias, boom). Мобы моста (mcw_ents) — отдельно: 'MCW_ENT' и тег слота 'MCW_E<id>', без
// MCW_BLOCK, поэтому count, countblocks, clearall и clear в mcw_batch их не трогают.
//
// ПРАВИЛА ЯЗЫКА, которым следует файл (подтверждение в ванили 5.0 / exe):
//  - var только в начале тела функции, до первого оператора: ws_lint нашёл 0 нарушений в 18774 ванильных
//    телах; пример V\game\temp.ws:7604-7608 (spawnbarrel); WIDE: invalid-local-var-placement.
//  - литералов массивов нет, массивы собираются PushBack: V\game\gui\menus\photomodeMenu.ws:436-448.
//  - name в одинарных кавычках, string в двойных: V\game\temp.ws:7608 LoadResource("barrel"),
//    photomodeMenu.ws:436 PushBack( 'RigidBody' ). Каста string->name нет (в таблице опкодов exe есть
//    OP_NameToString, нет обратного), name->string — NameToString, V\core\string.ws:66.
//  - exec function только глобальная (exe@0x3628e40), не latent (exe@0x36296b8), из скрипта не вызывается
//    (exe@0x3630760) — вся логика в обычных функциях MCW_*.
//  - глобальных переменных нет: глобальные объекты задаёт движок (bin\redscripts.ini [globals]: theGame,
//    thePlayer, theCamera ...). Состояние между вызовами живёт только в мире (теги сущностей).
//    theCamera — CCameraDirector, не CCamera из ini: ваниль зовёт у него методы, объявленные только в
//    V\engine\cameraDirector.ws (temp.ws:11178 GetTopmostCameraObject — cameraDirector.ws:30).
//  - map/set — ключевые слова 5.0 (таблица ключевых слов лексера около exe@0x3632088 рядом с TOKEN_MAP/TOKEN_SET;
//    exe@0x3630818), как имена не используются; остальные ключевые слова оттуда же — тоже.
//  - строковый аргумент exec из консоли/сети: как экранируются кавычки, '|', ':' — [проверить] на спайке
//    (mcw_echo); поэтому разделители пакета mcw_batch — '|' и ':', без пробелов и кавычек.
//  - числа: float->int только MCW_Round/FloorF/CeilF (V\core\math.ws:166-169), int->float — каст (float)x
//    RoundF (V\core\math.ws:172) не использовать: он отбрасывает дробь (-1.6 -> -1, 1.6 -> 1; спайк 2026-10-03,
//    mcw_roundtest), и целые см с отрицательной координатой не переживают (float)x / 100 * 100.
//    (V\engine\gameTime.ws:79), в строку — IntToString (V\core\string.ws:75). FloatToString не используется:
//    формат дробной части не гарантирован, поэтому всё целое.
//  - поле структуры у результата вызова (GetWorldPosition().X) нельзя (exe@0x36305b0 «Parent scope does not
//    come from variable»; в ванили 0 таких мест) — сначала в переменную; поле поля переменной (box.Max.Z)
//    можно: V\game\gui\menus\photomodeMenu.ws:3305.
// =====================================================================================================


// ----------------------------------------------------------------------------- вывод, числа, строки

// Строка ответа exec. Log( text : string ) — V\core\misc.ws:6; IntToString( value : int ) : string — V\core\string.ws:75.
function MCW_Line( seq : int, text : string )
{
	Log( "[MCW#" + IntToString( seq ) + "] " + text );
}

function MCW_End( seq : int )
{
	Log( "[MCW#" + IntToString( seq ) + "] end" );
}

// Округление к ближайшему, половина вверх. FloorF( a : float ) : int — V\core\math.ws:169. mcw_roundtest: на всех
// целых см из [-20000, 20000] после (float)x / 100 * 100 даёт x обратно (RoundF ошибается в 2286 случаях).
function MCW_Round( v : float ) : int
{
	return FloorF( v + 0.5 );
}

// float * k -> целое строкой.
function MCW_Scaled( v : float, k : float ) : string
{
	return IntToString( MCW_Round( v * k ) );
}

// метры -> сантиметры, градусы -> сотые
function MCW_X100( v : float ) : string
{
	return MCW_Scaled( v, 100.0 );
}

// Часы реального времени для замеров внутри одного кадра. GetLocalTimeAsMilliseconds() : int —
// V\engine\game.ws:79, единственный вызов в ванили — V\game\gui\main_menu\ingameMenu.ws:1876 (интервал в меню).
// [проверить] на спайке, что значение меняется внутри кадра; если нет, все <ms> будут 0.
function MCW_Ms() : int
{
	return theGame.GetLocalTimeAsMilliseconds();
}

// Время движка в мс. GetEngineTimeAsSeconds() : Float — V\engine\game.ws:58.
function MCW_EngineMs() : int
{
	return MCW_Round( theGame.GetEngineTimeAsSeconds() * 1000.0 );
}

// Строка -> int ровно для записи -?[0-9]+ (как _INT_RE фейка трека net): "", "-", "+5", " 5", "1.5", "12abc",
// "--5" — не число, как бы StringToInt ни разбирал хвост. Быстрый путь — каноническая запись (так шлёт мост,
// str(int)): число обратно в строку даёт ту же строку. Иначе снимаем один '-' и ведущие нули и проверяем
// каноничность остатка ("007" -> 7, "-0" -> 0). Больше int32 — не число (фейк на Python такое примет; мост таких
// координат не шлёт). StringToInt( value : string, optional defValue : int ) : int — V\core\string.ws:78;
// StrBeginsWith( str, match : string ) : bool — :51; StrRight( str, StrLen( str ) - 1 ) — как
// V\game\localizedContent.ws:377; срезание по одному символу в цикле — как NoTrailZeros, V\core\string.ws:120-121;
// унарный минус у int — V\game\components\inventoryComponent.ws:188.
function MCW_ParseInt( s : string, out v : int ) : bool
{
	var t : string;
	var neg : bool;

	v = StringToInt( s, 0 );
	if( IntToString( v ) == s )
		return true;
	t = s;
	neg = StrBeginsWith( t, "-" );
	if( neg )
		t = StrRight( t, StrLen( t ) - 1 );
	while( StrLen( t ) > 1 && StrBeginsWith( t, "0" ) )
		t = StrRight( t, StrLen( t ) - 1 );
	if( StrBeginsWith( t, "-" ) )
		return false;
	v = StringToInt( t, 0 );
	if( IntToString( v ) != t )
		return false;
	if( neg )
		v = -v;
	return true;
}

// Ключ позиции в целых см — по нему clear в mcw_batch находит блок.
function MCW_Key( x : int, y : int, z : int ) : string
{
	return IntToString( x ) + ":" + IntToString( y ) + ":" + IntToString( z );
}


// ----------------------------------------------------------------------------- шаблоны и спавн

// Основной куб: голый CEntity с одним CStaticMeshComponent (ящик ~1.03 м, pivot в центре, коллизия wood_solid).
// Обратные слэши в строковом литерале ваниль пишет как есть, без экранирования: V\game\gameplay\items\spells\
// signEntity.ws:708 ("gameplay\templates\signs\pc_igni_proj_cone.w2ent" содержит \t и всё равно работает).
function MCW_CratePath() : string
{
	return "gameplay\containers\_container_definitions\_mesh_entities\cont_crate.w2ent";
}

// Шаблон по депо-пути ("" или "-" = cont_crate; пустую строку exec из сети не разбирает — спайк 2026-10-03, шлём "-").
// LoadResource( resource : string, optional isDepotPath : bool ) : CResource — V\core\resource.ws:6;
// путь с isDepotPath=true и каст (CEntityTemplate) — как V\game\gameplay\items\spells\signEntity.ws:708.
function MCW_Template( path : string ) : CEntityTemplate
{
	var p : string;

	p = path;
	if( p == "" || p == "-" )
		p = MCW_CratePath();
	return (CEntityTemplate)LoadResource( p, true );
}

// Индекс типа блока из mcw_batch: 0 cont_crate, 1 barrel. Алиас без isDepotPath — V\game\temp.ws:7608 (spawnbarrel);
// алиас barrel -> gameplay\interactive_objects\oil_barrel\oil_barrel.w2ent (gameplay.xml в xml.bundle).
// 2..12 — блоки modMCWBlocks (tools\build_blocks.py, MVP по порядку): mcw\blocks\<имя>.w2ent. Пути целиком: как
// лексер понимает \" в конце литерала, не проверяли. Спайк 2026-10-03:
// mcw_cube(#, "mcw\blocks\stone.w2ent") показал куб с текстурой, бандлы LZ4HC игра читает.
function MCW_BlockPath( t : int ) : string
{
	switch( t )
	{
		case 2: return "mcw\blocks\grass_block.w2ent";
		case 3: return "mcw\blocks\dirt.w2ent";
		case 4: return "mcw\blocks\stone.w2ent";
		case 5: return "mcw\blocks\cobblestone.w2ent";
		case 6: return "mcw\blocks\oak_log.w2ent";
		case 7: return "mcw\blocks\oak_planks.w2ent";
		case 8: return "mcw\blocks\sand.w2ent";
		case 9: return "mcw\blocks\crafting_table.w2ent";
		case 10: return "mcw\blocks\oak_leaves.w2ent";
		case 11: return "mcw\blocks\glass.w2ent";
		case 12: return "mcw\blocks\_unknown.w2ent";
	}
	return "";
}

function MCW_TemplateByIndex( t : int ) : CEntityTemplate
{
	var b : string;

	if( t == 0 )
		return (CEntityTemplate)LoadResource( MCW_CratePath(), true );
	if( t == 1 )
		return (CEntityTemplate)LoadResource( "barrel" );
	b = MCW_BlockPath( t );
	if( b != "" )
		return (CEntityTemplate)LoadResource( b, true );
	return NULL;
}

// Число индексов шаблонов (0 cont_crate, 1 barrel, 2..12 блоки).
function MCW_TemplateCount() : int
{
	return 13;
}

// Создание блоков (MCW_Place и MCW_Spawn ниже). Теги — массив через PushBack.
// CreateEntity( entityTemplate : CEntityTemplate, pos : Vector, optional rot : EulerAngles,
//   optional useAppearancesFromIncludes : bool, optional forceBehaviorPose : bool, optional doNotAdjustPlacement : bool,
//   optional persistanceMode : EPersistanceMode, optional tagList : array<name> ) : CEntity — V\engine\game.ws:129-131.
// Все 8 аргументов: полная форма как V\game\temp.ws:6921 (..., PM_DontPersist, horseTag);
// doNotAdjustPlacement=true как V\game\npc\npc.ws:4766; EulerAngles( 0, 0, 0 ) как V\engine\entity.ws:141.
// PM_DontPersist — нативный enum (в .ws не объявлен, строка в exe), в ванили 13 вызовов.
// Появление блока. Новая сущность в кадре растворяется ~0,5 с (движок, замер 2026-10-03), а созданная заранее скрытой и
// показанная SetHideInGame( false, true ) — видна сразу, и после телепорта тоже (V\engine\entity.ws:241, проба
// mcw_fadetest). Поэтому блоки modMCWBlocks берутся из пула: по MCW_PoolPer() скрытых сущностей каждого типа ждут на
// «парковке» под миром (теги 'MCW_Q<t>' + 'MCW_P<t>'); set забирает одну (телепорт, теги, показ), clear возвращает
// (спрятать, на парковку) или удаляет, если пул полон; mcw_poll досоздаёт по одной. У живого блока теги 'MCW_BLOCK' +
// 'MCW_P<t>' — по второму clear знает тип. cont_crate и бочка (0, 1) — без пула, как раньше. Имена тегов из числа —
// только switch по литералам, как MCW_EntTag.
function MCW_PoolTag( t : int ) : name
{
	switch( t )
	{
		case 2: return 'MCW_Q2';
		case 3: return 'MCW_Q3';
		case 4: return 'MCW_Q4';
		case 5: return 'MCW_Q5';
		case 6: return 'MCW_Q6';
		case 7: return 'MCW_Q7';
		case 8: return 'MCW_Q8';
		case 9: return 'MCW_Q9';
		case 10: return 'MCW_Q10';
		case 11: return 'MCW_Q11';
		case 12: return 'MCW_Q12';
	}
	return '';
}

function MCW_TypeTag( t : int ) : name
{
	switch( t )
	{
		case 2: return 'MCW_P2';
		case 3: return 'MCW_P3';
		case 4: return 'MCW_P4';
		case 5: return 'MCW_P5';
		case 6: return 'MCW_P6';
		case 7: return 'MCW_P7';
		case 8: return 'MCW_P8';
		case 9: return 'MCW_P9';
		case 10: return 'MCW_P10';
		case 11: return 'MCW_P11';
		case 12: return 'MCW_P12';
	}
	return '';
}

function MCW_PoolPer() : int
{
	return 4;
}

// Парковка — под Геральтом на 800 м: ниже луча mcw_ground (до -300 м) и далеко от всего живого.
function MCW_Parking() : Vector
{
	if( thePlayer )
		return thePlayer.GetWorldPosition() - Vector( 0, 0, 800.0 );
	return Vector( 0, 0, -1000.0, 1.0 );
}

// Блок типа t в pos: из пула (сразу видно) или новой сущностью (растворится).
function MCW_Place( t : int, tpl : CEntityTemplate, pos : Vector ) : CEntity
{
	var tags : array<name>;
	var pool : array<CEntity>;
	var ent : CEntity;
	var q : name;

	q = MCW_PoolTag( t );
	if( IsNameValid( q ) )
	{
		pool = MCW_Tagged( q );
		if( pool.Size() > 0 && pool[0] )
		{
			ent = pool[0];
			ent.RemoveTag( q );
			ent.AddTag( 'MCW_BLOCK' );
			ent.TeleportWithRotation( pos, EulerAngles( 0, 0, 0 ) );
			ent.SetHideInGame( false, true );
			return ent;
		}
	}
	if( !tpl )
		return NULL;
	tags.PushBack( 'MCW_BLOCK' );
	if( IsNameValid( q ) )
		tags.PushBack( MCW_TypeTag( t ) );
	return theGame.CreateEntity( tpl, pos, EulerAngles( 0, 0, 0 ), false, false, true, PM_DontPersist, tags );
}

// Убрать блок: в пул его типа, если там меньше MCW_PoolPer(), иначе Destroy.
function MCW_Release( ent : CEntity )
{
	var t : int;
	var q : name;
	var pool : array<CEntity>;

	for( t = 2; t <= 12; t += 1 )
	{
		if( ent.HasTag( MCW_TypeTag( t ) ) )
		{
			q = MCW_PoolTag( t );
			pool = MCW_Tagged( q );             // .Size() у результата функции нельзя: «Only arrays coming from variables»
			if( pool.Size() < MCW_PoolPer() )
			{
				ent.SetHideInGame( true, true );
				ent.RemoveTag( 'MCW_BLOCK' );
				ent.AddTag( q );
				ent.TeleportWithRotation( MCW_Parking(), EulerAngles( 0, 0, 0 ) );
				return;
			}
			break;
		}
	}
	// Destroy() растворяет сущность ~0,5 с (сломанный блок «медленно исчезал»), скрытая пропадает в этом же кадре —
	// так убирает реквизит фоторежим (V\game\gui\menus\photomodeMenu.ws:3492-3493)
	ent.SetHideInGame( true, true );
	ent.Destroy();
}

// Досоздать одну скрытую сущность типу, у которого в пуле меньше всего (из mcw_poll). -> создано ли.
function MCW_PoolRefill() : bool
{
	var t, best, n, bestN : int;
	var tags : array<name>;
	var pool : array<CEntity>;
	var ent : CEntity;
	var tpl : CEntityTemplate;

	best = -1;
	bestN = MCW_PoolPer();
	for( t = 2; t <= 12; t += 1 )
	{
		pool = MCW_Tagged( MCW_PoolTag( t ) );
		n = pool.Size();
		if( n < bestN )
		{
			best = t;
			bestN = n;
		}
	}
	if( best < 0 )
		return false;
	tpl = MCW_TemplateByIndex( best );
	if( !tpl )
		return false;
	tags.PushBack( MCW_PoolTag( best ) );
	tags.PushBack( MCW_TypeTag( best ) );
	ent = theGame.CreateEntity( tpl, MCW_Parking(), EulerAngles( 0, 0, 0 ), false, false, true, PM_DontPersist, tags );
	if( !ent )
		return false;
	ent.SetHideInGame( true, true );
	return true;
}

// Спавн блока с тегом 'MCW_BLOCK' (+ 'MCW_TEST' у пробных одиночных) — пробы (cube, stress, boom); блоки моста —
// MCW_Place. Теги — массив через PushBack.
function MCW_Spawn( tpl : CEntityTemplate, pos : Vector, test : bool ) : CEntity
{
	var tags : array<name>;

	if( !tpl )
		return NULL;
	tags.PushBack( 'MCW_BLOCK' );
	if( test )
		tags.PushBack( 'MCW_TEST' );
	return theGame.CreateEntity( tpl, pos, EulerAngles( 0, 0, 0 ), false, false, true, PM_DontPersist, tags );
}

// Точка в dist метрах перед Геральтом. GetWorldPosition() : Vector — V\engine\node.ws:24,
// GetHeadingVector() : Vector — V\engine\node.ws:42; Vector + Vector * float — как V\game\gui\menus\photomodeMenu.ws:3355.
function MCW_Front( dist : float ) : Vector
{
	var p, h : Vector;

	p = thePlayer.GetWorldPosition();
	h = thePlayer.GetHeadingVector();
	return p + h * dist;
}

// Опора под точкой: луч +-5 м как ванильный spawn, V\game\temp.ws:6903. Без мира — точка как есть.
// GetWorld() : CWorld — V\engine\game.ws:155.
// StaticTrace( pointA, pointB : Vector, out position : Vector, out normal : Vector,
//   optional collisionGroupsNames : array<name> ) : bool — V\engine\world.ws:18 (4 аргумента из 5).
function MCW_SnapGround( p : Vector ) : Vector
{
	var hit, n, r : Vector;
	var world : CWorld;

	r = p;
	world = theGame.GetWorld();
	if( world )
	{
		if( world.StaticTrace( p + Vector( 0, 0, 5 ), p - Vector( 0, 0, 5 ), hit, n ) )
			r = hit;
	}
	return r;
}

// То же, но окно луча своё: от up м выше точки до down м ниже. Для mcw_boomat: окно ±5 м MCW_SnapGround поставило бы
// бочку на крышу из блоков над точкой взрыва. Нет мира или опоры — точка как есть.
function MCW_SnapDown( p : Vector, up : float, down : float ) : Vector
{
	var hit, n, r : Vector;
	var world : CWorld;

	r = p;
	world = theGame.GetWorld();
	if( world )
	{
		if( world.StaticTrace( p + Vector( 0, 0, up ), p - Vector( 0, 0, down ), hit, n ) )
			r = hit;
	}
	return r;
}

// Все сущности с тегом. GetEntitiesByTag( tag : name, out entities : array<CEntity> ) — V\engine\game.ws:140. O(N).
function MCW_Tagged( tag : name ) : array<CEntity>
{
	var ents : array<CEntity>;

	theGame.GetEntitiesByTag( tag, ents );
	return ents;
}

// Destroy() — V\engine\entity.ws:34.
function MCW_DestroyAll() : int
{
	var ents : array<CEntity>;
	var i, n : int;

	ents = MCW_Tagged( 'MCW_BLOCK' );
	n = 0;
	for( i = 0; i < ents.Size(); i += 1 )
	{
		if( ents[i] )
		{
			ents[i].Destroy();
			n += 1;
		}
	}
	return n;
}

// Низ блока по мировому AABB: CalcBoundingBox( out box : Box ) — V\engine\entity.ws:89, Box { Min, Max : Vector } —
// V\core\math.ws:24; AABB мировой — его Max.Z сравнивают с мировой Z, V\game\gameplay\interactive\ep1Chandelier.ws:100-104.
// Пустой AABB (меш ещё не застримился) или явно чужой (низ не ниже точки попадания либо ниже неё больше чем на 3 м:
// в AABB попала, например, зона взаимодействия бочки) — по pivot cont_crate: центр - 0.515 м.
function MCW_BlockBottom( ent : CEntity, hitZ : float ) : float
{
	var box : Box;
	var p : Vector;

	ent.CalcBoundingBox( box );
	if( box.Max.Z > box.Min.Z && box.Min.Z < hitZ && box.Min.Z > hitZ - 3.0 )
		return box.Min.Z;
	p = ent.GetWorldPosition();
	return p.Z - 0.515;
}

// Группы коллизий луча — список ванильного фоторежима, V\game\gui\menus\photomodeMenu.ws:436-448
// (в какой группе коллизия cont_crate, неизвестно — [проверить]).
function MCW_RayGroups() : array<name>
{
	var g : array<name>;

	g.PushBack( 'RigidBody' );
	g.PushBack( 'Static' );
	g.PushBack( 'Dynamic' );
	g.PushBack( 'Destructible' );
	g.PushBack( 'Terrain' );
	g.PushBack( 'Phantom' );
	g.PushBack( 'Water' );
	g.PushBack( 'Boat' );
	g.PushBack( 'BoatDocking' );
	g.PushBack( 'Door' );
	g.PushBack( 'Platforms' );
	g.PushBack( 'Fence' );
	g.PushBack( 'Debris' );
	return g;
}


// ----------------------------------------------------------------------------- транспорт

exec function mcw_ping( seq : int )
{
	MCW_Line( seq, "pong " + IntToString( MCW_EngineMs() ) );
	MCW_End( seq );
}

// StrLen( str : string ) : int — V\core\string.ws:6.
exec function mcw_echo( seq : int, s : string )
{
	MCW_Line( seq, "echo " + IntToString( StrLen( s ) ) + " " + s );
	MCW_End( seq );
}

// Что из двух попадает в ответ exec, а что только в scriptslog.txt.
// LogChannel( channel : name, text : string ) — V\core\misc.ws:9.
exec function mcw_logtest( seq : int )
{
	MCW_Line( seq, "via Log" );
	LogChannel( 'MCW', "[MCW#" + IntToString( seq ) + "] via LogChannel" );
	MCW_End( seq );
}


// ----------------------------------------------------------------------------- состояние мира

// GetHeading() : float (градусы) — V\engine\node.ws:39. theCamera — CCameraDirector (см. шапку):
// GetCameraPosition() : Vector — V\engine\cameraDirector.ws:20, GetCameraDirection() : Vector — :26 (так берёт
// взгляд ванильный spawn, V\game\temp.ws:6838). GetCommonMapManager() — V\game\r4Game.ws:222,
// GetCurrentArea() : name — V\game\commonMapManager.ws:148 (в 5.0 name, в 4.04 был int).
// GetDepotPath() : string — V\engine\world.ws:80.
exec function mcw_poll( seq : int )
{
	var p, c, d : Vector;
	var mm : CCommonMapManager;
	var world : CWorld;

	if( thePlayer )
	{
		p = thePlayer.GetWorldPosition();
		MCW_Line( seq, "pos " + MCW_X100( p.X ) + " " + MCW_X100( p.Y ) + " " + MCW_X100( p.Z )
			+ " " + MCW_X100( thePlayer.GetHeading() ) );
	}
	if( theCamera )
	{
		c = theCamera.GetCameraPosition();
		d = theCamera.GetCameraDirection();
		MCW_Line( seq, "cam " + MCW_X100( c.X ) + " " + MCW_X100( c.Y ) + " " + MCW_X100( c.Z )
			+ " " + MCW_Scaled( d.X, 10000.0 ) + " " + MCW_Scaled( d.Y, 10000.0 ) + " " + MCW_Scaled( d.Z, 10000.0 ) );
	}
	mm = theGame.GetCommonMapManager();
	if( mm )
		MCW_Line( seq, "area " + NameToString( mm.GetCurrentArea() ) );
	world = theGame.GetWorld();
	if( world )
		MCW_Line( seq, "world " + world.GetDepotPath() );
	if( thePlayer && world )
		MCW_PoolRefill();                      // пул блоков: по одной скрытой сущности за опрос (15 Гц)
	MCW_End( seq );
}


// ----------------------------------------------------------------------------- спавн

// Куб в 2 м перед Геральтом, низом на опоре (+0.5 м: pivot cont_crate в центре; у чужого шаблона pivot может быть
// внизу — тогда он повиснет на 0.5 м). comps = число CMeshComponent сразу после CreateEntity: меш cont_crate
// стримится (streamingDataBuffer), и компонента может ещё не быть — это и меряем.
// GetComponentsByClassName( className : name ) : array<CComponent> — V\engine\entity.ws:252; 'CMeshComponent'
// отдаёт и производные CStaticMeshComponent, как V\game\gameplay\items\interactive_objects\buffImmunityEntity.ws:169.
exec function mcw_cube( seq : int, path : string )
{
	var tpl : CEntityTemplate;
	var ent : CEntity;
	var comps : array<CComponent>;
	var ok, n : int;

	ok = 0;
	n = 0;
	tpl = MCW_Template( path );
	if( tpl && thePlayer )
	{
		ent = MCW_Spawn( tpl, MCW_SnapGround( MCW_Front( 2.0 ) ) + Vector( 0, 0, 0.5 ), true );
		if( ent )
		{
			ok = 1;
			comps = ent.GetComponentsByClassName( 'CMeshComponent' );
			n = comps.Size();
		}
	}
	MCW_Line( seq, "spawned " + IntToString( ok ) + " comps " + IntToString( n ) );
	MCW_End( seq );
}

// Шаблон по алиасу (denied_area = box_250cm 2.5 м, barrel = oil_barrel) и масштаб мешей.
// Масштаб есть только у компонентов: SetScale( scale : Vector ) — V\engine\components.ws:28, у CEntity/CNode
// только геттер GetLocalScale (V\engine\node.ws:18); Vector( s, s, s ) как V\game\gameplay\effects\effects\
// mutations\mutation7Base.ws:70. scaled = скольким компонентам задан масштаб (0, если меш ещё не застримился).
exec function mcw_alias( seq : int, alias : name, scale : float )
{
	var tpl : CEntityTemplate;
	var ent : CEntity;
	var comps : array<CComponent>;
	var ok, n, k, i : int;

	ok = 0;
	n = 0;
	k = 0;
	tpl = (CEntityTemplate)LoadResource( NameToString( alias ) );
	if( tpl && thePlayer )
	{
		ent = MCW_Spawn( tpl, MCW_SnapGround( MCW_Front( 2.0 ) ), true );
		if( ent )
		{
			ok = 1;
			comps = ent.GetComponentsByClassName( 'CMeshComponent' );
			n = comps.Size();
			if( scale > 0.0 )
			{
				for( i = 0; i < n; i += 1 )
				{
					if( comps[i] )
					{
						comps[i].SetScale( Vector( scale, scale, scale ) );
						k += 1;
					}
				}
			}
		}
	}
	MCW_Line( seq, "spawned " + IntToString( ok ) + " comps " + IntToString( n ) + " scaled " + IntToString( k ) );
	MCW_End( seq );
}

// n блоков сеткой перед Геральтом: ряды вдаль по взгляду, колонки поперёк, шаг spacing см (<=0 -> 100).
// Все на высоте опоры в 3 м перед игроком (неровный рельеф — часть блоков утонет или повиснет, для замера неважно).
// SqrtF( a : float ) : float — V\core\math.ws:145, CeilF( a : float ) : int — :166; перпендикуляр к взгляду
// (Y, -X) — как V\game\behavior_tree\tasks\monsters\btTaskSpawnDrownerAdds.ws:81.
exec function mcw_stress( seq : int, n : int, spacing : int, path : string )
{
	var tpl : CEntityTemplate;
	var origin, fwd, right, pos : Vector;
	var side, i, made, t0 : int;
	var sp : float;

	t0 = MCW_Ms();
	made = 0;
	tpl = MCW_Template( path );
	if( tpl && thePlayer && n > 0 )
	{
		side = CeilF( SqrtF( (float)n ) );
		sp = 1.0;
		if( spacing > 0 )
			sp = (float)spacing / 100.0;
		fwd = thePlayer.GetHeadingVector();
		right = Vector( fwd.Y, -fwd.X, 0 );
		origin = MCW_SnapGround( MCW_Front( 3.0 ) ) + Vector( 0, 0, 0.5 );
		for( i = 0; i < n; i += 1 )
		{
			pos = origin + fwd * ( sp * (float)( i / side ) ) + right * ( sp * ( (float)( i % side ) - (float)side * 0.5 ) );
			if( MCW_Spawn( tpl, pos, false ) )
				made += 1;
		}
	}
	MCW_Line( seq, "stress " + IntToString( made ) + " " + IntToString( MCW_Ms() - t0 ) );
	MCW_End( seq );
}

exec function mcw_clearall( seq : int )
{
	MCW_Line( seq, "cleared " + IntToString( MCW_DestroyAll() ) );
	MCW_End( seq );
}

exec function mcw_count( seq : int )
{
	var ents : array<CEntity>;

	ents = MCW_Tagged( 'MCW_BLOCK' );
	MCW_Line( seq, "count " + IntToString( ents.Size() ) );
	MCW_End( seq );
}

// Блоки моста без проб: MCW_TEST ставит только MCW_Spawn и только вместе с MCW_BLOCK (cube, alias, boom, boomat),
// поэтому блоки = MCW_BLOCK - MCW_TEST — два нативных списка, без обхода сущностей в скрипте. mcw_count не менялся.
exec function mcw_countblocks( seq : int )
{
	var all, tests : array<CEntity>;

	all = MCW_Tagged( 'MCW_BLOCK' );
	tests = MCW_Tagged( 'MCW_TEST' );
	MCW_Line( seq, "countblocks " + IntToString( all.Size() - tests.Size() ) + " " + IntToString( tests.Size() ) );
	MCW_End( seq );
}

// Пакет "set:x:y:z:t|clear:x:y:z|..." — x,y,z мировые см (центр блока считает мост, с поправкой на pivot), t — индекс шаблона
// 0..MCW_TemplateCount()-1 (0 cont_crate, 1 barrel, 2..12 блоки modMCWBlocks).
// КОНТРАКТ семантики (его же эмулирует fake_w3._x_mcw_batch трека net):
//  - сначала разбирается весь пакет, затем выполняются ВСЕ clear, затем ВСЕ set — порядок внутри пакета не важен;
//  - clear уничтожает каждый блок с тегом 'MCW_BLOCK' (любой: из пакета, cube, stress, boom), чья позиция,
//    округлённая до см, равна x:y:z; clear, не нашедший ни одного блока, — ошибка; повтор того же ключа в пакете
//    тоже ошибка (FindFirst отдаёт первый, второму попаданий не достаётся);
//  - set ставит блок, занятость клетки не проверяет: повторный set той же клетки даёт второй блок. Замена клетки =
//    clear+set в одном пакете; что стоит в клетке, знает мост;
//  - пустые куски между '|' пропускаются, пробелы не срезаются (" set:..." — ошибка);
//  - errors = нераспознанные команды (не set/clear, не то число полей, не -?[0-9]+, t вне 0..12) + clear без
//    попаданий + set, у которого не загрузился шаблон или не создалась сущность.
// Пример: 104 блока, "set:0:0:0:0|set:100:0:0:1|set:0:0:0:0|clear:100:0:0|clear:5:5:5|set:1:2:3:9|junk" ->
// "batch 3 0 4", count 107 (clear раньше set: 100:0:0 ещё пуст; два блока в 0:0:0).
// Бочка (физика) может сдвинуться — тогда clear её не найдёт и посчитает ошибку.
// Стоимость clear-прохода: GetEntitiesByTag('MCW_BLOCK') отдаёт N блоков; у каждого GetWorldPosition + 3 MCW_Round и
// сравнение с bbox всех clear-координат пакета — O(N) работы VM. Строковый ключ (3 IntToString + 4 конкатенации)
// и FindFirst по K clear-ключам (нативное сравнение строк) — только для M блоков внутри bbox: O(N + M*K).
// Пакет одного чанка даёт маленький bbox, и стресс-блоки (тысячи) отсекаются на сравнении чисел.
// [проверить]: отдаёт ли GetEntitiesByTag сущность, у которой в этом же кадре был Destroy(), — если да, второй
// пакет того же кадра посчитает её clear ещё раз (то же для count после clearall в одном кадре).
// StrSplit( str : string, subStr : string ) : array<string> — V\core\string.ws:201 (скриптовая в 5.0: цикл
// StrBeforeFirst/StrAfterFirst, O(len^2) на копиях строк — пачки держать в единицах КБ; пустые куски сохраняет,
// как str.split в Python). FindFirst у array<string> (-1, если нет) — V\game\gui\menus\photomodeMenu.ws:425.
// Min/Max( a, b : int ) : int — V\core\math.ws:82, :85.
exec function mcw_batch( seq : int, cmds : string )
{
	var list, f, clrKeys : array<string>;
	var clrHits, setT : array<int>;
	var setPos : array<Vector>;
	var ents : array<CEntity>;
	var tpls : array<CEntityTemplate>;
	var tplTried : array<bool>;
	var tpl : CEntityTemplate;
	var p : Vector;
	var t0, i, k, x, y, z, t, nSet, nClr, nErr : int;
	var x0, x1, y0, y1, z0, z1, px, py, pz : int;
	var good : bool;

	t0 = MCW_Ms();
	nSet = 0;
	nClr = 0;
	nErr = 0;
	list = StrSplit( cmds, "|" );
	for( i = 0; i < list.Size(); i += 1 )
	{
		if( list[i] == "" )
			continue;
		f = StrSplit( list[i], ":" );
		good = false;
		if( f[0] == "set" && f.Size() == 5 )
		{
			if( MCW_ParseInt( f[1], x ) && MCW_ParseInt( f[2], y ) && MCW_ParseInt( f[3], z ) && MCW_ParseInt( f[4], t ) )
			{
				if( t >= 0 && t < MCW_TemplateCount() )
				{
					setPos.PushBack( Vector( (float)x / 100.0, (float)y / 100.0, (float)z / 100.0, 1.0 ) );
					setT.PushBack( t );
					good = true;
				}
			}
		}
		else if( f[0] == "clear" && f.Size() == 4 )
		{
			if( MCW_ParseInt( f[1], x ) && MCW_ParseInt( f[2], y ) && MCW_ParseInt( f[3], z ) )
			{
				if( clrKeys.Size() == 0 )
				{
					x0 = x;
					x1 = x;
					y0 = y;
					y1 = y;
					z0 = z;
					z1 = z;
				}
				else
				{
					x0 = Min( x0, x );
					x1 = Max( x1, x );
					y0 = Min( y0, y );
					y1 = Max( y1, y );
					z0 = Min( z0, z );
					z1 = Max( z1, z );
				}
				clrKeys.PushBack( MCW_Key( x, y, z ) );
				clrHits.PushBack( 0 );
				good = true;
			}
		}
		if( !good )
			nErr += 1;
	}

	if( clrKeys.Size() > 0 )
	{
		ents = MCW_Tagged( 'MCW_BLOCK' );
		for( i = 0; i < ents.Size(); i += 1 )
		{
			if( !ents[i] )
				continue;
			p = ents[i].GetWorldPosition();
			px = MCW_Round( p.X * 100.0 );
			py = MCW_Round( p.Y * 100.0 );
			pz = MCW_Round( p.Z * 100.0 );
			if( px < x0 || px > x1 || py < y0 || py > y1 || pz < z0 || pz > z1 )
				continue;
			k = clrKeys.FindFirst( MCW_Key( px, py, pz ) );
			if( k >= 0 )
			{
				MCW_Release( ents[i] );
				clrHits[k] += 1;
				nClr += 1;
			}
		}
		for( i = 0; i < clrHits.Size(); i += 1 )
		{
			if( clrHits[i] == 0 )
				nErr += 1;
		}
	}

	if( setT.Size() > 0 )
	{
		// шаблоны грузим по разу на пакет и только встретившиеся типы
		tpls.Grow( MCW_TemplateCount() );
		tplTried.Grow( MCW_TemplateCount() );
		for( i = 0; i < setT.Size(); i += 1 )
		{
			if( !tplTried[ setT[i] ] )
			{
				tpls[ setT[i] ] = MCW_TemplateByIndex( setT[i] );
				tplTried[ setT[i] ] = true;
			}
			tpl = tpls[ setT[i] ];
			if( MCW_Place( setT[i], tpl, setPos[i] ) )
				nSet += 1;
			else
				nErr += 1;
		}
	}
	MCW_Line( seq, "batch " + IntToString( nSet ) + " " + IntToString( nClr ) + " " + IntToString( nErr )
		+ " " + IntToString( MCW_Ms() - t0 ) );
	MCW_End( seq );
}


// ----------------------------------------------------------------------------- лучи

// Луч камеры на 10 м, ближайшее попадание. Попадание в свой блок узнаём по компоненту, без пересчёта клетки.
// RayCastSync( start : Vector, end : Vector, out result : array<SRaycastHitResult>,
//   optional collisionGroupsNames : array<name> ) : bool — V\engine\physicsTraceManager.ws:34 (4 аргумента);
// GetTraceManager() — V\engine\world.ws:89; вызов как V\game\gui\menus\photomodeMenu.ws:3358.
// SRaycastHitResult { position, normal, distance, component } — physicsTraceManager.ws:10-16. Порядок попаданий
// не документирован, поэтому ищем минимум distance. CComponent.GetEntity() — V\engine\components.ws:13,
// HasTag( tag : name ) : bool — V\engine\node.ws:45; связка как V\game\player\r4Player.ws:14569.
// Нет камеры или мира (главное меню) — "look 0 0 0 0 0".
// При попадании ещё строка "normal <nx> <ny> <nz>" (x10000): SRaycastHitResult.normal — physicsTraceManager.ws:13,
// ваниль читает её из результата RayCastSync так же (V\game\player\playerAiming.ws:567, 598, 605). Мировая ли
// она и единичная ли — [проверить] на спайке; нулевую мост не использует.
exec function mcw_look( seq : int )
{
	var from, to, hp, hn : Vector;
	var res : array<SRaycastHitResult>;
	var groups : array<name>;
	var comp : CComponent;
	var ent : CEntity;
	var world : CWorld;
	var i, best, hit, isBlock : int;

	hit = 0;
	isBlock = 0;
	best = -1;
	world = theGame.GetWorld();
	if( theCamera && world )
	{
		from = theCamera.GetCameraPosition();
		to = from + theCamera.GetCameraDirection() * 10.0;
		groups = MCW_RayGroups();
		world.GetTraceManager().RayCastSync( from, to, res, groups );
		for( i = 0; i < res.Size(); i += 1 )
		{
			if( best < 0 || res[i].distance < res[best].distance )
				best = i;
		}
		if( best >= 0 )
		{
			hit = 1;
			hp = res[best].position;
			hn = res[best].normal;
			comp = res[best].component;
			if( comp )
			{
				ent = comp.GetEntity();
				if( ent )
				{
					if( ent.HasTag( 'MCW_BLOCK' ) )
						isBlock = 1;
				}
			}
		}
	}
	MCW_Line( seq, "look " + IntToString( hit ) + " " + MCW_X100( hp.X ) + " " + MCW_X100( hp.Y ) + " "
		+ MCW_X100( hp.Z ) + " " + IntToString( isBlock ) );
	if( hit == 1 )
		MCW_Line( seq, "normal " + MCW_Scaled( hn.X, 10000.0 ) + " " + MCW_Scaled( hn.Y, 10000.0 ) + " "
			+ MCW_Scaled( hn.Z, 10000.0 ) );
	MCW_End( seq );
}

// Высота опоры в точке (x, y) мировых см: луч сверху вниз по Terrain+Static, свои блоки (тег MCW_BLOCK) пропускаем.
// StaticTraceWithAdditionalInfo( pointA, pointB : Vector, out position : Vector, out normal : Vector,
//   out material : name, out component : CComponent, optional collisionGroupsNames : array<name> ) : bool —
// V\engine\world.ws:20 (7 аргументов); тег сущности компонента — как V\game\player\r4Player.ws:14569.
// Что трейс делает при старте внутри коллизии — [проверить]. Поэтому после попадания в блок следующий луч
// стартует внутри него, на 5 см выше низа (низ — MCW_BlockBottom):
//  - трейс не видит форму, в которой начал: сразу находит то, что под блоком (опору или следующий блок), даже если
//    блок утоплен в землю на несколько см; итерация на блок одна;
//  - трейс видит её (тот же блок ещё раз, distance 0): следующий луч — на 2 см ниже низа блока; итерации на блок
//    две. Если блок утоплен в землю глубже 2 см, такой старт окажется под поверхностью, heightfield снизу не
//    виден, и ответ будет "ground 0 0" — [проверить] на столбе, стоящем на земле.
// Лимит 256 итераций — столб не меньше 128 блоков при любом поведении трейса.
// Диапазон: +-300 м от Геральта, без игрока +-1000 м. Рельеф стримится — точки далеко от игрока могут не найтись.
// Нет мира — "ground 0 0". Сам поиск — MCW_GroundZ, его же зовёт пакетная mcw_grounds.
function MCW_GroundZ( world : CWorld, x : int, y : int, groups : array<name>, out z : float ) : bool
{
	var a, b, hit, n, p : Vector;
	var mat : name;
	var comp : CComponent;
	var ent, prev : CEntity;
	var zTop, zBot, zNext : float;
	var i : int;

	zTop = 1000.0;
	zBot = -1000.0;
	if( thePlayer )
	{
		p = thePlayer.GetWorldPosition();
		zTop = p.Z + 300.0;
		zBot = p.Z - 300.0;
	}
	a = Vector( (float)x / 100.0, (float)y / 100.0, zTop, 1.0 );
	b = Vector( a.X, a.Y, zBot, 1.0 );
	prev = NULL;
	for( i = 0; i < 256; i += 1 )
	{
		if( !world.StaticTraceWithAdditionalInfo( a, b, hit, n, mat, comp, groups ) )
			return false;
		ent = NULL;
		if( comp )
			ent = comp.GetEntity();
		if( !ent || !ent.HasTag( 'MCW_BLOCK' ) )
		{
			z = hit.Z;
			return true;
		}
		// сравнение сущностей — как V\game\components\inventoryComponent.ws:993 (GetEntity() == thePlayer)
		if( ent == prev )
			zNext = MCW_BlockBottom( ent, hit.Z ) - 0.02;
		else
			zNext = MCW_BlockBottom( ent, hit.Z ) + 0.05;
		if( zNext > a.Z - 0.01 )
			zNext = a.Z - 0.1;
		a = Vector( a.X, a.Y, zNext, 1.0 );
		prev = ent;
	}
	return false;
}

exec function mcw_ground( seq : int, x : int, y : int )
{
	var groups : array<name>;
	var world : CWorld;
	var z : float;

	world = theGame.GetWorld();
	groups.PushBack( 'Terrain' );
	groups.PushBack( 'Static' );
	if( world && MCW_GroundZ( world, x, y, groups, z ) )
		MCW_Line( seq, "ground 1 " + MCW_X100( z ) );
	else
		MCW_Line( seq, "ground 0 0" );
	MCW_End( seq );
}

// Пакет mcw_ground: cmds "x:y|x:y|..." (мировые см), до 64 колонн. Ответ — в порядке запроса, по 16 на строку:
// "gs <ok>:<z> <ok>:<z> ..." (ok 0 — опоры нет, z 0), затем "gms <мс>". Один вызов вместо десятков: лошадь MC на полном
// скаку открывает больше колонн в секунду, чем успевал mcw_ground по одной.
exec function mcw_grounds( seq : int, cmds : string )
{
	var list, f : array<string>;
	var groups : array<name>;
	var world : CWorld;
	var i, n, x, y, t0 : int;
	var z : float;
	var line : string;

	t0 = MCW_Ms();
	world = theGame.GetWorld();
	groups.PushBack( 'Terrain' );
	groups.PushBack( 'Static' );
	list = StrSplit( cmds, "|" );
	line = "";
	n = 0;
	for( i = 0; i < list.Size() && i < 64; i += 1 )
	{
		f = StrSplit( list[i], ":" );
		if( world && f.Size() == 2 && MCW_ParseInt( f[0], x ) && MCW_ParseInt( f[1], y ) && MCW_GroundZ( world, x, y, groups, z ) )
			line += " 1:" + MCW_X100( z );
		else
			line += " 0:0";
		n += 1;
		if( n == 16 )
		{
			MCW_Line( seq, "gs" + line );
			line = "";
			n = 0;
		}
	}
	if( n > 0 )
		MCW_Line( seq, "gs" + line );
	MCW_Line( seq, "gms " + IntToString( MCW_Ms() - t0 ) );
	MCW_End( seq );
}


// ----------------------------------------------------------------------------- прочее

// Ванильная взрывающаяся бочка в 4 м перед Геральтом. Класс COilBarrelEntity — V\game\gameplay\interactive\
// oilBarrel\oilBarrelEntity.ws:12; SetOnFire( explTime : float, randomize : bool ) — :75 (таймер Explosion:
// эффект, тряска камеры, урон огнём с нокдауном — :164-221; урон растёт с уровнем Геральта).
exec function mcw_boom( seq : int )
{
	var tpl : CEntityTemplate;
	var ent : CEntity;
	var barrel : COilBarrelEntity;
	var ok : int;

	ok = 0;
	if( thePlayer )
	{
		tpl = MCW_TemplateByIndex( 1 );
		ent = MCW_Spawn( tpl, MCW_SnapGround( MCW_Front( 4.0 ) ), true );
		barrel = (COilBarrelEntity)ent;
		if( barrel )
		{
			barrel.SetOnFire( 2.0, false );
			ok = 1;
		}
	}
	MCW_Line( seq, "boom " + IntToString( ok ) );
	MCW_End( seq );
}

// Бочка в точке (x, y, z мировых см): мост шлёт сюда explosion Майнкрафта. Опора — луч от 1 м выше точки до 5 м ниже
// (MCW_SnapDown): взрыв в воздухе — бочка на земле под ним, точка на пару см в рельефе (округление barrier) — на его
// поверхности; опоры нет — в самой точке. Запал 0.2 с вместо 2 с у mcw_boom: в Майнкрафте взрыв уже случился.
// SetOnFire кладёт explTime прямо в AddTimer( 'Explosion', explTime ) — oilBarrelEntity.ws:84-87. Теги — как у
// mcw_boom (MCW_BLOCK и MCW_TEST): mcw_countblocks бочку не считает. Без thePlayer (меню) — "boom 0", как mcw_boom.
exec function mcw_boomat( seq : int, x : int, y : int, z : int )
{
	var tpl : CEntityTemplate;
	var ent : CEntity;
	var barrel : COilBarrelEntity;
	var p : Vector;
	var ok : int;

	ok = 0;
	if( thePlayer )
	{
		p = Vector( (float)x / 100.0, (float)y / 100.0, (float)z / 100.0, 1.0 );
		tpl = MCW_TemplateByIndex( 1 );
		ent = MCW_Spawn( tpl, MCW_SnapDown( p, 1.0, 5.0 ), true );
		barrel = (COilBarrelEntity)ent;
		if( barrel )
		{
			barrel.SetOnFire( 0.2, false );
			ok = 1;
		}
	}
	MCW_Line( seq, "boom " + IntToString( ok ) );
	MCW_End( seq );
}

// GetGuiManager() : CR4GuiManager — V\game\r4Game.ws:234;
// ShowNotification( messageText : string, optional duration : float, optional queue : bool ) — V\game\gui\r4guimanager.ws:1405.
exec function mcw_notify( seq : int, s : string )
{
	theGame.GetGuiManager().ShowNotification( s );
	MCW_Line( seq, "notified" );
	MCW_End( seq );
}


// ----------------------------------------------------------------------------- сущности моста (мобы, этап 5)

// Тег слота 'MCW_E<id>' (слотов 64: id моста 0..63). string->name нет (шапка), поэтому имя из числа — switch по литералам, как ваниль:
// GetAuxNameForInt — V\game\explorations\exploration_movement_system\explorationStateManager.ws:744-760,
// GetRandomName — V\core\string.ws:129-150. Вне 0..63 — '' (IsNameValid — V\core\misc.ws:53; default: return '' —
// V\game\characterStats.ws:40). По тегу слота сущность находится нативно, без обхода и без состояния в скрипте:
// GetEntityByTag( tag : name ) : CEntity — V\engine\game.ws:137.
function MCW_EntTag( id : int ) : name
{
	switch( id )
	{
		case 0: return 'MCW_E0';
		case 1: return 'MCW_E1';
		case 2: return 'MCW_E2';
		case 3: return 'MCW_E3';
		case 4: return 'MCW_E4';
		case 5: return 'MCW_E5';
		case 6: return 'MCW_E6';
		case 7: return 'MCW_E7';
		case 8: return 'MCW_E8';
		case 9: return 'MCW_E9';
		case 10: return 'MCW_E10';
		case 11: return 'MCW_E11';
		case 12: return 'MCW_E12';
		case 13: return 'MCW_E13';
		case 14: return 'MCW_E14';
		case 15: return 'MCW_E15';
		case 16: return 'MCW_E16';
		case 17: return 'MCW_E17';
		case 18: return 'MCW_E18';
		case 19: return 'MCW_E19';
		case 20: return 'MCW_E20';
		case 21: return 'MCW_E21';
		case 22: return 'MCW_E22';
		case 23: return 'MCW_E23';
		case 24: return 'MCW_E24';
		case 25: return 'MCW_E25';
		case 26: return 'MCW_E26';
		case 27: return 'MCW_E27';
		case 28: return 'MCW_E28';
		case 29: return 'MCW_E29';
		case 30: return 'MCW_E30';
		case 31: return 'MCW_E31';
		case 32: return 'MCW_E32';
		case 33: return 'MCW_E33';
		case 34: return 'MCW_E34';
		case 35: return 'MCW_E35';
		case 36: return 'MCW_E36';
		case 37: return 'MCW_E37';
		case 38: return 'MCW_E38';
		case 39: return 'MCW_E39';
		case 40: return 'MCW_E40';
		case 41: return 'MCW_E41';
		case 42: return 'MCW_E42';
		case 43: return 'MCW_E43';
		case 44: return 'MCW_E44';
		case 45: return 'MCW_E45';
		case 46: return 'MCW_E46';
		case 47: return 'MCW_E47';
		case 48: return 'MCW_E48';
		case 49: return 'MCW_E49';
		case 50: return 'MCW_E50';
		case 51: return 'MCW_E51';
		case 52: return 'MCW_E52';
		case 53: return 'MCW_E53';
		case 54: return 'MCW_E54';
		case 55: return 'MCW_E55';
		case 56: return 'MCW_E56';
		case 57: return 'MCW_E57';
		case 58: return 'MCW_E58';
		case 59: return 'MCW_E59';
		case 60: return 'MCW_E60';
		case 61: return 'MCW_E61';
		case 62: return 'MCW_E62';
		case 63: return 'MCW_E63';
		default: return '';
	}
}

// Уничтожить все сущности с тегом (как MCW_DestroyAll, но для любого тега); MCW_DestroyAll не менялся.
function MCW_DestroyTagged( tag : name ) : int
{
	var ents : array<CEntity>;
	var i, n : int;

	ents = MCW_Tagged( tag );
	n = 0;
	for( i = 0; i < ents.Size(); i += 1 )
	{
		if( ents[i] )
		{
			ents[i].Destroy();
			n += 1;
		}
	}
	return n;
}

// Поворот по курсу (сотые градуса): EulerAngles( 0, курс, 0 ) — Yaw = курс в градусах, как
// V\game\actor.ws:4722 (CreateEntity с EulerAngles( 0, RandF() * 360, 0 )). EulerAngles { Pitch, Yaw, Roll } —
// V\core\math.ws:12-15.
function MCW_Heading( yaw : int ) : EulerAngles
{
	return EulerAngles( 0, (float)yaw / 100.0, 0 );
}

// Пакет "spawn:id:x:y:z:yaw:t|move:id:x:y:z:yaw|kill:id|wipe" — сущности моста (мобы Майнкрафта), id — слот моста
// 0..63, x, y, z — мировые см точки origin (опору и pivot шаблона учёл мост), yaw — курс в сотых градуса, t — индекс
// шаблона, как в mcw_batch (0 cont_crate, 1 barrel, 2..12 блоки modMCWBlocks). КОНТРАКТ (его же эмулирует fake_w3._x_mcw_ents):
//  - операции выполняются ПО ПОРЯДКУ (в отличие от mcw_batch); пустые куски между '|' пропускаются;
//  - spawn сперва уничтожает всё с тегом слота (повторный spawn того же id не плодит дубли — им мост чинит
//    расхождения), затем CreateEntity с тегами 'MCW_ENT' и 'MCW_E<id>' (без MCW_BLOCK: блоковые счётчики и clear
//    их не видят); ошибка — нет шаблона или сущности;
//  - move: GetEntityByTag тега слота + TeleportWithRotation; ошибка — слот пуст;
//  - kill: уничтожить всё с тегом слота; ошибка — слот был пуст (значит, мост и игра разошлись);
//  - wipe: уничтожить все 'MCW_ENT' (мост шлёт его первым после нового соединения и при сбоях), в killed;
//  - errors: нераспознанное (не то число полей, не -?[0-9]+, id вне 0..63) + ошибки выше.
// Teleport/TeleportWithRotation у CEntity — V\engine\entity.ws:44, :47; на CEntity так зовёт ваниль:
// V\game\behavior_tree\tasks\custom\btTaskBruxaSpawn.ws:109. Плавности нет: сущность прыгает на каждый move (мост шлёт
// их ~10 Гц). У CActor есть неблокирующие ActionSlideToWithHeadingAsync( target, heading, duration ) —
// V\game\actor.ws:814 (ваниль: btTaskDodge.ws:558) и ActionMoveToAsync — actor.ws:777 (btHorseRidingManager.ws:520):
// годятся для своего моба-актёра из REDkit, у ящика их нет. SetRawPlacement — entity.ws:91, новое в 5.0, вызовов в ванили
// нет: [проверить]. Сдвигается ли со статическим мешем его коллизия — [проверить].
exec function mcw_ents( seq : int, ops : string )
{
	var list, f : array<string>;
	var tags : array<name>;
	var tag : name;
	var ent : CEntity;
	var tpl : CEntityTemplate;
	var t0, i, k, id, x, y, z, yaw, t, nSpawn, nMove, nKill, nErr : int;
	var good : bool;

	t0 = MCW_Ms();
	nSpawn = 0;
	nMove = 0;
	nKill = 0;
	nErr = 0;
	list = StrSplit( ops, "|" );
	for( i = 0; i < list.Size(); i += 1 )
	{
		if( list[i] == "" )
			continue;
		f = StrSplit( list[i], ":" );
		good = false;
		if( f[0] == "wipe" && f.Size() == 1 )
		{
			nKill += MCW_DestroyTagged( 'MCW_ENT' );
			good = true;
		}
		else if( f[0] == "kill" && f.Size() == 2 )
		{
			if( MCW_ParseInt( f[1], id ) )
			{
				tag = MCW_EntTag( id );
				if( IsNameValid( tag ) )
				{
					k = MCW_DestroyTagged( tag );
					nKill += k;
					good = k > 0;
				}
			}
		}
		else if( f[0] == "move" && f.Size() == 6 )
		{
			if( MCW_ParseInt( f[1], id ) && MCW_ParseInt( f[2], x ) && MCW_ParseInt( f[3], y ) && MCW_ParseInt( f[4], z )
				&& MCW_ParseInt( f[5], yaw ) )
			{
				tag = MCW_EntTag( id );
				if( IsNameValid( tag ) )
				{
					ent = theGame.GetEntityByTag( tag );
					if( ent )
					{
						ent.TeleportWithRotation( Vector( (float)x / 100.0, (float)y / 100.0, (float)z / 100.0, 1.0 ), MCW_Heading( yaw ) );
						nMove += 1;
						good = true;
					}
				}
			}
		}
		else if( f[0] == "spawn" && f.Size() == 7 )
		{
			if( MCW_ParseInt( f[1], id ) && MCW_ParseInt( f[2], x ) && MCW_ParseInt( f[3], y ) && MCW_ParseInt( f[4], z )
				&& MCW_ParseInt( f[5], yaw ) && MCW_ParseInt( f[6], t ) )
			{
				tag = MCW_EntTag( id );
				tpl = MCW_TemplateByIndex( t );
				if( IsNameValid( tag ) && tpl )
				{
					MCW_DestroyTagged( tag );
					tags.Clear();
					tags.PushBack( 'MCW_ENT' );
					tags.PushBack( tag );
					ent = theGame.CreateEntity( tpl, Vector( (float)x / 100.0, (float)y / 100.0, (float)z / 100.0, 1.0 ), MCW_Heading( yaw ),
						false, false, true, PM_DontPersist, tags );
					if( ent )
					{
						nSpawn += 1;
						good = true;
					}
				}
			}
		}
		if( !good )
			nErr += 1;
	}
	MCW_Line( seq, "ents " + IntToString( nSpawn ) + " " + IntToString( nMove ) + " " + IntToString( nKill ) + " "
		+ IntToString( nErr ) + " " + IntToString( MCW_Ms() - t0 ) );
	MCW_End( seq );
}


// ----------------------------------------------------------------------------- пауза при потере фокуса

// Что держит игру на паузе. IsActive/IsPaused/IsPausedForReason/IsActivelyPaused/IsStopped — V\engine\game.ws:18-49;
// IsAnyMenu — V\game\gui\r4guimanager.ws:207; GameTimeToSeconds — V\engine\gameTime.ws:36.
// Причины пауз — строки из вызовов theGame.Pause(...) в V\game.
function MCW_B( b : bool ) : string
{
	if( b )
		return "1";
	return "0";
}

exec function mcw_state( seq : int )
{
	var reasons : array<string>;
	var s : string;
	var i : int;

	reasons.PushBack( "IngameMenu" );
	reasons.PushBack( "menus" );
	reasons.PushBack( "Popup" );
	reasons.PushBack( "FastMenu" );
	reasons.PushBack( "tutorial" );
	reasons.PushBack( "dialogOrCutscene" );
	reasons.PushBack( "" );
	s = "";
	for( i = 0; i < reasons.Size(); i += 1 )
	{
		if( theGame.IsPausedForReason( reasons[i] ) )
			s += " '" + reasons[i] + "'";
	}
	MCW_Line( seq, "state active " + MCW_B( theGame.IsActive() ) + " paused " + MCW_B( theGame.IsPaused() )
		+ " apause " + MCW_B( theGame.IsActivelyPaused() ) + " stopped " + MCW_B( theGame.IsStopped() )
		+ " menu " + MCW_B( theGame.GetGuiManager().IsAnyMenu() )
		+ " gtime " + IntToString( GameTimeToSeconds( theGame.GetGameTime() ) ) + " ems " + IntToString( MCW_EngineMs() ) );
	MCW_Line( seq, "reasons" + s );
	MCW_End( seq );
}

// Закрыть меню, которое движок открыл при потере фокуса, и снять паузу меню.
// GetRootMenu().CloseMenu() — так делает V\game\r4Game.ws:193 (OnGameLoadInitFinishedSuccess).
exec function mcw_resume( seq : int )
{
	var closed : int;

	closed = 0;
	if( theGame.GetGuiManager().IsAnyMenu() )
	{
		theGame.GetGuiManager().GetRootMenu().CloseMenu();
		closed = 1;
	}
	MCW_Line( seq, "resume " + IntToString( closed ) + " paused " + MCW_B( theGame.IsPaused() ) );
	MCW_End( seq );
}

// Как округляет RoundF (V\core\math.ws:172; ваниль рядом держит свой RoundMath, :175). Строка "round": RoundF для
// -2.5 -1.5 -0.5 0.5 1.5 2.5 -1.6 -1.4 1.4 1.6; строка "trip": сколько целых см x из [-20000, 20000] не
// возвращаются через (float)x / 100 * 100 у RoundF и у FloorF( v + 0.5 ).
exec function mcw_roundtest( seq : int )
{
	var v : array<float>;
	var s : string;
	var i, badR, badF : int;
	var f : float;

	v.PushBack( -2.5 );
	v.PushBack( -1.5 );
	v.PushBack( -0.5 );
	v.PushBack( 0.5 );
	v.PushBack( 1.5 );
	v.PushBack( 2.5 );
	v.PushBack( -1.6 );
	v.PushBack( -1.4 );
	v.PushBack( 1.4 );
	v.PushBack( 1.6 );
	s = "round";
	for( i = 0; i < v.Size(); i += 1 )
		s += " " + IntToString( RoundF( v[i] ) );
	MCW_Line( seq, s );
	badR = 0;
	badF = 0;
	for( i = -20000; i <= 20000; i += 1 )
	{
		f = ( (float)i / 100.0 ) * 100.0;
		if( RoundF( f ) != i )
			badR += 1;
		if( FloorF( f + 0.5 ) != i )
			badF += 1;
	}
	MCW_Line( seq, "trip " + IntToString( badR ) + " " + IntToString( badF ) );
	MCW_End( seq );
}

// Штатный выход из игры (для перезапуска с новыми модами без кликов по меню). ExitGame() — V\engine\game.ws:46.
exec function mcw_quit( seq : int )
{
	MCW_Line( seq, "quit" );
	MCW_End( seq );
	theGame.ExitGame();
}
