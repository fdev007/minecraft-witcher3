package mcwc.mixin;

import net.minecraft.client.MouseHandler;
import net.minecraft.client.input.MouseButtonInfo;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Invoker;

/** Ванильные обработчики GLFW-событий мыши: ввод от моста идёт через них (Control). */
@Mixin(MouseHandler.class)
public interface MouseHandlerAccessor {
	@Invoker("onButton")
	void mcwc$onButton(long window, MouseButtonInfo info, int action);

	@Invoker("onScroll")
	void mcwc$onScroll(long window, double dx, double dy);

	@Invoker("onMove")
	void mcwc$onMove(long window, double x, double y);
}
