// Модели из коробок Minecraft (мод modMCWModels, tools\build_models.py): тело Стива со скином на костях Геральта
// (катсцены, диалоги, вид от третьего лица) и лошадь MC по частям (поза — от клиента MC через мост).
//
//   mcwm_skin(seq, on)            skin <on> <частей>     тело Стива: 1 — надеть (идемпотентно), 0 — снять
//   mcwm_horse(seq, slot, x, y, z, yaw, head, walk, speed, flags)   horse <slot> <ok>
//                                 лошадь slot 0..3: ноги (см), курс W3 (сотые градуса), наклон головы MC (сотые
//                                 градуса, вниз +), фаза и сила шага MC (×1000), flags: 1 видна, 2 седло, 4 под всадником
//                                 8 — x, y, z: ноги от глаза игрока MC (под всадником; ставит MCWF_Tick от глаза камеры)
//
// Части — отдельные сущности с одной коробкой. Тело: пивот — сустав кости, ось «вдоль» — от сустава-цели к пивоту
// (вверх по конечности), «вперёд» — курс Геральта; прикреплены CreateAttachmentAtBoneWS (V\engine\entity.ws:348) и
// дальше двигаются с костями сами. Пока режим Стива от первого лица — части спрятаны (камера внутри головы), в остальное
// время спрятан сам Геральт: его тело, голова, волосы, броня и мечи — предметы (MCWF_HideItems); сам актёр видимый,
// иначе вместе с ним спрятались бы и прикреплённые части.

// ----------------------------------------------------------------------------- матрицы

// Поворот сущности из осей (вправо, вперёд, вверх) — строки Matrix (V\core\math.ws:18) и MatrixGetRotation (:522).
// [проверено пробой mcwx_mat: строки RotToMatrix — RotRight, RotForward, RotUp]
function MCWM_Rot( right, fwd, up : Vector ) : EulerAngles
{
	var m : Matrix;

	m.X = Vector( right.X, right.Y, right.Z, 0.0 );
	m.Y = Vector( fwd.X, fwd.Y, fwd.Z, 0.0 );
	m.Z = Vector( up.X, up.Y, up.Z, 0.0 );
	m.W = Vector( 0.0, 0.0, 0.0, 1.0 );
	return MatrixGetRotation( m );
}

// Проба соглашений: строки RotToMatrix против RotRight/RotForward/RotUp и обратный путь через MCWM_Rot.
exec function mcwx_mat( seq : int, pitch : int, yaw : int, roll : int )
{
	var r, r2 : EulerAngles;
	var m : Matrix;
	var f, rt, u : Vector;

	r = EulerAngles( (float)pitch, (float)yaw, (float)roll );
	m = RotToMatrix( r );
	MCW_Line( seq, "mX " + MCW_X100( m.X.X ) + " " + MCW_X100( m.X.Y ) + " " + MCW_X100( m.X.Z ) );
	MCW_Line( seq, "mY " + MCW_X100( m.Y.X ) + " " + MCW_X100( m.Y.Y ) + " " + MCW_X100( m.Y.Z ) );
	MCW_Line( seq, "mZ " + MCW_X100( m.Z.X ) + " " + MCW_X100( m.Z.Y ) + " " + MCW_X100( m.Z.Z ) );
	f = RotForward( r );
	rt = RotRight( r );
	u = RotUp( r );
	MCW_Line( seq, "fwd " + MCW_X100( f.X ) + " " + MCW_X100( f.Y ) + " " + MCW_X100( f.Z ) );
	MCW_Line( seq, "right " + MCW_X100( rt.X ) + " " + MCW_X100( rt.Y ) + " " + MCW_X100( rt.Z ) );
	MCW_Line( seq, "up " + MCW_X100( u.X ) + " " + MCW_X100( u.Y ) + " " + MCW_X100( u.Z ) );
	r2 = MCWM_Rot( rt, f, u );
	MCW_Line( seq, "back " + MCW_X100( r2.Pitch ) + " " + MCW_X100( r2.Yaw ) + " " + MCW_X100( r2.Roll ) );
	MCW_End( seq );
}

// ----------------------------------------------------------------------------- тело Стива

