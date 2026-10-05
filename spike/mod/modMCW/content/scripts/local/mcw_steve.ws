// =====================================================================================================
// mcw_steve.ws — проба «Стив вместо Геральта» по схеме SkyCraft: игроком управляет клиент Майнкрафта, Ведьмак
// держит невидимую куклу и камеру. Здесь только то, что в SkyCraft делает нативный плагин, — но скриптом:
//   камера по чужим координатам, кукла без управления и гравитации, скрытое тело, удар по NPC через боевой
//   конвейер игры, перехват урона по игроку. Контракт вывода как у mcw_spike.ws: "[MCW#seq] ...", затем "end".
//
//   mcws_cam(seq, mode, x, y, z, pitch, yaw)   cam <mode> <x> <y> <z> <pitch> <yaw> | dir <x> <y> <z>
//       mode 0 — вернуть управление камерой игре; 1 — поставить камеру (см, сотые градуса); 2 — то же +
//       UpdateWithoutInput(true). Строка cam — что вернул CCustomCamera, dir — позиция камеры рендера (theCamera).
//   mcws_puppet(seq, on)                        puppet <on> <gravityComp>
//       on 1 — BlockAllActions('MCW'), SetGravity(false) у CMovingPhysicalAgentComponent; on 0 — обратно.
//   mcws_ptp(seq, x, y, z, yaw)                 ptp <x> <y> <z>   (Teleport куклы; ответ — позиция после)
//   mcws_hide(seq, hide)                        hide <hide>       (SetVisibility у CActor)
//   mcws_hit(seq, dmg)                          hit <found> <hpBefore> <hpAfter>   (ближайший NPC в 15 м)
//   mcws_god(seq, on)                           god <on> <hits> <dmg>   (перехват ReduceDamage игрока)
//
// API — V\game\r4Game.ws:263 (GetGameCamera : CCustomCamera), V\game\cameras\combat\customCamera.ws:66-121
// (EnableManualControl, SetCameraWorldPosition/Rotation, UpdateWithoutInput), V\engine\cameraDirector.ws:20-21,
// V\game\player\player.ws:406 (BlockAllActions), V\game\components\movingAgentComponent.ws:203, 216 (SetGravity),
// V\game\actor.ws:1207 (SetVisibility), :2390 (GetHealth), V\game\actorsStorage.ws:84 (FindGameplayEntitiesInRange),
// V\game\gameplay\actions\baseAction.ws:109 (W3DamageAction.Initialize; образец — damageManagerProcessor.ws:2228-2240),
// V\game\player\playerWitcher.ws:2391 (ReduceDamage, объявлен в W3PlayerWitcher).
// =====================================================================================================

@addField(W3PlayerWitcher)
public var mcwsGod : bool;

@addField(W3PlayerWitcher)
public var mcwsHits : int;

@addField(W3PlayerWitcher)
public var mcwsDmg : float;

// Урон по игроку: при включённом перехвате копим его и обнуляем (в схеме SkyCraft урон уходит в Майнкрафт). В режиме
// Стива так всегда: удары NPC уходят в очередь mcw_npcs (MCWN_OnHurt, mcw_combat.ws), здоровье считает Майнкрафт.
@wrapMethod(W3PlayerWitcher)
function ReduceDamage( out damageData : W3DamageAction )
{
	var raw : float;
	var blocked : bool;

	raw = damageData.processedDmg.vitalityDamage;
	blocked = false;
	if( MCWN_TakesHits() )
		blocked = MCWN_Guard( damageData );        // щит MC: парирован до ванильного (mcw_combat.ws)
	wrappedMethod( damageData );
	if( mcwsGod || MCWN_TakesHits() )
	{
		mcwsHits += 1;
		mcwsDmg += damageData.processedDmg.vitalityDamage;
		if( MCWN_TakesHits() )
			MCWN_OnHurt( damageData, blocked, raw );
		damageData.processedDmg.vitalityDamage = 0;
	}
}

@addMethod(W3PlayerWitcher)
function MCWS_SetGod( on : bool )
{
	mcwsGod = on;
	if( on )
	{
		mcwsHits = 0;
		mcwsDmg = 0;
	}
}

