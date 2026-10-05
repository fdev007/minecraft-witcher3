package mcw;

import com.google.gson.JsonObject;
import it.unimi.dsi.fastutil.ints.Int2ObjectMap;
import it.unimi.dsi.fastutil.ints.Int2ObjectOpenHashMap;
import it.unimi.dsi.fastutil.ints.IntArrayList;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.projectile.arrow.AbstractArrow;
import net.minecraft.world.phys.Vec3;

/**
 * Стены Ведьмака для стрел MC. Сервер знает только блоки MC и barrier-рельеф, а стен домов, заборов и деревьев
 * Ведьмака у него нет — стрела пролетала дом насквозь. Траектория стрелы MC детерминирована (за тик: сдвиг на скорость,
 * скорость x0,99, минус гравитация), поэтому новая стрела сразу уходит мосту путём на PATH_TICKS тиков вперёд
 * (arrow_path), мост спрашивает у игры первое попадание луча по этому пути (mcw_path: Terrain, Static, двери, заборы),
 * а ответ arrow_wall{id, i, p} — точка, куда стрела воткнётся. В тике стрелы (AbstractArrowMixin) путь, дошедший до
 * точки (или уже проскочивший её за тик-два, пока шёл ответ), обрывается там: стрела торчит в стене Ведьмака, как в
 * блоке (звук, тряска, подобрать можно), и не падает (под ней воздух MC — shouldFall для тега mcw_wall ложь).
 *
 *   -> arrow_path{id, tick, p:[[x, y, z], ...]}   точки пути (координаты MC), первая — где стрела сейчас
 *   arrow_wall{id, i, p?}                          i — номер отрезка пути с попаданием, -1 — путь чист
 *
 * Путь кончился, а стрела летит (прицельная дальность лука больше) — путь от её текущего места заново. Только поток
 * сервера.
 */
final class ArrowWalls {
	static final String TAG = "mcw_wall";
	private static final int PATH_TICKS = 30;
	private static final double GRAVITY = 0.05;

	private static final class Path {
		final int tick;
		boolean answered;
		Vec3 wall;

		Path(final int tick) {
			this.tick = tick;
		}
	}

	private static final Int2ObjectOpenHashMap<Path> PATHS = new Int2ObjectOpenHashMap<>();

	private ArrowWalls() {
	}

	/** ENTITY_LOAD: новая летящая стрела — путь мосту. */
	static void onLoad(final Entity e) {
		if (e instanceof AbstractArrow a && e.level() instanceof ServerLevel && !a.getTags().contains(TAG)) {
			Mcw.later(() -> ask(a)); // скорость у выстрела задана до addFreshEntity, но выстрел мог прийти и иначе — после
		}
	}

	private static void ask(final AbstractArrow a) {
		Link link = Mcw.link();
		Vec3 v = a.getDeltaMovement();
		if (link == null || a.isRemoved() || v.lengthSqr() < 1.0E-4 || !Bridge.hasSession()) {
			return;
		}

		Vec3 p = a.position();
		double g = a.isNoGravity() ? 0.0 : GRAVITY;
		StringBuilder b = new StringBuilder();
		for (int k = 0; k <= PATH_TICKS; k++) {
			(b.isEmpty() ? b : b.append(',')).append('[').append(Json.d(p.x)).append(',').append(Json.d(p.y)).append(',').append(Json.d(p.z)).append(']');
			p = p.add(v);
			v = v.scale(0.99).subtract(0.0, g, 0.0);
		}

		int tick = Mcw.server().getTickCount();
		PATHS.put(a.getId(), new Path(tick));
		link.send("{\"t\":\"arrow_path\",\"id\":" + a.getId() + ",\"tick\":" + tick + ",\"p\":[" + b + "]}");
	}

	static void onWall(final JsonObject m) {
		Path path = PATHS.get(Json.i(m, "id", -1));
		if (path == null) {
			return;
		}

		path.answered = true;
		double[] p = Json.arr(m, "p");
		path.wall = Json.i(m, "i", -1) >= 0 && Bridge.validPos(p) ? new Vec3(p[0], p[1], p[2]) : null;
	}

	/**
	 * Перед тиком стрелы (AbstractArrowMixin): стена Ведьмака на отрезке этого тика или уже позади (ответ пришёл, когда
	 * стрела её проскочила) -> точка, куда воткнуть; иначе null.
	 */
	static Vec3 wallHit(final AbstractArrow a) {
		Path path = PATHS.get(a.getId());
		if (path == null || path.wall == null) {
			return null;
		}

		Vec3 v = a.getDeltaMovement();
		double len2 = v.lengthSqr();
		if (len2 < 1.0E-8) {
			return null;
		}

		Vec3 d = path.wall.subtract(a.position());
		double t = d.dot(v) / len2;
		// далеко от прямой отрезка — стрелу что-то развернуло (отскок): эта стена уже не её
		double off = d.subtract(v.scale(t)).length();
		if (t > 1.0 || t < -4.0 || off > 1.0 + Math.sqrt(len2) * 0.5) {
			return null;
		}

		PATHS.remove(a.getId());
		return path.wall;
	}

	/** END_SERVER_TICK: убрать пути воткнувшихся и пропавших стрел; путь без стены кончился — спросить следующий. */
	static void tick(final ServerLevel level, final int tick) {
		IntArrayList gone = new IntArrayList();
		IntArrayList again = new IntArrayList();
		for (Int2ObjectMap.Entry<Path> en : PATHS.int2ObjectEntrySet()) {
			Entity e = level.getEntity(en.getIntKey());
			Path p = en.getValue();
			if (!(e instanceof AbstractArrow a) || a.isRemoved() || a.getDeltaMovement().lengthSqr() < 1.0E-6) {
				gone.add(en.getIntKey());
			} else if (p.answered && p.wall == null && tick - p.tick >= PATH_TICKS - 2) {
				again.add(en.getIntKey());
			} else if (!p.answered && tick - p.tick > 40) {
				gone.add(en.getIntKey()); // моста нет или ответ потерялся: стрела летит как в чистом MC
			}
		}

		for (int i = 0; i < gone.size(); i++) {
			PATHS.remove(gone.getInt(i));
		}

		for (int i = 0; i < again.size(); i++) {
			if (level.getEntity(again.getInt(i)) instanceof AbstractArrow a) {
				ask(a);
			}
		}
	}

	static void clear() {
		PATHS.clear();
	}
}
