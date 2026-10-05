// =====================================================================================================
// mcw_combat.ws — бой в режиме Стива (docs/DEVELOPMENT.md «Что дальше», п. 4; схема SkyCraft): удары и стрелы Майнкрафта по NPC
// Ведьмака, удары NPC по Геральту — в Майнкрафт, стрелы MC в мире Ведьмака. Считает всё Майнкрафт: урон, крит,
// щит, броню. Ведьмак даёт NPC (их позиции для прокси на сервере MC), реакцию на удар (анимация, кровь, полоска
// здоровья, агрессия) и картинку стрел. Контракт вывода как у mcw_spike.ws: "[MCW#seq] ...", затем "end".
//
//   mcw_npcs(seq, r, maxn)           t <время движка, мс>   (момент снимка: по нему мост считает скорость NPC)
//                                    n <id> <x> <y> <z> <курс> <радиус> <рост> <flags>   (см, курс — градусы)
//                                    h <id> <доля x10000>   удары NPC по Геральту с прошлого вызова
//       NPC (CNewNPC) живые, не лошади, не дальше r м, не больше maxn ближних. id — свой номер NPC, живёт, пока NPC
//       в списке (новый сейв — с 1 заново). flags: 1 враждебен Геральту, 2 человек, 4 чудовище, 8 животное.
//       Доля удара — урон по жизненной силе / максимум жизненной силы Геральта; сам урон Ведьмак обнулил
//       (ReduceDamage, mcw_steve.ws): здоровье игрока считает Майнкрафт.
//   mcw_npcwarn(seq, id, heavy)      npcwarn <ok>
//       удар MC по NPC сейчас начнётся: как начало замаха Геральта (CActor.SetAttackData, V\game\actor.ws:5374-5403) —
//       Time2Dodge, swingType/swingDir, ParryStart (если NPC в защите или со щитом), IsAttacked. Мост шлёт это сразу по
//       удару MC, а сам удар (mcw_npchit) — через combat.impact_delay_s: деревьям поведения NPC нужно время поднять
//       парирование (btTaskPerformParry.ws:266-292) или отскочить (btTaskDodge.ws:123).
//   mcw_npchit(seq, id, frac, heavy, ranged, ratio)   npchit <ok> <hp до> <hp после> <парировал> <уклонился>   (x100)
//       удар MC по NPC — удар Геральта его мечом (стальным, по чудовищам — серебряным; W3Action_Attack.Init сам кладёт
//       урон меча с его статами, attackAction.ws:92-118), умноженный на ratio/1000 — силу удара MC против полного
//       удара железным мечом; броня и сопротивления NPC — ванильные. Меча нет — frac/10000 максимума здоровья NPC
//       прямым уроном. Атакующий — Геральт, реакция тяжёлая (heavy 1) или лёгкая, дальняя (ranged 1, стрела) или
//       ближняя. Как ванильный удар Геральта (CActor.TestParryAndCounter и
//       PrepareAttackAction, V\game\actor.ws:5409-5505, :5625-5698): W3Action_Attack, ближний — проверка
//       парирования самим NPC (CNewNPC.PerformParryCheck, npc.ws:1028: защита, угол, выносливость; анимация — его
//       дерево поведения) и уклонения (IsCurrentlyDodging, как CActor.ReduceDamage, actor.ws:5935); стрелу держит
//       щит (IsShielded). Прямой урон ванильный ReduceDamage не обнуляет (он добавляется после, damageManagerProcessor.ws:
//       479, 517-545), поэтому парированному и увернувшемуся урона не добавляем сами.
//   пробы: mcwx_npcbox(seq, n) — габариты NPC против капсулы; mcwx_arrows(seq) — пул стрел; mcwx_arrowtest(seq, mode) —
//       стрела-шаблон перед камерой (видна ли, компоненты)
//   mcwf_guard(seq, on)              fpguard <on>
//       игрок MC держит щит: кукла Геральта в защите (SetGuarded), удар NPC спереди — парирован (MCWN_Guard): урона
//       в Ведьмаке нет, NPC отшатывается (ReactToReflectedAttack), а удар целиком уходит в MC — там его ловит щит.
//   mcwa_arrows(seq, cmds)           arrows <показано> <ошибок>
//       стрелы MC (cmds через '|'): "<слот>:<x>:<y>:<z>:<dx>:<dy>:<dz>:<vx>:<vy>:<vz>:<летит>" — позиция (см),
//       направление (x1000), скорость (см/с) в осях Ведьмака; "<слот>:off" — спрятать; "init" — только завести пул.
//       Стрела — ванильный arrow_01.w2ent лучников (V\game\behavior_tree\tasks\combat\btTaskShoot.ws:33), без
//       выстрела: своей физики и урона у неё нет, её двигает мост, а между вызовами таймер MCWA_Tick по скорости.
//       Свободные стрелы ждут в 10 м под Геральтом (MCWA_Park), видимыми: иначе меш не стримится.
//
// API: V\game\actorsStorage.ws:84 (FindGameplayEntitiesInRange), V\game\actor.ws:545 (GetAttitude), :1641/:1691/:1712
// (IsHuman/IsMonster/IsAnimal), :2390/:2413 (GetHealth/GetMaxHealth), V\game\npc\npc.ws:855 (IsHorse),
// V\game\components\movingAgentComponent.ws:275-276 (GetCapsuleHeight/Radius у CMovingPhysicalAgentComponent), V\game\gameplay\actions\baseAction.ws:109
// (W3DamageAction.Initialize; образец — mcws_hit в mcw_steve.ws, проверен на волке), V\core\math.ws:380
// (VecToRotation), V\engine\entity.ws:261-263 (PlayEffect/StopEffect), :302 (SoundEvent), звуки и след стрелы —
// V\game\projectile\arrowProjectile.ws:8, :35, :124.
// =====================================================================================================

