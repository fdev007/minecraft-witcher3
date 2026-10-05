package mcw;

import com.google.gson.JsonObject;
import net.minecraft.commands.CommandSource;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.Identifier;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.util.Mth;
import net.minecraft.world.damagesource.DamageSource;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntitySpawnReason;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.chunk.LevelChunk;
import net.minecraft.world.phys.Vec3;

/**
 * Разбор сообщений и всё, что уходит обратно помимо снапшота. Протокол v1 — mc/protocol_examples.jsonl.
 * Ответы (ack, dig, cmd_out, ground_ok, recipes, pong, error) — тому соединению, что спросило; события мира (chunk,
 * blocks, inv, hp, ent, explosion, pteleport, death, respawn) — мосту, то есть соединению, приславшему hello.
 * Только поток сервера: Link кладёт входящие в очередь задач сервера, события Fabric приходят на нём же.
 */
final class Bridge {
	private static final BlockSync SYNC = new BlockSync();
	private static final Actions ACTIONS = new Actions();
	private static final EntSync ENTS = new EntSync();
	/** Соединение, приславшее hello; null — моста нет, мир не зеркалим. */
	private static Link.Peer session;
	/** Кто прислал обрабатываемое сейчас сообщение: ему ответы. */
	private static Link.Peer from;
	private static String lastInv = "";
	private static boolean invDirty;
	/** Inventory.getTimesChanged() на прошлой проверке: поменялся — inv сверяем в этом же тике, а не раз в 10. */
	private static int lastInvTimes = -1;
	private static float lastHp = -1.0F;
	private static int lastFood = -1;

	private Bridge() {
	}

	static BlockSync sync() {
		return SYNC;
	}

	/** Мост на связи (прислал hello): ему идут события мира. */
	static boolean hasSession() {
		return session != null;
	}

	static void handle(final Link.Peer peer, final JsonObject m) {
		if (peer.evicted) {
			return; // хвост вытесненного моста
		}

		MinecraftServer server = Mcw.server();
		ServerPlayer p = Mcw.geralt();
		int tick = server.getTickCount();
		String t = Json.s(m, "t", "");
		if (p == null && !t.equals("ping") && !t.equals("cmd")) {
			// Геральт меняется (клиент Майнкрафта входит или вышел), это тики; hello подождёт, остальное — отказ
			if (t.equals("hello")) {
				Mcw.later(() -> handle(peer, m));
			} else {
				peer.send("{\"t\":\"error\",\"tick\":" + tick + ",\"why\":\"no_geralt\",\"text\":" + Json.q(t) + "}");
			}

			return;
		}

		from = peer;
		try {
			switch (t) {
				case "hello" -> hello(peer, server, p, m, tick);
				case "pos" -> pos(p, m, tick);
				case "act" -> ACTIONS.act(peer, p, m, tick);
				case "snap" -> snap(peer, server, p, Json.i(m, "r", SYNC.active() ? SYNC.radius() : Mcw.config().radius), tick);
				case "give" -> give(p, m, tick);
				case "cmd" -> cmd(server, m, tick);
				case "ground" -> ground(p.level(), m, tick);
				case "ground_clear" -> groundClear(p.level(), m, tick);
				case "recipes" -> reply(Craft.recipes(p, m, tick));
				case "craft" -> reply(Craft.craft(p, m, tick));
				case "spawn" -> spawn(p, m, tick);
				case "horse" -> reply(Horses.summon(p, tick));
				case "npcs" -> npcs(p, m);
				case "phurt" -> Npcs.hurtPlayer(p, m);
				case "arrow_wall" -> ArrowWalls.onWall(m);
				case "npc_block" -> Npcs.blocked(p, m);
				case "ping" -> reply("{\"t\":\"pong\",\"ts\":" + m.get("ts") + ",\"tick\":" + tick + ",\"mspt\":"
					+ Json.d(server.getAverageTickTimeNanos() / 1.0E6) + ",\"players\":" + server.getPlayerList().getPlayerCount()
					+ ",\"chunks\":" + SYNC.trackedChunks() + ",\"mirror\":" + SYNC.mirrorSize() + ",\"bridge\":" + (peer == session)
					+ ",\"ents\":" + ENTS.known() + "}");
				default -> error("unknown_type", t);
			}
		} catch (RuntimeException e) {
			Mcw.LOG.warn("mcw: сообщение {} упало", m, e);
			error("exception", t + ": " + e);
		} finally {
			from = null;
		}
	}

