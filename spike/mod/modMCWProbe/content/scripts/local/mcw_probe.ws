// =====================================================================================================
// modMCWProbe — проба рискованных конструкций 5.0 для состояния моста. Компиляция проверена --reload 2026-10-03.
// Установка: <игра>\mods\modMCWProbe\content\scripts\local\mcw_probe.ws, UTF-8 без BOM, отдельно от modMCW.
// Скрипты всех модов компилируются одним проходом: ошибка здесь роняет компиляцию целиком, и modMCW тоже
// не загрузится. Поэтому ставить ПОСЛЕ того, как modMCW скомпилировался и ответил на mcw_ping; при ошибке
// компиляции — удалить папку modMCWProbe и записать текст ошибки в RESULTS.md.
//
// Что проверяем:
//  1. @addField(CR4Player) с array<CEntity>, map<string, CEntity> (объект как значение map — в ванили только
//     через struct, photomodeMenu.ws:292) и int.
//  2. @wrapMethod(CR4Player) на event OnSpawned: срабатывает ли при загрузке сейва и смене мира — токен
//     меняется, размеры полей обнуляются (новый объект игрока).
//  3. map-операции на поле чужого объекта (thePlayer.mcwpIndex[...]) из глобальной exec-функции.
//
// Контракт вывода тот же, что у modMCW: строки "[MCW#<seq>] ...", последняя "[MCW#<seq>] end".
//   mcwp_state(seq : int)               state <token> <slots> <index>      (без игрока: state -1 -1 -1)
//   mcwp_put(seq : int, key : string)   put <key> <size>                   (index[key] = thePlayer, slots += thePlayer)
//   mcwp_get(seq : int, key : string)   get <key> <found>                  (1 = ключ есть и хэндл живой)
//   mcwp_del(seq : int, key : string)   del <key> <ok>                     (1 = ключ был и удалён)
//
// Синтаксис аннотаций. REDkit wiki «WS: Script Compilation Errors overrides»
// (https://cdprojektred.atlassian.net/wiki/spaces/W3REDkit/pages/36241598): @addField(CPlayer) public var dummy : int;
// @wrapMethod(CPlayer) function CheatGod2(on : bool) { wrappedMethod(on); ... }. Аннотации есть с REDkit 4.0.103444
// (патч 4.04a, 06.06.2024); в 5.0.1042178 «Events can be annotated properly now».
// Реальный мод, обновлённый под 5.0 (коммит 6ceb7956 «Update mod to game version 5.0», 2026-09-30):
// https://github.com/p-marques/w3-ac/blob/9323ebbec9558b544359f3b56e6680d0ce9cdaf2/src/mods/modAbsoluteCamera/content/scripts/local/ACIntegrations.ws
//   @addField(CR4Player) public var aCameraManager : CACameraManager;
//   @wrapMethod(CR4Player) function OnSpawned(spawnData : SEntitySpawnData) { var result : bool; ...
//       result = wrappedMethod(spawnData); ... }
// Тот же приём на 4.04: https://github.com/Aelto/tw3-random-encounters-reworked/blob/c0619548924bb67b64aeced3a82c9b2658d3d909/src/annotations.ws
// Событие оборачивается ключевым словом function (так у всех найденных модов), хотя в ванили это
// event OnSpawned( spawnData : SEntitySpawnData ) — V\game\player\r4Player.ws:489.
// Если 5.0 откажет с «declared as event function ... Cannot override as normal function» (exe@0x362ac20) —
// заменить function на event в обёртке ниже, остальное не трогать.
// Поля объявлены public: к ним обращаются глобальные exec-функции через thePlayer, а поля, которые ваниль
// читает снаружи, объявлены public (V\game\npc\npc.ws:173); так же в вики и в w3-ac.
// Аннотации — только на верхнем уровне файла (exe@0x3629fd0), по одной на объявление.
// =====================================================================================================

@addField(CR4Player)
public var mcwpSlots : array<CEntity>;

@addField(CR4Player)
public var mcwpIndex : map<string, CEntity>;

@addField(CR4Player)
public var mcwpToken : int;

