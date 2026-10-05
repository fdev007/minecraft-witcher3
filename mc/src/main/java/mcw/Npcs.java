package mcw;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import it.unimi.dsi.fastutil.ints.Int2IntOpenHashMap;
import it.unimi.dsi.fastutil.ints.Int2ObjectMap;
import it.unimi.dsi.fastutil.ints.Int2ObjectOpenHashMap;
import it.unimi.dsi.fastutil.ints.IntArrayList;
import it.unimi.dsi.fastutil.ints.IntOpenHashSet;
import net.minecraft.network.chat.Component;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.util.Mth;
import net.minecraft.world.damagesource.DamageSource;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntityDimensions;
import net.minecraft.world.entity.EntitySpawnReason;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.decoration.ArmorStand;
import net.minecraft.world.entity.projectile.arrow.AbstractArrow;
import net.minecraft.world.phys.Vec3;

/**
 * Прокси NPC Ведьмака для боя (docs/DEVELOPMENT.md «Что дальше», п. 4; схема SkyCraft): мост шлёт npcs{l} — NPC вокруг Геральта
 * (mcw_npcs), мод держит на их местах невидимые стойки для брони с их размерами. Клиент MC целится и бьёт в них как в
 * мобов, его стрелы в них попадают; урон Майнкрафт считает сам (оружие, крит, сила замаха, зачарования, скорость
 * стрелы), но не наносит: миксины hurtServer отдают его сюда, а сюда — мосту npc_hit, и мост бьёт NPC в Ведьмаке
 * (mcw_npchit). Обратно: удар NPC по Геральту — phurt{id, dmg}, урон игроку от его прокси (mobAttack): щит, броня,
 * отбрасывание — ванильные.
 *
 *   npcs{l:[[id, x, y, z, курс, ширина, рост, flags, vx, vy, vz], ...]}   ноги и скорость (блоки/с), координаты MC
 *   phurt{id, dmg}                          удар NPC id по игроку клиента, урон в здоровье MC
 *   -> npc_hit{tick, id, dmg, kind, crit}   kind: melee | arrow | other; урон — до брони (у прокси её нет)
 *   npc_block{id, how}                      NPC парировал (parry) удар MC или увернулся (dodge): отдача игроку
 *   -> npc_map{m:[[id, сущность MC], ...]}   при каждой смене: по нему мост ведёт прокси у клиента сам (без задержки
 *                                           сервера и интерполяции клиента — mcclient NpcProxies)
 *
 * Размер — ровно ширина и рост NPC (миксин LivingEntity.getDimensions), позиция на сервере — по скорости от последнего
 * npcs до начала каждого тика (не дальше 0,25 с), чтобы стрела и проверка дальности удара видели NPC там, где он
 * сейчас. Не живут дольше сеанса: тег mcw_npc, чужие (из сохранённого чанка после рестарта) убирает onLoad. Только
 * поток сервера (getDimensions зовётся и с потоков загрузки чанков — там таблица только читается).
 */
final class Npcs {
	static final String TAG = "mcw_npc";
	/** Экстраполяция по скорости — не дальше (секунды): мост замолчал — прокси стоит. */
	private static final double MAX_AHEAD_S = 0.25;

	private static final class Proxy {
		final LivingEntity e;
		double x;
		double y;
		double z;
		double vx;
		double vy;
		double vz;
		long t;
		volatile EntityDimensions dims;

		Proxy(final LivingEntity e) {
			this.e = e;
		}
	}

	private static final Int2ObjectOpenHashMap<Proxy> BY_W3 = new Int2ObjectOpenHashMap<>();
	/** id сущности MC -> номер NPC Ведьмака. */
	private static final Int2IntOpenHashMap W3_BY_ENT = new Int2IntOpenHashMap();
	/** id сущности MC -> размеры (для getDimensions; копия, чтобы не трогать BY_W3 с чужих потоков). */
	private static final Int2ObjectOpenHashMap<EntityDimensions> DIMS = new Int2ObjectOpenHashMap<>();
	private static boolean mapDirty;

	static {
		W3_BY_ENT.defaultReturnValue(Integer.MIN_VALUE);
	}

	private Npcs() {
	}

	static int size() {
		return BY_W3.size();
	}

	/** Наш прокси (или чужой с тегом — тогда его уберёт onLoad). */
	static boolean isProxy(final Entity e) {
		return W3_BY_ENT.containsKey(e.getId()) || e.getTags().contains(TAG);
	}

	/** Размер прокси для LivingEntity.getDimensions, null — не прокси. */
	static EntityDimensions dims(final Entity e) {
		synchronized (DIMS) {
			return DIMS.get(e.getId());
		}
	}