	/** Прокси NPC Ведьмака — только у игрока клиента: марионетка не бьёт, а бить её некому (Npcs). */
	private static void npcs(final ServerPlayer p, final JsonObject m) {
		if (p instanceof GeraltPlayer) {
			Npcs.clear();
		} else {
			Npcs.update(p, m);
		}
	}

	/** Ответ тому, кто прислал текущее сообщение. */
	private static void reply(final String line) {
		Link.Peer p = from;
		if (p != null) {
			p.send(line);
		}
	}

	private static void error(final String why, final String text) {
		reply("{\"t\":\"error\",\"tick\":" + Mcw.server().getTickCount() + ",\"why\":" + Json.q(why) + ",\"text\":" + Json.q(text) + "}");
	}

	/** Радиус снапшота: не больше view-distance сервера — дальше чанки вокруг Геральта не грузятся. */
	private static int clampRadius(final MinecraftServer server, final int r) {
		return Math.max(0, Math.min(r, server.getPlayerList().getViewDistance()));
	}

	private static void hello(final Link.Peer peer, final MinecraftServer server, final ServerPlayer p, final JsonObject m, final int tick) {
		Mcw.link().promote(peer);
		session = peer;
		ACTIONS.reset();
		ENTS.reset();
		// мост на hello забывает всё сам: снимать старые чанки unchunk'ами незачем
		SYNC.stop();
		int r = Mcw.config().radius;
		if (m.has("want") && m.get("want").isJsonObject()) {
			r = Json.i(m.getAsJsonObject("want"), "radius", r);
		}

		r = clampRadius(server, r);
		ServerLevel level = p.level();
		peer.send("{\"t\":\"hello\",\"v\":1,\"mc\":" + Json.q(server.getServerVersion()) + ",\"mod\":" + Json.q(Mcw.version())
			+ ",\"dim\":" + Json.q(dim(level)) + ",\"world\":" + Json.q(Json.s(m, "world", ""))
			+ ",\"tick\":" + tick + ",\"radius\":" + r
			+ ",\"player\":{\"name\":" + Json.q(p.getGameProfile().name()) + ",\"uuid\":" + Json.q(p.getUUID().toString())
			+ ",\"p\":" + vec(p.position()) + ",\"yaw\":" + Json.d(p.getYRot()) + ",\"client\":" + !(p instanceof GeraltPlayer) + "}"
			+ ",\"y\":{\"min\":" + level.getMinY() + ",\"max\":" + level.getMaxY() + "}"
			+ ",\"ent\":{\"r\":" + Json.d(Mcw.config().entRadius) + ",\"every\":" + Mcw.config().entEvery + ",\"max\":" + Mcw.config().entMax + "}}");
		SYNC.start(level, r, p.chunkPosition(), tick);
		lastInv = "";
		lastHp = -1.0F;
		lastFood = -1;
		sendInventory(p, tick, true);
		sendHp(p, tick, true);
	}

	/** Новый снапшот моста (другой радиус); у отладочного соединения снапшота нет. */
	private static void snap(final Link.Peer peer, final MinecraftServer server, final ServerPlayer p, final int r, final int tick) {
		if (session == null || peer != session) {
			error("no_hello", "snap is for the bridge (the connection that sent hello)");
			return;
		}

		SYNC.start(p.level(), clampRadius(server, r), p.chunkPosition(), tick);
	}

