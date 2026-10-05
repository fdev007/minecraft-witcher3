package mcw.mixin;

import mcw.McwHooks;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.level.ServerExplosion;
import net.minecraft.world.phys.Vec3;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * Взрыв (TNT, крипер, кровать...) — мосту как эффект; снесённые блоки придут обычным blocks.
 *
 * По образцу примера minecraft-gta5-passthrough (плагин universal-modder), MIT,
 * Copyright (c) 2026 Rehan and universal-modder contributors.
 */
@Mixin(ServerExplosion.class)
abstract class ServerExplosionMixin {
	@Shadow
	public abstract ServerLevel level();

	@Shadow
	public abstract Vec3 center();

	@Shadow
	public abstract float radius();

	@Shadow
	public abstract Entity getDirectSourceEntity();

	@Inject(method = "explode", at = @At("HEAD"))
	private void mcw$explode(final CallbackInfoReturnable<Integer> cir) {
		Entity src = this.getDirectSourceEntity();
		McwHooks.explosion(this.level(), this.center(), this.radius(), src == null ? "" : BuiltInRegistries.ENTITY_TYPE.getKey(src.getType()).getPath());
	}
}