function MCWS_Pos( p : Vector ) : string
{
	return MCW_X100( p.X ) + " " + MCW_X100( p.Y ) + " " + MCW_X100( p.Z );
}

exec function mcws_cam( seq : int, mode : int, x : int, y : int, z : int, pitch : int, yaw : int )
{
	var cam : CCustomCamera;
	var r : EulerAngles;

	cam = theGame.GetGameCamera();
	if( !cam )
	{
		MCW_Line( seq, "cam -1" );
		MCW_End( seq );
		return;
	}
	if( mode == 0 )
	{
		cam.EnableManualControl( true );
	}
	else
	{
		cam.EnableManualControl( false );
		cam.SetCameraWorldPosition( Vector( (float)x / 100.0, (float)y / 100.0, (float)z / 100.0, 1.0 ) );
		cam.SetCameraWorldRotation( EulerAngles( (float)pitch / 100.0, (float)yaw / 100.0, 0 ) );
		if( mode == 2 )
			cam.UpdateWithoutInput( true );
	}
	r = cam.GetCameraWorldRotation();
	MCW_Line( seq, "cam " + IntToString( mode ) + " " + MCWS_Pos( cam.GetCameraWorldPosition() ) + " "
		+ MCW_X100( r.Pitch ) + " " + MCW_X100( r.Yaw ) );
	MCW_Line( seq, "dir " + MCWS_Pos( theCamera.GetCameraPosition() ) );
	MCW_End( seq );
}

exec function mcws_puppet( seq : int, on : int )
{
	var mac : CMovingPhysicalAgentComponent;
	var g : int;

	g = 0;
	mac = (CMovingPhysicalAgentComponent)thePlayer.GetMovingAgentComponent();
	if( mac )
		g = 1;
	if( on == 1 )
	{
		thePlayer.BlockAllActions( 'MCW', true );
		if( mac )
			mac.SetGravity( false );
	}
	else
	{
		thePlayer.BlockAllActions( 'MCW', false );
		if( mac )
			mac.SetGravity( true );
	}
	MCW_Line( seq, "puppet " + IntToString( on ) + " " + IntToString( g ) );
	MCW_End( seq );
}

exec function mcws_ptp( seq : int, x : int, y : int, z : int, yaw : int )
{
	thePlayer.TeleportWithRotation( Vector( (float)x / 100.0, (float)y / 100.0, (float)z / 100.0, 1.0 ),
		EulerAngles( 0, (float)yaw / 100.0, 0 ) );
	MCW_Line( seq, "ptp " + MCWS_Pos( thePlayer.GetWorldPosition() ) );
	MCW_End( seq );
}

exec function mcws_hide( seq : int, hide : int )
{
	thePlayer.SetVisibility( hide == 0 );
	MCW_Line( seq, "hide " + IntToString( hide ) );
	MCW_End( seq );
}

exec function mcws_hit( seq : int, dmg : int )
{
	var ents : array<CGameplayEntity>;
	var npc, best : CActor;
	var action : W3DamageAction;
	var i : int;
	var d, bestD, before, after : float;

	FindGameplayEntitiesInRange( ents, thePlayer, 15.0, 50, , , , 'CNewNPC' );
	bestD = 1000.0;
	for( i = 0; i < ents.Size(); i += 1 )
	{
		npc = (CActor)ents[i];
		if( !npc || !npc.IsAlive() || npc == thePlayer )
			continue;
		d = VecDistance( npc.GetWorldPosition(), thePlayer.GetWorldPosition() );
		if( d < bestD )
		{
			bestD = d;
			best = npc;
		}
	}
	if( !best )
	{
		MCW_Line( seq, "hit 0 0 0" );
		MCW_End( seq );
		return;
	}
	before = best.GetHealth();
	action = new W3DamageAction in theGame;
	action.Initialize( thePlayer, best, NULL, "MCW", EHRT_Heavy, CPS_AttackPower, true, false, false, false );
	action.AddDamage( theGame.params.DAMAGE_NAME_DIRECT, (float)dmg );
	theGame.damageMgr.ProcessAction( action );
	delete action;
	after = best.GetHealth();
	MCW_Line( seq, "hit 1 " + MCW_X100( before ) + " " + MCW_X100( after ) + " " + MCW_X100( bestD ) );
	MCW_End( seq );
}