	/**
	 * Точка от моста годится: конечные числа (Gson разбирает NaN и Infinity) в пределах, где Minecraft вообще
	 * держит сущности (те же границы у /tp).
	 */
	static boolean validPos(final double[] at) {
		if (at == null || at.length != 3) {
			return false;
		}

		for (double v : at) {
			if (!Double.isFinite(v)) {
				return false;
			}
		}

		return Level.isInSpawnableBounds(BlockPos.containing(at[0], at[1], at[2]));
	}

	/** yaw/pitch из сообщения или текущие; NaN если прислали не число. */
	static float angle(final JsonObject m, final String key, final float cur) {
		double v = Json.num(m, key, cur);
		return Double.isFinite(v) ? (float) v : Float.NaN;
	}

	private static void pos(final ServerPlayer p, final JsonObject m, final int tick) {
		double[] at = Json.arr(m, "p");
		float yaw = angle(m, "yaw", p.getYRot());
		float pitch = angle(m, "pitch", p.getXRot());
		if (!validPos(at) || Float.isNaN(yaw) || Float.isNaN(pitch)) {
			error("bad_args", "pos.p/yaw/pitch: three finite numbers inside the world");
			return;
		}

		long before = p.chunkPosition().toLong();
		place(p, at, yaw, pitch);
		if (SYNC.active() && p.chunkPosition().toLong() != before) {
			SYNC.retrack(p.chunkPosition(), tick);
		}
	}

	/**
	 * Дальше этого (блоков) от точки моста игрока клиента переносит сервер: быстрое перемещение, загрузка сейва. Меньше
	 * 10 м, после которых кукла в Ведьмаке перестаёт тянуться за игроком MC (mcw_steve.ws, mcwf_mc: far).
	 */
	private static final double CLIENT_JUMP = 8.0;
	/** Последний отказ тянуть игрока клиента вниз (лог не чаще раза в 5 с). */
	private static long lastDownRefusal;

	/**
	 * Поставить Геральта туда, где он в Ведьмаке. Марионетку — сразу. Игрок клиента (mcclient) идёт за Ведьмаком сам,
	 * мост шлёт ему позу напрямую, а сервер принимает его ходы (noPhysics, player_movement_check=false); сюда — только
	 * страховка: разошлись больше чем на {@link #CLIENT_JUMP} — телепорт с сервера, клиент его подтвердит.
	 */
	static void place(final ServerPlayer p, final double[] at, final float yaw, final float pitch) {
		if (p instanceof GeraltPlayer g) {
			g.moveTo(at[0], at[1], at[2], yaw, pitch);
		} else if (p.position().distanceToSqr(at[0], at[1], at[2]) > CLIENT_JUMP * CLIENT_JUMP) {
			if (Math.hypot(at[0] - p.getX(), at[2] - p.getZ()) < CLIENT_JUMP && at[1] < p.getY()) {
				// почти отвесно вниз: кукла Ведьмака провалилась под землю (2026-10-04: на лошади MC за рельефом, которого
				// ещё не было), а не перенос — туда игрока не тянем, иначе он падает в пустоту и умирает
				long now = System.currentTimeMillis();
				if (now - lastDownRefusal > 5000L) {
					lastDownRefusal = now;
					Mcw.LOG.info("mcw: {} в {} {} {}, а мост ведёт отвесно вниз в {} {} {} - не тяну (кукла под землёй)",
						p.getGameProfile().name(), p.getX(), p.getY(), p.getZ(), at[0], at[1], at[2]);
				}

				return;
			}

			Mcw.LOG.info("mcw: {} в {} {} {}, а мост ведёт в {} {} {} - телепорт", p.getGameProfile().name(), p.getX(), p.getY(), p.getZ(), at[0], at[1], at[2]);
			p.connection.teleport(at[0], at[1], at[2], yaw, pitch);
		}
	}

