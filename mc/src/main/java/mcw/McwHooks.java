package mcw;

import java.util.UUID;
import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.damagesource.DamageSource;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntityDimensions;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.projectile.arrow.AbstractArrow;
import net.minecraft.world.phys.Vec3;

/** Публичный вход для миксинов (они в другом пакете, а Bridge — package-private). */
public final class McwHooks {
	/** Сколько раз Level.setBlock что-то поменял; act по разнице понимает, изменил ли клик мир. */
	private static long changes;

	private McwHooks() {
	}

	public static void blockChanged(final ServerLevel level, final BlockPos pos) {
		changes++;
		Bridge.onSetBlock(level, pos);
	}

	public static void explosion(final ServerLevel level, final Vec3 center, final float radius, final String src) {
		Bridge.onExplosion(level, center, radius, src);
	}

	/** Марионетка или игрок клиента под именем Геральта. */
	public static boolean isGeralt(final Object o) {
		return o instanceof GeraltPlayer || o != null && o == Mcw.geralt();
	}

	/** Пакет действия от игрока клиента обработан (GamePacketListenerMixin): изменения блоков — мосту сразу. */
	public static void clientAction(final Object player) {
		if (player != null && player == Mcw.geralt()) {
			Bridge.flushBlocks(Mcw.server().getTickCount());
		}
	}

	/** Вход игрока с этим UUID (PlayerListMixin): если это Геральт, марионетка уходит. */
	public static void beforeLogin(final UUID id) {
		Mcw.dropPuppet(id);
	}

	/** Миксины hurtServer: true — это прокси NPC Ведьмака, урон ушёл мосту и не наносится (Npcs.onHurt). */
	public static boolean npcHurt(final Object e, final DamageSource src, final float amount) {
		return e instanceof LivingEntity le && Npcs.isProxy(le) && Npcs.onHurt(le, src, amount);
	}

	/** Размер прокси NPC Ведьмака (LivingEntityMixin.getDimensions), null — не прокси. */
	public static EntityDimensions npcDims(final Object e) {
		return e instanceof Entity en ? Npcs.dims(en) : null;
	}

	/** Стена Ведьмака на пути стрелы в этом тике (ArrowWalls) или null. */
	public static Vec3 arrowWall(final AbstractArrow a) {
		return ArrowWalls.wallHit(a);
	}

	/** Стрела воткнулась в стену Ведьмака: под ней воздух MC, падать ей не надо. */
	public static void arrowStuck(final AbstractArrow a) {
		a.addTag(ArrowWalls.TAG);
	}

	public static boolean inWitcherWall(final Object a) {
		return a instanceof Entity e && e.getTags().contains(ArrowWalls.TAG);
	}

	/** Прокси NPC Ведьмака (Npcs): ПКМ по нему ничего не делает. */
	public static boolean isNpc(final Object e) {
		return e instanceof Entity en && Npcs.isProxy(en);
	}

	/** Читать только с потока сервера. */
	static long changes() {
		return changes;
	}
}