@addField(W3PlayerWitcher)
public var mcwmSkin : bool;

@addField(W3PlayerWitcher)
public var mcwmParts : array<CEntity>;

@addField(W3PlayerWitcher)
public var mcwmShown : int;                   // -1 не знаем, 0 части спрятаны, 1 видны

@addField(W3PlayerWitcher)
public var mcwmTicks : int;

// Части: шаблон (путь целиком: строка не может кончаться обратной косой), кость крепления, кость пивота, кость-цель (ось «вдоль»).
function MCWM_SkinPart( i : int, out part : string, out bone : name, out pivot : name, out target : name ) : bool
{
	switch( i )
	{
		case 0: part = "mcw\models\steve\head.w2ent"; bone = 'head'; pivot = 'head'; target = 'neck'; return true;
		case 1: part = "mcw\models\steve\hat.w2ent"; bone = 'head'; pivot = 'head'; target = 'neck'; return true;
		case 2: part = "mcw\models\steve\body.w2ent"; bone = 'torso3'; pivot = 'neck'; target = 'pelvis'; return true;
		case 3: part = "mcw\models\steve\jacket.w2ent"; bone = 'torso3'; pivot = 'neck'; target = 'pelvis'; return true;
		case 4: part = "mcw\models\steve\r_arm1.w2ent"; bone = 'r_bicep'; pivot = 'r_bicep'; target = 'r_forearm'; return true;
		case 5: part = "mcw\models\steve\r_sleeve1.w2ent"; bone = 'r_bicep'; pivot = 'r_bicep'; target = 'r_forearm'; return true;
		case 6: part = "mcw\models\steve\r_arm2.w2ent"; bone = 'r_forearm'; pivot = 'r_forearm'; target = 'r_hand'; return true;
		case 7: part = "mcw\models\steve\r_sleeve2.w2ent"; bone = 'r_forearm'; pivot = 'r_forearm'; target = 'r_hand'; return true;
		case 8: part = "mcw\models\steve\l_arm1.w2ent"; bone = 'l_bicep'; pivot = 'l_bicep'; target = 'l_forearm'; return true;
		case 9: part = "mcw\models\steve\l_sleeve1.w2ent"; bone = 'l_bicep'; pivot = 'l_bicep'; target = 'l_forearm'; return true;
		case 10: part = "mcw\models\steve\l_arm2.w2ent"; bone = 'l_forearm'; pivot = 'l_forearm'; target = 'l_hand'; return true;
		case 11: part = "mcw\models\steve\l_sleeve2.w2ent"; bone = 'l_forearm'; pivot = 'l_forearm'; target = 'l_hand'; return true;
		case 12: part = "mcw\models\steve\r_leg1.w2ent"; bone = 'r_thigh'; pivot = 'r_thigh'; target = 'r_shin'; return true;
		case 13: part = "mcw\models\steve\r_pants1.w2ent"; bone = 'r_thigh'; pivot = 'r_thigh'; target = 'r_shin'; return true;
		case 14: part = "mcw\models\steve\r_leg2.w2ent"; bone = 'r_shin'; pivot = 'r_shin'; target = 'r_foot'; return true;
		case 15: part = "mcw\models\steve\r_pants2.w2ent"; bone = 'r_shin'; pivot = 'r_shin'; target = 'r_foot'; return true;
		case 16: part = "mcw\models\steve\l_leg1.w2ent"; bone = 'l_thigh'; pivot = 'l_thigh'; target = 'l_shin'; return true;
		case 17: part = "mcw\models\steve\l_pants1.w2ent"; bone = 'l_thigh'; pivot = 'l_thigh'; target = 'l_shin'; return true;
		case 18: part = "mcw\models\steve\l_leg2.w2ent"; bone = 'l_shin'; pivot = 'l_shin'; target = 'l_foot'; return true;
		case 19: part = "mcw\models\steve\l_pants2.w2ent"; bone = 'l_shin'; pivot = 'l_shin'; target = 'l_foot'; return true;
	}
	return false;
}

