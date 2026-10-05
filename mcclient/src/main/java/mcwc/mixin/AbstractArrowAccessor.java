package mcwc.mixin;

import net.minecraft.world.entity.projectile.arrow.AbstractArrow;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Invoker;

/** Стрела воткнулась (IN_GROUND, синхронизируется с сервера): мосту — летит она или торчит (Control.arrows). */
@Mixin(AbstractArrow.class)
public interface AbstractArrowAccessor {
	@Invoker("isInGround")
	boolean mcwc$inGround();
}
