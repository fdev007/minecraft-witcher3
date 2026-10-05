// Коллизия Ведьмака для физики MC: стены, полы, двери (п. 3 «Что дальше»). Пока — пробы mcwx_* (лучи, навмеш,
// кости), по их итогам — рабочий сканер.

// Группы коллизий по битам маски: 1 Terrain, 2 Static, 4 Door, 8 Fence, 16 Platforms, 32 Destructible, 64 Foliage,
// 128 RigidBody, 256 Boat, 512 Character, 1024 Water. Имена — как в V\game (PushBack('Terrain') и т. п.).
function MCWX_Groups( mask : int ) : array<name>
{
	var g : array<name>;

	if( ( mask & 1 ) != 0 )
		g.PushBack( 'Terrain' );
	if( ( mask & 2 ) != 0 )
		g.PushBack( 'Static' );
	if( ( mask & 4 ) != 0 )
		g.PushBack( 'Door' );
	if( ( mask & 8 ) != 0 )
		g.PushBack( 'Fence' );
	if( ( mask & 16 ) != 0 )
		g.PushBack( 'Platforms' );
	if( ( mask & 32 ) != 0 )
		g.PushBack( 'Destructible' );
	if( ( mask & 64 ) != 0 )
		g.PushBack( 'Foliage' );
	if( ( mask & 128 ) != 0 )
		g.PushBack( 'RigidBody' );
	if( ( mask & 256 ) != 0 )
		g.PushBack( 'Boat' );
	if( ( mask & 512 ) != 0 )
		g.PushBack( 'Character' );
	if( ( mask & 1024 ) != 0 )
		g.PushBack( 'Water' );
	return g;
}

function MCWX_CompName( comp : CComponent ) : string
{
	var ent : CEntity;

	if( !comp )
		return "-";
	ent = comp.GetEntity();
	if( !ent )
		return comp.GetName();
	return ent.GetReadableName() + "/" + comp.GetName();
}

// Все попадания луча a -> b (см, мировые) — RayCastSync отдаёт массив (V\engine\physicsTraceManager.ws:34).
// Для сравнения — StaticTrace того же луча (первое попадание).
exec function mcwx_ray( seq : int, ax : int, ay : int, az : int, bx : int, by : int, bz : int, mask : int )
{
	var a, b, hit, n : Vector;
	var res : array<SRaycastHitResult>;
	var groups : array<name>;
	var world : CWorld;
	var i, cnt : int;

	world = theGame.GetWorld();
	groups = MCWX_Groups( mask );
	a = Vector( (float)ax / 100.0, (float)ay / 100.0, (float)az / 100.0, 1.0 );
	b = Vector( (float)bx / 100.0, (float)by / 100.0, (float)bz / 100.0, 1.0 );
	world.GetTraceManager().RayCastSync( a, b, res, groups );
	cnt = res.Size();
	MCW_Line( seq, "rays " + IntToString( cnt ) );
	for( i = 0; i < cnt; i += 1 )
	{
		MCW_Line( seq, "hit " + MCW_X100( res[i].distance ) + " " + MCW_X100( res[i].position.X ) + " "
			+ MCW_X100( res[i].position.Y ) + " " + MCW_X100( res[i].position.Z ) + " " + MCW_X100( res[i].normal.X ) + " "
			+ MCW_X100( res[i].normal.Y ) + " " + MCW_X100( res[i].normal.Z ) + " " + MCWX_CompName( res[i].component ) );
	}
	if( world.StaticTrace( a, b, hit, n, groups ) )
		MCW_Line( seq, "static " + MCW_X100( hit.X ) + " " + MCW_X100( hit.Y ) + " " + MCW_X100( hit.Z ) + " "
			+ MCW_X100( n.X ) + " " + MCW_X100( n.Y ) + " " + MCW_X100( n.Z ) );
	else
		MCW_Line( seq, "static none" );
	MCW_End( seq );
}