// Создать и прикрепить части по текущей позе. Ось «вверх» части — от цели к пивоту, «вперёд» — курс Геральта без
// составляющей вдоль оси (если почти параллельны — вперёд мира), «вправо» = вперёд × вверх.
@addMethod(W3PlayerWitcher)
function MCWM_SkinOn() : int
{
	var part : string;
	var bone, pivot, target : name;
	var tpl : CEntityTemplate;
	var ent : CEntity;
	var p, t, up, fwd, right : Vector;
	var r : EulerAngles;
	var tags : array<name>;
	var i, n : int;

	n = 0;
	tags.PushBack( 'MCW_SKIN' );
	for( i = 0; MCWM_SkinPart( i, part, bone, pivot, target ); i += 1 )
	{
		tpl = (CEntityTemplate)LoadResource( part, true );
		if( !tpl )
			continue;
		p = GetBoneWorldPosition( pivot );
		t = GetBoneWorldPosition( target );
		up = VecNormalize( p - t );
		fwd = VecFromHeading( GetHeading() );
		fwd = fwd - up * VecDot( fwd, up );
		if( VecLength( fwd ) < 0.1 )
			fwd = Vector( 0.0, 1.0, 0.0 ) - up * up.Y;
		fwd = VecNormalize( fwd );
		right = VecCross( fwd, up );
		r = MCWM_Rot( right, fwd, up );
		ent = theGame.CreateEntity( tpl, p, r, true, false, true, PM_DontPersist, tags );
		if( !ent )
			continue;
		if( !ent.CreateAttachmentAtBoneWS( this, bone, p, r ) )
		{
			ent.Destroy();
			continue;
		}
		mcwmParts.PushBack( ent );
		n += 1;
	}
	mcwmSkin = n > 0;
	mcwmShown = -1;
	if( mcwmSkin )
		AddTimer( 'MCWM_Tick', 0.1, true );
	return n;
}

@addMethod(W3PlayerWitcher)
function MCWM_SkinOff()
{
	var i : int;

	RemoveTimer( 'MCWM_Tick' );
	for( i = 0; i < mcwmParts.Size(); i += 1 )
	{
		if( mcwmParts[i] )
			mcwmParts[i].Destroy();
	}
	mcwmParts.Clear();
	mcwmSkin = false;
	if( !mcwfOn || mcwfSusp )
	{
		SetVisibility( true );
		MCWF_HideItems( false );
	}
}

// Раз в 0,1 с: в режиме Стива от первого лица части спрятаны (Геральта прячет сам режим), иначе видны, а у Геральта
// спрятаны предметы — тело, голова, волосы, броня, мечи (MCWF_HideItems; сцены и mcwf_on(0) показывают их снова).
// Сам актёр — видимый: SetVisibility( false ) прячет и всё, что прикреплено к его костям, то есть тело Стива
// [проверено 2026-10-03, mcwx_skinvis].
@addMethod(W3PlayerWitcher)
timer function MCWM_Tick( dt : float, id : int )
{
	var want, i : int;

	if( !mcwmSkin )
		return;
	want = 1;
	if( mcwfOn && !mcwfSusp )
		want = 0;
	if( want != mcwmShown )
	{
		for( i = 0; i < mcwmParts.Size(); i += 1 )
		{
			if( mcwmParts[i] )
				mcwmParts[i].SetHideInGame( want == 0 );
		}
		mcwmShown = want;
	}
	mcwmTicks += 1;
	if( want == 1 )
	{
		if( !GetVisibility() )
			SetVisibility( true );
		if( mcwmTicks % 3 == 0 )
			MCWF_HideItems( true );
	}
}

exec function mcwm_skin( seq : int, on : int )
{
	var w : W3PlayerWitcher;
	var n : int;

	w = (W3PlayerWitcher)thePlayer;
	if( !w )
	{
		MCW_Line( seq, "skin -1 0" );
		MCW_End( seq );
		return;
	}
	if( on == 1 && !w.mcwmSkin )
		w.MCWM_SkinOn();
	else if( on == 0 && w.mcwmSkin )
		w.MCWM_SkinOff();
	n = w.mcwmParts.Size();
	if( w.mcwmSkin )
		MCW_Line( seq, "skin 1 " + IntToString( n ) );
	else
		MCW_Line( seq, "skin 0 " + IntToString( n ) );
	MCW_End( seq );
}