@addField(W3PlayerWitcher)
public var mcwnActs : array<CActor>;

@addField(W3PlayerWitcher)
public var mcwnIds : array<int>;

@addField(W3PlayerWitcher)
public var mcwnNext : int;

@addField(W3PlayerWitcher)
public var mcwnHurtId : array<int>;

@addField(W3PlayerWitcher)
public var mcwnHurtFrac : array<float>;

@addField(W3PlayerWitcher)
public var mcwnGuard : bool;

// PerformParryCheck у CNewNPC защищённый (npc.ws:1028): ваниль зовёт его из CActor.TestParryAndCounter атакующего.
@addMethod(CNewNPC)
function MCWN_ParryCheck( pi : SParryInfo ) : bool
{
	return PerformParryCheck( pi );
}

// Урон по Геральту уходит в Майнкрафт: режим Стива включён, глаз камеры — от клиента MC (mcwf_mc), сцены нет.
@addMethod(W3PlayerWitcher)
function MCWN_TakesHits() : bool
{
	return mcwfOn && mcwfSrc == 1 && !mcwfSusp;
}

// Номер NPC; add — завести, если его ещё нет. -1 — нет и не завели.
@addMethod(W3PlayerWitcher)
function MCWN_IdOf( a : CActor, add : bool ) : int
{
	var i : int;

	i = mcwnActs.FindFirst( a );
	if( i >= 0 )
		return mcwnIds[i];
	if( !add )
		return -1;
	mcwnNext += 1;
	mcwnActs.PushBack( a );
	mcwnIds.PushBack( mcwnNext );
	return mcwnNext;
}

// Из ReduceDamage (mcw_steve.ws), до ванильного: игрок MC держит щит, удар NPC ближний, парируемый и спереди (курс
// куклы — взгляд, mcwf) -> парирован: ванильный CActor.ReduceDamage обнулит его (actor.ws:5991-6013), NPC отшатнётся.
// -> парирован ли (тогда в MC уходит урон до парирования — щит MC решит сам: блок, износ щита).
@addMethod(W3PlayerWitcher)
function MCWN_Guard( out damageData : W3DamageAction ) : bool
{
	var aa : W3Action_Attack;
	var att : CActor;

	aa = (W3Action_Attack)damageData;
	att = (CActor)damageData.attacker;
	if( !mcwnGuard || !aa || !att || !aa.IsActionMelee() || !aa.CanBeParried() )
		return false;
	if( aa.IsParried() )
		return true;
	if( AbsF( AngleDistance( GetHeading(), VecHeading( att.GetWorldPosition() - GetWorldPosition() ) ) ) > 90.0 )
		return false;
	aa.SetIsParried( true );
	att.ReactToReflectedAttack( this );
	return true;
}