	static void update(final ServerPlayer p, final JsonObject m) {
		JsonArray l = m.has("l") && m.get("l").isJsonArray() ? m.getAsJsonArray("l") : new JsonArray();
		ServerLevel level = p.level();
		IntOpenHashSet seen = new IntOpenHashSet();
		long now = System.nanoTime();
		for (JsonElement el : l) {
			if (!el.isJsonArray() || el.getAsJsonArray().size() < 8) {
				continue;
			}

			JsonArray a = el.getAsJsonArray();
			int id = a.get(0).getAsInt();
			double x = a.get(1).getAsDouble();
			double y = a.get(2).getAsDouble();
			double z = a.get(3).getAsDouble();
			float yaw = a.get(4).getAsFloat();
			float w = (float) Mth.clamp(a.get(5).getAsDouble(), 0.2, 8.0);
			float h = (float) Mth.clamp(a.get(6).getAsDouble(), 0.3, 12.0);
			int flags = a.get(7).getAsInt();
			double vx = a.size() > 10 ? a.get(8).getAsDouble() : 0.0;
			double vy = a.size() > 10 ? a.get(9).getAsDouble() : 0.0;
			double vz = a.size() > 10 ? a.get(10).getAsDouble() : 0.0;
			if (!Bridge.validPos(new double[] {x, y, z}) || !Float.isFinite(yaw) || !Double.isFinite(vx + vy + vz)) {
				continue;
			}

			Proxy pr = BY_W3.get(id);
			if (pr != null && (pr.e.isRemoved() || pr.e.level() != level)) {
				drop(id);
				pr = null;
			}

			if (pr == null) {
				pr = spawn(level, id, flags, x, y, z, yaw);
				if (pr == null) {
					continue;
				}
			}

			seen.add(id);
			pr.x = x;
			pr.y = y;
			pr.z = z;
			pr.vx = vx;
			pr.vy = vy;
			pr.vz = vz;
			pr.t = now;
			resize(pr, w, h);
			pr.e.setYRot(yaw);
			pr.e.setYHeadRot(yaw);
			pr.e.setYBodyRot(yaw);
			place(pr, now);
		}

		IntArrayList gone = new IntArrayList();
		for (Int2ObjectMap.Entry<Proxy> en : BY_W3.int2ObjectEntrySet()) {
			if (!seen.contains(en.getIntKey())) {
				gone.add(en.getIntKey());
			}
		}

		for (int i = 0; i < gone.size(); i++) {
			drop(gone.getInt(i));
		}

		sendMap();
	}

	/** START_SERVER_TICK: прокси — туда, где NPC сейчас по последней скорости. */
	static void tick() {
		long now = System.nanoTime();
		for (Proxy pr : BY_W3.values()) {
			if (!pr.e.isRemoved()) {
				place(pr, now);
			}
		}
	}

	private static void place(final Proxy pr, final long now) {
		double k = Math.min(MAX_AHEAD_S, (now - pr.t) / 1.0E9);
		LivingEntity e = pr.e;
		e.setPos(pr.x + pr.vx * k, pr.y + pr.vy * k, pr.z + pr.vz * k);
		e.setDeltaMovement(Vec3.ZERO);
		e.clearFire();
		e.needsSync = true; // позиция клиенту каждый тик, а не раз в updateInterval (3 тика); клиент всё равно ведёт сам
	}

	private static void resize(final Proxy pr, final float w, final float h) {
		EntityDimensions d = pr.dims;
		if (d != null && Math.abs(d.width() - w) < 0.01F && Math.abs(d.height() - h) < 0.01F) {
			return;
		}

		d = EntityDimensions.scalable(w, h);
		pr.dims = d;
		synchronized (DIMS) {
			DIMS.put(pr.e.getId(), d);
		}

		pr.e.refreshDimensions();
	}

	private static Proxy spawn(final ServerLevel level, final int id, final int flags, final double x, final double y, final double z, final float yaw) {
		ArmorStand e = EntityType.ARMOR_STAND.create(level, EntitySpawnReason.COMMAND);
		if (e == null) {
			return null;
		}

		e.snapTo(x, y, z, yaw, 0.0F);
		e.addTag(TAG);
		e.setInvisible(true);
		e.setNoGravity(true);
		e.setSilent(true);
		// имя — в сообщении о смерти игрока («Geralt was slain by ...»)
		e.setCustomName(Component.literal((flags & 2) != 0 ? "Witcher NPC" : (flags & 4) != 0 ? "Witcher monster" : "Witcher beast"));
		e.setCustomNameVisible(false);
		Proxy pr = new Proxy(e);
		// в таблицу — до addFreshEntity: ENTITY_LOAD (onLoad) приходит изнутри него и снёс бы «чужой» прокси
		BY_W3.put(id, pr);
		W3_BY_ENT.put(e.getId(), id);
		if (!level.addFreshEntity(e)) {
			BY_W3.remove(id);
			W3_BY_ENT.remove(e.getId());
			return null;
		}

		mapDirty = true;
		return pr;
	}