// ----------------------------------------------------------------------------- лошадь MC

// Части лошади: шаблон mcw\models\horse\<имя>.w2ent и группа вращения 0 body, 1 tail, 2 шея с головой, 3..6 ноги
// (левая задняя, правая задняя, левая передняя, правая передняя). Пивоты групп — м в осях лошади от ног
// (build_models.py horse_parts, models.json "horse_groups"), x вправо, y вперёд, z вверх.
function MCWM_HorsePart( i : int, out part : string, out grp : int, out saddle : bool ) : bool
{
	saddle = false;
	switch( i )
	{
		case 0: part = "mcw\models\horse\body.w2ent"; grp = 0; return true;
		case 1: part = "mcw\models\horse\tail.w2ent"; grp = 1; return true;
		case 2: part = "mcw\models\horse\head_parts.w2ent"; grp = 2; return true;
		case 3: part = "mcw\models\horse\head.w2ent"; grp = 2; return true;
		case 4: part = "mcw\models\horse\left_ear.w2ent"; grp = 2; return true;
		case 5: part = "mcw\models\horse\right_ear.w2ent"; grp = 2; return true;
		case 6: part = "mcw\models\horse\mane.w2ent"; grp = 2; return true;
		case 7: part = "mcw\models\horse\upper_mouth.w2ent"; grp = 2; return true;
		case 8: part = "mcw\models\horse\left_hind_leg.w2ent"; grp = 3; return true;
		case 9: part = "mcw\models\horse\right_hind_leg.w2ent"; grp = 4; return true;
		case 10: part = "mcw\models\horse\left_front_leg.w2ent"; grp = 5; return true;
		case 11: part = "mcw\models\horse\right_front_leg.w2ent"; grp = 6; return true;
		case 12: part = "mcw\models\horse\saddle.w2ent"; grp = 0; saddle = true; return true;
		case 13: part = "mcw\models\horse\head_saddle.w2ent"; grp = 2; saddle = true; return true;
		case 14: part = "mcw\models\horse\mouth_saddle_wrap.w2ent"; grp = 2; saddle = true; return true;
		case 15: part = "mcw\models\horse\left_saddle_mouth.w2ent"; grp = 2; saddle = true; return true;
		case 16: part = "mcw\models\horse\right_saddle_mouth.w2ent"; grp = 2; saddle = true; return true;
	}
	return false;
}

// Пивот группы (м, оси лошади) — из mc_models.json: пивот модели p (px) -> корень 1.1p + (0, -2.4016, 0) ->
// (-x, -z, 24 - y) / 16. Хвост — от пивота тела.
function MCWM_HorsePivot( grp : int ) : Vector
{
	switch( grp )
	{
		case 0: return Vector( 0.0, -0.34375, 0.893850 );            // body (0, 11, 5)
		case 1: return Vector( 0.0, -0.48125, 1.237600 );            // tail (0, -5, 2) от body
		case 2: return Vector( 0.0, 0.825, 1.375100 );               // head_parts (0, 4, -12)
		case 3: return Vector( -0.275, -0.48125, 0.687600 );         // left_hind_leg (4, 14, 7)
		case 4: return Vector( 0.275, -0.48125, 0.687600 );          // right_hind_leg (-4, 14, 7)
		case 5: return Vector( -0.275, 0.6875, 0.687600 );           // left_front_leg (4, 14, -10)
		case 6: return Vector( 0.275, 0.6875, 0.687600 );            // right_front_leg (-4, 14, -10)
	}
	return Vector( 0, 0, 0 );
}

@addField(W3PlayerWitcher)
public var mcwmHorse : array<CEntity>;        // 4 слота по 17 частей, слот s — [17s, 17s + 16]

// Лошадь под всадником (flags 8 у mcwm_horse): её ставит таймер вида от первого лица (MCWF_Tick) каждый кадр от того
// же глаза, что и камеру, — ноги = глаз + mcwmRideOff. Иначе камера шла по экстраполированному глазу, а лошадь — по
// последнему вызову моста, и на скаку они расходились туда-сюда на десятки см (2026-10-04, «лошадь трясёт»).
@addField(W3PlayerWitcher)
public var mcwmRide : int;                    // слот + 1; 0 — никто не едет

