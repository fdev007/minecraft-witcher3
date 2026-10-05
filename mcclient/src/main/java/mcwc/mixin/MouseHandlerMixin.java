package mcwc.mixin;

import net.minecraft.client.Minecraft;
import net.minecraft.client.MouseHandler;
import org.spongepowered.asm.mixin.Final;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * «Захват мыши» без GLFW: системный курсор принадлежит Ведьмаку. Флаг mouseGrabbed всё равно нужен — без него
 * Minecraft.handleKeybinds не держит удар (continueAttack), а клик без захвата только захватывает мышь. Обзор мышью
 * клиенту не нужен: взгляд присылает мост (look, pose). Остальное — как в ванильных grabMouse и releaseMouse.
 */
@Mixin(MouseHandler.class)
abstract class MouseHandlerMixin {
	@Shadow
	private boolean mouseGrabbed;

	@Shadow
	@Final
	private Minecraft minecraft;

	@Inject(method = "grabMouse", at = @At("HEAD"), cancellable = true)
	private void mcwc$grab(final CallbackInfo ci) {
		ci.cancel();
		if (!this.mouseGrabbed) {
			this.mouseGrabbed = true;
			this.minecraft.setScreen(null);
			this.minecraft.missTime = 10000;
		}
	}

	@Inject(method = "releaseMouse", at = @At("HEAD"), cancellable = true)
	private void mcwc$release(final CallbackInfo ci) {
		ci.cancel();
		this.mouseGrabbed = false;
	}
}