// Из ReduceDamage (mcw_steve.ws), уже после ванильного: удар NPC по Геральту — в очередь для mcw_npcs. Падение,
// окружение (огонь, утопление) и урон со временем (кровотечение, яд) не идут: их Майнкрафт считает сам или их нет.
// blocked — парирован щитом MC (MCWN_Guard): тогда в MC уходит raw, урон до парирования.
@addMethod(W3PlayerWitcher)
function MCWN_OnHurt( out damageData : W3DamageAction, blocked : bool, raw : float )
{
	var att : CActor;
	var maxv, dmg : float;

	att = (CActor)damageData.attacker;
	dmg = damageData.processedDmg.vitalityDamage;
	if( blocked )
		dmg = raw;
	if( !att || att == this || dmg <= 0 || damageData.IsDoTDamage() || damageData.IsActionEnvironment() )
		return;
	if( damageData.GetBuffSourceName() == "FallingDamage" )
		return;
	maxv = GetStatMax( BCS_Vitality );
	if( maxv <= 0 )
		return;
	mcwnHurtId.PushBack( MCWN_IdOf( att, true ) );
	mcwnHurtFrac.PushBack( dmg / maxv );
}

exec function mcw_npcs( seq : int, r : int, maxn : int )
{
	var w : W3PlayerWitcher;
	var ents : array<CGameplayEntity>;
	var found, keep : array<CActor>;
	var dist : array<float>;
	var ids : array<int>;
	var npc : CNewNPC;
	var a : CActor;
	var mac : CMovingPhysicalAgentComponent;
	var p, me : Vector;
	var i, k, best, flags, rad, h : int;
	var d, bestD : float;

	w = (W3PlayerWitcher)thePlayer;
	if( !w )
	{
		MCW_End( seq );
		return;
	}
	MCW_Line( seq, "t " + IntToString( MCW_EngineMs() ) );
	// сначала удары по Геральту: номера атакующих — по списку до его пересборки
	for( i = 0; i < w.mcwnHurtId.Size(); i += 1 )
		MCW_Line( seq, "h " + IntToString( w.mcwnHurtId[i] ) + " " + IntToString( MCW_Round( w.mcwnHurtFrac[i] * 10000.0 ) ) );
	w.mcwnHurtId.Clear();
	w.mcwnHurtFrac.Clear();

	me = w.GetWorldPosition();
	FindGameplayEntitiesInRange( ents, w, (float)r, Max( maxn * 3, 16 ), , , , 'CNewNPC' );
	for( i = 0; i < ents.Size(); i += 1 )
	{
		npc = (CNewNPC)ents[i];
		if( !npc || npc == w || !npc.IsAlive() || npc.IsHorse() )
			continue;
		found.PushBack( npc );
		dist.PushBack( VecDistance( npc.GetWorldPosition(), me ) );
	}
	// ближние maxn: выбор минимума по кругу (NPC вокруг — десятки, не тысячи)
	while( keep.Size() < maxn && found.Size() > 0 )
	{
		best = 0;
		bestD = dist[0];
		for( k = 1; k < found.Size(); k += 1 )
		{
			if( dist[k] < bestD )
			{
				best = k;
				bestD = dist[k];
			}
		}
		keep.PushBack( found[best] );
		found.Erase( best );
		dist.Erase( best );
	}
	// номера тех, кто остался в списке, сохраняются; ушедших забываем
	for( i = 0; i < keep.Size(); i += 1 )
		ids.PushBack( w.MCWN_IdOf( keep[i], true ) );
	w.mcwnActs.Clear();
	w.mcwnIds.Clear();
	for( i = 0; i < keep.Size(); i += 1 )
	{
		a = keep[i];
		w.mcwnActs.PushBack( a );
		w.mcwnIds.PushBack( ids[i] );
		p = a.GetWorldPosition();
		rad = 40;
		h = 180;
		mac = (CMovingPhysicalAgentComponent)a.GetMovingAgentComponent();
		if( mac )
		{
			rad = MCW_Round( mac.GetCapsuleRadius() * 100.0 );
			h = MCW_Round( mac.GetCapsuleHeight() * 100.0 );
		}
		flags = 0;
		if( a.GetAttitude( w ) == AIA_Hostile )
			flags += 1;
		if( a.IsHuman() )
			flags += 2;
		if( a.IsMonster() )
			flags += 4;
		if( a.IsAnimal() )
			flags += 8;
		MCW_Line( seq, "n " + IntToString( ids[i] ) + " " + IntToString( MCW_Round( p.X * 100.0 ) ) + " "
			+ IntToString( MCW_Round( p.Y * 100.0 ) ) + " " + IntToString( MCW_Round( p.Z * 100.0 ) ) + " "
			+ IntToString( MCW_Round( a.GetHeading() ) ) + " " + IntToString( rad ) + " " + IntToString( h ) + " "
			+ IntToString( flags ) );
	}
	MCW_End( seq );
}