	private static void give(final ServerPlayer p, final JsonObject m, final int tick) {
		Identifier id = Identifier.tryParse(Json.s(m, "item", ""));
		Item item = id == null ? null : BuiltInRegistries.ITEM.getOptional(id).orElse(null);
		if (item == null) {
			error("bad_item", Json.s(m, "item", ""));
			return;
		}

		int n = Math.max(1, Json.i(m, "n", 1));
		while (n > 0) {
			int k = Math.min(n, item.getDefaultMaxStackSize());
			if (!p.getInventory().add(new ItemStack(item, k))) {
				break; // инвентарь полон — остаток пропадает (как /give без места)
			}

			n -= k;
		}

		sendInventory(p, tick, false);
	}

	/**
	 * spawn{id, type, d} — для тестов: моб type (по умолчанию крипер) в d блоках перед Геральтом по курсу, на первой
	 * опоре сверху вниз от +3 до −8 блоков от его ног, где моб помещается. Как /summon: EntityType.spawn с причиной
	 * COMMAND (finalizeSpawn, ИИ ванильный). Ответ — ack{id, ok, why?, ent, p}; why: bad_type, bad_args, no_ground,
	 * rejected. Дальше моб живёт сам и приходит мосту в ent.
	 */
	private static void spawn(final ServerPlayer p, final JsonObject m, final int tick) {
		int id = Json.i(m, "id", 0);
		Identifier tid = Identifier.tryParse(Json.s(m, "type", "minecraft:creeper"));
		EntityType<?> type = tid == null ? null : BuiltInRegistries.ENTITY_TYPE.getOptional(tid).orElse(null);
		if (type == null || !type.canSummon()) {
			reply(spawnAck(id, false, "bad_type", null, tick));
			return;
		}

		double d = Json.num(m, "d", 5.0);
		if (!Double.isFinite(d) || d < 0.0 || d > 32.0) {
			reply(spawnAck(id, false, "bad_args", null, tick));
			return;
		}

		ServerLevel level = p.level();
		Vec3 dir = Vec3.directionFromRotation(0.0F, p.getYRot());
		double x = p.getX() + dir.x * d;
		double z = p.getZ() + dir.z * d;
		BlockPos at = null;
		int y0 = Mth.floor(p.getY());
		for (int y = y0 + 3; y >= y0 - 8 && at == null; y--) {
			BlockPos pos = BlockPos.containing(x, y, z);
			BlockPos below = pos.below();
			if (level.isInWorldBounds(pos) && level.getBlockState(below).isFaceSturdy(level, below, Direction.UP)
				&& level.noCollision(type.getSpawnAABB(pos.getX() + 0.5, pos.getY(), pos.getZ() + 0.5))) {
				at = pos;
			}
		}

		if (at == null) {
			reply(spawnAck(id, false, "no_ground", null, tick));
			return;
		}

		Entity e = type.spawn(level, at, EntitySpawnReason.COMMAND);
		reply(spawnAck(id, e != null, e == null ? "rejected" : null, e, tick));
	}

	private static String spawnAck(final int id, final boolean ok, final String why, final Entity e, final int tick) {
		return "{\"t\":\"ack\",\"tick\":" + tick + ",\"id\":" + id + ",\"ok\":" + ok + (why == null ? "" : ",\"why\":" + Json.q(why))
			+ (e == null ? "" : ",\"ent\":" + e.getId() + ",\"p\":" + vec(e.position())) + "}";
	}

