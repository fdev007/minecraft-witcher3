package mcw;

import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.network.protocol.game.ClientboundSetHeldSlotPacket;
import net.minecraft.network.protocol.game.ServerboundPlayerActionPacket;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;

/**
 * act: break / place / use / cancel / select. Решает Minecraft: ломка идёт через
 * ServerPlayerGameMode.handleBlockBreakAction (дальность, mayInteract, время копания, дроп по инструменту),
 * установка — как правый клик ванильного клиента: useItemOn по блоку, а если он ответил PASS — useItem (вёдра,
 * лодки, жемчуг, еда; они целятся лучом из глаз, поэтому важны yaw/pitch). Перед ack сбрасываем blocks, чтобы мост
 * увидел изменение раньше подтверждения. ack и dig уходят тому соединению, что прислало act.
 *
 * Только поток сервера.
 */
final class Actions {
	/** Копание в выживании: START сейчас, STOP через нужное число тиков, стадии 0-9 по ходу. */
	private static final class Dig {
		final Link.Peer peer;
		final int id;
		final BlockPos pos;
		final Direction face;
		final int start;
		final int total;
		final BlockState was;
		int stage = -1;

		Dig(final Link.Peer peer, final int id, final BlockPos pos, final Direction face, final int start, final int total, final BlockState was) {
			this.peer = peer;
			this.id = id;
			this.pos = pos;
			this.face = face;
			this.start = start;
			this.total = total;
			this.was = was;
		}
	}

	private Dig dig;

	void reset() {
		this.dig = null;
	}

	void act(final Link.Peer peer, final ServerPlayer p, final JsonObject m, final int tick) {
		int id = Json.i(m, "id", 0);
		String a = Json.s(m, "a", "");
		// позиция и взгляд на момент действия: pos приходит 5-20 Гц, без этого будет ложное too_far
		double[] at = Json.arr(m, "p");
		if (at != null) {
			float yaw = Bridge.angle(m, "yaw", p.getYRot());
			float pitch = Bridge.angle(m, "pitch", p.getXRot());
			if (!Bridge.validPos(at) || Float.isNaN(yaw) || Float.isNaN(pitch)) {
				ack(peer, id, false, "bad_args", tick);
				return;
			}

			Bridge.place(p, at, yaw, pitch);
		}

		int slot = Json.i(m, "slot", -1);
		if (slot >= 0 && slot <= 8) {
			select(p, slot);
		} else if (slot != -1) {
			ack(peer, id, false, "bad_slot", tick);
			return;
		}

		switch (a) {
			case "break" -> breakBlock(peer, p, m, id, tick);
			case "place" -> place(peer, p, m, id, tick);
			case "use" -> use(peer, p, id, tick);
			case "cancel" -> {
				cancel(p, tick);
				ack(peer, id, true, null, tick);
			}
			case "select" -> ack(peer, id, true, null, tick);
			default -> ack(peer, id, false, "unknown_action", tick);
		}

		Bridge.inventoryChanged();
	}

	private void breakBlock(final Link.Peer peer, final ServerPlayer p, final JsonObject m, final int id, final int tick) {
		int[] b = Json.ints(m, "b");
		if (b == null || b.length != 3) {
			ack(peer, id, false, "bad_args", tick);
			return;
		}

		ServerLevel level = p.level();
		BlockPos pos = new BlockPos(b[0], b[1], b[2]);
		Direction face = face(m);
		String why = check(p, level, pos);
		if (why != null) {
			ack(peer, id, false, why, tick);
			return;
		}

		BlockState was = level.getBlockState(pos);
		if (was.isAir()) {
			ack(peer, id, false, "air", tick);
			return;
		}

		if (was.getDestroySpeed(level, pos) < 0.0F) {
			ack(peer, id, false, "unbreakable", tick);
			return;
		}

		if (p.blockActionRestricted(level, pos, p.gameMode.getGameModeForPlayer())) {
			ack(peer, id, false, "not_allowed", tick);
			return;
		}

		cancel(p, tick);
		float progress = was.getDestroyProgress(p, level, pos);
		p.gameMode.handleBlockBreakAction(pos, ServerboundPlayerActionPacket.Action.START_DESTROY_BLOCK, face, level.getMaxY(), 0);
		if (p.getAbilities().instabuild || progress >= 1.0F) {
			// творческий режим или «мгновенный» блок: START уже сломал
			Bridge.flushBlocks(tick);
			boolean ok = level.getBlockState(pos) != was;
			ack(peer, id, ok, ok ? null : "rejected", tick);
			return;
		}

		int total = (int) Math.ceil(1.0F / progress);
		this.dig = new Dig(peer, id, pos, face, tick, total, was);
		sendDig(this.dig, 0, tick);
	}

