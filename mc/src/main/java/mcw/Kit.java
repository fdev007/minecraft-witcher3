package mcw;

import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;

/**
 * Стартовый набор Геральта — один раз на игрока: метка {@link #TAG} в его данных (entity tags, сохраняются в playerdata,
 * общей у марионетки и игрока клиента). Новая версия набора — новая метка. Вместе с гейм-рулом keep_inventory (Mcw)
 * набор не пропадает и после смерти.
 *
 * Хотбар: меч, лук, кирка, топор, лопата, булыжник, доски, земля, жареная говядина; в левой руке щит; на нём железная
 * броня; в рюкзаке стрелы и остальные блоки, которые Ведьмак умеет показывать (modMCWBlocks), и верстак. Занятый слот
 * хотбара освобождается: прежняя стопка уходит в рюкзак.
 */
final class Kit {
	static final String TAG = "mcw_kit_1";

	private Kit() {
	}

	/** Поток сервера. true — набор выдан сейчас. */
	static boolean ensure(final ServerPlayer p) {
		if (p.getTags().contains(TAG)) {
			return false;
		}

		p.addTag(TAG);
		Inventory inv = p.getInventory();
		Item[] bar = {Items.IRON_SWORD, Items.BOW, Items.IRON_PICKAXE, Items.IRON_AXE, Items.IRON_SHOVEL, Items.COBBLESTONE,
			Items.OAK_PLANKS, Items.DIRT, Items.COOKED_BEEF};
		int[] barCount = {1, 1, 1, 1, 1, 64, 64, 64, 32};
		for (int i = 0; i < bar.length; i++) {
			put(p, inv, i, new ItemStack(bar[i], barCount[i]));
		}

		equip(p, EquipmentSlot.OFFHAND, Items.SHIELD);
		equip(p, EquipmentSlot.HEAD, Items.IRON_HELMET);
		equip(p, EquipmentSlot.CHEST, Items.IRON_CHESTPLATE);
		equip(p, EquipmentSlot.LEGS, Items.IRON_LEGGINGS);
		equip(p, EquipmentSlot.FEET, Items.IRON_BOOTS);
		Item[] pack = {Items.ARROW, Items.STONE, Items.OAK_LOG, Items.SAND, Items.GLASS, Items.GRASS_BLOCK, Items.OAK_LEAVES};
		for (Item it : pack) {
			add(p, inv, new ItemStack(it, 64));
		}

		add(p, inv, new ItemStack(Items.CRAFTING_TABLE, 4));
		inv.setSelectedSlot(0);
		Bridge.inventoryChanged();
		Mcw.LOG.info("mcw: {} получил стартовый набор ({})", p.getGameProfile().name(), TAG);
		return true;
	}

	/** Сначала новый предмет, потом старый в рюкзак: освобождённый слот inv.add взял бы первым, и старое затёрлось бы. */
	private static void put(final ServerPlayer p, final Inventory inv, final int slot, final ItemStack stack) {
		ItemStack old = inv.getItem(slot);
		inv.setItem(slot, stack);
		if (!old.isEmpty()) {
			add(p, inv, old);
		}
	}

	private static void equip(final ServerPlayer p, final EquipmentSlot slot, final Item item) {
		ItemStack old = p.getItemBySlot(slot);
		if (!old.isEmpty()) {
			add(p, p.getInventory(), old);
		}

		p.setItemSlot(slot, new ItemStack(item));
	}

	/** В рюкзак, а нет места — на землю под ноги (подберёт сам). */
	private static void add(final ServerPlayer p, final Inventory inv, final ItemStack stack) {
		if (!inv.add(stack) && !stack.isEmpty()) {
			p.drop(stack, false);
		}
	}
}