// Сетка колонн вокруг Геральта: half — полуширина (м), step — шаг (см). Луч сверху (+up м) вниз (до -down м) по маске;
// печатает колонны, где попаданий больше одного или верхнее выше Геральта на 1,5 м (крыша, стена, дерево).
exec function mcwx_scan( seq : int, half : int, step : int, up : int, down : int, mask : int )
{
	var p, a, b : Vector;
	var res : array<SRaycastHitResult>;
	var groups : array<name>;
	var world : CWorld;
	var tm : CScriptBatchQueryAccessor;
	var x, y, i, cnt, shown : int;
	var line : string;

	world = theGame.GetWorld();
	tm = world.GetTraceManager();
	groups = MCWX_Groups( mask );
	p = thePlayer.GetWorldPosition();
	MCW_Line( seq, "at " + MCW_X100( p.X ) + " " + MCW_X100( p.Y ) + " " + MCW_X100( p.Z ) );
	shown = 0;
	for( x = -half * 100; x <= half * 100; x += step )
	{
		for( y = -half * 100; y <= half * 100; y += step )
		{
			a = Vector( p.X + (float)x / 100.0, p.Y + (float)y / 100.0, p.Z + (float)up, 1.0 );
			b = Vector( a.X, a.Y, p.Z - (float)down, 1.0 );
			res.Clear();
			tm.RayCastSync( a, b, res, groups );
			cnt = res.Size();
			if( cnt == 0 )
				continue;
			if( cnt < 2 && res[0].position.Z < p.Z + 1.5 )
				continue;
			line = "col " + IntToString( x ) + " " + IntToString( y ) + " " + IntToString( cnt );
			for( i = 0; i < cnt && i < 6; i += 1 )
				line += " " + MCW_X100( res[i].position.Z - p.Z ) + ":" + MCW_X100( res[i].normal.Z );
			MCW_Line( seq, line );
			shown += 1;
			if( shown >= 120 )
			{
				MCW_Line( seq, "cut" );
				MCW_End( seq );
				return;
			}
		}
	}
	MCW_End( seq );
}

// Навмеш: высота пола в окне [zMin, zMax] (V\engine\world.ws:74) и проходимость отрезка (:43).
exec function mcwx_nav( seq : int, x : int, y : int, zmin : int, zmax : int )
{
	var z : float;

	if( theGame.GetWorld().NavigationComputeZ( Vector( (float)x / 100.0, (float)y / 100.0, 0.0, 1.0 ), (float)zmin / 100.0,
		(float)zmax / 100.0, z ) )
		MCW_Line( seq, "navz 1 " + MCW_X100( z ) );
	else
		MCW_Line( seq, "navz 0 0" );
	MCW_End( seq );
}

exec function mcwx_navline( seq : int, ax : int, ay : int, az : int, bx : int, by : int, bz : int, r : int )
{
	var ok : int;

	ok = 0;
	if( theGame.GetWorld().NavigationLineTest( Vector( (float)ax / 100.0, (float)ay / 100.0, (float)az / 100.0, 1.0 ),
		Vector( (float)bx / 100.0, (float)by / 100.0, (float)bz / 100.0, 1.0 ), (float)r / 100.0 ) )
		ok = 1;
	MCW_Line( seq, "navline " + IntToString( ok ) );
	MCW_End( seq );
}

// Кости Геральта: индекс и мировая точка относительно ног (см). Имена — кандидаты для тела Стива.
exec function mcwx_bones( seq : int )
{
	var names : array<name>;
	var p, b : Vector;
	var r : EulerAngles;
	var i, idx : int;

	names.PushBack( 'pelvis' );
	names.PushBack( 'torso' );
	names.PushBack( 'torso2' );
	names.PushBack( 'torso3' );
	names.PushBack( 'neck' );
	names.PushBack( 'head' );
	names.PushBack( 'l_shoulder' );
	names.PushBack( 'l_bicep' );
	names.PushBack( 'l_forearm' );
	names.PushBack( 'l_hand' );
	names.PushBack( 'r_shoulder' );
	names.PushBack( 'r_bicep' );
	names.PushBack( 'r_forearm' );
	names.PushBack( 'r_hand' );
	names.PushBack( 'l_thigh' );
	names.PushBack( 'l_shin' );
	names.PushBack( 'l_foot' );
	names.PushBack( 'r_thigh' );
	names.PushBack( 'r_shin' );
	names.PushBack( 'r_foot' );
	p = thePlayer.GetWorldPosition();
	MCW_Line( seq, "yaw " + MCW_X100( thePlayer.GetHeading() ) );
	for( i = 0; i < names.Size(); i += 1 )
	{
		idx = thePlayer.GetBoneIndex( names[i] );
		if( idx < 0 )
		{
			MCW_Line( seq, "bone " + NameToString( names[i] ) + " -1" );
			continue;
		}
		thePlayer.GetBoneWorldPositionAndRotationByIndex( idx, b, r );
		MCW_Line( seq, "bone " + NameToString( names[i] ) + " " + IntToString( idx ) + " " + MCW_X100( b.X - p.X ) + " "
			+ MCW_X100( b.Y - p.Y ) + " " + MCW_X100( b.Z - p.Z ) + " " + MCW_X100( r.Pitch ) + " " + MCW_X100( r.Yaw ) + " "
			+ MCW_X100( r.Roll ) );
	}
	MCW_End( seq );
}