	/** END_SERVER_TICK. */
	void tick(final ServerPlayer p, final int tick) {
		Dig d = this.dig;
		if (d == null) {
			return;
		}

		BlockState now = p.level().getBlockState(d.pos);
		if (now != d.was) {
			// блок сменился без нас (песок упал, взрыв) — копать нечего
			this.dig = null;
			p.gameMode.handleBlockBreakAction(d.pos, ServerboundPlayerActionPacket.Action.ABORT_DESTROY_BLOCK, d.face, p.level().getMaxY(), 0);
			sendDig(d, -1, tick);
			ack(d.peer, d.id, false, "changed", tick);
			return;
		}

		int elapsed = tick - d.start;
		int stage = Math.min(9, elapsed * 10 / d.total);
		if (elapsed + 1 >= d.total) {
			this.dig = null;
			p.gameMode.handleBlockBreakAction(d.pos, ServerboundPlayerActionPacket.Action.STOP_DESTROY_BLOCK, d.face, p.level().getMaxY(), 0);
			Bridge.flushBlocks(tick);
			boolean ok = p.level().getBlockState(d.pos) != d.was;
			sendDig(d, -1, tick);
			ack(d.peer, d.id, ok, ok ? null : "rejected", tick);
		} else if (stage != d.stage) {
			sendDig(d, stage, tick);
		}
	}

	private void cancel(final ServerPlayer p, final int tick) {
		Dig d = this.dig;
		if (d == null) {
			return;
		}

		this.dig = null;
		p.gameMode.handleBlockBreakAction(d.pos, ServerboundPlayerActionPacket.Action.ABORT_DESTROY_BLOCK, d.face, p.level().getMaxY(), 0);
		sendDig(d, -1, tick);
		ack(d.peer, d.id, false, "cancelled", tick);
	}

	/**
	 * Правый клик по блоку b, грань face. sneak = true — как Shift у клиента: верстак, сундук, дверь не
	 * перехватывают клик, блок ставится на них. ok только если мир или инвентарь реально поменялись (или началось
	 * использование предмета — еда, лук); клик, который съел сам блок (открыл верстак), — why: used_block.
	 */
	private void place(final Link.Peer peer, final ServerPlayer p, final JsonObject m, final int id, final int tick) {
		int[] b = Json.ints(m, "b");
		double[] hit = Json.arr(m, "hit");
		if (b == null || b.length != 3 || hit != null && (hit.length != 3 || !Double.isFinite(hit[0] + hit[1] + hit[2]))) {
			ack(peer, id, false, "bad_args", tick);
			return;
		}

		ServerLevel level = p.level();
		BlockPos pos = new BlockPos(b[0], b[1], b[2]);
		Direction face = face(m);
		String why = check(p, level, pos);
		if (why == null && pos.relative(face).getY() > level.getMaxY()) {
			why = "too_high";
		}

		if (why != null) {
			ack(peer, id, false, why, tick);
			return;
		}

		// точка попадания внутри блока (0..1); по умолчанию — центр грани
		Vec3 loc = hit != null
			? new Vec3(pos.getX() + hit[0], pos.getY() + hit[1], pos.getZ() + hit[2])
			: Vec3.atCenterOf(pos).add(face.getUnitVec3().scale(0.5));
		ItemStack stack = p.getMainHandItem();
		BlockState targetWas = level.getBlockState(pos.relative(face));
		long changes0 = McwHooks.changes();
		String inv0 = Bridge.inventorySig(p);
		boolean sneak = m.has("sneak") && m.get("sneak").getAsBoolean();
		p.setShiftKeyDown(sneak);
		InteractionResult r;
		boolean usedItem = false;
		try {
			r = p.gameMode.useItemOn(p, level, stack, InteractionHand.MAIN_HAND, new BlockHitResult(loc, face, pos, false));
			if (r instanceof InteractionResult.Pass && !stack.isEmpty()) {
				// как клиент: блок и предмет «по блоку» отказались — предмет сам по себе (ведро, лодка, жемчуг, еда)
				r = p.gameMode.useItem(p, level, stack, InteractionHand.MAIN_HAND);
				usedItem = true;
			}
		} finally {
			p.setShiftKeyDown(false);
		}

		closeMenu(p);
		Bridge.flushBlocks(tick);
		boolean changed = McwHooks.changes() != changes0 || !inv0.equals(Bridge.inventorySig(p)) || p.isUsingItem();
		String reason = null;
		if (!changed) {
			if (r.consumesAction()) {
				reason = "used_block";
			} else if (stack.isEmpty()) {
				reason = "no_item";
			} else if (!usedItem && !targetWas.canBeReplaced()) {
				reason = "occupied";
			} else {
				reason = "rejected";
			}
		}

		ack(peer, id, changed, reason, tick);
	}

