package mcwc.mixin;

import net.minecraft.client.KeyboardHandler;
import net.minecraft.client.input.CharacterEvent;
import net.minecraft.client.input.KeyEvent;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Invoker;

/** Ванильные обработчики GLFW-событий клавиатуры: ввод от моста идёт через них (Control). */
@Mixin(KeyboardHandler.class)
public interface KeyboardHandlerAccessor {
	@Invoker("keyPress")
	void mcwc$keyPress(long window, int action, KeyEvent event);

	@Invoker("charTyped")
	void mcwc$charTyped(long window, CharacterEvent event);
}