// Разрез: колонны от (x0,y0) до (x1,y1) с шагом step (см, относительно Геральта), в каждой — занятость клеток по z от
// zlo до zhi с шагом zstep (см от ног Геральта). Луч вниз даёт верхние грани (нормаль вверх), луч вверх — нижние;
// идём сверху из воздуха: верхняя грань — вход в твёрдое, нижняя — выход. '#' твёрдо, '.' воздух.
exec function mcwx_slice( seq : int, x0 : int, y0 : int, x1 : int, y1 : int, n : int, zlo : int, zhi : int, zstep : int,
	mask : int )
{
	var p, a, b : Vector;
	var dn, up : array<SRaycastHitResult>;
	var groups : array<name>;
	var tm : CScriptBatchQueryAccessor;
	var zs : array<float>;
	var sg : array<int>;
	var i, j, k, c, depth, best : int;
	var t, z, zc, bz : float;
	var line : string;

	tm = theGame.GetWorld().GetTraceManager();
	groups = MCWX_Groups( mask );
	p = thePlayer.GetWorldPosition();
	for( c = 0; c < n; c += 1 )
	{
		t = 0.0;
		if( n > 1 )
			t = (float)c / (float)( n - 1 );
		a = Vector( p.X + ( (float)x0 + (float)( x1 - x0 ) * t ) / 100.0, p.Y + ( (float)y0 + (float)( y1 - y0 ) * t ) / 100.0,
			p.Z + 40.0, 1.0 );
		b = Vector( a.X, a.Y, p.Z - 15.0, 1.0 );
		dn.Clear();
		up.Clear();
		tm.RayCastSync( a, b, dn, groups );
		tm.RayCastSync( b, a, up, groups );
		zs.Clear();
		sg.Clear();
		for( i = 0; i < dn.Size(); i += 1 )
		{
			if( dn[i].normal.Z > 0.02 )
			{
				zs.PushBack( dn[i].position.Z );
				sg.PushBack( 1 );
			}
		}
		for( i = 0; i < up.Size(); i += 1 )
		{
			if( up[i].normal.Z < -0.02 )
			{
				zs.PushBack( up[i].position.Z );
				sg.PushBack( -1 );
			}
		}
		line = "s ";
		for( k = 0; ( (float)zhi - (float)k * (float)zstep ) >= (float)zlo; k += 1 )
		{
			zc = p.Z + ( (float)zhi - (float)k * (float)zstep ) / 100.0;
			depth = 0;
			for( j = 0; j < zs.Size(); j += 1 )
			{
				if( zs[j] > zc )
					depth += sg[j];
			}
			if( depth > 0 )
				line += "#";
			else
				line += ".";
		}
		line += " " + IntToString( dn.Size() ) + "/" + IntToString( up.Size() );
		MCW_Line( seq, line );
	}
	MCW_End( seq );
}


// ----------------------------------------------------------------------------- сканер колонн для физики клиента MC

// Попадание в своё (блоки и мобы моста) — не коллизия Ведьмака: их столкновения клиент MC знает сам.
function MCWC_Own( comp : CComponent ) : bool
{
	var ent : CEntity;

	if( !comp )
		return false;
	ent = comp.GetEntity();
	if( !ent )
		return false;
	return ent.HasTag( 'MCW_BLOCK' ) || ent.HasTag( 'MCW_ENT' );
}

