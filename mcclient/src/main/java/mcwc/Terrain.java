package mcwc;

import com.google.gson.JsonArray;
import it.unimi.dsi.fastutil.longs.Long2FloatOpenHashMap;
import net.minecraft.world.level.ChunkPos;

/**
 * Земля Ведьмака для физики клиента (этап 3, как у SkyCraft: коллизия чужой игры подмешивается в столкновения MC).
 * Мост шлёт высоту поверхности под центром каждой колонны 1×1 (mcw_ground, непрерывная, в координатах MC), а клиент
 * ставит на её место столбик до этой высоты (EntityMixin) — вместо barrier-рельефа сервера, который округлён до целых
 * блоков и превращает склон в лестницу из ступеней по блоку (выше 0,6 — без прыжка не зайти). Barrier игроку клиента
 * не мешает (BlockStateMixin), им пользуются прицел и установка блоков. Столбики в столкновения ставит Solid — для
 * колонн, о которых не прислал своего (дома, стены): у mcw_ground крыша — тоже поверхность.
 *
 *   terrain{c:[x, z, h_cm, ...], reset?}   колонны (h — y поверхности в сантиметрах), reset — забыть все (другой мир)
 */
public final class Terrain {
	private static final Long2FloatOpenHashMap HEIGHT = new Long2FloatOpenHashMap();

	static {
		HEIGHT.defaultReturnValue(Float.NaN);
	}

	private Terrain() {
	}

	static void apply(final boolean reset, final JsonArray c) {
		if (reset) {
			HEIGHT.clear();
		}

		if (c == null) {
			return;
		}

		for (int i = 0; i + 2 < c.size(); i += 3) {
			HEIGHT.put(ChunkPos.asLong(c.get(i).getAsInt(), c.get(i + 1).getAsInt()), c.get(i + 2).getAsInt() / 100.0F);
		}
	}

	static int size() {
		return HEIGHT.size();
	}

	/** Высота поверхности колонны или NaN. */
	public static float height(final int x, final int z) {
		return HEIGHT.get(ChunkPos.asLong(x, z));
	}
}