	/** Предмет в руке без цели-блока (еда, жемчуг, ведро по лучу взгляда): useItem, как правый клик в воздух. */
	private void use(final Link.Peer peer, final ServerPlayer p, final int id, final int tick) {
		ItemStack stack = p.getMainHandItem();
		if (stack.isEmpty()) {
			ack(peer, id, false, "no_item", tick);
			return;
		}

		long changes0 = McwHooks.changes();
		String inv0 = Bridge.inventorySig(p);
		p.gameMode.useItem(p, p.level(), stack, InteractionHand.MAIN_HAND);
		closeMenu(p);
		Bridge.flushBlocks(tick);
		boolean changed = McwHooks.changes() != changes0 || !inv0.equals(Bridge.inventorySig(p)) || p.isUsingItem();
		ack(peer, id, changed, changed ? null : "rejected", tick);
	}

	/**
	 * Слот хотбара — как ServerGamePacketListenerImpl.handleSetCarriedItem: смена слота во время использования
	 * правой рукой (еда, лук, щит) прерывает использование. Новый sel мост увидит в inv того же тика. У игрока клиента
	 * слот выбирает клиент: ему пакет, как у pick block, иначе следующий его клик пришёл бы со старым слотом.
	 */
	private static void select(final ServerPlayer p, final int slot) {
		if (p.getInventory().getSelectedSlot() != slot && p.getUsedItemHand() == InteractionHand.MAIN_HAND) {
			p.stopUsingItem();
		}

		p.getInventory().setSelectedSlot(slot);
		p.resetLastActionTime();
		if (!(p instanceof GeraltPlayer)) {
			p.connection.send(new ClientboundSetHeldSlotPacket(slot));
		}
	}

	/** Клиента нет — меню (верстак, сундук), открытое кликом, некому закрыть. */
	private static void closeMenu(final ServerPlayer p) {
		if (p.containerMenu != p.inventoryMenu) {
			p.closeContainer();
		}
	}

	/** Общие проверки обработчиков пакетов клиента: высота, чанк загружен, дальность, mayInteract. */
	private static String check(final ServerPlayer p, final ServerLevel level, final BlockPos pos) {
		if (pos.getY() > level.getMaxY()) {
			return "too_high";
		}

		if (pos.getY() < level.getMinY()) {
			return "too_low";
		}

		if (!level.isInWorldBounds(pos)) {
			return "out_of_world";
		}

		// сразу после дальнего pos чанк ещё грузится: мосту — повторить act чуть позже
		if (level.getChunkSource().getChunkNow(pos.getX() >> 4, pos.getZ() >> 4) == null) {
			return "unloaded";
		}

		if (!p.isWithinBlockInteractionRange(pos, 1.0)) {
			return "too_far";
		}

		if (!level.mayInteract(p, pos)) {
			return "not_allowed";
		}

		return null;
	}

	private static Direction face(final JsonObject m) {
		Direction d = Direction.byName(Json.s(m, "face", "up"));
		return d == null ? Direction.UP : d;
	}

	private static void sendDig(final Dig d, final int stage, final int tick) {
		d.stage = stage;
		d.peer.send("{\"t\":\"dig\",\"tick\":" + tick + ",\"id\":" + d.id + ",\"b\":[" + d.pos.getX() + "," + d.pos.getY() + "," + d.pos.getZ()
			+ "],\"stage\":" + stage + ",\"ticks_total\":" + d.total + "}");
	}

	static void ack(final Link.Peer peer, final int id, final boolean ok, final String why, final int tick) {
		peer.send("{\"t\":\"ack\",\"tick\":" + tick + ",\"id\":" + id + ",\"ok\":" + ok + (why == null ? "" : ",\"why\":" + Json.q(why)) + "}");
	}
}
