package mcwc.mixin;

import mcwc.Control;
import net.minecraft.client.renderer.LevelRenderer;
import net.minecraft.core.BlockPos;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * Экран «Loading terrain» ждёт, пока секция под игроком скомпилируется и станет видна (LevelLoadTracker.WaitingForPlayerChunk).
 * Мир мы не рисуем, секции не компилируются, и вход каждый раз шёл по таймауту в 30 с. Пока мир спрятан, секция
 * «готова» сразу; внутри самого LevelRenderer этот метод нужен только рендеру мира, а он пропущен.
 */
@Mixin(LevelRenderer.class)
abstract class LevelRendererMixin {
	@Inject(method = "isSectionCompiledAndVisible", at = @At("HEAD"), cancellable = true)
	private void mcwc$ready(final BlockPos pos, final CallbackInfoReturnable<Boolean> cir) {
		if (Control.hideWorld) {
			cir.setReturnValue(true);
		}
	}
}