// Твёрдые промежутки подколонны (x, y) — по чётности: луч сверху вниз даёт верхние грани (нормаль вверх), снизу
// вверх — нижние; RayCastSync отдаёт все попадания, без порядка и только по граням к лучу (замер 2026-10-03,
// mcwx_slice). Идём сверху из воздуха: верхняя грань — вход в твёрдое, нижняя — выход. -> "низ,верх;..." в см от z0,
// сверху вниз; низ "-" — твёрдое до дна окна (земля). Промежутки ниже z0-8 м и выше z0+12 м не нужны.
function MCWC_Spans( tm : CScriptBatchQueryAccessor, x : float, y : float, z0 : float, groups : array<name> ) : string
{
	var dn, up : array<SRaycastHitResult>;
	var zs : array<float>;
	var sg : array<int>;
	var a, b : Vector;
	var i, j, sv, depth : int;
	var zv, top : float;
	var txt : string;

	a = Vector( x, y, z0 + 300.0, 1.0 );
	b = Vector( x, y, z0 - 60.0, 1.0 );
	tm.RayCastSync( a, b, dn, groups );
	tm.RayCastSync( b, a, up, groups );
	for( i = 0; i < dn.Size(); i += 1 )
	{
		if( dn[i].normal.Z > 0.02 && !MCWC_Own( dn[i].component ) )
		{
			zs.PushBack( dn[i].position.Z );
			sg.PushBack( 1 );
		}
	}
	for( i = 0; i < up.Size(); i += 1 )
	{
		if( up[i].normal.Z < -0.02 && !MCWC_Own( up[i].component ) )
		{
			zs.PushBack( up[i].position.Z );
			sg.PushBack( -1 );
		}
	}
	// вставками по убыванию z (событий единицы-десяток)
	for( i = 1; i < zs.Size(); i += 1 )
	{
		zv = zs[i];
		sv = sg[i];
		j = i - 1;
		while( j >= 0 && zs[j] < zv )
		{
			zs[j + 1] = zs[j];
			sg[j + 1] = sg[j];
			j -= 1;
		}
		zs[j + 1] = zv;
		sg[j + 1] = sv;
	}
	txt = "";
	depth = 0;
	top = 0.0;
	for( i = 0; i < zs.Size(); i += 1 )
	{
		if( sg[i] > 0 )
		{
			if( depth == 0 )
				top = zs[i];
			depth += 1;
		}
		else if( depth > 0 )
		{
			depth -= 1;
			if( depth == 0 && top > z0 - 8.0 && zs[i] < z0 + 12.0 )
			{
				if( txt != "" )
					txt += ";";
				txt += MCW_X100( zs[i] - z0 ) + "," + MCW_X100( top - z0 );
			}
		}
	}
	if( depth > 0 && top > z0 - 8.0 )
	{
		if( txt != "" )
			txt += ";";
		txt += "-," + MCW_X100( top - z0 );
	}
	return txt;
}

// Колонна блока MC (bx, bz). Высота земли (только Terrain) — всегда; статика (дома, заборы, деревья, двери) — шаром
// r 0,55 м по окну z0-8..z0+12 м; есть — 16 подколонн 25 см (MCWC_Spans), повтор предыдущей — "=".
//   "t bx bz h"                    без статики (h — земля, см от z0, или n)
//   "c bx bz h d s0/s1/.../s15"    со статикой: d 1 — в колонне дверь (её коллизия меняется), sK = i + 4j, где i по x
//                                  MC, j по z MC (подколонна (bx + (i + 0,5) / 4, bz + (j + 0,5) / 4))
function MCWC_Block( world : CWorld, tm : CScriptBatchQueryAccessor, bx : int, bz : int, z0 : float, gT : array<name>,
	gS : array<name>, gD : array<name>, gAll : array<name> ) : string
{
	var hit, n : Vector;
	var cx, cy : float;
	var i, j : int;
	var hs, s, prev, txt : string;

	// центр блока в Ведьмаке — coords.block_center_w: (bx + 0,5, 0,5 - bz)
	cx = (float)bx + 0.5;
	cy = 0.5 - (float)bz;
	hs = "n";
	if( world.StaticTrace( Vector( cx, cy, z0 + 300.0, 1.0 ), Vector( cx, cy, z0 - 300.0, 1.0 ), hit, n, gT ) )
		hs = MCW_X100( hit.Z - z0 );
	if( !world.SweepTest( Vector( cx, cy, z0 + 12.0, 1.0 ), Vector( cx, cy, z0 - 8.0, 1.0 ), 0.55, hit, n, gS ) )
		return "t " + IntToString( bx ) + " " + IntToString( bz ) + " " + hs;
	txt = "c " + IntToString( bx ) + " " + IntToString( bz ) + " " + hs + " ";
	if( world.SweepTest( Vector( cx, cy, z0 + 4.0, 1.0 ), Vector( cx, cy, z0 - 2.0, 1.0 ), 0.75, hit, n, gD ) )
		txt += "1 ";
	else
		txt += "0 ";
	prev = "?";
	for( j = 0; j < 4; j += 1 )
	{
		for( i = 0; i < 4; i += 1 )
		{
			s = MCWC_Spans( tm, (float)bx + ( (float)i + 0.5 ) / 4.0, 1.0 - (float)bz - ( (float)j + 0.5 ) / 4.0, z0, gAll );
			if( i + j > 0 )
				txt += "/";
			if( s == prev )
				txt += "=";
			else
				txt += s;
			prev = s;
		}
	}
	return txt;
}

