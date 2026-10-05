package mcw;

import java.io.IOException;
import java.util.UUID;
import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.entity.event.v1.ServerLivingEntityEvents;
import net.fabricmc.fabric.api.entity.event.v1.ServerPlayerEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerChunkEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerEntityEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.TickTask;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.level.gamerules.GameRules;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Серверная половина мэшапа Minecraft × The Witcher 3: мир блоков вокруг Геральта, ServerPlayer на его месте
 * и TCP-связь с мостом (bridge.py). Картинку рисует Ведьмак, здесь только правила.
 *
 * Геральт — либо марионетка {@link GeraltPlayer} (клиента нет), либо игрок скрытого клиента Майнкрафта (mcclient), который
 * зашёл под тем же именем: марионетка уходит при его входе и возвращается, когда он выходит. UUID один (offline), так
 * что инвентарь и позиция у них общие — playerdata.
 */
public final class Mcw implements ModInitializer {
	public static final Logger LOG = LoggerFactory.getLogger("mcw");
	/** Вход клиента сорвался после того, как марионетку убрали: столько тиков без Геральта — марионетку обратно. */
	private static final int NO_GERALT_TICKS = 200;
	private static volatile MinecraftServer server;
	private static volatile McwConfig config;
	private static volatile Link link;
	private static volatile ServerPlayer geralt;
	private static int noGeraltTicks;

	@Override
	public void onInitialize() {
		config = McwConfig.load(FabricLoader.getInstance().getConfigDir());
		ServerLifecycleEvents.SERVER_STARTED.register(Mcw::started);
		ServerLifecycleEvents.SERVER_STOPPING.register(s -> {
			Link l = link;
			if (l != null) {
				l.stop();
			}

			Bridge.reset();
		});
		ServerTickEvents.END_SERVER_TICK.register(Mcw::tick);
		ServerChunkEvents.CHUNK_LOAD.register(Bridge::onChunkLoad);
		ServerChunkEvents.CHUNK_UNLOAD.register(Bridge::onChunkUnload);
		ServerEntityEvents.ENTITY_LOAD.register((e, level) -> {
			Npcs.onLoad(e);       // прокси NPC прошлого сеанса
			ArrowWalls.onLoad(e); // новая стрела: путь мосту, стены Ведьмака на нём
		});
		ServerTickEvents.START_SERVER_TICK.register(s -> Npcs.tick()); // прокси — туда, где NPC сейчас
		ServerPlayerEvents.JOIN.register(Mcw::joined);
		ServerPlayerEvents.LEAVE.register(Mcw::left);
		ServerPlayerEvents.AFTER_RESPAWN.register((old, now, alive) -> {
			if (old == geralt && !(now instanceof GeraltPlayer)) {
				Bridge.onRespawn(now); // марионетка зовёт сама из GeraltPlayer.respawnNow
			}
		});
		ServerLivingEntityEvents.AFTER_DEATH.register((e, src) -> {
			if (e instanceof ServerPlayer p) {
				Bridge.onClientDeath(p, src);
			}
		});
		LOG.info("mcw {} loaded, bridge port {}", version(), config.port);
	}

	private static void started(final MinecraftServer s) {
		server = s;
		// игрок клиента идёт за Ведьмаком сам: быстрое перемещение — прыжок на сотни блоков за пакет, проверка «moved too
		// quickly» его бы откатывала; смерть — сразу возрождение, экрана смерти в скрытом окне никто не увидит, и без
		// потери инвентаря
		GameRules rules = s.overworld().getGameRules();
		rules.set(GameRules.PLAYER_MOVEMENT_CHECK, false, s);
		rules.set(GameRules.IMMEDIATE_RESPAWN, true, s);
		rules.set(GameRules.KEEP_INVENTORY, true, s);   // стартовый набор (Kit) и всё добытое не пропадают при смерти
		// сами монстры MC не появляются: Геральт в MC не спит (фантомы бессонницы летали ящиками и убивали его), а ночью
		// у его ног вылезали бы зомби и скелеты — в Ведьмаке свои чудовища. Призыв (spawn, F6) и лошадь работают
		rules.set(GameRules.SPAWN_PHANTOMS, false, s);
		rules.set(GameRules.SPAWN_MONSTERS, false, s);
		rules.set(GameRules.SPAWN_PATROLS, false, s);
		rules.set(GameRules.SPAWN_WANDERING_TRADERS, false, s);
		geralt = GeraltPlayer.spawn(s, config);
		Kit.ensure(geralt);
		Link l = new Link(s, config);
		try {
			l.start();
		} catch (IOException e) {
			// без связи мод бесполезен, но сервер оставляем жить: виден лог и можно зайти отладочным клиентом
			LOG.error("mcw: не открыл {}:{}", config.host, config.port, e);
		}

		link = l;
	}

	private static void tick(final MinecraftServer s) {
		if (geralt == null && s.isRunning() && ++noGeraltTicks > NO_GERALT_TICKS) {
			LOG.warn("mcw: клиент {} так и не вошёл - возвращаю марионетку", config.name);
			puppetBack(s);
		}

		Bridge.tick(s);
	}

	/**
	 * Вход игрока с UUID Геральта (PlayerList.disconnectAllPlayersWithProfile, миксин): марионетку убираем как отключённого
	 * игрока — с сохранением playerdata, — иначе ваниль ждала бы её отключения, а марионетка не отключается.
	 */
	static void dropPuppet(final UUID id) {
		if (geralt instanceof GeraltPlayer g && g.getUUID().equals(id)) {
			LOG.info("mcw: входит клиент {} - марионетка уходит", config.name);
			geralt = null;
			noGeraltTicks = 0;
			g.disconnect();
			server.getPlayerList().remove(g);
		}
	}

	private static void joined(final ServerPlayer p) {
		if (p instanceof GeraltPlayer || !p.getGameProfile().name().equals(config.name)) {
			return;
		}

		dropPuppet(p.getUUID()); // марионетка успела вернуться, пока клиент входил
		Bridge.onGeraltChanged(p);
	}

	private static void left(final ServerPlayer p) {
		if (p == geralt && !(p instanceof GeraltPlayer)) {
			LOG.info("mcw: клиент {} вышел - марионетка вернётся", config.name);
			geralt = null;
			noGeraltTicks = 0;
			// после remove: playerdata клиента уже сохранена, марионетка подхватит позицию и инвентарь
			later(() -> {
				if (geralt == null && server.isRunning()) {
					puppetBack(server);
				}
			});
		}
	}

	private static void puppetBack(final MinecraftServer s) {
		noGeraltTicks = 0;
		Bridge.onGeraltChanged(GeraltPlayer.spawn(s, config));
	}

	/** Задача в очередь сервера — всегда в очередь, даже с потока сервера (execute там выполнил бы сразу). */
	static void later(final Runnable r) {
		MinecraftServer s = server;
		if (s != null) {
			s.schedule(new TickTask(s.getTickCount(), r));
		}
	}

	static MinecraftServer server() {
		return server;
	}

	static McwConfig config() {
		return config;
	}

	static Link link() {
		return link;
	}

	/** null — Геральт меняется (клиент входит или вышел). */
	static ServerPlayer geralt() {
		return geralt;
	}

	static void setGeralt(final ServerPlayer p) {
		geralt = p;
	}

	static String version() {
		return FabricLoader.getInstance().getModContainer("mcw").map(c -> c.getMetadata().getVersion().getFriendlyString()).orElse("?");
	}
}