	private static void drop(final int id) {
		Proxy pr = BY_W3.remove(id);
		if (pr != null) {
			W3_BY_ENT.remove(pr.e.getId());
			synchronized (DIMS) {
				DIMS.remove(pr.e.getId());
			}

			pr.e.discard();
			mapDirty = true;
		}
	}

	private static void sendMap() {
		Link link = Mcw.link();
		if (!mapDirty || link == null) {
			return;
		}

		mapDirty = false;
		StringBuilder b = new StringBuilder();
		for (Int2ObjectMap.Entry<Proxy> en : BY_W3.int2ObjectEntrySet()) {
			(b.isEmpty() ? b : b.append(',')).append('[').append(en.getIntKey()).append(',').append(en.getValue().e.getId()).append(']');
		}

		link.send("{\"t\":\"npc_map\",\"m\":[" + b + "]}");
	}

	/** Мост отключился, Геральт сменился: прокси больше некому вести. */
	static void clear() {
		for (int id : BY_W3.keySet().toIntArray()) {
			drop(id);
		}

		sendMap();
	}

	/** ServerEntityEvents.ENTITY_LOAD: прокси прошлого сеанса из сохранённого чанка — убрать. */
	static void onLoad(final Entity e) {
		if (e.getTags().contains(TAG) && !W3_BY_ENT.containsKey(e.getId())) {
			e.discard();
		}
	}

	/**
	 * Из миксинов hurtServer (LivingEntity, ArmorStand): true — это прокси, урон не наносится (ответ hurtServer —
	 * «попал», чтобы удар и стрела MC отработали как по мобу). Урон от Геральта (рукой или его стрелой) — мосту.
	 */
	static boolean onHurt(final LivingEntity e, final DamageSource src, final float amount) {
		int id = W3_BY_ENT.get(e.getId());
		if (id == Integer.MIN_VALUE) {
			return e.getTags().contains(TAG);
		}

		ServerPlayer g = Mcw.geralt();
		Link link = Mcw.link();
		if (g == null || link == null || src.getEntity() != g || !(amount > 0.0F)) {
			return true;
		}

		Entity direct = src.getDirectEntity();
		String kind = direct instanceof AbstractArrow ? "arrow" : direct == g ? "melee" : "other";
		// крит рукой — как Player.canCriticalAttack (падает и не на земле); у стрелы — её флаг
		boolean crit = direct instanceof AbstractArrow arrow ? arrow.isCritArrow() : g.fallDistance > 0.0 && !g.onGround();
		link.send("{\"t\":\"npc_hit\",\"tick\":" + Mcw.server().getTickCount() + ",\"id\":" + id + ",\"dmg\":" + Json.d(amount)
			+ ",\"kind\":" + Json.q(kind) + ",\"crit\":" + crit + "}");
		return true;
	}

	/**
	 * npc_block{id, how}: NPC Ведьмака парировал удар MC (how parry) — игрока отбрасывает от него, как Геральта от
	 * щита (r4Player.ReactToReflectedAttack); увернулся (dodge) — ничего, удар просто ушёл в воздух.
	 */
	static void blocked(final ServerPlayer p, final JsonObject m) {
		Proxy by = BY_W3.get(Json.i(m, "id", -1));
		if (by == null || by.e.isRemoved() || !"parry".equals(Json.s(m, "how", ""))) {
			return;
		}

		p.knockback(0.5, by.e.getX() - p.getX(), by.e.getZ() - p.getZ());
		p.hurtMarked = true; // скорость — клиенту (ClientboundSetEntityMotionPacket)
	}

	/** phurt: удар NPC Ведьмака по игроку. Источник — его прокси (щит и отбрасывание по направлению), нет прокси — общий. */
	static void hurtPlayer(final ServerPlayer p, final JsonObject m) {
		double dmg = Json.num(m, "dmg", 0.0);
		if (!(dmg > 0.0) || !Double.isFinite(dmg) || p.isDeadOrDying()) {
			return;
		}

		Proxy by = BY_W3.get(Json.i(m, "id", -1));
		DamageSource src = by != null && !by.e.isRemoved() ? p.damageSources().mobAttack(by.e) : p.damageSources().generic();
		p.hurtServer(p.level(), src, (float) Math.min(dmg, 1000.0));
	}
}
