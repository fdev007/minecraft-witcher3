package mcw.mixin;

import mcw.McwHooks;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.damagesource.DamageSource;
import net.minecraft.world.entity.decoration.ArmorStand;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.phys.Vec3;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * Стойка-прокси NPC (Npcs): урон — мосту, а не поломка стойки (ArmorStand.hurtServer свой, без super); ПКМ по ней не
 * меняет предметы в руке и на стойке (ArmorStand.interactAt).
 */
@Mixin(ArmorStand.class)
abstract class ArmorStandMixin {
	@Inject(method = "hurtServer", at = @At("HEAD"), cancellable = true)
	private void mcw$npc(final ServerLevel level, final DamageSource src, final float amount, final CallbackInfoReturnable<Boolean> cir) {
		if (McwHooks.npcHurt(this, src, amount)) {
			cir.setReturnValue(true);
		}
	}

	@Inject(method = "interactAt", at = @At("HEAD"), cancellable = true)
	private void mcw$noSwap(final Player player, final Vec3 at, final InteractionHand hand, final CallbackInfoReturnable<InteractionResult> cir) {
		if (McwHooks.isNpc(this)) {
			cir.setReturnValue(InteractionResult.PASS);
		}
	}
}
