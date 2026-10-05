package mcw.mixin;

import mcw.McwHooks;
import net.minecraft.sounds.SoundEvent;
import net.minecraft.world.entity.projectile.arrow.AbstractArrow;
import net.minecraft.world.phys.Vec3;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * Стрела MC втыкается в стену Ведьмака (ArrowWalls): перед тиком — если стена на этом отрезке пути или уже позади,
 * стрела встаёт в точку попадания, как AbstractArrow.onHitBlock (на 0,05 назад по знакам скорости, звук, тряска, без
 * крита), и дальше тик идёт по ветке «в земле». Блока MC под ней нет — shouldFall для своих (тег mcw_wall) ложь, иначе
 * она тут же упала бы.
 */
@Mixin(AbstractArrow.class)
abstract class AbstractArrowMixin {
	@Shadow
	public int shakeTime;

	@Shadow
	protected abstract void setInGround(boolean inGround);

	@Shadow
	protected abstract SoundEvent getHitGroundSoundEvent();

	@Inject(method = "tick", at = @At("HEAD"))
	private void mcw$wall(final CallbackInfo ci) {
		AbstractArrow self = (AbstractArrow) (Object) this;
		Vec3 w = self.level().isClientSide() ? null : McwHooks.arrowWall(self);
		if (w == null) {
			return;
		}

		Vec3 v = self.getDeltaMovement();
		self.setPos(w.subtract(new Vec3(Math.signum(v.x), Math.signum(v.y), Math.signum(v.z)).scale(0.05)));
		self.setDeltaMovement(Vec3.ZERO);
		self.playSound(this.getHitGroundSoundEvent(), 1.0F, 1.2F / (self.getRandom().nextFloat() * 0.2F + 0.9F));
		this.setInGround(true);
		this.shakeTime = 7;
		self.setCritArrow(false);
		McwHooks.arrowStuck(self);
		self.needsSync = true;
	}

	@Inject(method = "shouldFall", at = @At("HEAD"), cancellable = true)
	private void mcw$stay(final CallbackInfoReturnable<Boolean> cir) {
		if (McwHooks.inWitcherWall(this)) {
			cir.setReturnValue(false);
		}
	}
}
