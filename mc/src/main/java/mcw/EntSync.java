package mcw;

import it.unimi.dsi.fastutil.ints.Int2ObjectMap;
import it.unimi.dsi.fastutil.ints.Int2ObjectOpenHashMap;
import it.unimi.dsi.fastutil.ints.IntArrayList;
import it.unimi.dsi.fastutil.ints.IntOpenHashSet;
import java.util.Comparator;
import java.util.List;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.util.Mth;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.entity.monster.Creeper;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.ItemStack;

/**
 * Поток ent: мобы и предметы на земле не дальше ent_radius блоков от Геральта, дельтами. Раз в ent_every тиков
 * сравниваем с тем, что мост уже знает: новые — spawn (всё о сущности), сдвинулись больше MOVE_EPS или повернулись
 * больше YAW_EPS — move, сменили здоровье — hp, фитиль крипера — fuse, стопка предмета — item, пропали (умерли,
 * подобраны, взорвались, ушли из радиуса или из ent_max ближних, другой уровень) — remove. Ничего не поменялось —
 * сообщения нет.
 *
 * id — Entity.getId(): живёт до рестарта сервера, а после рестарта мост всё равно шлёт hello и получает всё заново.
 * Только поток сервера.
 */
final class EntSync {
	private static final double MOVE_EPS = 0.02;
	private static final float YAW_EPS = 2.0F;

	/** Что мост знает о сущности: последнее отправленное. */
	private static final class Seen {
		double x;
		double y;
		double z;
		float yaw;
		float hp;
		int fuse;
		String item;
		int count;
	}

	private final Int2ObjectOpenHashMap<Seen> seen = new Int2ObjectOpenHashMap<>();

	/** hello, отключение моста: следующий тик отдаст всё заново как spawn. */
	void reset() {
		this.seen.clear();
	}

	int known() {
		return this.seen.size();
	}

	/** Мобы и предметы; игроки (Геральт, отладочный клиент) и прокси NPC Ведьмака (Npcs) — нет. */
	static boolean wanted(final Entity e) {
		return e.isAlive() && (e instanceof ItemEntity || e instanceof LivingEntity && !(e instanceof Player)) && !Npcs.isProxy(e);
	}

	void tick(final ServerPlayer p, final int tick, final McwConfig cfg) {
		ServerLevel level = p.level();
		double r = cfg.entRadius;
		List<Entity> near = level.getEntities(p, p.getBoundingBox().inflate(r), e -> wanted(e) && e.distanceToSqr(p) <= r * r);
		if (near.size() > cfg.entMax) {
			near.sort(Comparator.comparingDouble(e -> e.distanceToSqr(p)));
			near = near.subList(0, cfg.entMax);
		}

		StringBuilder spawn = new StringBuilder();
		StringBuilder move = new StringBuilder();
		StringBuilder hp = new StringBuilder();
		StringBuilder fuse = new StringBuilder();
		StringBuilder item = new StringBuilder();
		IntOpenHashSet alive = new IntOpenHashSet(near.size());
		for (Entity e : near) {
			int id = e.getId();
			alive.add(id);
			float yaw = e.getYRot();
			float health = e instanceof LivingEntity le ? le.getHealth() : Float.NaN;
			int f = e instanceof Creeper c && (c.getSwellDir() > 0 || c.isIgnited()) ? 1 : 0;
			String what = null;
			int count = 0;
			if (e instanceof ItemEntity ie) {
				ItemStack st = ie.getItem();
				what = BuiltInRegistries.ITEM.getKey(st.getItem()).toString();
				count = st.getCount();
			}

			Seen s = this.seen.get(id);
			boolean placed;
			if (s == null) {
				s = new Seen();
				this.seen.put(id, s);
				placed = true;
				sep(spawn).append('[').append(id).append(',').append(Json.q(EntityType.getKey(e.getType()).toString())).append(',')
					.append(pose(e, yaw)).append(',').append(Json.d(health)).append(']');
				if (f != 0) {
					sep(fuse).append('[').append(id).append(',').append(f).append(']');
				}

				if (what != null) {
					sep(item).append('[').append(id).append(',').append(Json.q(what)).append(',').append(count).append(']');
				}
			} else {
				double dx = e.getX() - s.x;
				double dy = e.getY() - s.y;
				double dz = e.getZ() - s.z;
				// не дотянуло до eps — позицию «как знает мост» не трогаем: медленный дрейф накопится и уйдёт move
				placed = dx * dx + dy * dy + dz * dz > MOVE_EPS * MOVE_EPS || Math.abs(Mth.wrapDegrees(yaw - s.yaw)) > YAW_EPS;
				if (placed) {
					sep(move).append('[').append(id).append(',').append(pose(e, yaw)).append(']');
				}

				// NaN у предмета: Float.compare считает NaN равными, лишнего hp нет
				if (Float.compare(health, s.hp) != 0) {
					sep(hp).append('[').append(id).append(',').append(Json.d(health)).append(']');
				}

				if (f != s.fuse) {
					sep(fuse).append('[').append(id).append(',').append(f).append(']');
				}

				if (what != null && (!what.equals(s.item) || count != s.count)) {
					sep(item).append('[').append(id).append(',').append(Json.q(what)).append(',').append(count).append(']');
				}
			}

			if (placed) {
				s.x = e.getX();
				s.y = e.getY();
				s.z = e.getZ();
				s.yaw = yaw;
			}

			s.hp = health;
			s.fuse = f;
			s.item = what;
			s.count = count;
		}

		IntArrayList gone = new IntArrayList();
		for (Int2ObjectMap.Entry<Seen> en : this.seen.int2ObjectEntrySet()) {
			if (!alive.contains(en.getIntKey())) {
				gone.add(en.getIntKey());
			}
		}

		StringBuilder remove = new StringBuilder();
		for (int k = 0; k < gone.size(); k++) {
			this.seen.remove(gone.getInt(k));
			sep(remove).append(gone.getInt(k));
		}

		if (spawn.isEmpty() && move.isEmpty() && hp.isEmpty() && fuse.isEmpty() && item.isEmpty() && remove.isEmpty()) {
			return;
		}

		Mcw.link().send("{\"t\":\"ent\",\"tick\":" + tick + ",\"spawn\":[" + spawn + "],\"move\":[" + move + "],\"hp\":[" + hp
			+ "],\"fuse\":[" + fuse + "],\"item\":[" + item + "],\"remove\":[" + remove + "]}");
	}

	/** x,y,z (ноги), yaw. */
	private static String pose(final Entity e, final float yaw) {
		return Json.d(e.getX()) + "," + Json.d(e.getY()) + "," + Json.d(e.getZ()) + "," + Json.d(yaw);
	}

	private static StringBuilder sep(final StringBuilder b) {
		return b.isEmpty() ? b : b.append(',');
	}
}
