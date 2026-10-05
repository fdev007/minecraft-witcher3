package mcwc.mixin;

import net.minecraft.client.gui.components.toasts.Toast;
import net.minecraft.client.gui.components.toasts.ToastManager;
import net.minecraft.client.multiplayer.ClientPacketListener;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Redirect;

/**
 * Без тоста «Chat messages can't be verified» при входе: сервер MCW офлайновый, предупреждение было бы на каждом входе
 * и висело бы поверх Ведьмака. Это единственный тост в handleLogin.
 */
@Mixin(ClientPacketListener.class)
abstract class ClientPacketListenerMixin {
	@Redirect(method = "handleLogin", at = @At(value = "INVOKE", target = "Lnet/minecraft/client/gui/components/toasts/ToastManager;addToast(Lnet/minecraft/client/gui/components/toasts/Toast;)V"))
	private void mcwc$noUnsecureToast(final ToastManager toasts, final Toast toast) {
	}
}