// NPC по номеру из mcw_npcs (живой) или NULL.
function MCWN_Find( w : W3PlayerWitcher, id : int ) : CNewNPC
{
	var i : int;
	var npc : CNewNPC;

	i = -1;
	if( w )
		i = w.mcwnIds.FindFirst( id );
	if( i >= 0 )
		npc = (CNewNPC)w.mcwnActs[i];
	if( npc && npc.IsAlive() )
		return npc;
	return NULL;
}

exec function mcw_npcwarn( seq : int, id : int, heavy : int )
{
	var w : W3PlayerWitcher;
	var npc : CNewNPC;

	w = (W3PlayerWitcher)thePlayer;
	npc = MCWN_Find( w, id );
	if( !npc )
	{
		MCW_Line( seq, "npcwarn 0" );
		MCW_End( seq );
		return;
	}
	if( heavy == 1 )
	{
		npc.SignalGameplayEventParamInt( 'swingType', (int)AST_Vertical );
		npc.SignalGameplayEventParamInt( 'swingDir', (int)ASD_UpDown );
		npc.SignalGameplayEventParamInt( 'Time2Dodge', (int)EDT_Attack_Heavy );
	}
	else
	{
		npc.SignalGameplayEventParamInt( 'swingType', (int)AST_Horizontal );
		npc.SignalGameplayEventParamInt( 'swingDir', (int)ASD_LeftRight );
		npc.SignalGameplayEventParamInt( 'Time2Dodge', (int)EDT_Attack_Light );
	}
	if( npc.IsShielded( w ) || npc.IsGuarded() )
		npc.SignalGameplayEventParamInt( 'ParryStart', heavy );
	npc.IsAttacked( true );
	MCW_Line( seq, "npcwarn 1" );
	MCW_End( seq );
}

exec function mcw_npchit( seq : int, id : int, frac : int, heavy : int, ranged : int, ratio : int )
{
	var w : W3PlayerWitcher;
	var npc : CNewNPC;
	var action : W3Action_Attack;
	var pi : SParryInfo;
	var wid : SItemUniqueId;
	var hrt : EHitReactionType;
	var st : EAttackSwingType;
	var sd : EAttackSwingDirection;
	var att : name;
	var parried, dodged : bool;
	var maxh, before, after : float;

	w = (W3PlayerWitcher)thePlayer;
	npc = MCWN_Find( w, id );
	if( !npc || frac <= 0 )
	{
		MCW_Line( seq, "npchit 0 0 0 0 0" );
		MCW_End( seq );
		return;
	}
	before = npc.GetHealth();
	maxh = npc.GetMaxHealth();
	if( maxh <= 0 )
		maxh = before;
	hrt = EHRT_Light;
	att = 'attack_light';
	st = AST_Horizontal;
	sd = ASD_LeftRight;
	if( heavy == 1 )
	{
		hrt = EHRT_Heavy;
		att = 'attack_heavy';
		st = AST_Vertical;
		sd = ASD_UpDown;
	}
	// меч, которым Геральт бил бы этого NPC: по чудовищам (сущность — essence) серебряный, если он есть
	wid = w.GetEquippedSword( true );
	if( npc.UsesEssence() && w.GetInventory().IsIdValid( w.GetEquippedSword( false ) ) )
		wid = w.GetEquippedSword( false );
	parried = false;
	dodged = false;
	if( ranged == 1 )
		parried = npc.IsShielded( w );
	else
	{
		pi = w.ProcessParryInfo( w, npc, st, sd, att, wid, true );
		parried = npc.MCWN_ParryCheck( pi );
		if( parried )
			npc.SetDetailedHitReaction( st, sd );
		else
			dodged = npc.IsCurrentlyDodging() && VecDistanceSquared( npc.GetWorldPosition(), w.GetWorldPosition() ) > 1.7;
	}
	action = new W3Action_Attack in theGame.damageMgr;
	// урон — лёгкого удара: крит и сила замаха MC уже в ratio (тяжёлый удар Ведьмака добавил бы своё сверху)
	action.Init( w, npc, NULL, wid, 'attack_light', "MCW", hrt, true, true, att, st, sd, ranged != 1, ranged == 1, false, false );
	action.ClearEffects();
	if( parried )
	{
		action.ClearDamage();
		action.SetIsParried( true );
		w.SignalGameplayEvent( 'AttackParried' );
		npc.SignalGameplayEvent( 'ParryingAttack' );
	}
	else if( dodged )
	{
		action.ClearDamage();
		action.SetWasDodged();
	}
	else if( ratio > 0 && action.GetDamageValueTotal() > 0 )
		action.MultiplyAllDamageBy( (float)ratio / 1000.0 );
	else
	{
		action.ClearDamage();
		action.AddDamage( theGame.params.DAMAGE_NAME_DIRECT, maxh * (float)frac / 10000.0 );
	}
	theGame.damageMgr.ProcessAction( action );
	delete action;
	after = npc.GetHealth();
	MCW_Line( seq, "npchit 1 " + MCW_X100( before ) + " " + MCW_X100( after ) + " " + MCW_B( parried ) + " " + MCW_B( dodged ) );
	MCW_End( seq );
}