	/** Команда от имени сервера; весь вывод (успех и ошибки) — в cmd_out. */
	private static void cmd(final MinecraftServer server, final JsonObject m, final int tick) {
		String c = Json.s(m, "c", "");
		StringBuilder out = new StringBuilder();
		CommandSource capture = new CommandSource() {
			@Override
			public void sendSystemMessage(final Component message) {
				if (!out.isEmpty()) {
					out.append('\n');
				}

				out.append(message.getString());
			}

			@Override
			public boolean acceptsSuccess() {
				return true;
			}

			@Override
			public boolean acceptsFailure() {
				return true;
			}

			@Override
			public boolean shouldInformAdmins() {
				return false;
			}
		};
		CommandSourceStack src = server.createCommandSourceStack().withSource(capture);
		server.getCommands().performPrefixedCommand(src, c);
		reply("{\"t\":\"cmd_out\",\"tick\":" + tick + ",\"c\":" + Json.q(c) + ",\"text\":" + Json.q(out.toString()) + "}");
		inventoryChanged(); // /give, /clear
	}

	/** Колонны рельефа Ведьмака {x, z, y0, y1, ...} (включительно) как barrier — см. {@link Ground}. */
	private static void ground(final ServerLevel level, final JsonObject m, final int tick) {
		int[] c = Json.ints(m, "c");
		if (c == null || c.length % 4 != 0) {
			error("bad_args", "ground.c");
			return;
		}

		reply(Ground.place(level, c, tick));
	}

	/** Убрать свой barrier-рельеф: box [x0,z0,x1,z1] или весь; any — ещё и любые barrier в box. */
	private static void groundClear(final ServerLevel level, final JsonObject m, final int tick) {
		int[] box = Json.ints(m, "box");
		boolean any = m.has("any") && m.get("any").getAsBoolean();
		if (box != null && box.length != 4 || any && (box == null || Ground.sweepTooBig(box))) {
			error("bad_args", "ground_clear.box: [x0,z0,x1,z1], x0<=x1, z0<=z1; with any - at most 64x64 chunks");
			return;
		}

		reply(Ground.clear(level, box, any, tick));
	}

	/** END_SERVER_TICK. */
	static void tick(final MinecraftServer server) {
		ServerPlayer p = Mcw.geralt();
		if (p == null) {
			return;
		}

		int tick = server.getTickCount();
		// копание идёт и для отладочного соединения без hello
		ACTIONS.tick(p, tick);
		Horses.tick(p, tick);
		if (session == null) {
			return;
		}

		if (SYNC.active() && p.level() != SYNC.level()) {
			// страховка: смену уровня должны были сообщить teleport или respawn
			Mcw.LOG.warn("mcw: {} в {}, а зеркалим {} — пересобираю снапшот", p.getGameProfile().name(), dim(p.level()), dim(SYNC.level()));
			onTeleport(p);
		}

		SYNC.flush(tick);
		if (tick % 20 == 0 && SYNC.active()) {
			SYNC.retrack(p.chunkPosition(), tick);
		}

		// подбор с земли, крафт, /give и прочее мимо act меняют Inventory.timesChanged — inv уходит в том же тике;
		// раз в 10 тиков — сверка подписью (износ инструмента timesChanged не трогает)
		int times = p.getInventory().getTimesChanged();
		if (invDirty || times != lastInvTimes || tick % 10 == 0) {
			lastInvTimes = times;
			sendInventory(p, tick, false);
		}

		sendHp(p, tick, false);
		ArrowWalls.tick(p.level(), tick);
		McwConfig cfg = Mcw.config();
		if (tick % cfg.entEvery == 0) {
			ENTS.tick(p, tick, cfg);
		}
	}

	static void flushBlocks(final int tick) {
		SYNC.flush(tick);
	}

	static void onSetBlock(final ServerLevel level, final BlockPos pos) {
		SYNC.onSetBlock(level, pos);
	}

	/** Fabric зовёт с потока сервера; если когда-нибудь нет — перекидываем туда же, состояние не потокобезопасно. */
	static void onChunkLoad(final ServerLevel level, final LevelChunk chunk) {
		if (!level.getServer().isSameThread()) {
			Mcw.later(() -> onChunkLoad(level, chunk));
		} else if (session != null) {
			SYNC.onChunkLoad(level, chunk, level.getServer().getTickCount());
		}
	}