@addField(W3PlayerWitcher)
public var mcwmRideOff : Vector;

@addField(W3PlayerWitcher)
public var mcwmRideH : float;

@addField(W3PlayerWitcher)
public var mcwmRideRx : array<float>;

@addField(W3PlayerWitcher)
public var mcwmRideFlags : int;

// Поворот группы: курс h (градусы W3) и наклон вокруг оси x лошади на phi (радианы, правая тройка: +phi задирает
// перёд вверх). Оси: X' = (cos h, sin h, 0), Y' = (-cos phi sin h, cos phi cos h, sin phi), Z' = X' × Y'.
function MCWM_HorseRot( h : float, phi : float, out fwd : Vector, out up : Vector ) : EulerAngles
{
	var ch, sh, cp, sp : float;
	var right : Vector;

	ch = CosF( Deg2Rad( h ) );
	sh = SinF( Deg2Rad( h ) );
	cp = CosF( phi );
	sp = SinF( phi );
	right = Vector( ch, sh, 0.0 );
	fwd = Vector( -cp * sh, cp * ch, sp );
	up = Vector( sp * sh, -sp * ch, cp );
	return MCWM_Rot( right, fwd, up );
}

// Части лошади слота: ноги feet, курс h (градусы W3), углы групп rx (радианы модели MC), flags как у mcwm_horse.
@addMethod(W3PlayerWitcher)
function MCWM_HorsePlace( slot : int, feet : Vector, h : float, rx : array<float>, flags : int )
{
	var part : string;
	var grp, i, k : int;
	var saddle, show : bool;
	var tpl : CEntityTemplate;
	var ent : CEntity;
	var tags : array<name>;
	var piv, f, u : Vector;
	var ch, sh : float;
	var rot : array<EulerAngles>;
	var gpos : array<Vector>;

	while( mcwmHorse.Size() < 68 )
		mcwmHorse.PushBack( NULL );
	ch = CosF( Deg2Rad( h ) );
	sh = SinF( Deg2Rad( h ) );
	for( k = 0; k < 7; k += 1 )
	{
		piv = MCWM_HorsePivot( k );
		gpos.PushBack( feet + Vector( piv.X * ch - piv.Y * sh, piv.X * sh + piv.Y * ch, piv.Z ) );
		// rx модели (конец части назад) = поворот вокруг x лошади на -rx (меш: перёд +Y, верх +Z)
		rot.PushBack( MCWM_HorseRot( h, -rx[k], f, u ) );
	}
	tags.PushBack( 'MCW_HORSE' );
	for( i = 0; MCWM_HorsePart( i, part, grp, saddle ); i += 1 )
	{
		show = ( flags & 1 ) != 0 && ( !saddle || ( flags & 2 ) != 0 );
		ent = mcwmHorse[slot * 17 + i];
		if( !ent && show )
		{
			tpl = (CEntityTemplate)LoadResource( part, true );
			if( tpl )
				ent = theGame.CreateEntity( tpl, gpos[grp], rot[grp], true, false, true, PM_DontPersist, tags );
			mcwmHorse[slot * 17 + i] = ent;
		}
		if( !ent )
			continue;
		ent.SetHideInGame( !show );
		if( show )
			ent.TeleportWithRotation( gpos[grp], rot[grp] );
	}
}

