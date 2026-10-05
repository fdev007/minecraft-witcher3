package mcwc.mixin;

import mcwc.NpcProxies;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntityDimensions;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.Pose;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/** Размер прокси NPC Ведьмака — ширина и рост NPC от моста (NpcProxies), как на сервере, а не стойки для брони. */
@Mixin(LivingEntity.class)
abstract class LivingEntityMixin {
	@Inject(method = "getDimensions", at = @At("HEAD"), cancellable = true)
	private void mcwc$npcSize(final Pose pose, final CallbackInfoReturnable<EntityDimensions> cir) {
		EntityDimensions d = NpcProxies.dims((Entity) (Object) this);
		if (d != null) {
			cir.setReturnValue(d);
		}
	}
}