exec function mcws_god( seq : int, on : int )
{
	var w : W3PlayerWitcher;

	w = (W3PlayerWitcher)thePlayer;
	if( !w )
	{
		MCW_Line( seq, "god -1 0 0" );
		MCW_End( seq );
		return;
	}
	if( on == 0 || on == 1 )
		w.MCWS_SetGod( on == 1 );
	MCW_Line( seq, "god " + IntToString( on ) + " " + IntToString( w.mcwsHits ) + " " + MCW_X100( w.mcwsDmg ) );
	MCW_End( seq );
}

// Своя камера: CStaticCamera из шаблона, как у плаката (V\game\gameplay\poster.ws:98-103: CreateEntity, длительности
// перехода, Run; :112 Stop). Двигаем телепортом каждый кадр. Хэндл — в поле игрока.
//   mcws_scam(seq, op, x, y, z, pitch, yaw, path)   scam <op> <ok> <x> <y> <z> | dir <x> <y> <z>
//   op 1 — создать по path и Run; 2 — TeleportWithRotation; 0 — Stop и Destroy.
@addField(W3PlayerWitcher)
public var mcwsCam : CStaticCamera;

exec function mcws_scam( seq : int, op : int, x : int, y : int, z : int, pitch : int, yaw : int, path : string )
{
	var w : W3PlayerWitcher;
	var tpl : CEntityTemplate;
	var p : Vector;
	var r : EulerAngles;
	var ok : int;

	ok = 0;
	w = (W3PlayerWitcher)thePlayer;
	p = Vector( (float)x / 100.0, (float)y / 100.0, (float)z / 100.0, 1.0 );
	r = EulerAngles( (float)pitch / 100.0, (float)yaw / 100.0, 0 );
	if( w )
	{
		if( op == 1 )
		{
			if( w.mcwsCam )
			{
				w.mcwsCam.Stop();
				w.mcwsCam.Destroy();
			}
			tpl = (CEntityTemplate)LoadResource( path, true );
			if( tpl )
				w.mcwsCam = (CStaticCamera)theGame.CreateEntity( tpl, p, r );
			if( w.mcwsCam )
			{
				w.mcwsCam.activationDuration = 0;
				w.mcwsCam.deactivationDuration = 0;
				w.mcwsCam.Run();
				ok = 1;
			}
		}
		else if( op == 2 && w.mcwsCam )
		{
			w.mcwsCam.TeleportWithRotation( p, r );
			ok = 1;
		}
		else if( op == 0 && w.mcwsCam )
		{
			w.mcwsCam.Stop();
			w.mcwsCam.Destroy();
			ok = 1;
		}
	}
	MCW_Line( seq, "scam " + IntToString( op ) + " " + IntToString( ok ) + " " + MCWS_Pos( p ) );
	MCW_Line( seq, "dir " + MCWS_Pos( theCamera.GetCameraPosition() ) );
	MCW_End( seq );
}


// ----------------------------------------------------------------------------- вид от первого лица
// Взгляд считает Ведьмак (мышь читается здесь каждый кадр, без лишнего кадра на сеть), позиция глаза — от Геральта
// (src 0) или от моста (src 1: позиция из Майнкрафта, mcwf_eye, экстраполяция по скорости до 0,1 с).
// Мышь — GI_MouseDampX/Y, как V\game\player\player.ws:593 (IK_MouseX/Y в input.settings): 0,4 единицы на пиксель
// (замер 2026-10-03: rel 200 px -> 80), +X вправо, +Y вниз. sens 30 = 0,12 градуса на пиксель. Тик — таймер раз в кадр,
// AddTimer( имя, 0, true ) как V\game\gameplay\devices\traps\base_trap.ws:143.
// Камера — CStaticCamera из poster_camera.w2ent (см. mcws_scam). Калибровка 2026-10-03: компонент камеры жёстко
// сидит в (0; 1,28; 1,46) м от origin, поворачивается вместе с сущностью (наклон, потом курс), смотрит в −forward:
// курс взгляда = курс сущности + 180, наклон взгляда = −наклон сущности.
//   mcwf_on(seq, on, src, sens)   fp <on> <src> <yaw> <pitch>       (sens — сотые доли градуса на единицу оси)
//   mcwf_eye(seq, x, y, z, vx, vy, vz)   look <yaw> <pitch> <ticks>  (см и см/с; ответ — взгляд для Майнкрафта)
//   mcwf_mc(seq, x, y, z, vx, vy, vz, eyeh)   look <yaw> <pitch> <ticks> <far>   то же + кукла: Геральт каждый кадр
//                                 ставится в ноги игрока MC (глаз − eyeh см). far 1 — Геральт дальше 10 м от ног MC
//                                 (его перенёс Ведьмак: быстрое перемещение, сцена), куклу тогда не тянем
//   mcwf_fov(seq, fov)            fov <камера> <рендер>   (×100; fov > 0 — поставить камере)
//   mcwf_state(seq)               fpstate <on> <src> <yaw> <pitch> <ticks> <accX> <accY> <susp>   (×100; susp 1 —
//                                 идёт сцена, вид от первого лица ей уступил)
//   mcwf_interact(seq)            interact <0|1>     есть ли у Ведьмака подсказка взаимодействия (дверь, NPC, сундук)
// Пока вид от первого лица включён, всё надетое и взятое в руку (мечи на спине, арбалет, волосы, голова) спрятано —
// MCWF_HideItems, повторно раз в 30 кадров: меч могут вытащить.
@addField(W3PlayerWitcher)
public var mcwfOn : bool;

