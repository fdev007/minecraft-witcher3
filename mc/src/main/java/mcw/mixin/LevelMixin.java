package mcw.mixin;

import mcw.McwHooks;
import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.state.BlockState;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * Единая точка всех изменений блоков: игрок, песок, вода, взрывы, команды идут через Level.setBlock(pos,state,
 * flags,recursion). Здесь только позиция; итоговое состояние BlockSync читает в конце тика.
 *
 * По образцу примера minecraft-gta5-passthrough (плагин universal-modder), MIT,
 * Copyright (c) 2026 Rehan and universal-modder contributors.
 */
@Mixin(Level.class)
abstract class LevelMixin {
	@Inject(method = "setBlock(Lnet/minecraft/core/BlockPos;Lnet/minecraft/world/level/block/state/BlockState;II)Z", at = @At("RETURN"))
	private void mcw$changed(final BlockPos pos, final BlockState state, final int flags, final int recursionLeft, final CallbackInfoReturnable<Boolean> cir) {
		if (cir.getReturnValueZ() && (Object) this instanceof ServerLevel level) {
			McwHooks.blockChanged(level, pos);
		}
	}
}
