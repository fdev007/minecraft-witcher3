package mcwc.mixin;

import com.mojang.blaze3d.systems.CommandEncoder;
import com.mojang.blaze3d.textures.GpuTexture;
import mcwc.Control;
import mcwc.Mcwc;
import net.minecraft.client.Minecraft;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.Redirect;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * Окно всегда «в фокусе» (настоящего фокуса у него не бывает, см. WindowMixin), заголовок постоянный — по нему окно
 * находит оверлей, — а кадр в мире очищается ключевым цветом: мир не рисуется (GameRendererMixin), и всё, кроме руки и
 * HUD, оверлей вырежет.
 */
@Mixin(Minecraft.class)
abstract class MinecraftMixin {
	@Inject(method = "createTitle", at = @At("HEAD"), cancellable = true)
	private void mcwc$title(final CallbackInfoReturnable<String> cir) {
		cir.setReturnValue(Mcwc.TITLE);
	}

	@Inject(method = "isWindowActive", at = @At("HEAD"), cancellable = true)
	private void mcwc$active(final CallbackInfoReturnable<Boolean> cir) {
		cir.setReturnValue(true);
	}

	@Redirect(method = "runTick", at = @At(value = "INVOKE", target = "Lcom/mojang/blaze3d/systems/CommandEncoder;clearColorAndDepthTextures(Lcom/mojang/blaze3d/textures/GpuTexture;ILcom/mojang/blaze3d/textures/GpuTexture;D)V"))
	private void mcwc$clear(final CommandEncoder enc, final GpuTexture color, final int argb, final GpuTexture depth, final double d) {
		boolean key = Control.hideWorld && ((Minecraft) (Object) this).level != null;
		enc.clearColorAndDepthTextures(color, key ? Mcwc.KEY : argb, depth, d);
	}
}
