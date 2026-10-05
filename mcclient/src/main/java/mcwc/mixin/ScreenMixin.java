package mcwc.mixin;

import net.minecraft.client.gui.GuiGraphics;
import net.minecraft.client.gui.screens.Screen;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Курсор поверх экранов MC (инвентарь, верстак, сундук): у скрытого окна системного курсора нет, а системный курсор
 * Ведьмака спрятан. Стрелка в единицах GUI, поверх подсказок (свой слой), ведёт её Control.cursor.
 */
@Mixin(Screen.class)
abstract class ScreenMixin {
	/** Стрелка: 'B' — чёрный контур, 'W' — белая заливка. */
	private static final String[] ARROW = {
		"B",
		"BB",
		"BWB",
		"BWWB",
		"BWWWB",
		"BWWWWB",
		"BWWWWWB",
		"BWWWWWWB",
		"BWWWWBBBB",
		"BWBBWB",
		"BB BWB",
		"B   BWB",
		"     BB",
	};

	@Inject(method = "renderWithTooltipAndSubtitles", at = @At("TAIL"))
	private void mcwc$cursor(final GuiGraphics g, final int mouseX, final int mouseY, final float partial, final CallbackInfo ci) {
		g.nextStratum();
		for (int y = 0; y < ARROW.length; y++) {
			String row = ARROW[y];
			for (int x = 0; x < row.length(); x++) {
				char c = row.charAt(x);
				if (c == 'B' || c == 'W') {
					g.fill(mouseX + x, mouseY + y, mouseX + x + 1, mouseY + y + 1, c == 'B' ? 0xFF000000 : 0xFFFFFFFF);
				}
			}
		}
	}
}