@addField(W3PlayerWitcher)
public var mcwfSrc : int;

@addField(W3PlayerWitcher)
public var mcwfYaw : float;

@addField(W3PlayerWitcher)
public var mcwfPitch : float;

@addField(W3PlayerWitcher)
public var mcwfSens : float;

@addField(W3PlayerWitcher)
public var mcwfEye : Vector;

@addField(W3PlayerWitcher)
public var mcwfVel : Vector;

@addField(W3PlayerWitcher)
public var mcwfT : float;

@addField(W3PlayerWitcher)
public var mcwfTicks : int;

@addField(W3PlayerWitcher)
public var mcwfAccX : float;

@addField(W3PlayerWitcher)
public var mcwfAccY : float;

@addField(W3PlayerWitcher)
public var mcwfPuppet : bool;

@addField(W3PlayerWitcher)
public var mcwfEyeH : float;

@addField(W3PlayerWitcher)
public var mcwfFar : bool;

@addField(W3PlayerWitcher)
public var mcwfFreeze : bool;

@addField(W3PlayerWitcher)
public var mcwfSusp : bool;

// Сцена Ведьмака: диалог, катсцена, лошадь или лодка — вид от первого лица на это время уступает: своя камера
// останавливается (сцена ставит свою), Геральт виден, куклу не тянем. Мост видит это в mcwf_state (susp) и гасит
// режим Стива. V\game\r4Game.ws:1331, V\game\actor.ws:684, :6420.
@addMethod(W3PlayerWitcher)
function MCWF_InScene() : bool
{
	return theGame.IsDialogOrCutscenePlaying() || IsInNonGameplayCutscene() || IsUsingVehicle();
}

// Надетое и взятое в руку — спрятать или показать (V\game\components\inventoryComponent.ws:211, :1144, :1157, :1161).
@addMethod(W3PlayerWitcher)
function MCWF_HideItems( hide : bool )
{
	var inv : CInventoryComponent;
	var items : array< SItemUniqueId >;
	var ent : CItemEntity;
	var i : int;

	inv = GetInventory();
	if( !inv )
		return;
	inv.GetAllItems( items );
	for( i = 0; i < items.Size(); i += 1 )
	{
		if( inv.IsItemMounted( items[i] ) || inv.IsItemHeld( items[i] ) )
		{
			ent = inv.GetItemEntityUnsafe( items[i] );
			if( ent )
				ent.SetHideInGame( hide );
		}
	}
}

// Поставить сущность камеры так, чтобы глаз оказался в eye и смотрел по (yaw, pitch) — градусы, курс как у W3.
@addMethod(W3PlayerWitcher)
function MCWF_Place( eye : Vector, yaw : float, pitch : float )
{
	var ye, pe, sy, cy, sp, cp, oy, oz : float;

	ye = AngleNormalize( yaw + 180.0 );
	pe = -pitch;
	sp = SinF( Deg2Rad( pe ) );
	cp = CosF( Deg2Rad( pe ) );
	oy = 1.28 * cp - 1.46 * sp;
	oz = 1.28 * sp + 1.46 * cp;
	sy = SinF( Deg2Rad( ye ) );
	cy = CosF( Deg2Rad( ye ) );
	mcwsCam.TeleportWithRotation( eye - Vector( -oy * sy, oy * cy, oz ), EulerAngles( pe, ye, 0 ) );
}