exec function mcwf_guard( seq : int, on : int )
{
	var w : W3PlayerWitcher;

	w = (W3PlayerWitcher)thePlayer;
	if( !w )
	{
		MCW_Line( seq, "fpguard -1" );
		MCW_End( seq );
		return;
	}
	w.mcwnGuard = on == 1;
	w.SetGuarded( on == 1 );
	MCW_Line( seq, "fpguard " + IntToString( on ) );
	MCW_End( seq );
}


// Стены Ведьмака на пути стрелы MC (mc ArrowWalls): cmds "x:y:z|x:y:z|..." — точки пути (мировые см), первое
// попадание по отрезкам: Terrain, Static, двери, заборы, платформы (не листва: стрела MC сквозь куст летит; не
// персонажи: в NPC стрела попадает через прокси на сервере). StaticTrace — V\engine\world.ws (как MCWC_Block).
//   mcw_path(seq, cmds)              path <отрезок> <x> <y> <z>   (см) | path -1   (путь чист)
exec function mcw_path( seq : int, cmds : string )
{
	var list, f : array<string>;
	var pts : array<Vector>;
	var groups : array<name>;
	var world : CWorld;
	var hit, n : Vector;
	var i, x, y, z : int;

	world = theGame.GetWorld();
	list = StrSplit( cmds, "|" );
	for( i = 0; i < list.Size(); i += 1 )
	{
		f = StrSplit( list[i], ":" );
		if( f.Size() == 3 && MCW_ParseInt( f[0], x ) && MCW_ParseInt( f[1], y ) && MCW_ParseInt( f[2], z ) )
			pts.PushBack( Vector( (float)x / 100.0, (float)y / 100.0, (float)z / 100.0, 1.0 ) );
	}
	groups.PushBack( 'Terrain' );
	groups.PushBack( 'Static' );
	groups.PushBack( 'Door' );
	groups.PushBack( 'Fence' );
	groups.PushBack( 'Platforms' );
	for( i = 0; world && i + 1 < pts.Size(); i += 1 )
	{
		if( world.StaticTrace( pts[i], pts[i + 1], hit, n, groups ) )
		{
			MCW_Line( seq, "path " + IntToString( i ) + " " + MCW_X100( hit.X ) + " " + MCW_X100( hit.Y ) + " " + MCW_X100( hit.Z ) );
			MCW_End( seq );
			return;
		}
	}
	MCW_Line( seq, "path -1" );
	MCW_End( seq );
}


// ----------------------------------------------------------------------------- стрелы Майнкрафта
@addField(W3PlayerWitcher)
public var mcwaEnts : array<CEntity>;

@addField(W3PlayerWitcher)
public var mcwaPos : array<Vector>;

@addField(W3PlayerWitcher)
public var mcwaVel : array<Vector>;

@addField(W3PlayerWitcher)
public var mcwaRot : array<EulerAngles>;

@addField(W3PlayerWitcher)
public var mcwaT : array<float>;

@addField(W3PlayerWitcher)
public var mcwaFly : array<bool>;

@addField(W3PlayerWitcher)
public var mcwaOn : array<bool>;

@addField(W3PlayerWitcher)
public var mcwaTimer : bool;

@addField(W3PlayerWitcher)
public var mcwaVer : int;

@addField(W3PlayerWitcher)
public var mcwaParkT : float;

function MCWA_Slots() : int
{
	return 8;
}

