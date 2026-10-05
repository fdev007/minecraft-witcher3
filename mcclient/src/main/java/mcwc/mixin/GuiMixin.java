package mcwc.mixin;

import com.mojang.blaze3d.pipeline.RenderPipeline;
import mcwc.Control;
import net.minecraft.client.gui.Gui;
import net.minecraft.client.renderer.RenderPipelines;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Redirect;

/**
 * Прицел и индикатор атаки — обычным GUI-пайплайном, а не CROSSHAIR: тот инвертирует цвет под собой, а под прицелом у
 * нас не мир, а ключевой цвет, и прицел выходил зелёным (инверсия magenta). Спрайт прицела белый — белым и будет.
 */
@Mixin(Gui.class)
abstract class GuiMixin {
	@Redirect(method = "renderCrosshair", at = @At(value = "FIELD", target = "Lnet/minecraft/client/renderer/RenderPipelines;CROSSHAIR:Lcom/mojang/blaze3d/pipeline/RenderPipeline;"), require = 1, allow = 8)
	private RenderPipeline mcwc$crosshair() {
		return Control.hideWorld ? RenderPipelines.GUI_TEXTURED : RenderPipelines.CROSSHAIR;
	}
}
