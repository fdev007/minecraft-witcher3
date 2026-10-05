package mcwc;

import com.sun.jna.Platform;
import com.sun.jna.Pointer;
import com.sun.jna.platform.win32.User32;
import com.sun.jna.platform.win32.WinDef.HWND;
import com.sun.jna.platform.win32.WinUser;
import org.lwjgl.glfw.GLFWNativeWin32;

/**
 * Окно клиента в Windows (JNA из библиотек Minecraft): без кнопки на панели задач, не активируется ни кликом, ни
 * показом и встаёт под все окна. Ведьмак встаёт на паузу, как только теряет фокус, поэтому окно Майнкрафта фокус не
 * получает никогда; поверх Ведьмака его картинку кладёт оверлей, он же держит окно сразу под Ведьмаком.
 */
public final class Win {
	private static final int WS_EX_TOOLWINDOW = 0x00000080;
	private static final int WS_EX_NOACTIVATE = 0x08000000;
	private static final int WS_EX_APPWINDOW = 0x00040000;
	private static final HWND HWND_BOTTOM = new HWND(Pointer.createConstant(1));

	private Win() {
	}

	/** Окно создано скрытым (WindowMixin): показать его уже неактивируемым, внизу z-порядка. */
	public static void showBehind(final long glfwWindow) {
		if (!Platform.isWindows()) {
			return;
		}

		HWND h = new HWND(new Pointer(GLFWNativeWin32.glfwGetWin32Window(glfwWindow)));
		int ex = User32.INSTANCE.GetWindowLong(h, WinUser.GWL_EXSTYLE);
		User32.INSTANCE.SetWindowLong(h, WinUser.GWL_EXSTYLE, (ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) & ~WS_EX_APPWINDOW);
		User32.INSTANCE.SetWindowPos(h, HWND_BOTTOM, 0, 0, 0, 0,
			WinUser.SWP_NOMOVE | WinUser.SWP_NOSIZE | WinUser.SWP_NOACTIVATE | WinUser.SWP_SHOWWINDOW);
		Mcwc.LOG.info("mcwc: окно показано без фокуса, под остальными");
	}
}