// Где ждут свободные стрелы: в 10 м под Геральтом. Меш у arrow_01.w2ent стримится (сразу после CreateEntity у неё
// один CTriggerActivatorComponent0), а стрела, созданная на общей парковке в 800 м под миром, меш так и не получала и
// после переноса к камере оставалась невидимой (2026-10-04, mcwx_arrows / mcwx_arrowtest). Под Геральтом — в зоне
// стриминга, под землёй её не видно.
@addMethod(W3PlayerWitcher)
function MCWA_Park( i : int ) : Vector
{
	return GetWorldPosition() + Vector( (float)i * 0.3, 0, -10.0 );
}

// Пул стрел: созданная сущность в кадре растворяется ~0,5 с (как блоки, MCW_Place), поэтому все стрелы создаются
// заранее под Геральтом (MCWA_Park) и не прячутся вовсе — свободные стоят под землёй. Пул прошлой версии (на дальней
// парковке, спрятанный) — снести и создать заново. -> сколько создано сейчас.
@addMethod(W3PlayerWitcher)
function MCWA_Init() : int
{
	var tpl : CEntityTemplate;
	var tags : array<name>;
	var ent : CEntity;
	var i, n : int;

	n = 0;
	if( mcwaVer != 2 )
	{
		for( i = 0; i < mcwaEnts.Size(); i += 1 )
		{
			if( mcwaEnts[i] )
				mcwaEnts[i].Destroy();
		}
		mcwaEnts.Clear();
		mcwaVer = 2;
	}
	if( mcwaEnts.Size() != MCWA_Slots() )
	{
		mcwaEnts.Clear();
		mcwaPos.Clear();
		mcwaVel.Clear();
		mcwaRot.Clear();
		mcwaT.Clear();
		mcwaFly.Clear();
		mcwaOn.Clear();
		mcwaEnts.Grow( MCWA_Slots() );
		mcwaPos.Grow( MCWA_Slots() );
		mcwaVel.Grow( MCWA_Slots() );
		mcwaRot.Grow( MCWA_Slots() );
		mcwaT.Grow( MCWA_Slots() );
		mcwaFly.Grow( MCWA_Slots() );
		mcwaOn.Grow( MCWA_Slots() );
	}
	tpl = (CEntityTemplate)LoadResource( "items\weapons\projectiles\arrows\arrow_01.w2ent", true );
	if( !tpl )
		return 0;
	tags.PushBack( 'MCW_ARROW' );
	for( i = 0; i < MCWA_Slots(); i += 1 )
	{
		if( mcwaEnts[i] )
			continue;
		ent = theGame.CreateEntity( tpl, MCWA_Park( i ), EulerAngles( 0, 0, 0 ), false, false, true, PM_DontPersist, tags );
		if( !ent )
			continue;
		mcwaEnts[i] = ent;
		mcwaOn[i] = false;
		mcwaFly[i] = false;
		n += 1;
	}
	if( !mcwaTimer )
	{
		AddTimer( 'MCWA_Tick', 0, true );
		mcwaTimer = true;
	}
	return n;
}

// Каждый кадр: летящие стрелы — вперёд по скорости от последней позиции моста (не дальше 0,1 с).
@addMethod(W3PlayerWitcher)
timer function MCWA_Tick( dt : float, id : int )
{
	var i : int;
	var now, age : float;

	now = theGame.GetEngineTimeAsSeconds();
	if( now - mcwaParkT > 0.5 )
	{
		// свободные — за Геральтом (на лошади он уезжает из зоны стриминга)
		mcwaParkT = now;
		for( i = 0; i < mcwaEnts.Size(); i += 1 )
		{
			if( !mcwaOn[i] && mcwaEnts[i] )
				mcwaEnts[i].TeleportWithRotation( MCWA_Park( i ), EulerAngles( 0, 0, 0 ) );
		}
	}
	for( i = 0; i < mcwaEnts.Size(); i += 1 )
	{
		if( !mcwaOn[i] || !mcwaFly[i] || !mcwaEnts[i] )
			continue;
		age = ClampF( now - mcwaT[i], 0.0, 0.1 );
		mcwaEnts[i].TeleportWithRotation( mcwaPos[i] + mcwaVel[i] * age, mcwaRot[i] );
	}
}

@addMethod(W3PlayerWitcher)
function MCWA_Off( i : int )
{
	if( !mcwaEnts[i] )
		return;
	if( mcwaOn[i] )
	{
		mcwaEnts[i].StopEffect( 'arrow_trail' );
		mcwaEnts[i].TeleportWithRotation( MCWA_Park( i ), EulerAngles( 0, 0, 0 ) );
	}
	mcwaOn[i] = false;
	mcwaFly[i] = false;
}

