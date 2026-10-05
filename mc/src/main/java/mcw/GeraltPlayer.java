package mcw;

import com.mojang.authlib.GameProfile;
import java.util.Optional;
import net.minecraft.core.UUIDUtil;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.TickTask;
import net.minecraft.server.level.ClientInformation;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.network.CommonListenerCookie;
import net.minecraft.util.ProblemReporter;
import net.minecraft.world.damagesource.DamageSource;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.level.portal.TeleportTransition;
import net.minecraft.world.level.storage.TagValueInput;
import net.minecraft.world.level.storage.ValueInput;
import net.minecraft.world.phys.Vec3;
import org.jspecify.annotations.Nullable;

/**
 * Настоящий ServerPlayer на месте Геральта: он в PlayerList (сервер не уходит в паузу, у измерения есть тикеты
 * PLAYER_SIMULATION — сущности не замерзают), грузит чанки вокруг себя, ломает и ставит блоки по правилам
 * выживания. Двигает его только мост (pos), физики у него нет (PlayerMixin: noPhysics), гравитации тоже.
 *
 * Схема создания и тика — из fabric-carpet 1.4.194 (carpet.patches.EntityPlayerMPFake, MIT,
 * Copyright (c) 2020 gnembon): placeNewPlayer с FakeConnection, ручной loadPlayerData (placeNewPlayer в 1.21.11
 * playerdata не читает), doTick() из tick() — клиентского соединения в ServerConnectionListener нет, тикать
 * игрока, кроме нас, некому; teleport(TeleportTransition) сам снимает флаг смены измерения.
 */
public final class GeraltPlayer extends ServerPlayer {
	/** Куда поставил мост; сервер Геральта сам не двигает. */
	private Vec3 hold;
	private float holdYaw;
	private float holdPitch;

	public GeraltPlayer(final MinecraftServer server, final ServerLevel level, final GameProfile profile, final ClientInformation info) {
		super(server, level, profile, info);
	}

	/** Поток сервера, SERVER_STARTED. */
	static GeraltPlayer spawn(final MinecraftServer server, final McwConfig cfg) {
		ServerLevel level = server.overworld();
		GameProfile profile = new GameProfile(UUIDUtil.createOfflinePlayerUUID(cfg.name), cfg.name);
		GeraltPlayer p = new GeraltPlayer(server, level, profile, ClientInformation.createDefault());
		p.snapTo(cfg.spawnX, cfg.spawnY, cfg.spawnZ, 0.0F, 0.0F);
		server.getPlayerList().placeNewPlayer(new FakeConnection(), p, new CommonListenerCookie(profile, 0, p.clientInformation(), false));
		boolean saved = loadSaved(p);
		p.setup();
		// сохранённая позиция — где Геральт стоял при остановке; без неё — spawn из конфига
		p.moveTo(p.getX(), p.getY(), p.getZ(), p.getYRot(), p.getXRot());
		Mcw.LOG.info("mcw: {} в мире ({}), {} {} {}", cfg.name, saved ? "из playerdata" : "новый", p.getX(), p.getY(), p.getZ());
		return p;
	}

	private static boolean loadSaved(final GeraltPlayer p) {
		try (ProblemReporter.ScopedCollector problems = new ProblemReporter.ScopedCollector(p.problemPath(), Mcw.LOG)) {
			Optional<ValueInput> in = p.level().getServer().getPlayerList().loadPlayerData(p.nameAndId())
				.map(tag -> TagValueInput.create(problems, p.registryAccess(), tag));
			in.ifPresent(v -> {
				p.load(v);
				p.loadAndSpawnEnderPearls(v);
			});
			return in.isPresent();
		}
	}

	/** То, что не сохраняется или должно быть так всегда, — после создания и после респауна. */
	private void setup() {
		this.setNoGravity(true);
		this.setDeltaMovement(Vec3.ZERO);
		this.getAbilities().mayfly = true;
		this.getAbilities().flying = true;
		this.onUpdateAbilities();
	}

	/** Позиция от моста (pos или act с p). */
	public void moveTo(final double x, final double y, final double z, final float yaw, final float pitch) {
		this.hold = new Vec3(x, y, z);
		this.holdYaw = yaw;
		this.holdPitch = pitch;
		this.absSnapTo(x, y, z, yaw, pitch);
		this.setYHeadRot(yaw);
		this.setDeltaMovement(Vec3.ZERO);
		this.connection.resetPosition();
		this.level().getChunkSource().move(this);
	}

