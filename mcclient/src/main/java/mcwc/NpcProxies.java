package mcwc;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import it.unimi.dsi.fastutil.ints.Int2ObjectOpenHashMap;
import net.minecraft.client.Minecraft;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntityDimensions;
import net.minecraft.world.entity.InterpolationHandler;

/**
 * Прокси NPC Ведьмака на клиенте (бой, mc\...\Npcs): сервер ставит их по позициям моста раз в тик, а клиент ещё и
 * интерполирует их 3 тика — по быстрому волку удар уходил мимо. Поэтому мост шлёт позиции NPC и сюда, напрямую, а
 * клиент перед каждым выбором цели под прицелом (GameRenderer.pick, PickMixin) ставит прокси туда, где NPC сейчас по
 * последней скорости, без интерполяции: цель под прицелом MC — та, что Ведьмак рисует в этом кадре. Размер прокси —
 * ширина и рост NPC (LivingEntityMixin.getDimensions), как и на сервере.
 *
 *   npcs{l:[[сущность MC, x, y, z, vx, vy, vz, ширина, рост], ...]}   ноги и скорость (блоки/с); пустой — прокси нет
 *
 * Данные старше STALE_NS — не трогаем: мост замолчал, прокси ведёт сервер.
 */
public final class NpcProxies {
	private static final long STALE_NS = 1_000_000_000L;
	private static final double MAX_AHEAD_S = 0.25;

	private record P(double x, double y, double z, double vx, double vy, double vz, EntityDimensions dims) {
	}

	private static final Int2ObjectOpenHashMap<P> BY_ENT = new Int2ObjectOpenHashMap<>();
	private static long recv;

	private NpcProxies() {
	}

	static void apply(final JsonArray l) {
		BY_ENT.clear();
		recv = System.nanoTime();
		for (JsonElement el : l) {
			JsonArray a = el.getAsJsonArray();
			if (a.size() < 9) {
				continue;
			}

			BY_ENT.put(a.get(0).getAsInt(), new P(a.get(1).getAsDouble(), a.get(2).getAsDouble(), a.get(3).getAsDouble(), a.get(4).getAsDouble(),
				a.get(5).getAsDouble(), a.get(6).getAsDouble(), EntityDimensions.scalable(a.get(7).getAsFloat(), a.get(8).getAsFloat())));
		}
	}

	static int size() {
		return BY_ENT.size();
	}

	/** Размер прокси для LivingEntity.getDimensions, null — не прокси (или мост о нём не сказал). */
	public static EntityDimensions dims(final Entity e) {
		P p = BY_ENT.get(e.getId());
		return p == null ? null : p.dims;
	}

	/** Перед выбором цели: прокси — туда, где NPC сейчас (поток рендера). */
	public static void place(final Minecraft mc) {
		long now = System.nanoTime();
		if (mc.level == null || BY_ENT.isEmpty() || now - recv > STALE_NS) {
			return;
		}

		double k = Math.min(MAX_AHEAD_S, (now - recv) / 1.0E9);
		for (Int2ObjectOpenHashMap.Entry<P> en : BY_ENT.int2ObjectEntrySet()) {
			Entity e = mc.level.getEntity(en.getIntKey());
			if (e == null) {
				continue;
			}

			P p = en.getValue();
			if (Math.abs(e.getBbWidth() - p.dims.width()) > 0.001F || Math.abs(e.getBbHeight() - p.dims.height()) > 0.001F) {
				e.refreshDimensions();
			}

			InterpolationHandler ih = e.getInterpolation();
			if (ih != null) {
				ih.cancel();
			}

			e.setPos(p.x + p.vx * k, p.y + p.vy * k, p.z + p.vz * k);
		}
	}
}
