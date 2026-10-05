package mcw.mixin;

import com.mojang.authlib.GameProfile;
import java.util.UUID;
import mcw.FakeGameListener;
import mcw.GeraltPlayer;
import mcw.McwHooks;
import net.minecraft.network.Connection;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ClientInformation;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.network.CommonListenerCookie;
import net.minecraft.server.network.ServerGamePacketListenerImpl;
import net.minecraft.server.players.PlayerList;
import net.minecraft.world.entity.Entity;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.Redirect;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * Геральт в PlayerList без клиента. Как в fabric-carpet 1.4.194 (carpet.mixins.PlayerList_fakePlayersMixin, MIT,
 * Copyright (c) 2020 gnembon): в placeNewPlayer подменяем listener на {@link FakeGameListener} (пустой send),
 * в respawn — создаём снова GeraltPlayer, а не обычный ServerPlayer, которого никто бы не тикал.
 * Своё: при входе клиента Майнкрафта под именем Геральта марионетку убираем до проверки дубля (она не отключается,
 * и ваниль ждала бы её вечно) — {@link McwHooks#beforeLogin}.
 */
@Mixin(PlayerList.class)
abstract class PlayerListMixin {
	@Redirect(method = "placeNewPlayer", at = @At(value = "NEW", target = "(Lnet/minecraft/server/MinecraftServer;Lnet/minecraft/network/Connection;Lnet/minecraft/server/level/ServerPlayer;Lnet/minecraft/server/network/CommonListenerCookie;)Lnet/minecraft/server/network/ServerGamePacketListenerImpl;"))
	private ServerGamePacketListenerImpl mcw$listener(final MinecraftServer server, final Connection connection, final ServerPlayer player, final CommonListenerCookie cookie) {
		return player instanceof GeraltPlayer
			? new FakeGameListener(server, connection, player, cookie)
			: new ServerGamePacketListenerImpl(server, connection, player, cookie);
	}

	@Redirect(method = "respawn", at = @At(value = "NEW", target = "(Lnet/minecraft/server/MinecraftServer;Lnet/minecraft/server/level/ServerLevel;Lcom/mojang/authlib/GameProfile;Lnet/minecraft/server/level/ClientInformation;)Lnet/minecraft/server/level/ServerPlayer;"))
	private ServerPlayer mcw$respawn(final MinecraftServer server, final ServerLevel level, final GameProfile profile, final ClientInformation info,
		final ServerPlayer old, final boolean keepAll, final Entity.RemovalReason reason) {
		return old instanceof GeraltPlayer
			? new GeraltPlayer(server, level, profile, info)
			: new ServerPlayer(server, level, profile, info);
	}

	@Inject(method = "disconnectAllPlayersWithProfile", at = @At("HEAD"))
	private void mcw$dropPuppet(final UUID id, final CallbackInfoReturnable<Boolean> cir) {
		McwHooks.beforeLogin(id);
	}
}