	static void onChunkUnload(final ServerLevel level, final LevelChunk chunk) {
		if (!level.getServer().isSameThread()) {
			Mcw.later(() -> onChunkUnload(level, chunk));
		} else if (session != null) {
			SYNC.onChunkUnload(level, chunk, level.getServer().getTickCount());
		}
	}

	static void onDisconnect(final Link.Peer peer) {
		if (peer == session) {
			reset();
		}
	}

	/** Моста нет (отключился или сервер останавливается): ничего не зеркалим. */
	static void reset() {
		session = null;
		ACTIONS.reset();
		ENTS.reset();
		SYNC.stop();
		Npcs.clear();
		ArrowWalls.clear();
	}

	/** Только эффект: снесённые блоки придут в blocks того же тика. */
	static void onExplosion(final ServerLevel level, final Vec3 c, final float r, final String src) {
		if (session != null && level == SYNC.level()) {
			Mcw.link().send("{\"t\":\"explosion\",\"tick\":" + Mcw.server().getTickCount() + ",\"p\":" + vec(c) + ",\"r\":" + Json.d(r)
				+ ",\"src\":" + Json.q(src) + "}");
		}
	}

	/**
	 * Сервер сам сдвинул Геральта (/tp, жемчуг, портал). Другой уровень — после pteleport мост получает unchunk
	 * всех старых чанков и снапшот нового уровня (chunk..., snap_end).
	 */
	static void onTeleport(final ServerPlayer p) {
		if (session == null) {
			return;
		}

		int tick = Mcw.server().getTickCount();
		Mcw.link().send("{\"t\":\"pteleport\",\"tick\":" + tick + ",\"p\":" + vec(p.position()) + ",\"dim\":" + Json.q(dim(p.level())) + "}");
		follow(p, tick);
	}

	/** Зеркало — за Геральтом: другой уровень — новый снапшот, тот же — retrack без ожидания проверки раз в секунду. */
	private static void follow(final ServerPlayer p, final int tick) {
		if (!SYNC.active()) {
			return;
		}

		if (p.level() != SYNC.level()) {
			SYNC.start(p.level(), SYNC.radius(), p.chunkPosition(), tick);
		} else {
			SYNC.retrack(p.chunkPosition(), tick);
		}
	}

	/** msg — текст смерти, снятый до ServerPlayer.die (тот чистит CombatTracker). */
	static void onDeath(final String msg) {
		if (session != null) {
			Mcw.link().send("{\"t\":\"death\",\"tick\":" + Mcw.server().getTickCount() + ",\"msg\":" + Json.q(msg) + "}");
		}
	}

	static void onRespawn(final ServerPlayer p) {
		Mcw.setGeralt(p);
		if (session != null) {
			int tick = Mcw.server().getTickCount();
			Mcw.link().send("{\"t\":\"respawn\",\"tick\":" + tick + ",\"p\":" + vec(p.position()) + ",\"dim\":" + Json.q(dim(p.level())) + "}");
			follow(p, tick); // умер в Нижнем мире — возродился в обычном
			lastInv = "";
			sendInventory(p, tick, true);
			sendHp(p, tick, true);
		}
	}

	/**
	 * Геральт сменился: зашёл клиент Майнкрафта с его именем (марионетку убрал миксин PlayerList при входе) или
	 * клиент ушёл и вернулась марионетка. Мосту — geralt{client, p, dim}, зеркало — за новым игроком, inv и hp заново.
	 */
	static void onGeraltChanged(final ServerPlayer p) {
		Mcw.setGeralt(p);
		ACTIONS.reset();
		Npcs.clear();
		Kit.ensure(p);
		boolean client = !(p instanceof GeraltPlayer);
		Mcw.LOG.info("mcw: Геральт теперь {} ({} {} {})", client ? "игрок клиента" : "марионетка", p.getX(), p.getY(), p.getZ());
		if (session != null) {
			int tick = Mcw.server().getTickCount();
			Mcw.link().send("{\"t\":\"geralt\",\"tick\":" + tick + ",\"client\":" + client + ",\"p\":" + vec(p.position()) + ",\"dim\":"
				+ Json.q(dim(p.level())) + "}");
			follow(p, tick);
			lastInv = "";
			lastInvTimes = -1;
			sendInventory(p, tick, true);
			sendHp(p, tick, true);
		}
	}