// f — куски команды "<слот>:<x>:<y>:<z>:<dx>:<dy>:<dz>:<vx>:<vy>:<vz>:<летит>". -> показана ли.
@addMethod(W3PlayerWitcher)
function MCWA_Set( f : array<string> ) : bool
{
	var v : array<int>;
	var i, k, x : int;
	var fly : bool;
	var pos, dir : Vector;
	var rot : EulerAngles;

	for( k = 0; k < 11; k += 1 )
	{
		if( !MCW_ParseInt( f[k], x ) )
			return false;
		v.PushBack( x );
	}
	i = v[0];
	if( i < 0 || i >= mcwaEnts.Size() || !mcwaEnts[i] )
		return false;
	pos = Vector( (float)v[1] / 100.0, (float)v[2] / 100.0, (float)v[3] / 100.0, 1.0 );
	dir = Vector( (float)v[4] / 1000.0, (float)v[5] / 1000.0, (float)v[6] / 1000.0, 0.0 );
	fly = v[10] == 1;
	if( VecLength( dir ) > 0.01 )
		rot = VecToRotation( dir );
	else
		rot = mcwaRot[i];
	mcwaPos[i] = pos;
	mcwaVel[i] = Vector( (float)v[7] / 100.0, (float)v[8] / 100.0, (float)v[9] / 100.0, 0.0 );
	mcwaRot[i] = rot;
	mcwaT[i] = theGame.GetEngineTimeAsSeconds();
	mcwaEnts[i].TeleportWithRotation( pos, rot );
	if( !mcwaOn[i] )
	{
		mcwaOn[i] = true;
		if( fly )
		{
			mcwaEnts[i].PlayEffect( 'arrow_trail' );
			mcwaEnts[i].SoundEvent( "cmb_arrow_swoosh" );
		}
	}
	else if( mcwaFly[i] && !fly )
	{
		mcwaEnts[i].StopEffect( 'arrow_trail' );
		mcwaEnts[i].SoundEvent( "cmb_arrow_impact_dirt" );
	}
	mcwaFly[i] = fly;
	return true;
}

exec function mcwa_arrows( seq : int, cmds : string )
{
	var w : W3PlayerWitcher;
	var list, f : array<string>;
	var i, slot, shown, bad : int;

	w = (W3PlayerWitcher)thePlayer;
	if( !w )
	{
		MCW_Line( seq, "arrows -1 0" );
		MCW_End( seq );
		return;
	}
	if( w.mcwaEnts.Size() != MCWA_Slots() || w.mcwaVer != 2 || cmds == "init" )
		w.MCWA_Init();
	shown = 0;
	bad = 0;
	list = StrSplit( cmds, "|" );
	for( i = 0; i < list.Size(); i += 1 )
	{
		if( list[i] == "" || list[i] == "init" )
			continue;
		f = StrSplit( list[i], ":" );
		if( f.Size() == 2 && f[1] == "off" && MCW_ParseInt( f[0], slot ) && slot >= 0 && slot < w.mcwaEnts.Size() )
			w.MCWA_Off( slot );
		else if( f.Size() == 11 && w.MCWA_Set( f ) )
			shown += 1;
		else
			bad += 1;
	}
	MCW_Line( seq, "arrows " + IntToString( shown ) + " " + IntToString( bad ) );
	MCW_End( seq );
}

// Проба: габариты NPC — мировая коробка сущности (CEntity.CalcBoundingBox, V\engine\entity.ws:89) против капсулы
// движения. Ближние n: "box <id> <x> <y> <z> <minX> <minY> <minZ> <maxX> <maxY> <maxZ> <r> <h> <курс>" (см).
exec function mcwx_npcbox( seq : int, n : int )
{
	var w : W3PlayerWitcher;
	var mac : CMovingPhysicalAgentComponent;
	var box : Box;
	var p : Vector;
	var i, r, h : int;

	w = (W3PlayerWitcher)thePlayer;
	for( i = 0; w && i < w.mcwnActs.Size() && i < n; i += 1 )
	{
		if( !w.mcwnActs[i] )
			continue;
		w.mcwnActs[i].CalcBoundingBox( box );
		p = w.mcwnActs[i].GetWorldPosition();
		r = 0;
		h = 0;
		mac = (CMovingPhysicalAgentComponent)w.mcwnActs[i].GetMovingAgentComponent();
		if( mac )
		{
			r = MCW_Round( mac.GetCapsuleRadius() * 100.0 );
			h = MCW_Round( mac.GetCapsuleHeight() * 100.0 );
		}
		MCW_Line( seq, "box " + IntToString( w.mcwnIds[i] ) + " " + MCWS_Pos( p ) + " " + MCWS_Pos( box.Min ) + " "
			+ MCWS_Pos( box.Max ) + " " + IntToString( r ) + " " + IntToString( h ) + " "
			+ IntToString( MCW_Round( w.mcwnActs[i].GetHeading() ) ) );
	}
	MCW_End( seq );
}