// Обёртка события спавна игрока. W3PlayerWitcher.OnSpawned (V\game\player\playerWitcher.ws:189) зовёт
// super.OnSpawned на :282, поэтому для Геральта сработает обёртка CR4Player. Оборачивать можно только метод,
// объявленный в самом классе: CR4Player.OnSpawned — V\game\player\r4Player.ws:489 (exe@0x3629440 запрещает
// унаследованные). ws_lint выдаёт тут WARN wrap-kind (event обёрнут как function) — это сознательно, см. шапку.
@wrapMethod(CR4Player)
function OnSpawned( spawnData : SEntitySpawnData )
{
	var result : bool;

	result = wrappedMethod( spawnData );
	this.mcwpToken = MCWP_NewToken();
	Log( "[MCWP] onspawned token " + IntToString( this.mcwpToken ) );
}

// Новый токен на каждый спавн игрока: глобальных счётчиков нет, поэтому часы + случайная добавка.
// GetLocalTimeAsMilliseconds() : int — V\engine\game.ws:79; RandRange( max : int, optional min : int ) : int —
// V\core\math.ws:64; оператор % на int — как V\game\environmentManager.ws:223.
function MCWP_NewToken() : int
{
	return ( theGame.GetLocalTimeAsMilliseconds() % 1000000 ) * 1000 + RandRange( 1000 );
}

// Log( text : string ) — V\core\misc.ws:6; IntToString — V\core\string.ws:75.
function MCWP_Line( seq : int, text : string )
{
	Log( "[MCW#" + IntToString( seq ) + "] " + text );
}

function MCWP_End( seq : int )
{
	Log( "[MCW#" + IntToString( seq ) + "] end" );
}

// Size() у map — V\game\gui\menus\photomodeMenu.ws:3583; у массива — встроенный OP_ArraySize.
exec function mcwp_state( seq : int )
{
	if( thePlayer )
		MCWP_Line( seq, "state " + IntToString( thePlayer.mcwpToken ) + " " + IntToString( thePlayer.mcwpSlots.Size() )
			+ " " + IntToString( thePlayer.mcwpIndex.Size() ) );
	else
		MCWP_Line( seq, "state -1 -1 -1" );
	MCWP_End( seq );
}

// map[key] = v — OP_MapGetOrAdd, присваивание как V\game\gui\menus\photomodeMenu.ws:3229 (там своё поле, this).
// Спайк 2026-10-03: thePlayer.mcwpIndex[ key ] = ... в глобальной функции 5.00c не компилирует («Left hand side is
// const»), а чтение map[key], Contains, Erase и PushBack через thePlayer компилируются. Поэтому запись — методом
// самого класса через @addMethod (строки ошибок @addMethod есть в exe 5.00c).
@addMethod(CR4Player)
function MCWP_Put( key : string, e : CEntity ) : int
{
	mcwpIndex[ key ] = e;
	mcwpSlots.PushBack( e );
	return mcwpIndex.Size();
}

exec function mcwp_put( seq : int, key : string )
{
	var size : int;

	size = -1;
	if( thePlayer )
		size = thePlayer.MCWP_Put( key, thePlayer );
	MCWP_Line( seq, "put " + key + " " + IntToString( size ) );
	MCWP_End( seq );
}

// Contains — V\game\gui\menus\photomodeMenu.ws:3221. Чтение map[key] без Contains добавило бы ключ (GetOrAdd).
exec function mcwp_get( seq : int, key : string )
{
	var ent : CEntity;
	var found : int;

	found = 0;
	if( thePlayer )
	{
		if( thePlayer.mcwpIndex.Contains( key ) )
		{
			ent = thePlayer.mcwpIndex[ key ];
			if( ent )
				found = 1;
		}
	}
	MCWP_Line( seq, "get " + key + " " + IntToString( found ) );
	MCWP_End( seq );
}

// Erase( key ) у map — V\game\player\playerWitcher.ws:12733.
exec function mcwp_del( seq : int, key : string )
{
	var ok : int;

	ok = 0;
	if( thePlayer )
	{
		if( thePlayer.mcwpIndex.Contains( key ) )
		{
			thePlayer.mcwpIndex.Erase( key );
			ok = 1;
		}
	}
	MCWP_Line( seq, "del " + key + " " + IntToString( ok ) );
	MCWP_End( seq );
}