	/** Смерть игрока клиента (марионетка сообщает сама из GeraltPlayer.die). */
	static void onClientDeath(final ServerPlayer p, final DamageSource src) {
		if (p == Mcw.geralt() && !(p instanceof GeraltPlayer)) {
			onDeath(src.getLocalizedDeathMessage(p).getString());
		}
	}

	static void inventoryChanged() {
		invDirty = true;
	}

	static String dim(final ServerLevel level) {
		return level == null ? "" : level.dimension().identifier().toString();
	}

	private static String vec(final Vec3 v) {
		return "[" + Json.d(v.x) + "," + Json.d(v.y) + "," + Json.d(v.z) + "]";
	}

	/** Подпись инвентаря: выбранный слот и непустые слоты [slot,id,n]; по ней act понимает, что что-то поменялось. */
	static String inventorySig(final ServerPlayer p) {
		Inventory inv = p.getInventory();
		StringBuilder s = new StringBuilder();
		for (int i = 0; i < inv.getContainerSize(); i++) {
			ItemStack st = inv.getItem(i);
			if (st.isEmpty()) {
				continue;
			}

			if (!s.isEmpty()) {
				s.append(',');
			}

			s.append('[').append(i).append(',').append(Json.q(BuiltInRegistries.ITEM.getKey(st.getItem()).toString())).append(',').append(st.getCount()).append(']');
		}

		return inv.getSelectedSlot() + "|" + s;
	}

	/** Износ повреждённых предметов: [slot,повреждение,предел],... (в подпись inventorySig не входит: на act не влияет). */
	static String damageSig(final ServerPlayer p) {
		Inventory inv = p.getInventory();
		StringBuilder s = new StringBuilder();
		for (int i = 0; i < inv.getContainerSize(); i++) {
			ItemStack st = inv.getItem(i);
			if (st.isDamaged()) {
				(s.isEmpty() ? s : s.append(',')).append('[').append(i).append(',').append(st.getDamageValue()).append(',').append(st.getMaxDamage()).append(']');
			}
		}

		return s.toString();
	}

	/**
	 * inv{sel, s:[[slot,id,n],...], dmg:[[slot,повреждение,предел],...]} — только непустые слоты; шлём, если что-то
	 * поменялось. Слоты — Inventory 1.21.11 целиком (43): 0–8 хотбар, 9–35 рюкзак, 36–39 броня (ноги…голова),
	 * 40 левая рука, 41–42 тело и седло (у игрока пусты).
	 */
	private static void sendInventory(final ServerPlayer p, final int tick, final boolean force) {
		invDirty = false;
		String sig = inventorySig(p);
		String dmg = damageSig(p);
		String full = sig + "#" + dmg;
		if (!force && full.equals(lastInv)) {
			return;
		}

		lastInv = full;
		int bar = sig.indexOf('|');
		Mcw.link().send("{\"t\":\"inv\",\"tick\":" + tick + ",\"sel\":" + sig.substring(0, bar) + ",\"s\":[" + sig.substring(bar + 1) + "],\"dmg\":["
			+ dmg + "]}");
	}

	private static void sendHp(final ServerPlayer p, final int tick, final boolean force) {
		float hp = p.getHealth();
		int food = p.getFoodData().getFoodLevel();
		if (!force && hp == lastHp && food == lastFood) {
			return;
		}

		lastHp = hp;
		lastFood = food;
		Mcw.link().send("{\"t\":\"hp\",\"tick\":" + tick + ",\"hp\":" + Json.d(hp) + ",\"food\":" + food + "}");
	}
}