	/**
	 * Смена измерения (/execute in ... tp, портал). Флаг isChangingDimension ванильный listener снимает только по
	 * пакету клиента (подтверждение телепорта); без клиента — сами, как Carpet (EntityPlayerMPFake.teleport).
	 * Иначе ServerPlayer.isInvulnerableTo навсегда true. FakeGameListener.teleport внутри super мосту не сказал:
	 * в новом уровне игрока тогда ещё не было — говорим здесь.
	 */
	@Override
	public @Nullable ServerPlayer teleport(final TeleportTransition t) {
		ServerLevel was = this.level();
		ServerPlayer r = super.teleport(t);
		if (this.isChangingDimension()) {
			this.hasChangedDimension();
		}

		if (r != null && this.level() != was) {
			this.connection.resetPosition();
			this.level().getChunkSource().move(this);
			this.onServerTeleport();
		}

		return r;
	}

	/**
	 * Портал выхода из Края: ваниль убирает игрока из мира и ждёт от клиента PERFORM_RESPAWN после титров.
	 * Клиента нет — возрождаемся сами, как handleClientCommand при wonGame (с инвентарём, в точке возрождения).
	 */
	@Override
	public void showEndCredits() {
		super.showEndCredits();
		MinecraftServer server = this.level().getServer();
		server.schedule(new TickTask(server.getTickCount(), () -> this.respawnNow(true)));
	}

	/** Сервер сдвинул Геральта сам (/tp, жемчуг, портал): принимаем как новую точку и говорим мосту. */
	void onServerTeleport() {
		if (this.hold == null) {
			return;
		}

		this.hold = this.position();
		this.holdYaw = this.getYRot();
		this.holdPitch = this.getXRot();
		Bridge.onTeleport(this);
	}

	@Override
	public void tick() {
		if (this.level().getServer().getTickCount() % 10 == 0) {
			this.connection.resetPosition();
			this.level().getChunkSource().move(this);
		}

		super.tick();
		this.doTick();
		// на месте, где поставил мост: никакой инерции, отталкиваний и падений
		if (this.hold != null && !this.isRemoved() && this.position().distanceToSqr(this.hold) > 1.0E-6) {
			this.absSnapTo(this.hold.x, this.hold.y, this.hold.z, this.holdYaw, this.holdPitch);
		}

		this.setDeltaMovement(Vec3.ZERO);
		// Геральт в Ведьмаке стоит на земле; в воздухе Player.getDestroySpeed режет скорость копания в 5 раз
		this.setOnGround(true);
	}

	/**
	 * Подбор с земли: Player.aiStep (из doTick) трогает предметы в рамке +1/+0.5/+1 блока вокруг Геральта, ItemEntity
	 * сам кладёт стопку в инвентарь и зовёт take. noPhysics и удержание позиции подбору не мешают. inv — в этом же тике.
	 */
	@Override
	public void take(final Entity entity, final int amount) {
		super.take(entity, amount);
		Bridge.inventoryChanged();
	}

	@Override
	public void die(final DamageSource cause) {
		// текст до super.die: ServerPlayer.die в конце зовёт CombatTracker.recheckStatus, и после него остаётся
		// только обобщённое «Geralt died»
		String msg = this.getCombatTracker().getDeathMessage().getString();
		super.die(cause);
		Bridge.onDeath(msg);
		// не изнутри тика сущностей: respawn удаляет игрока из мира
		MinecraftServer server = this.level().getServer();
		server.schedule(new TickTask(server.getTickCount(), () -> this.respawnNow(false)));
	}

	/** fromEnd — после портала выхода из Края (инвентарь сохраняется), иначе — после смерти. */
	private void respawnNow(final boolean fromEnd) {
		MinecraftServer server = this.level().getServer();
		if (!server.getPlayerList().getPlayers().contains(this)) {
			return;
		}

		// PlayerListMixin вернёт новый GeraltPlayer; listener клиента делает это сам в handleClientCommand
		ServerPlayer next;
		if (fromEnd) {
			this.wonGame = false;
			next = server.getPlayerList().respawn(this, true, Entity.RemovalReason.CHANGED_DIMENSION);
		} else {
			next = server.getPlayerList().respawn(this, false, Entity.RemovalReason.KILLED);
		}

		next.connection.player = next;
		if (next instanceof GeraltPlayer g) {
			g.setup();
			if (this.hold != null) {
				g.moveTo(this.hold.x, this.hold.y, this.hold.z, this.holdYaw, this.holdPitch);
			}

			Bridge.onRespawn(g);
		}
	}

	@Override
	public String getIpAddress() {
		return "127.0.0.1";
	}
}
