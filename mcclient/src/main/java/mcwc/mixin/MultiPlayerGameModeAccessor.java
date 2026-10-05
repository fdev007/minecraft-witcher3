package mcwc.mixin;

import net.minecraft.client.multiplayer.MultiPlayerGameMode;
import net.minecraft.core.BlockPos;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;

/** Какой блок копает игрок (трещины без рендера мира, GameRendererMixin); стадия — ванильный getDestroyStage. */
@Mixin(MultiPlayerGameMode.class)
public interface MultiPlayerGameModeAccessor {
	@Accessor("destroyBlockPos")
	BlockPos mcwc$destroyBlockPos();
}
