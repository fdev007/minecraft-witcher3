package mcwc;

import java.io.IOException;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientLifecycleEvents;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.loader.api.FabricLoader;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Скрытый клиент Майнкрафта для мэшапа с Ведьмаком (docs/DEVELOPMENT.md, «Что дальше», п. 1). Играет Геральтом на сервере MCW
 * (..\mc), но мир не рисует: кадр — ключевой цвет, поверх него рука, предмет и HUD. Окно стоит за окном Ведьмака и
 * никогда не получает фокус (Ведьмак без фокуса встаёт на паузу), картинку снимает и кладёт поверх Ведьмака
 * ..\overlay\mcw_overlay.exe. Ввод и позу присылает мост (..\bridge) по TCP — {@link Link}, разбор — {@link Control}.
 *
 * Настройки — системные свойства JVM: -Dmcwc.port=25601 (порт для моста), -Dmcwc.decorated=true (окно с рамкой, для
 * отладки).
 */
public final class Mcwc implements ClientModInitializer {
	public static final Logger LOG = LoggerFactory.getLogger("mcwc");
	/** Заголовок окна: по нему его находит оверлей. */
	public static final String TITLE = "MCW Client";
	/** Ключевой цвет фона (ARGB): magenta, а не зелёный — зелёная в HUD полоска опыта. Оверлей: --key magenta. */
	public static final int KEY = 0xFFFF00FF;
	private static Link link;

	@Override
	public void onInitializeClient() {
		int port = Integer.getInteger("mcwc.port", 25601);
		link = new Link(port);
		try {
			link.start();
		} catch (IOException e) {
			LOG.error("mcwc: не открыл 127.0.0.1:{} для моста", port, e);
		}

		ClientTickEvents.START_CLIENT_TICK.register(Control::startTick);
		ClientTickEvents.END_CLIENT_TICK.register(Control::endTick);
		ClientLifecycleEvents.CLIENT_STOPPING.register(c -> link.stop());
		LOG.info("mcwc {} loaded, bridge port {}", version(), port);
	}

	static Link link() {
		return link;
	}

	static String version() {
		return FabricLoader.getInstance().getModContainer("mcwc").map(c -> c.getMetadata().getVersion().getFriendlyString()).orElse("?");
	}
}