@addMethod(W3PlayerWitcher)
timer function MCWF_Tick( dt : float, id : int )
{
	var mx, my, age : float;
	var eye, feet : Vector;
	var r : EulerAngles;

	if( !mcwfOn || !mcwsCam )
		return;
	if( MCWF_InScene() != mcwfSusp )
	{
		mcwfSusp = !mcwfSusp;
		if( mcwfSusp )
		{
			mcwsCam.Stop();
			SetVisibility( true );
			MCWF_HideItems( false );
		}
		else
		{
			r = theCamera.GetCameraRotation();         // после сцены — взгляд туда, куда смотрела её камера (как mcwf_on)
			mcwfYaw = r.Yaw;
			mcwfPitch = 0;
			mcwsCam.Run();
			MCWF_HideItems( true );
		}
	}
	if( mcwfSusp )
		return;
	mx = theInput.GetActionValue( 'GI_MouseDampX' );
	my = theInput.GetActionValue( 'GI_MouseDampY' );
	mcwfAccX += mx;
	mcwfAccY += my;
	mcwfTicks += 1;
	if( !mcwfFreeze )                              // открыт экран MC (инвентарь): мышь ведёт его курсор, не взгляд
	{
		mcwfYaw = AngleNormalize( mcwfYaw - mx * mcwfSens );
		mcwfPitch = ClampF( mcwfPitch - my * mcwfSens, -89.0, 89.0 );    // мышь вниз (+Y) — взгляд вниз
	}
	if( mcwfSrc == 1 )
	{
		age = ClampF( theGame.GetEngineTimeAsSeconds() - mcwfT, 0.0, 0.1 );
		eye = mcwfEye + mcwfVel * age;
		if( mcwfPuppet )
		{
			feet = eye - Vector( 0, 0, mcwfEyeH );
			// рассинхрон — только по горизонтали: Ведьмак переносит Геральта вбок (быстрое перемещение, сцена), а кукла
			// под землёй прямо под игроком MC (провалилась вслед за ним, 2026-10-04) — не перенос, её тянем обратно
			mcwfFar = VecDistance2D( GetWorldPosition(), feet ) > 10.0;
			if( !mcwfFar )
				TeleportWithRotation( feet, EulerAngles( 0, mcwfYaw, 0 ) );
			else
				eye = GetWorldPosition() + Vector( 0, 0, 1.75 );   // рассинхрон (быстрое перемещение, провал MC): глаз Геральта
		}
	}
	else
	{
		eye = GetWorldPosition() + Vector( 0, 0, 1.75 );
	}
	MCWF_Place( eye, mcwfYaw, mcwfPitch );
	if( mcwmRide > 0 && mcwfSrc == 1 )
		MCWM_HorsePlace( mcwmRide - 1, eye + mcwmRideOff, mcwmRideH, mcwmRideRx, mcwmRideFlags );   // лошадь — от этого же глаза
	if( mcwfTicks % 30 == 0 )
	{
		MCWF_HideItems( true );
		if( !GetVisibility() )
			SetVisibility( true );                   // актёр видим для ИИ (см. mcwf_on); прежние версии его прятали
	}
}

