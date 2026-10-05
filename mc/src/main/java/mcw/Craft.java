package mcw;

import com.google.gson.JsonObject;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.Identifier;
import net.minecraft.resources.ResourceKey;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.stats.Stats;
import net.minecraft.util.context.ContextMap;
import net.minecraft.world.MenuProvider;
import net.minecraft.world.entity.player.StackedItemContents;
import net.minecraft.world.inventory.AbstractCraftingMenu;
import net.minecraft.world.inventory.ClickType;
import net.minecraft.world.inventory.CraftingMenu;
import net.minecraft.world.inventory.RecipeBookMenu;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.crafting.CraftingRecipe;
import net.minecraft.world.item.crafting.Recipe;
import net.minecraft.world.item.crafting.RecipeHolder;
import net.minecraft.world.item.crafting.display.RecipeDisplay;
import net.minecraft.world.item.crafting.display.ShapedCraftingRecipeDisplay;
import net.minecraft.world.item.crafting.display.ShapelessCraftingRecipeDisplay;
import net.minecraft.world.item.crafting.display.SlotDisplayContext;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.phys.Vec3;

/**
 * recipes / craft. Решает Minecraft: рецепты — из RecipeManager сервера, а крафт идёт через ванильные меню, как у
 * клиента с книгой рецептов: 2x2 — InventoryMenu самого игрока, 3x3 — CraftingMenu верстака в радиусе досягаемости
 * (меню открывает сам блок, getMenuProvider). На каждый крафт: handlePlacement (ServerPlaceRecipe раскладывает
 * ингредиенты из инвентаря на сетку, результат считает slotChangedCraftingGrid через RecipeManager), затем shift-клик
 * по результату (quickMoveStack: onCraftedBy, расход сетки и остатки вроде пустых вёдер — ResultSlot.onTake). В конце
 * меню закрываем, как клиент: removed() возвращает остатки сетки в инвентарь.
 *
 * Проверку книги рецептов (handlePlaceRecipe пускает только открытые рецепты) не повторяем: руками в сетку можно
 * положить любой рецепт; doLimitedCrafting ванильный ResultContainer.setRecipeUsed проверяет сам (why: rejected).
 * Только поток сервера.
 */
final class Craft {
	private static final int MAX_CRAFTS = 256;

	private Craft() {
	}

	/** Сетка, в которую рецепт влезает: 2 (инвентарь), 3 (верстак) или 0 (особый рецепт без раскладки). Как клиентская книга рецептов. */
	static int grid(final Recipe<?> r) {
		if (!(r instanceof CraftingRecipe) || r.placementInfo().isImpossibleToPlace()) {
			return 0;
		}

		int best = 0;
		for (RecipeDisplay d : r.display()) {
			int g = 0;
			if (d instanceof ShapedCraftingRecipeDisplay s) {
				g = Math.max(s.width(), s.height()) <= 2 ? 2 : Math.max(s.width(), s.height()) <= 3 ? 3 : 0;
			} else if (d instanceof ShapelessCraftingRecipeDisplay s) {
				g = s.ingredients().size() <= 4 ? 2 : s.ingredients().size() <= 9 ? 3 : 0;
			}

			if (g != 0 && (best == 0 || g < best)) {
				best = g;
			}
		}

		return best;
	}

	/** Что даёт один крафт (предмет и число) — по описанию рецепта для книги; пусто, если не разобрать. */
	static ItemStack result(final Recipe<?> r, final ContextMap ctx) {
		for (RecipeDisplay d : r.display()) {
			ItemStack st = d.result().resolveForFirstStack(ctx);
			if (!st.isEmpty()) {
				return st;
			}
		}

		return ItemStack.EMPTY;
	}

	/** Ближайший к глазам верстак, до которого Геральт дотянется (как клик по блоку: дальность + 1 блок, mayInteract). */
	static BlockPos findTable(final ServerPlayer p) {
		ServerLevel level = p.level();
		Vec3 eye = p.getEyePosition();
		int r = (int) Math.ceil(p.blockInteractionRange() + 1.0);
		BlockPos c = BlockPos.containing(eye);
		BlockPos best = null;
		double bestD = Double.MAX_VALUE;
		for (BlockPos pos : BlockPos.betweenClosed(c.offset(-r, -r, -r), c.offset(r, r, r))) {
			if (!level.getBlockState(pos).is(Blocks.CRAFTING_TABLE) || !p.isWithinBlockInteractionRange(pos, 1.0) || !level.mayInteract(p, pos)) {
				continue;
			}

			double d = Vec3.atCenterOf(pos).distanceToSqr(eye);
			if (d < bestD) {
				bestD = d;
				best = pos.immutable();
			}
		}

		return best;
	}

