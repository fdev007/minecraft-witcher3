package mcw.mixin;

import mcw.McwHooks;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.player.Player;
import org.objectweb.asm.Opcodes;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Player.tick каждый тик пишет noPhysics = isSpectator(); Геральту — всегда true. Без коллизий он не застревает
 * и не задыхается в barrier-рельефе Ведьмака (Entity.isInWall при noPhysics = false). В отличие от образца —
 * только для Геральта: марионетки и игрока клиента (тогда сервер ещё и принимает его ходы без проверки коллизий —
 * ServerGamePacketListenerImpl.handleMovePlayer).
 *
 * По образцу примера minecraft-gta5-passthrough (плагин universal-modder), MIT,
 * Copyright (c) 2026 Rehan and universal-modder contributors.
 */
@Mixin(Player.class)
abstract class PlayerMixin {
	@Inject(method = "tick", at = @At(value = "FIELD", target = "Lnet/minecraft/world/entity/player/Player;noPhysics:Z", opcode = Opcodes.PUTFIELD, shift = At.Shift.AFTER))
	private void mcw$ghost(final CallbackInfo ci) {
		if (McwHooks.isGeralt(this)) {
			((Entity) (Object) this).noPhysics = true;
		}
	}
}
