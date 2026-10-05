package mcwc.mixin;

import mcwc.Control;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.phys.Vec3;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Пока клиент идёт за Ведьмаком (Control.follow), своего движения у игрока нет: ни гравитации, ни инерции, ни
 * выталкивания из barrier-рельефа — позицию ставит Control.startTick. aiStep не трогаем: там же взмах руки
 * (updateSwingTime) и покачивание предмета.
 */
@Mixin(Player.class)
abstract class PlayerMixin {
	@Inject(method = "travel", at = @At("HEAD"), cancellable = true)
	private void mcwc$hold(final Vec3 input, final CallbackInfo ci) {
		if (Control.follow && (Object) this instanceof LocalPlayer) {
			ci.cancel();
		}
	}
}