	/**
	 * recipes{id} -> recipes{id, table, r:[[recipe, item, count, max, grid], ...]}: рецепты верстака, которые можно
	 * скрафтить из инвентаря хотя бы раз (max — сколько раз подряд). grid 3 без верстака рядом тоже в списке: craft
	 * ответит no_table. Сортировка — по предмету, затем по id рецепта.
	 */
	static String recipes(final ServerPlayer p, final JsonObject m, final int tick) {
		ServerLevel level = p.level();
		BlockPos table = findTable(p);
		StackedItemContents have = new StackedItemContents();
		p.getInventory().fillStackedContents(have);
		ContextMap ctx = SlotDisplayContext.fromLevel(level);
		List<Object[]> rows = new ArrayList<>();
		for (RecipeHolder<?> h : level.getServer().getRecipeManager().getRecipes()) {
			Recipe<?> r = h.value();
			int g = grid(r);
			if (g == 0 || !have.canCraft(r, null)) {
				continue;
			}

			ItemStack out = result(r, ctx);
			if (out.isEmpty()) {
				continue;
			}

			rows.add(new Object[] {h.id().identifier().toString(), BuiltInRegistries.ITEM.getKey(out.getItem()).toString(), out.getCount(),
				have.getBiggestCraftableStack(r, null), g});
		}

		rows.sort(Comparator.<Object[], String>comparing(o -> (String) o[1]).thenComparing(o -> (String) o[0]));
		StringBuilder b = new StringBuilder();
		for (Object[] o : rows) {
			(b.isEmpty() ? b : b.append(',')).append('[').append(Json.q((String) o[0])).append(',').append(Json.q((String) o[1])).append(',')
				.append(o[2]).append(',').append(o[3]).append(',').append(o[4]).append(']');
		}

		return "{\"t\":\"recipes\",\"tick\":" + tick + ",\"id\":" + Json.i(m, "id", 0) + ",\"table\":" + pos(table) + ",\"r\":[" + b + "]}";
	}

	/**
	 * craft{id, recipe, n} -> ack{id, ok, why?, made, crafts, item, table}. n крафтов подряд, пока хватает ингредиентов;
	 * ok — скрафтилось хотя бы раз. why: bad_recipe, impossible (особый рецепт), no_table, missing (не хватает с первого
	 * раза), inv_full (результату некуда лечь), rejected (сетка разложена, а результата нет: doLimitedCrafting, выключенный
	 * предмет). Если остановились после нескольких крафтов, why объясняет, почему не все n.
	 */
	static String craft(final ServerPlayer p, final JsonObject m, final int tick) {
		int id = Json.i(m, "id", 0);
		int n = Math.max(1, Math.min(MAX_CRAFTS, Json.i(m, "n", 1)));
		ServerLevel level = p.level();
		Identifier rid = Identifier.tryParse(Json.s(m, "recipe", ""));
		RecipeHolder<?> h = rid == null ? null : level.getServer().getRecipeManager().byKey(ResourceKey.create(Registries.RECIPE, rid)).orElse(null);
		if (h == null || !(h.value() instanceof CraftingRecipe)) {
			return ack(id, 0, 0, "bad_recipe", null, null, tick);
		}

		int g = grid(h.value());
		if (g == 0) {
			return ack(id, 0, 0, "impossible", null, null, tick);
		}

		if (p.containerMenu != p.inventoryMenu) {
			p.closeContainer();
		}

		BlockPos table = null;
		AbstractCraftingMenu menu = p.inventoryMenu;
		if (g == 3) {
			table = findTable(p);
			MenuProvider mp = table == null ? null : level.getBlockState(table).getMenuProvider(level, table);
			if (mp == null) {
				return ack(id, 0, 0, "no_table", null, null, tick);
			}

			p.openMenu(mp);
			if (!(p.containerMenu instanceof CraftingMenu cm)) {
				p.closeContainer();
				return ack(id, 0, 0, "rejected", null, pos(table), tick);
			}

			p.awardStat(Stats.INTERACT_WITH_CRAFTING_TABLE);
			menu = cm;
		}

		int made = 0;
		int crafts = 0;
		String why = null;
		String item = null;
		try {
			for (int k = 0; k < n; k++) {
				RecipeBookMenu.PostPlaceAction act = menu.handlePlacement(false, p.isCreative(), h, level, p.getInventory());
				ItemStack res = menu.getResultSlot().getItem().copy();
				if (res.isEmpty()) {
					// PLACE_GHOST_RECIPE — ингредиентов нет; NOTHING без результата — разложили, а рецепт не отдал предмет
					why = act == RecipeBookMenu.PostPlaceAction.PLACE_GHOST_RECIPE ? "missing" : "rejected";
					break;
				}

				menu.clicked(0, 0, ClickType.QUICK_MOVE, p);
				if (ItemStack.matches(menu.getResultSlot().getItem(), res) && !gridEmpty(menu)) {
					why = "inv_full";
					break;
				}

				made += res.getCount();
				crafts++;
				item = BuiltInRegistries.ITEM.getKey(res.getItem()).toString();
			}
		} finally {
			// остатки сетки — в инвентарь, как при закрытии экрана клиентом
			if (menu == p.inventoryMenu) {
				p.inventoryMenu.removed(p);
			} else {
				p.closeContainer();
			}
		}

		Bridge.inventoryChanged();
		return ack(id, made, crafts, crafts > 0 && crafts == n ? null : why, item, pos(table), tick);
	}

	private static boolean gridEmpty(final AbstractCraftingMenu menu) {
		for (Slot s : menu.getInputGridSlots()) {
			if (s.hasItem()) {
				return false;
			}
		}

		return true;
	}

	private static String ack(final int id, final int made, final int crafts, final String why, final String item, final String table, final int tick) {
		return "{\"t\":\"ack\",\"tick\":" + tick + ",\"id\":" + id + ",\"ok\":" + (crafts > 0) + (why == null ? "" : ",\"why\":" + Json.q(why))
			+ ",\"made\":" + made + ",\"crafts\":" + crafts + (item == null ? "" : ",\"item\":" + Json.q(item)) + ",\"table\":" + (table == null ? "null" : table)
			+ "}";
	}

	private static String pos(final BlockPos b) {
		return b == null ? "null" : "[" + b.getX() + "," + b.getY() + "," + b.getZ() + "]";
	}
}