exec function mcwf_on( seq : int, on : int, src : int, sens : int )
{
	var w : W3PlayerWitcher;
	var tpl : CEntityTemplate;
	var r : EulerAngles;

	w = (W3PlayerWitcher)thePlayer;
	if( !w )
	{
		MCW_Line( seq, "fp -1" );
		MCW_End( seq );
		return;
	}
	if( on == 1 )
	{
		w.mcwfSrc = src;
		w.mcwfSens = (float)sens / 100.0;
		if( src != 1 )
			w.mcwfPuppet = false;               // кукла — только пока глаз от Майнкрафта (mcwf_mc)
		if( !w.mcwsCam )
		{
			tpl = (CEntityTemplate)LoadResource( "gameplay\poster\poster_camera.w2ent", true );
			if( tpl )
				w.mcwsCam = (CStaticCamera)theGame.CreateEntity( tpl, w.GetWorldPosition(), w.GetWorldRotation() );
		}
		if( w.mcwsCam && !w.mcwfOn )
		{
			r = theCamera.GetCameraRotation();
			w.mcwfYaw = r.Yaw;
			w.mcwfPitch = 0;
			w.mcwfTicks = 0;
			w.mcwfAccX = 0;
			w.mcwfAccY = 0;
			w.mcwfEye = w.GetWorldPosition() + Vector( 0, 0, 1.75 );
			w.mcwfVel = Vector( 0, 0, 0 );
			w.mcwfT = theGame.GetEngineTimeAsSeconds();
			w.MCWF_Place( w.mcwfEye, w.mcwfYaw, w.mcwfPitch );
			w.mcwsCam.activationDuration = 0;
			w.mcwsCam.deactivationDuration = 0;
			w.mcwsCam.Run();
			w.mcwfOn = true;
			// тело Геральта не в кадре, как у SkyCraft: у него всё тело — предметы (голова, тело, волосы, броня, мечи), их
			// и прячем. Сам актёр остаётся видимым: SetVisibility( false ) делала его невидимым и для ИИ — деревья
			// поведения NPC проверяют видимость цели (CBTCondIsVisible, GetCombatTarget().GetVisibility(),
			// V\game\behavior_tree\conditions\btCondIsVisible.ws:61), и враги ходили рядом, не нападая (2026-10-04)
			w.SetVisibility( true );
			w.MCWF_HideItems( true );
			w.AddTimer( 'MCWF_Tick', 0, true );
		}
	}
	else
	{
		w.RemoveTimer( 'MCWF_Tick' );
		w.mcwfOn = false;
		w.mcwfPuppet = false;
		w.mcwfFreeze = false;
		w.mcwfSusp = false;
		w.SetVisibility( true );
		w.MCWF_HideItems( false );
		if( w.mcwsCam )
		{
			w.mcwsCam.Stop();
			w.mcwsCam.Destroy();
		}
	}
	MCW_Line( seq, "fp " + IntToString( on ) + " " + IntToString( w.mcwfSrc ) + " " + MCW_X100( w.mcwfYaw ) + " "
		+ MCW_X100( w.mcwfPitch ) );
	MCW_End( seq );
}

exec function mcwf_eye( seq : int, x : int, y : int, z : int, vx : int, vy : int, vz : int )
{
	var w : W3PlayerWitcher;

	w = (W3PlayerWitcher)thePlayer;
	if( !w )
	{
		MCW_Line( seq, "look -1 0 0" );
		MCW_End( seq );
		return;
	}
	w.mcwfEye = Vector( (float)x / 100.0, (float)y / 100.0, (float)z / 100.0, 1.0 );
	w.mcwfVel = Vector( (float)vx / 100.0, (float)vy / 100.0, (float)vz / 100.0, 0.0 );
	w.mcwfT = theGame.GetEngineTimeAsSeconds();
	MCW_Line( seq, "look " + MCW_X100( w.mcwfYaw ) + " " + MCW_X100( w.mcwfPitch ) + " " + IntToString( w.mcwfTicks ) );
	MCW_End( seq );
}

