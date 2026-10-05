package mcwc.mixin;

import net.minecraft.client.Camera;
import net.minecraft.client.renderer.LevelRenderer;
import net.minecraft.client.renderer.RenderBuffers;
import net.minecraft.client.renderer.state.LevelRenderState;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;
import org.spongepowered.asm.mixin.gen.Invoker;

/** Рамка блока под прицелом без рендера мира (GameRendererMixin): ванильное извлечение, буферы LevelRenderer. */
@Mixin(LevelRenderer.class)
public interface LevelRendererAccessor {
	@Accessor("renderBuffers")
	RenderBuffers mcwc$renderBuffers();

	@Accessor("levelRenderState")
	LevelRenderState mcwc$levelRenderState();

	@Invoker("extractBlockOutline")
	void mcwc$extractBlockOutline(Camera camera, LevelRenderState state);
}
