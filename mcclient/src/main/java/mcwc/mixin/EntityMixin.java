package mcwc.mixin;

import java.util.List;
import mcwc.Solid;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.level.Level;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.shapes.VoxelShape;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Redirect;

/**
 * Коллизия Ведьмака (Solid: дома, стены, полы; Terrain: земля) — в столкновения игрока клиента и его лошади: к
 * «столкновениям с сущностями» в Entity.collide. Тот же список идёт и в подъём на ступеньку (collectColliders), поэтому
 * склоны из колонн с разницей до 0,6 и ступени лестниц проходятся сами.
 */
@Mixin(Entity.class)
abstract class EntityMixin {
	@Redirect(method = "collide", at = @At(value = "INVOKE", target = "Lnet/minecraft/world/level/Level;getEntityCollisions(Lnet/minecraft/world/entity/Entity;Lnet/minecraft/world/phys/AABB;)Ljava/util/List;"))
	private List<VoxelShape> mcwc$terrain(final Level level, final Entity entity, final AABB box) {
		List<VoxelShape> base = level.getEntityCollisions(entity, box);
		return Solid.active(entity) ? Solid.withShapes(base, box) : base;
	}
}