// Мышь и бой — у Майнкрафта (клиент MC, mcclient\): ЛКМ Ведьмака — лёгкий удар и особая атака, ПКМ — ведьмачье
// чутьё или блок, СКМ — бросок, Q — знак, 1/2 — мечи (Documents\The Witcher 3\input.settings). Взаимодействия (E:
// двери, NPC, сундуки) остаются Ведьмаку: E уходит в MC, только когда подсказки нет (mcwf_interact). Уровень on:
// 0 — снять свои блокировки, 1 — блокировать бой, знаки и мечи, 2 — ещё и движение (ходит клиент MC, Геральт — кукла). Источник 'MCWF', снимаются только свои
// (BlockAction/UnblockAction, V\game\player\player.ws:314).
//   mcwf_block(seq, on)           fpblock <on>
exec function mcwf_block( seq : int, on : int )
{
	var w : W3PlayerWitcher;
	var acts, moves : array< EInputActionBlock >;
	var i : int;

	w = (W3PlayerWitcher)thePlayer;
	if( !w )
	{
		MCW_Line( seq, "fpblock -1" );
		MCW_End( seq );
		return;
	}
	acts.PushBack( EIAB_LightAttacks );
	acts.PushBack( EIAB_HeavyAttacks );
	acts.PushBack( EIAB_SpecialAttackLight );
	acts.PushBack( EIAB_SpecialAttackHeavy );
	acts.PushBack( EIAB_SwordAttack );
	acts.PushBack( EIAB_Fists );
	acts.PushBack( EIAB_Parry );
	acts.PushBack( EIAB_Counter );
	acts.PushBack( EIAB_ExplorationFocus );
	acts.PushBack( EIAB_ThrowBomb );
	acts.PushBack( EIAB_Crossbow );
	acts.PushBack( EIAB_UsableItem );
	acts.PushBack( EIAB_Signs );                     // Q — бросить предмет в MC, а не знак
	acts.PushBack( EIAB_DrawWeapon );                // 1, 2 — слоты хотбара MC, а не мечи
	acts.PushBack( EIAB_CallHorse );                 // X — лошадь Майнкрафта (мост: horse), а не Плотва
	moves.PushBack( EIAB_Movement );
	moves.PushBack( EIAB_Jump );
	moves.PushBack( EIAB_RunAndSprint );
	moves.PushBack( EIAB_Sprint );
	moves.PushBack( EIAB_Dodge );
	moves.PushBack( EIAB_Roll );
	moves.PushBack( EIAB_Climb );
	moves.PushBack( EIAB_Slide );
	moves.PushBack( EIAB_Explorations );
	for( i = 0; i < acts.Size(); i += 1 )
	{
		if( on >= 1 )
			w.BlockAction( acts[i], 'MCWF' );
		else
			w.UnblockAction( acts[i], 'MCWF' );
	}
	for( i = 0; i < moves.Size(); i += 1 )
	{
		if( on >= 2 )
			w.BlockAction( moves[i], 'MCWF' );
		else
			w.UnblockAction( moves[i], 'MCWF' );
	}
	MCW_Line( seq, "fpblock " + IntToString( on ) );
	MCW_End( seq );
}

exec function mcwf_mc( seq : int, x : int, y : int, z : int, vx : int, vy : int, vz : int, eyeh : int )
{
	var w : W3PlayerWitcher;
	var far : int;

	w = (W3PlayerWitcher)thePlayer;
	if( !w )
	{
		MCW_Line( seq, "look -1 0 0 0" );
		MCW_End( seq );
		return;
	}
	w.mcwfEye = Vector( (float)x / 100.0, (float)y / 100.0, (float)z / 100.0, 1.0 );
	w.mcwfVel = Vector( (float)vx / 100.0, (float)vy / 100.0, (float)vz / 100.0, 0.0 );
	w.mcwfT = theGame.GetEngineTimeAsSeconds();
	w.mcwfEyeH = (float)eyeh / 100.0;
	w.mcwfPuppet = w.mcwfSrc == 1;
	far = 0;
	if( w.mcwfFar )
		far = 1;
	MCW_Line( seq, "look " + MCW_X100( w.mcwfYaw ) + " " + MCW_X100( w.mcwfPitch ) + " " + IntToString( w.mcwfTicks ) + " "
		+ IntToString( far ) );
	MCW_End( seq );
}

//   mcwf_freeze(seq, on)          fpfreeze <on>      взгляд не двигается от мыши (открыт экран Майнкрафта)
exec function mcwf_freeze( seq : int, on : int )
{
	var w : W3PlayerWitcher;

	w = (W3PlayerWitcher)thePlayer;
	if( !w )
	{
		MCW_Line( seq, "fpfreeze -1" );
		MCW_End( seq );
		return;
	}
	w.mcwfFreeze = on == 1;
	MCW_Line( seq, "fpfreeze " + IntToString( on ) );
	MCW_End( seq );
}

