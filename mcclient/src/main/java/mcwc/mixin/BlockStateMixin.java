package mcwc.mixin;

import mcwc.Solid;
import net.minecraft.core.BlockPos;
import net.minecraft.world.level.BlockGetter;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockBehaviour;
import net.minecraft.world.phys.shapes.CollisionContext;
import net.minecraft.world.phys.shapes.EntityCollisionContext;
import net.minecraft.world.phys.shapes.Shapes;
import net.minecraft.world.phys.shapes.VoxelShape;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * Barrier-рельеф сервера игроку клиента не мешает, пока у него есть земля Ведьмака (Terrain): иначе он стоял бы на
 * округлённых до блока ступенях. Форма barrier для прицела и установки блоков (getShape) не меняется.
 */
@Mixin(BlockBehaviour.BlockStateBase.class)
abstract class BlockStateMixin {
	@Shadow
	public abstract Block getBlock();

	@Inject(method = "getCollisionShape(Lnet/minecraft/world/level/BlockGetter;Lnet/minecraft/core/BlockPos;Lnet/minecraft/world/phys/shapes/CollisionContext;)Lnet/minecraft/world/phys/shapes/VoxelShape;", at = @At("HEAD"), cancellable = true)
	private void mcwc$noBarrier(final BlockGetter level, final BlockPos pos, final CollisionContext ctx, final CallbackInfoReturnable<VoxelShape> cir) {
		if (this.getBlock() == Blocks.BARRIER && ctx instanceof EntityCollisionContext e && Solid.active(e.getEntity())) {
			cir.setReturnValue(Shapes.empty());
		}
	}
}