// flags 8: x, y, z — ноги лошади от глаза игрока MC (см, оси W3), лошадь под всадником: ставит MCWF_Tick от глаза
// камеры каждый кадр (mcwmRide), сейчас — от последнего глаза моста.
exec function mcwm_horse( seq : int, slot : int, x : int, y : int, z : int, yaw : int, head : int, walk : int,
	speed : int, flags : int )
{
	var w : W3PlayerWitcher;
	var feet : Vector;
	var h, pos, spd, o, phi : float;
	var rx : array<float>;

	w = (W3PlayerWitcher)thePlayer;
	if( !w || slot < 0 || slot > 3 )
	{
		MCW_Line( seq, "horse " + IntToString( slot ) + " 0" );
		MCW_End( seq );
		return;
	}
	feet = Vector( (float)x / 100.0, (float)y / 100.0, (float)z / 100.0, 1.0 );
	h = (float)yaw / 100.0;
	pos = (float)walk / 1000.0;
	spd = (float)speed / 1000.0;
	// углы групп в радианах модели MC (rx: + — конец части назад): mc_models.json anim.horse
	o = CosF( 0.6662 * pos + Pi() );
	rx.PushBack( 0.0 );                                                  // body
	rx.PushBack( 0.5235988 + 0.75 * spd );                               // tail
	phi = 0.5235988 + Deg2Rad( (float)head / 100.0 );
	if( spd > 0.2 )
		phi += 0.15 * spd * CosF( 0.8 * pos );
	rx.PushBack( phi );                                                  // шея с головой
	rx.PushBack( -0.5 * o * spd );                                       // левая задняя
	rx.PushBack( 0.5 * o * spd );                                        // правая задняя
	rx.PushBack( 0.8 * o * spd );                                        // левая передняя
	rx.PushBack( -0.8 * o * spd );                                       // правая передняя
	if( ( flags & 8 ) != 0 && w.mcwfOn && w.mcwfSrc == 1 )
	{
		w.mcwmRide = slot + 1;
		w.mcwmRideOff = Vector( feet.X, feet.Y, feet.Z, 0.0 );
		w.mcwmRideH = h;
		w.mcwmRideRx = rx;
		w.mcwmRideFlags = flags;
		feet = w.mcwfEye + w.mcwmRideOff;
	}
	else
	{
		if( w.mcwmRide == slot + 1 )
			w.mcwmRide = 0;
		if( ( flags & 8 ) != 0 )
			feet = w.mcwfEye + Vector( feet.X, feet.Y, feet.Z, 0.0 );   // вид от первого лица выключен: от глаза моста
	}
	w.MCWM_HorsePlace( slot, feet, h, rx, flags );
	MCW_Line( seq, "horse " + IntToString( slot ) + " 1" );
	MCW_End( seq );
}

// Проба: где части тела Стива относительно ног Геральта (см) и прикреплены ли.
exec function mcwx_skinpos( seq : int )
{
	var w : W3PlayerWitcher;
	var p, q : Vector;
	var i : int;

	w = (W3PlayerWitcher)thePlayer;
	p = w.GetWorldPosition();
	MCW_Line( seq, "skin " + IntToString( (int)w.mcwmSkin ) + " shown " + IntToString( w.mcwmShown ) + " parts "
		+ IntToString( w.mcwmParts.Size() ) + " vis " + IntToString( (int)w.GetVisibility() ) );
	for( i = 0; i < w.mcwmParts.Size(); i += 1 )
	{
		if( !w.mcwmParts[i] )
		{
			MCW_Line( seq, "part " + IntToString( i ) + " null" );
			continue;
		}
		q = w.mcwmParts[i].GetWorldPosition();
		MCW_Line( seq, "part " + IntToString( i ) + " " + MCW_X100( q.X - p.X ) + " " + MCW_X100( q.Y - p.Y ) + " "
			+ MCW_X100( q.Z - p.Z ) + " att " + IntToString( (int)w.mcwmParts[i].HasAttachment() ) );
	}
	MCW_End( seq );
}

// Проба видимости: mode 1 — таймер скина стоп, Геральт виден; 2 — таймер стоп, Геральт спрятан; 3 — таймер снова.
exec function mcwx_skinvis( seq : int, mode : int )
{
	var w : W3PlayerWitcher;

	w = (W3PlayerWitcher)thePlayer;
	if( mode == 3 )
	{
		w.mcwmShown = -1;
		w.AddTimer( 'MCWM_Tick', 0.1, true );
	}
	else
	{
		w.RemoveTimer( 'MCWM_Tick' );
		w.SetVisibility( mode == 1 );
	}
	MCW_Line( seq, "vis " + IntToString( (int)w.GetVisibility() ) );
	MCW_End( seq );
}