exec function mcwf_fov( seq : int, fov : int )
{
	var w : W3PlayerWitcher;
	var cur : float;

	w = (W3PlayerWitcher)thePlayer;
	cur = -1;
	if( w && w.mcwsCam )
	{
		if( fov > 0 )
			w.mcwsCam.SetFov( (float)fov / 100.0 );
		cur = w.mcwsCam.GetFov();
	}
	MCW_Line( seq, "fov " + MCW_X100( cur ) + " " + MCW_X100( theCamera.GetFov() ) );
	MCW_End( seq );
}

exec function mcwf_state( seq : int )
{
	var w : W3PlayerWitcher;
	var on, susp : int;

	w = (W3PlayerWitcher)thePlayer;
	if( !w )
	{
		MCW_Line( seq, "fpstate -1" );
		MCW_End( seq );
		return;
	}
	on = 0;
	if( w.mcwfOn )
		on = 1;
	susp = 0;
	if( w.mcwfOn && w.mcwfSusp )
		susp = 1;
	MCW_Line( seq, "fpstate " + IntToString( on ) + " " + IntToString( w.mcwfSrc ) + " " + MCW_X100( w.mcwfYaw ) + " "
		+ MCW_X100( w.mcwfPitch ) + " " + IntToString( w.mcwfTicks ) + " " + MCW_X100( w.mcwfAccX ) + " "
		+ MCW_X100( w.mcwfAccY ) + " " + IntToString( susp ) );
	MCW_End( seq );
}

// Подсказка взаимодействия Ведьмака сейчас: CInteractionsManager.GetActiveInteraction (V\game\interactionsManager.ws:131).
// Есть — E обрабатывает сам Ведьмак (дверь, разговор, сундук), нет — мост отдаёт E инвентарю Майнкрафта.
exec function mcwf_interact( seq : int )
{
	var mgr : CInteractionsManager;
	var on : int;

	on = 0;
	mgr = theGame.GetInteractionsManager();
	if( mgr && mgr.GetActiveInteraction() )
		on = 1;
	MCW_Line( seq, "interact " + IntToString( on ) );
	MCW_End( seq );
}


// ----------------------------------------------------------------------------- проба: растворение новой сущности
// Блок шаблона t (как в mcw_batch) в 2,5 м перед камерой с тегом MCW_FADETEST; hidden 1 — сразу спрятать (immediate).
// mcw_fadeshow: 1 — показать, 2 — перенести на 2,5 м перед камерой и показать, 0 — спрятать (immediate), -1 — удалить
// все пробные. Вопрос: растворяется ли сущность,
// созданная заранее и показанная позже (тогда новые блоки можно брать из пула), или только при создании.
//   mcw_fadetest(seq, t, hidden)  fadetest <ok>
//   mcw_fadeshow(seq, show)       fadeshow <n>
exec function mcw_fadetest( seq : int, t : int, hidden : int )
{
	var ent : CEntity;
	var tags : array<name>;
	var ok : int;

	tags.PushBack( 'MCW_FADETEST' );
	ok = 0;
	ent = theGame.CreateEntity( MCW_TemplateByIndex( t ), theCamera.GetCameraPosition() + theCamera.GetCameraDirection() * 2.5,
		EulerAngles( 0, 0, 0 ), false, false, true, PM_DontPersist, tags );
	if( ent )
	{
		ok = 1;
		if( hidden == 1 )
			ent.SetHideInGame( true, true );
	}
	MCW_Line( seq, "fadetest " + IntToString( ok ) );
	MCW_End( seq );
}

exec function mcw_fadeshow( seq : int, show : int )
{
	var ents : array<CEntity>;
	var i : int;

	theGame.GetEntitiesByTag( 'MCW_FADETEST', ents );
	for( i = 0; i < ents.Size(); i += 1 )
	{
		if( show == 1 )
			ents[i].SetHideInGame( false, true );
		else if( show == 2 )
		{
			// из «парковки»: телепорт скрытого блока на 2,5 м перед камерой и показ в том же кадре
			ents[i].TeleportWithRotation( theCamera.GetCameraPosition() + theCamera.GetCameraDirection() * 2.5, EulerAngles( 0, 0, 0 ) );
			ents[i].SetHideInGame( false, true );
		}
		else if( show == 0 )
			ents[i].SetHideInGame( true, true );
		else
			ents[i].Destroy();
	}
	MCW_Line( seq, "fadeshow " + IntToString( ents.Size() ) );
	MCW_End( seq );
}