// Колонны блоков для физики клиента MC: zc — ноги игрока (см), cmds "bx:bz|bx:bz|...". Ответ — строка на блок
// (MCWC_Block), "stop k" — с k-го блока не успели (ответ exec не длиннее 1999 байт), "cms <мс>" — время.
exec function mcw_cols( seq : int, zc : int, cmds : string )
{
	var list, f : array<string>;
	var gT, gS, gD, gAll : array<name>;
	var world : CWorld;
	var tm : CScriptBatchQueryAccessor;
	var i, bx, bz, t0, total : int;
	var z0 : float;
	var line : string;

	t0 = MCW_Ms();
	world = theGame.GetWorld();
	tm = world.GetTraceManager();
	gT.PushBack( 'Terrain' );
	gS.PushBack( 'Static' );
	gS.PushBack( 'Door' );
	gS.PushBack( 'Fence' );
	gS.PushBack( 'Platforms' );
	gS.PushBack( 'Foliage' );
	gD.PushBack( 'Door' );
	gAll.PushBack( 'Terrain' );
	gAll.PushBack( 'Static' );
	gAll.PushBack( 'Door' );
	gAll.PushBack( 'Fence' );
	gAll.PushBack( 'Platforms' );
	gAll.PushBack( 'Foliage' );
	z0 = (float)zc / 100.0;
	total = 40;
	list = StrSplit( cmds, "|" );
	for( i = 0; i < list.Size(); i += 1 )
	{
		f = StrSplit( list[i], ":" );
		if( f.Size() != 2 || !MCW_ParseInt( f[0], bx ) || !MCW_ParseInt( f[1], bz ) )
			continue;
		// блок со статикой — до ~650 байт: ответ целиком не длиннее 1999 (длиннее игра теряет весь)
		if( total > 1300 )
		{
			MCW_Line( seq, "stop " + IntToString( i ) );
			break;
		}
		line = MCWC_Block( world, tm, bx, bz, z0, gT, gS, gD, gAll );
		total += StrLen( line ) + 18;
		MCW_Line( seq, line );
	}
	MCW_Line( seq, "cms " + IntToString( MCW_Ms() - t0 ) );
	MCW_End( seq );
}

// Точка внутри твёрдого? SweepTest шаром r из точки на 1 см вниз (V\engine\world.ws:24) по маске групп.
exec function mcwx_inside( seq : int, x : int, y : int, z : int, r : int, mask : int )
{
	var a, hit, n : Vector;
	var groups : array<name>;

	groups = MCWX_Groups( mask );
	a = Vector( (float)x / 100.0, (float)y / 100.0, (float)z / 100.0, 1.0 );
	if( theGame.GetWorld().SweepTest( a, a - Vector( 0.0, 0.0, 0.01, 0.0 ), (float)r / 100.0, hit, n, groups ) )
		MCW_Line( seq, "inside 1 " + MCW_X100( hit.Z ) + " " + MCW_X100( n.X ) + " " + MCW_X100( n.Y ) + " " + MCW_X100( n.Z ) );
	else
		MCW_Line( seq, "inside 0" );
	MCW_End( seq );
}

