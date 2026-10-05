package mcw.mixin;

import mcw.McwHooks;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.network.ServerGamePacketListenerImpl;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.level.LevelReader;
import net.minecraft.world.phys.AABB;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.Redirect;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Игрок клиента (mcclient) поставил, сломал или применил предмет — изменения блоков мосту сразу, а не в конце тика:
 * пакет клиента сервер обрабатывает между тиками, и блок в Ведьмаке появлялся до 50 мс позже. TAIL достаётся только
 * потоку сервера: на потоке сети ensureRunningOnSameThread перекладывает пакет в очередь сервера исключением.
 */
@Mixin(ServerGamePacketListenerImpl.class)
abstract class GamePacketListenerMixin {
	@Shadow
	public ServerPlayer player;

	@Inject(method = {"handleUseItemOn", "handlePlayerAction", "handleUseItem"}, at = @At("TAIL"))
	private void mcw$flush(final CallbackInfo ci) {
		McwHooks.clientAction(this.player);
	}

	/**
	 * Лошадь под Геральтом ходит по коллизии Ведьмака клиента (Solid), а у сервера под ней barrier-рельеф, округлённый
	 * до блоков, и крыши: проверка «moved wrongly» и «столкнулся с новым» откатывала бы её назад. Как
	 * player_movement_check=false для самого игрока — верим клиенту.
	 */
	@Redirect(method = "handleMoveVehicle", at = @At(value = "INVOKE",
		target = "Lnet/minecraft/server/level/ServerLevel;noCollision(Lnet/minecraft/world/entity/Entity;Lnet/minecraft/world/phys/AABB;)Z"))
	private boolean mcw$vehicleNoCollision(final ServerLevel level, final Entity entity, final AABB box) {
		return !McwHooks.isGeralt(this.player) && level.noCollision(entity, box);
	}

	@Redirect(method = "handleMoveVehicle", at = @At(value = "INVOKE",
		target = "Lnet/minecraft/server/network/ServerGamePacketListenerImpl;isEntityCollidingWithAnythingNew(Lnet/minecraft/world/level/LevelReader;Lnet/minecraft/world/entity/Entity;Lnet/minecraft/world/phys/AABB;DDD)Z"))
	private boolean mcw$vehicleNew(final ServerGamePacketListenerImpl self, final LevelReader level, final Entity entity,
		final AABB box, final double x, final double y, final double z) {
		return !McwHooks.isGeralt(this.player) && this.isEntityCollidingWithAnythingNew(level, entity, box, x, y, z);
	}

	@Shadow
	private boolean isEntityCollidingWithAnythingNew(final LevelReader level, final Entity entity, final AABB box, final double x,
		final double y, final double z) {
		throw new AssertionError();
	}
}
