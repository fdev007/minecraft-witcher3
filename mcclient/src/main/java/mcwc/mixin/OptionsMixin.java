package mcwc.mixin;

import net.minecraft.client.InactivityFpsLimit;
import net.minecraft.client.Options;
import net.minecraft.client.tutorial.TutorialSteps;
import net.minecraft.sounds.SoundSource;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Настройки, без которых скрытый клиент не работает, — после каждой загрузки options.txt (Options.load зовётся из
 * конструктора Minecraft, до первого экрана; RETURN, а не TAIL: без файла, на первом запуске, load выходит сразу): без
 * паузы и без снижения FPS вне фокуса, без виньетки (она затемнила бы края Ведьмака), без музыки, обучения, онбординга
 * и предупреждения о сторонних серверах. Дальность прорисовки — минимум для луча прицела: мир всё равно не рисуется.
 */
@Mixin(Options.class)
abstract class OptionsMixin {
	@Inject(method = "load", at = @At("RETURN"))
	private void mcwc$force(final CallbackInfo ci) {
		Options o = (Options) (Object) this;
		o.pauseOnLostFocus = false;
		o.onboardAccessibility = false;
		o.skipMultiplayerWarning = true;
		o.joinedFirstServer = true;
		o.tutorialStep = TutorialSteps.NONE;
		o.inactivityFpsLimit().set(InactivityFpsLimit.MINIMIZED);
		o.vignette().set(false);
		o.realmsNotifications().set(false);
		o.renderDistance().set(4);
		o.getSoundSourceOptionInstance(SoundSource.MUSIC).set(0.0);
		// окна не видно, громкость в нём не убавить: звук клиента выключен целиком, вернуть — -Dmcwc.sound=true
		if (!Boolean.getBoolean("mcwc.sound")) {
			o.getSoundSourceOptionInstance(SoundSource.MASTER).set(0.0);
		}
	}
}
