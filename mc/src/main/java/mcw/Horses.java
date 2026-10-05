package mcw;

import java.util.List;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.EntitySpawnReason;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.ai.attributes.AttributeInstance;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.entity.animal.equine.AbstractHorse;
import net.minecraft.world.entity.animal.equine.Horse;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.Vec3;

/**
 * Лошадь Геральта — лошадь Майнкрафта вместо Плотвы (режим Стива, X): мост шлёт horse{}, мод ставит её в 3 блоках за
 * спиной Геральта — ручную, под седлом, быструю. Одна: найденная по тегу в радиусе 256 блоков переезжает, иначе новая.
 * В Ведьмаке её рисует mcw_models.ws (части коробками) по позе от клиента MC; ездит и правит ею клиент (ПКМ — сесть,
 * Shift — слезть), коллизию Ведьмака ей даёт он же (Solid). Сервер под всадником её не откатывает
 * (GamePacketListenerMixin.handleMoveVehicle).
 */
final class Horses {
	static final String TAG = "mcw_horse";
	/** Сущность лошади Геральта (id), -1 — не знаем: ищем по тегу раз в 2 с. */
	private static int horseId = -1;

	private Horses() {
	}

	/** -> horse{ok, id, new, p} мосту. */
	static String summon(final ServerPlayer p, final int tick) {
		ServerLevel level = p.level();
		AbstractHorse h = find(level, p);
		boolean created = false;
		if (h == null) {
			Horse nh = EntityType.HORSE.create(level, EntitySpawnReason.COMMAND);
			if (nh == null) {
				return "{\"t\":\"horse\",\"ok\":false,\"tick\":" + tick + "}";
			}

			nh.addTag(TAG);
			nh.setTamed(true);
			nh.setOwner(p);
			nh.setPersistenceRequired();
			nh.setItemSlot(EquipmentSlot.SADDLE, new ItemStack(Items.SADDLE));
			base(nh, Attributes.MAX_HEALTH, 30.0);
			base(nh, Attributes.MOVEMENT_SPEED, 0.3375);      // самая быстрая лошадь ванили
			base(nh, Attributes.JUMP_STRENGTH, 1.0);          // ~5 блоков
			nh.setHealth(nh.getMaxHealth());
			h = nh;
			created = true;
		}

		Vec3 dir = Vec3.directionFromRotation(0.0F, p.getYRot());
		double x = p.getX() - dir.x * 3.0;
		double z = p.getZ() - dir.z * 3.0;
		// высота — ноги Геральта: рядом с ним земля та же; на barrier сервер её и поставит, клиент — на землю Ведьмака
		h.snapTo(x, p.getY() + 0.5, z, p.getYRot(), 0.0F);
		h.setDeltaMovement(Vec3.ZERO);
		if (created) {
			level.addFreshEntity(h);
		}

		horseId = h.getId();
		Mcw.LOG.info("mcw: лошадь Геральта {} ({}) в {}", created ? "призвана" : "подошла", h.getId(), h.position());
		return "{\"t\":\"horse\",\"ok\":true,\"id\":" + h.getId() + ",\"new\":" + created + ",\"p\":[" + Json.d(h.getX()) + ","
			+ Json.d(h.getY()) + "," + Json.d(h.getZ()) + "],\"tick\":" + tick + "}";
	}

	private static void base(final AbstractHorse h, final net.minecraft.core.Holder<net.minecraft.world.entity.ai.attributes.Attribute> a,
		final double v) {
		AttributeInstance i = h.getAttribute(a);
		if (i != null) {
			i.setBaseValue(v);
		}
	}

	/**
	 * END_SERVER_TICK. Под всадником лошадью правит клиент — по непрерывной земле Ведьмака, а она до 0,5 м ниже верха
	 * barrier-рельефа сервера (округлён до блока). Слез — сервер получает лошадь с ногами внутри barrier-блока, а MC не
	 * выталкивает из формы, в которой сущность уже стоит: лошадь проваливалась сквозь столбик (2026-10-04: y 1,0 при
	 * земле 1,63). Ноги в barrier — на верх столбика; Ведьмак рисует её на настоящей земле (мост, _horse_ground).
	 */
	static void tick(final ServerPlayer p, final int tick) {
		ServerLevel level = p.level();
		AbstractHorse h = horseId >= 0 && level.getEntity(horseId) instanceof AbstractHorse e && e.isAlive() ? e : null;
		if (h == null) {
			if (tick % 40 != 0) {
				return;
			}

			h = find(level, p);
			if (h == null) {
				return;
			}

			horseId = h.getId();
		}

		if (h.getControllingPassenger() != null) {
			return;
		}

		BlockPos.MutableBlockPos bp = BlockPos.containing(h.getX(), h.getY() + 0.01, h.getZ()).mutable();
		if (!level.getBlockState(bp).is(Blocks.BARRIER)) {
			return;
		}

		for (int k = 0; k < 16 && level.getBlockState(bp).is(Blocks.BARRIER); k++) {
			bp.move(Direction.UP);
		}

		h.setPos(h.getX(), bp.getY(), h.getZ());
		Vec3 d = h.getDeltaMovement();
		h.setDeltaMovement(d.x, Math.max(0.0, d.y), d.z);
	}

	private static AbstractHorse find(final ServerLevel level, final ServerPlayer p) {
		List<AbstractHorse> list = level.getEntitiesOfClass(AbstractHorse.class, new AABB(p.blockPosition()).inflate(256.0),
			e -> e.isAlive() && e.getTags().contains(TAG));
		return list.isEmpty() ? null : list.get(0);
	}
}
