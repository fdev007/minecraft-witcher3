package mcwc.mixin;

import com.mojang.blaze3d.platform.Window;
import mcwc.Win;
import org.lwjgl.glfw.GLFW;
import org.spongepowered.asm.mixin.Final;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Окно создаётся скрытым, без фокуса и без рамки (рамка — -Dmcwc.decorated=true), а показывает его Win.showBehind уже
 * неактивируемым: показ средствами GLFW активирует окно, если процесс запущен с STARTF_USESHOWWINDOW, а Ведьмак без
 * фокуса встаёт на паузу. Без рамки клиентская область = окно: оверлей снимает его 1:1 поверх Ведьмака.
 */
@Mixin(Window.class)
abstract class WindowMixin {
	@Shadow
	@Final
	private long handle;

	@Inject(method = "<init>", at = @At(value = "INVOKE", target = "Lorg/lwjgl/glfw/GLFW;glfwCreateWindow(IILjava/lang/CharSequence;JJ)J", remap = false))
	private void mcwc$hints(final CallbackInfo ci) {
		GLFW.glfwWindowHint(GLFW.GLFW_VISIBLE, GLFW.GLFW_FALSE);
		GLFW.glfwWindowHint(GLFW.GLFW_FOCUSED, GLFW.GLFW_FALSE);
		GLFW.glfwWindowHint(GLFW.GLFW_FOCUS_ON_SHOW, GLFW.GLFW_FALSE);
		GLFW.glfwWindowHint(GLFW.GLFW_DECORATED, Boolean.getBoolean("mcwc.decorated") ? GLFW.GLFW_TRUE : GLFW.GLFW_FALSE);
	}

	@Inject(method = "<init>", at = @At("TAIL"))
	private void mcwc$show(final CallbackInfo ci) {
		Win.showBehind(this.handle);
	}
}