// Где можно стоять под крышей: колонны (шаг step см) в прямоугольнике x0..x1, y0..y1 (см от Геральта), у которых над
// верхом промежутка «до земли» есть промежуток на высоте 1,9..6 м выше. "cov x y пол потолок" (см от ног Геральта).
exec function mcwx_covered( seq : int, x0 : int, x1 : int, y0 : int, y1 : int, step : int )
{
	var p : Vector;
	var tm : CScriptBatchQueryAccessor;
	var gAll : array<name>;
	var x, y, shown : int;
	var s : string;
	var parts, ab : array<string>;
	var floorZ, ceilZ, lo, hi : int;
	var i : int;

	tm = theGame.GetWorld().GetTraceManager();
	gAll = MCWX_Groups( 95 );
	p = thePlayer.GetWorldPosition();
	shown = 0;
	for( x = x0; x <= x1; x += step )
	{
		for( y = y0; y <= y1; y += step )
		{
			s = MCWC_Spans( tm, p.X + (float)x / 100.0, p.Y + (float)y / 100.0, p.Z, gAll );
			parts = StrSplit( s, ";" );
			if( parts.Size() < 2 )
				continue;
			// промежутки сверху вниз: последний — «до земли», предпоследний — над ним
			ab = StrSplit( parts[parts.Size() - 1], "," );
			if( ab.Size() != 2 || ab[0] != "-" || !MCW_ParseInt( ab[1], floorZ ) )
				continue;
			ab = StrSplit( parts[parts.Size() - 2], "," );
			if( ab.Size() != 2 || !MCW_ParseInt( ab[0], ceilZ ) )
				continue;
			if( ceilZ - floorZ < 190 || ceilZ - floorZ > 600 )
				continue;
			MCW_Line( seq, "cov " + IntToString( x ) + " " + IntToString( y ) + " " + IntToString( floorZ ) + " "
				+ IntToString( ceilZ ) );
			shown += 1;
			if( shown >= 60 )
			{
				MCW_Line( seq, "cut" );
				MCW_End( seq );
				return;
			}
		}
	}
	MCW_End( seq );
}

// Почему нет взаимодействия: состояние Геральта для CInteractionsManager.CanProcessGuiInteractions
// (V\game\interactionsManager.ws:10) и двери в радиусе r см (коллизия группы Door) с их CDoorComponent.
exec function mcwx_interact( seq : int, r : int )
{
	var ents : array<CEntity>;
	var groups : array<name>;
	var mgr : CInteractionsManager;
	var act : CInteractionComponent;
	var door : CDoorComponent;
	var p : Vector;
	var i, n : int;

	mgr = theGame.GetInteractionsManager();
	act = NULL;
	if( mgr )
		act = mgr.GetActiveInteraction();
	MCW_Line( seq, "player air " + IntToString( (int)thePlayer.IsInAir() ) + " caninteract "
		+ IntToString( (int)thePlayer.substateManager.CanInteract() ) + " state " + NameToString( thePlayer.GetCurrentStateName() )
		+ " allowed " + IntToString( (int)thePlayer.IsActionAllowed( EIAB_Interactions ) ) );
	if( act )
		MCW_Line( seq, "active " + act.GetName() + " " + act.GetEntity().GetReadableName() );
	else
		MCW_Line( seq, "active none" );
	p = thePlayer.GetWorldPosition();
	groups.PushBack( 'Door' );
	n = theGame.GetWorld().SphereOverlapTest( ents, p, (float)r / 100.0, groups );
	for( i = 0; i < ents.Size(); i += 1 )
	{
		door = (CDoorComponent)ents[i].GetComponentByClassName( 'CDoorComponent' );
		if( !door )
		{
			MCW_Line( seq, "ent " + ents[i].GetReadableName() + " nodoor" );
			continue;
		}
		MCW_Line( seq, "door " + ents[i].GetReadableName() + " d " + MCW_X100( VecDistance( p, ents[i].GetWorldPosition() ) )
			+ " open " + IntToString( (int)door.IsOpen() ) + " locked " + IntToString( (int)door.IsLocked() ) + " inter "
			+ IntToString( (int)door.IsInteractive() ) + " en " + IntToString( (int)door.IsEnabled() ) + " name " + door.GetName() );
	}
	MCW_End( seq );
}

// Толкнуть двери в радиусе r см от Геральта силой force (как Аард: V\game\gameplayEntity.ws:366, 3000) или AddDoorUser
// (mode 1, как NPC у толкаемой двери: V\game\gameplay\interactive\newDoor.ws:81).
exec function mcwx_push( seq : int, r : int, force : int, mode : int )
{
	var ents : array<CEntity>;
	var groups : array<name>;
	var door : CDoorComponent;
	var p : Vector;
	var i : int;

	p = thePlayer.GetWorldPosition();
	groups.PushBack( 'Door' );
	theGame.GetWorld().SphereOverlapTest( ents, p, (float)r / 100.0, groups );
	for( i = 0; i < ents.Size(); i += 1 )
	{
		door = (CDoorComponent)ents[i].GetComponentByClassName( 'CDoorComponent' );
		if( !door )
			continue;
		if( mode == 1 )
			door.AddDoorUser( thePlayer );
		else if( mode == 2 )
			door.Open( false, false );
		else
			door.AddForceImpulse( p, (float)force );
		MCW_Line( seq, "pushed " + ents[i].GetReadableName() + " open " + IntToString( (int)door.IsOpen() ) );
	}
	MCW_End( seq );
}
