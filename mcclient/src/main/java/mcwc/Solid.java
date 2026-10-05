package mcwc;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import it.unimi.dsi.fastutil.longs.Long2ObjectOpenHashMap;
import java.util.ArrayList;
import java.util.List;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.util.Mth;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.level.ChunkPos;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.shapes.Shapes;
import net.minecraft.world.phys.shapes.VoxelShape;

/**
 * Коллизия Ведьмака для физики клиента: дома, стены, полы, заборы, деревья, двери (мост: solid.py, игра: mcw_cols).
 * Колонна 1×1 без статики — одна земля (как Terrain, но без крыш), со статикой — 16 подколонн 25 см, в каждой твёрдые
 * промежутки по высоте. Колонн, о которых мост ещё не сказал, касается Terrain (высота mcw_ground, крыша — сплошная).
 *
 *   solid{c:[[x, z, h, d, subs], ...], reset?}   h — y земли в см или null; subs — null или 16 списков [низ, верх, ...]
 *                                                в см (низ null — до земли), подколонна K = i + 4j: x + i/4, z + j/4
 */
public final class Solid {
	/** Промежуток «до земли» — столбик такой высоты: глубже за тик не провалиться. */
	private static final double DEPTH = 4.0;
	/** Висящий промежуток (пол второго этажа, балка, ступень) выталкивает наверх, только если ноги в нём не глубже. */
	private static final double STEP = 0.6;
	private static final Long2ObjectOpenHashMap<Col> COLS = new Long2ObjectOpenHashMap<>();

	/** h — земля (блоки) или NaN; subs — null или 16 массивов [низ, верх, ...] в блоках сверху вниз, низ NaN — до земли. */
	private record Col(float h, float[][] subs) {
	}

	private Solid() {
	}

	static void apply(final boolean reset, final JsonArray c) {
		if (reset) {
			COLS.clear();
		}

		if (c == null) {
			return;
		}

		for (JsonElement el : c) {
			JsonArray a = el.getAsJsonArray();
			float h = a.get(2).isJsonNull() ? Float.NaN : a.get(2).getAsInt() / 100.0F;
			float[][] subs = null;
			if (!a.get(4).isJsonNull()) {
				JsonArray s = a.get(4).getAsJsonArray();
				subs = new float[16][];
				for (int k = 0; k < 16 && k < s.size(); k++) {
					JsonArray v = s.get(k).getAsJsonArray();
					float[] f = new float[v.size()];
					for (int i = 0; i < f.length; i++) {
						f[i] = v.get(i).isJsonNull() ? Float.NaN : v.get(i).getAsInt() / 100.0F;
					}

					subs[k] = f;
				}
			}

			COLS.put(ChunkPos.asLong(a.get(0).getAsInt(), a.get(1).getAsInt()), new Col(h, subs));
		}
	}

	static int size() {
		return COLS.size();
	}

	/** Мост знает опору колонны (Solid или Terrain). */
	static boolean known(final int x, final int z) {
		return COLS.containsKey(ChunkPos.asLong(x, z)) || !Float.isNaN(Terrain.height(x, z));
	}

	/**
	 * Подменяем столкновения только игроку клиента, который ходит сам, и тому, чем он правит (лошадь), — и только когда
	 * мост уже что-то прислал.
	 */
	public static boolean active(final Entity e) {
		// e бывает null: столкновения частиц (взрыв, пыль) идут через контекст без сущности
		if (e == null || Control.follow || (COLS.isEmpty() && Terrain.size() == 0)) {
			return false;
		}

		return e instanceof LocalPlayer || e.getControllingPassenger() instanceof LocalPlayer;
	}

	/** Столбики и промежутки колонн вокруг box (с запасом на подъём на ступеньку) — к списку столкновений сущности. */
	public static List<VoxelShape> withShapes(final List<VoxelShape> base, final AABB box) {
		AABB near = box.inflate(1.0, 0.0, 1.0);
		double lo = box.minY - 2.0;
		double hi = box.maxY + 2.0;
		List<VoxelShape> out = null;
		for (int x = Mth.floor(near.minX); x <= Mth.floor(near.maxX); x++) {
			for (int z = Mth.floor(near.minZ); z <= Mth.floor(near.maxZ); z++) {
				Col c = COLS.get(ChunkPos.asLong(x, z));
				if (c == null || c.subs == null) {
					float h = c != null ? c.h : Terrain.height(x, z);
					if (Float.isNaN(h) || h < lo || h - DEPTH > hi) {
						continue;
					}

					out = add(out, base, new AABB(x, h - DEPTH, z, x + 1, h, z + 1));
					continue;
				}

				for (int k = 0; k < 16; k++) {
					double sx = x + (k & 3) * 0.25;
					double sz = z + (k >> 2) * 0.25;
					float[] s = c.subs[k];
					if (s == null || sx + 0.25 < near.minX || sx > near.maxX || sz + 0.25 < near.minZ || sz > near.maxZ) {
						continue;
					}

					for (int i = 0; i + 1 < s.length; i += 2) {
						double top = s[i + 1];
						double bot = Float.isNaN(s[i]) ? top - DEPTH : s[i];
						if (top < lo || bot > hi) {
							continue;
						}

						out = add(out, base, new AABB(sx, bot, sz, sx + 0.25, top, sz + 0.25));
					}
				}
			}
		}

		return out == null ? base : out;
	}

	private static List<VoxelShape> add(final List<VoxelShape> out, final List<VoxelShape> base, final AABB box) {
		List<VoxelShape> o = out == null ? new ArrayList<>(base) : out;
		o.add(Shapes.create(box));
		return o;
	}

	/**
	 * Ноги (x, y, z) внутри твёрдого: куда вытолкнуть наверх, иначе NaN. Земля (и промежуток «до земли») выталкивает
	 * всегда — это провал сквозь неё; висящий промежуток (пол этажа, ступень) — только с глубины не больше STEP, чтобы
	 * игрок у стены или под балкой не оказался на крыше.
	 */
	static double pushUp(final double x, final double y, final double z) {
		int bx = Mth.floor(x);
		int bz = Mth.floor(z);
		Col c = COLS.get(ChunkPos.asLong(bx, bz));
		if (c == null || c.subs == null) {
			float h = c != null ? c.h : Terrain.height(bx, bz);
			return !Float.isNaN(h) && y < h - 0.001 ? h : Double.NaN;
		}

		int i = Mth.clamp(Mth.floor((x - bx) * 4.0), 0, 3);
		int j = Mth.clamp(Mth.floor((z - bz) * 4.0), 0, 3);
		float[] s = c.subs[i + 4 * j];
		if (s == null) {
			return Double.NaN;
		}

		for (int k = 0; k + 1 < s.length; k += 2) {
			double top = s[k + 1];
			boolean ground = Float.isNaN(s[k]);
			if (y < top - 0.001 && (ground || (y > s[k] && top - y <= STEP))) {
				return top;
			}
		}

		return Double.NaN;
	}
}
