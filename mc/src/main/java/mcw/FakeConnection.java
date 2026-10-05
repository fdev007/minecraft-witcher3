package mcw;

import io.netty.channel.embedded.EmbeddedChannel;
import mcw.mixin.ConnectionAccessor;
import net.minecraft.network.Connection;
import net.minecraft.network.PacketListener;
import net.minecraft.network.ProtocolInfo;
import net.minecraft.network.protocol.PacketFlow;

/**
 * Соединение без клиента для Геральта. Схема из fabric-carpet 1.4.194 (carpet.patches.FakeClientConnection,
 * MIT, Copyright (c) 2020 gnembon): EmbeddedChannel делает isConnected() = true (иначе эндер-жемчуг и часть
 * логики считают игрока отключённым), обработчики протокола — пустые, ничего не читается.
 * Пакеты сюда не пишутся: их глотает {@link FakeGameListener#send}.
 */
public final class FakeConnection extends Connection {
	public FakeConnection() {
		super(PacketFlow.SERVERBOUND);
		((ConnectionAccessor) (Object) this).mcw$setChannel(new EmbeddedChannel());
	}

	@Override
	public void setReadOnly() {
	}

	@Override
	public void handleDisconnection() {
	}

	@Override
	public void setListenerForServerboundHandshake(final PacketListener listener) {
	}

	@Override
	public <T extends PacketListener> void setupInboundProtocol(final ProtocolInfo<T> info, final T listener) {
	}
}