// Проба пула стрел: "a <слот> <есть> <показана> <летит> <x> <y> <z> <pitch> <yaw>" (см, градусы) и "atimer <таймер>".
exec function mcwx_arrows( seq : int )
{
	var w : W3PlayerWitcher;
	var i, ex, on, fly : int;
	var p : Vector;
	var r : EulerAngles;

	w = (W3PlayerWitcher)thePlayer;
	if( !w )
	{
		MCW_End( seq );
		return;
	}
	MCW_Line( seq, "atimer " + MCW_B( w.mcwaTimer ) + " " + IntToString( w.mcwaEnts.Size() ) );
	for( i = 0; i < w.mcwaEnts.Size(); i += 1 )
	{
		ex = 0;
		on = 0;
		fly = 0;
		p = Vector( 0, 0, 0 );
		r = EulerAngles( 0, 0, 0 );
		if( w.mcwaEnts[i] )
		{
			ex = 1;
			p = w.mcwaEnts[i].GetWorldPosition();
			r = w.mcwaEnts[i].GetWorldRotation();
		}
		if( w.mcwaOn[i] )
			on = 1;
		if( w.mcwaFly[i] )
			fly = 1;
		MCW_Line( seq, "a " + IntToString( i ) + " " + IntToString( ex ) + " " + IntToString( on ) + " " + IntToString( fly ) + " "
			+ MCWS_Pos( p ) + " " + MCW_X100( r.Pitch ) + " " + MCW_X100( r.Yaw ) );
	}
	MCW_End( seq );
}

// Проба: стрела-шаблон перед камерой. mode 1 — новая сущность (без пряток) на 2,5 м перед камерой, тег MCW_ARROWTEST;
// 2 — то же со SetHideInGame( true, true ) и сразу ( false, true ); 0 — удалить пробные. Печатает компоненты:
// "comp <класс> <включён> <видим>" у CDrawableComponent (V\engine\components.ws:16, :470).
exec function mcwx_arrowtest( seq : int, mode : int )
{
	var tpl : CEntityTemplate;
	var ent : CEntity;
	var ents : array<CEntity>;
	var comps : array<CComponent>;
	var tags : array<name>;
	var d : CDrawableComponent;
	var p, dir : Vector;
	var i : int;

	if( mode == 0 )
	{
		theGame.GetEntitiesByTag( 'MCW_ARROWTEST', ents );
		for( i = 0; i < ents.Size(); i += 1 )
			ents[i].Destroy();
		MCW_Line( seq, "arrowtest 0 " + IntToString( ents.Size() ) );
		MCW_End( seq );
		return;
	}
	tpl = (CEntityTemplate)LoadResource( "items\weapons\projectiles\arrows\arrow_01.w2ent", true );
	dir = theCamera.GetCameraDirection();
	p = theCamera.GetCameraPosition() + dir * 2.5;
	tags.PushBack( 'MCW_ARROWTEST' );
	ent = theGame.CreateEntity( tpl, p, EulerAngles( 0, VecHeading( dir ) + 90.0, 0 ), false, false, true, PM_DontPersist, tags );
	if( !ent )
	{
		MCW_Line( seq, "arrowtest -1" );
		MCW_End( seq );
		return;
	}
	if( mode == 2 )
	{
		ent.SetHideInGame( true, true );
		ent.SetHideInGame( false, true );
	}
	comps = ent.GetComponentsByClassName( 'CComponent' );
	MCW_Line( seq, "arrowtest " + IntToString( mode ) + " " + IntToString( comps.Size() ) + " " + MCWS_Pos( p ) );
	for( i = 0; i < comps.Size(); i += 1 )
	{
		d = (CDrawableComponent)comps[i];
		if( d )
			MCW_Line( seq, "comp " + comps[i].GetName() + " " + MCW_B( d.IsEnabled() ) + " " + MCW_B( d.IsVisible() ) );
		else
			MCW_Line( seq, "comp " + comps[i].GetName() + " - -" );
	}
	MCW_End( seq );
}
