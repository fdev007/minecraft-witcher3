package mcw;

import io.netty.channel.ChannelFutureListener;
import java.util.Set;
import net.minecraft.network.Connection;
import net.minecraft.network.DisconnectionDetails;
import net.minecraft.network.chat.Component;
import net.minecraft.network.protocol.Packet;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.network.CommonListenerCookie;
import net.minecraft.server.network.ServerGamePacketListenerImpl;
import net.minecraft.world.entity.PositionMoveRotation;
import net.minecraft.world.entity.Relative;
import org.jspecify.annotations.Nullable;

/**
 * Игровой listener Геральта: все исходящие пакеты в никуда (иначе они копятся в EmbeddedChannel без предела),
 * отключения игнорируем — Геральт уходит только вместе с сервером. Схема из fabric-carpet 1.4.194
 * (carpet.patches.NetHandlerPlayServerFake, MIT, Copyright (c) 2020 gnembon). Подставляется миксином
 * {@link mcw.mixin.PlayerListMixin} в PlayerList.placeNewPlayer.
 */
public final class FakeGameListener extends ServerGamePacketListenerImpl {
	public FakeGameListener(final MinecraftServer server, final Connection connection, final ServerPlayer player, final CommonListenerCookie cookie) {
		super(server, connection, player, cookie);
	}

	@Override
	public void send(final Packet<?> packet) {
	}

	@Override
	public void send(final Packet<?> packet, final @Nullable ChannelFutureListener listener) {
	}

	@Override
	public void disconnect(final Component reason) {
		Mcw.LOG.info("mcw: {} не отключаем: {}", this.player.getGameProfile().name(), reason.getString());
	}

	@Override
	public void disconnect(final DisconnectionDetails details) {
		Mcw.LOG.info("mcw: {} не отключаем: {}", this.player.getGameProfile().name(), details.reason().getString());
	}

	/**
	 * Клиента нет — ждать его загрузки нечего. Ванильный listener после смерти ставит waitingForRespawn и снимает
	 * его только в handleClientCommand(PERFORM_RESPAWN), которого не будет: без этого Геральт после первой смерти
	 * навсегда неуязвим (ServerPlayer.isInvulnerableTo проверяет hasClientLoaded).
	 */
	@Override
	public boolean hasClientLoaded() {
		return true;
	}

	/**
	 * /tp, эндер-жемчуг: сервер сдвинул Геральта сам — подгружаем чанки и сообщаем мосту. При смене измерения
	 * и при респауне игрока в новом уровне ещё нет (ServerPlayer.teleport зовёт нас до addDuringTeleport) — тогда
	 * мосту сообщают {@link GeraltPlayer#teleport} и {@link Bridge#onRespawn}.
	 */
	@Override
	public void teleport(final PositionMoveRotation pos, final Set<Relative> relative) {
		super.teleport(pos, relative);
		if (this.player.level().getPlayerByUUID(this.player.getUUID()) != null) {
			this.resetPosition();
			this.player.level().getChunkSource().move(this.player);
			if (this.player instanceof GeraltPlayer g) {
				g.onServerTeleport();
			}
		}
	}
}
