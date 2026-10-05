package mcw.mixin;

import io.netty.channel.Channel;
import net.minecraft.network.Connection;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;

/** Connection.channel без настоящего сокета: FakeConnection кладёт туда EmbeddedChannel. */
@Mixin(Connection.class)
public interface ConnectionAccessor {
	@Accessor("channel")
	void mcw$setChannel(Channel channel);
}
