package mcw.mixin;

import mcw.McwHooks;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.damagesource.DamageSource;
import net.minecraft.world.entity.EntityDimensions;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.Pose;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * Урон по прокси NPC Ведьмака (Npcs) не наносится, а уходит мосту: удар и стрела MC отрабатывают как по мобу (ответ —
 * «попал»), здоровье прокси не меняется, смерти нет. Прокси — стойка для брони переопределяет hurtServer целиком — ArmorStandMixin. Размер прокси — ширина и рост NPC Ведьмака
 * (getDimensions), а не стойки.
 */
@Mixin(LivingEntity.class)
abstract class LivingEntityMixin {
	@Inject(method = "hurtServer", at = @At("HEAD"), cancellable = true)
	private void mcw$npc(final ServerLevel level, final DamageSource src, final float amount, final CallbackInfoReturnable<Boolean> cir) {
		if (McwHooks.npcHurt(this, src, amount)) {
			cir.setReturnValue(true);
		}
	}

	@Inject(method = "getDimensions", at = @At("HEAD"), cancellable = true)
	private void mcw$npcSize(final Pose pose, final CallbackInfoReturnable<EntityDimensions> cir) {
		EntityDimensions d = McwHooks.npcDims(this);
		if (d != null) {
			cir.setReturnValue(d);
		}
	}
}
