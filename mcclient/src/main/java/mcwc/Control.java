package mcwc;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import it.unimi.dsi.fastutil.ints.IntOpenHashSet;
import it.unimi.dsi.fastutil.ints.IntSet;
import java.util.Comparator;
import java.util.List;
import mcwc.mixin.AbstractArrowAccessor;
import mcwc.mixin.KeyboardHandlerAccessor;
import mcwc.mixin.MouseHandlerAccessor;
import net.minecraft.SharedConstants;
import net.minecraft.client.Minecraft;
import net.minecraft.client.input.KeyEvent;
import net.minecraft.client.input.MouseButtonInfo;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.util.Mth;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.MoverType;
import net.minecraft.world.entity.animal.equine.AbstractHorse;
import net.minecraft.world.entity.projectile.arrow.AbstractArrow;
import net.minecraft.world.phys.Vec3;
import net.minecraft.world.phys.shapes.CollisionContext;
import org.lwjgl.glfw.GLFW;

/**
 * Что присылает мост и что клиент отвечает. Всё — на потоке рендера (Link кладёт сообщения в очередь Minecraft).
 *
 * Мост → клиент:
 *   key{k, a}          клавиша GLFW (k — GLFW_KEY_*), a: 1 нажата, 0 отпущена, 2 повтор
 *   btn{b, a}          кнопка мыши: 0 левая, 1 правая, 2 средняя, 3/4 боковые
 *   scroll{d}          колесо: +1 вверх (предыдущий слот), −1 вниз
 *   char{c}            символ (код Unicode) — для чата
 *   cursor{dx, dy}     сдвиг курсора в пикселях окна, пока открыт экран MC (инвентарь, верстак): мышь Ведьмака
 *                      в это время его камеру не крутит (mcwf_freeze), а движение читает мост (Raw Input)
 *   release{}          отпустить всё, что нажато мостом (Ведьмак потерял фокус, режим выключен)
 *   pose{p, yaw, pitch}  следовать за Ведьмаком: ноги в p (координаты MC), взгляд; включает follow
 *   look{yaw, pitch}   только взгляд
 *   follow{on}         следовать за позой моста или ходить самому (этап 2: WASD — клиенту, физика MC)
 *   fov{v}             вертикальный FOV мира в градусах, как у камеры Ведьмака (0 — ванильный); рука — свои 70
 *   outline{on}        рамка блока под прицелом (по умолчанию да)
 *   terrain{c, reset?} земля Ведьмака для физики, пока ходит сам (Terrain): [x, z, h_cm, ...]
 *   npcs{l}            прокси NPC Ведьмака для боя: позиции, скорости, размеры (NpcProxies)
 *   world{hide}        прятать мир (по умолчанию да) — false для отладки: окно покажет обычный Майнкрафт
 *   ping{ts}           → pong{ts, tick}
 * Клиент → мост:
 *   hello{v, mc, mod, name, title, key, world, win}   при подключении
 *   state{tick, world, p, yaw, pitch, hp, food, sel, swing, block, screen, follow, fps, win}   каждый тик клиента (20 Гц)
 *   eye{p, v, h, ground}   пока ходит сам: глаз (интерполированный, как в кадре), скорость (блоков/с) и высота глаза
 *                      над ногами — каждый кадр, не чаще 60 раз в секунду; по ним мост ставит камеру и куклу Ведьмака
 *   horses{h}, arrows{a}   лошади и стрелы рядом — каждый кадр, не чаще 60 раз в секунду (Ведьмак рисует их сам)
 *   pong, error{why, text}
 *
 * Ввод идёт через ванильные обработчики GLFW (KeyboardHandler.keyPress, MouseHandler.onButton/onScroll) с настоящим
 * окном: всё работает, как с клавиатуры, — хотбар, удар и взмах, экраны, чат.
 */
public final class Control {
	/** Мир не рисуем: фон — Mcwc.KEY (GameRendererMixin, MinecraftMixin). */
	public static volatile boolean hideWorld = true;
	/** Игрок стоит там, где его поставил мост (PlayerMixin отключает ему движение). */
	public static volatile boolean follow;
	/** Вертикальный FOV мира от моста, 0 — ванильный (GameRendererMixin). */
	public static volatile float fov;
	/** Рамка блока под прицелом (GameRendererMixin). */
	public static volatile boolean outline = true;
	/** Пустоты под ногами столько блоков — гравитацию выключаем: рельеф Ведьмака (barrier) ещё не пришёл. */
	private static final int FLOOR_SCAN = 16;
	private static final long EYE_PERIOD_NS = 1_000_000_000L / 60;
	/** Лошадей мосту — не дальше этого (блоков) и не больше HORSES: у Ведьмака 4 слота моделей. */
	private static final double HORSE_RANGE = 48.0;
	private static final int HORSES = 4;
	private static boolean horsesSent;
	/** Стрел мосту — не дальше этого (блоков) и не больше ARROWS: у Ведьмака 8 стрел в пуле. */
	private static final double ARROW_RANGE = 64.0;
	private static final int ARROWS = 8;
	private static boolean arrowsSent;
	/** Лошадь прижимается к земле, если опора не дальше этого (блоков, snapDown): ступени земли Ведьмака мельче. */
	private static final double SNAP = 0.55;
	/** Лошадь выталкивается наверх, только если ушла в землю глубже (иначе её дёргало на каждой мелочи). */
	private static final double VEHICLE_SINK = 0.25;
	/** Лошадь, которой страховка (guard) выключала гравитацию: слез — вернуть. */
	private static Entity vehicleGuarded;
	private static long lastEye;
	private static Vec3 target;
	private static float targetYaw;
	private static float targetPitch;
	private static final IntSet KEYS = new IntOpenHashSet();
	private static final IntSet BUTTONS = new IntOpenHashSet();
	private static Link.Peer bridge;
	private static int ticks;
	/** Курсор экранов MC в пикселях окна; своего системного курсора у скрытого окна нет — рисуем (ScreenMixin). */
	private static double cursorX;
	private static double cursorY;
	private static Object lastScreen;

	private Control() {
	}

	static void onBridgeUp(final Link.Peer p) {
		bridge = p;
		releaseAll();
		Minecraft mc = Minecraft.getInstance();
		JsonObject m = msg("hello");
		m.addProperty("v", 1);
		m.addProperty("mc", SharedConstants.getCurrentVersion().name());
		m.addProperty("mod", Mcwc.version());
		m.addProperty("name", mc.getUser().getName());
		m.addProperty("title", Mcwc.TITLE);
		m.addProperty("key", "magenta");
		m.addProperty("world", mc.level != null);
		m.add("win", win(mc));
		p.send(m.toString());
	}

	static void onBridgeDown() {
		bridge = null;
		releaseAll();
	}

	static void handle(final Link.Peer from, final JsonObject m) {
		if (from != bridge) {
			return; // хвост вытесненного соединения
		}

		String t = m.has("t") ? m.get("t").getAsString() : "";
		try {
			switch (t) {
				case "key" -> key(m.get("k").getAsInt(), m.get("a").getAsInt());
				case "btn" -> button(m.get("b").getAsInt(), m.get("a").getAsInt());
				case "scroll" -> scroll(m.get("d").getAsDouble());
				case "char" -> typed(m.get("c").getAsInt());
				case "cursor" -> cursor(m.get("dx").getAsDouble(), m.get("dy").getAsDouble());
				case "release" -> releaseAll();
				case "pose" -> pose(m);
				case "look" -> look(m.get("yaw").getAsFloat(), m.get("pitch").getAsFloat());
				case "follow" -> setFollow(m.get("on").getAsBoolean());
				case "fov" -> fov = Math.max(0.0F, Math.min(170.0F, m.get("v").getAsFloat()));
				case "outline" -> outline = m.get("on").getAsBoolean();
				case "terrain" -> Terrain.apply(m.has("reset") && m.get("reset").getAsBoolean(), m.has("c") ? m.getAsJsonArray("c") : null);
				case "solid" -> Solid.apply(m.has("reset") && m.get("reset").getAsBoolean(), m.has("c") ? m.getAsJsonArray("c") : null);
				case "npcs" -> NpcProxies.apply(m.has("l") ? m.getAsJsonArray("l") : new JsonArray());
				case "world" -> hideWorld = m.get("hide").getAsBoolean();
				case "ping" -> {
					JsonObject r = msg("pong");
					r.add("ts", m.get("ts"));
					from.send(r.toString());
				}
				default -> error(from, "unknown_type", t);
			}
		} catch (RuntimeException e) {
			Mcwc.LOG.warn("mcwc: сообщение {} упало", m, e);
			error(from, "bad_args", t + ": " + e);
		}
	}

	private static void error(final Link.Peer to, final String why, final String text) {
		JsonObject r = msg("error");
		r.addProperty("why", why);
		r.addProperty("text", text);
		to.send(r.toString());
	}

	// ------------------------------------------------------------------------------------------------ ввод
	private static long window() {
		return Minecraft.getInstance().getWindow().handle();
	}

	private static void key(final int k, final int a) {
		if (k < 0 || k > GLFW.GLFW_KEY_LAST || a < 0 || a > 2) {
			throw new IllegalArgumentException("key " + k + " / " + a);
		}

		if (a == 0 && !KEYS.remove(k)) {
			return; // отпускать то, что не нажимали, незачем
		}

		if (a == 1) {
			KEYS.add(k);
		}

		Minecraft mc = Minecraft.getInstance();
		((KeyboardHandlerAccessor) mc.keyboardHandler).mcwc$keyPress(window(), a, new KeyEvent(k, GLFW.glfwGetKeyScancode(k), mods()));
	}

	private static void button(final int b, final int a) {
		if (b < 0 || b > GLFW.GLFW_MOUSE_BUTTON_LAST || a < 0 || a > 1) {
			throw new IllegalArgumentException("btn " + b + " / " + a);
		}

		if (a == 0 && !BUTTONS.remove(b)) {
			return;
		}

		if (a == 1) {
			BUTTONS.add(b);
		}

		Minecraft mc = Minecraft.getInstance();
		((MouseHandlerAccessor) mc.mouseHandler).mcwc$onButton(window(), new MouseButtonInfo(b, mods()), a);
	}

	/**
	 * Модификаторы GLFW по клавишам, которые держит мост: экраны MC узнают Shift/Ctrl из события (shift-клик в
	 * инвентаре), а не из настоящей клавиатуры — её у скрытого окна нет.
	 */
	private static int mods() {
		int m = 0;
		if (KEYS.contains(GLFW.GLFW_KEY_LEFT_SHIFT) || KEYS.contains(GLFW.GLFW_KEY_RIGHT_SHIFT)) {
			m |= GLFW.GLFW_MOD_SHIFT;
		}

		if (KEYS.contains(GLFW.GLFW_KEY_LEFT_CONTROL) || KEYS.contains(GLFW.GLFW_KEY_RIGHT_CONTROL)) {
			m |= GLFW.GLFW_MOD_CONTROL;
		}

		if (KEYS.contains(GLFW.GLFW_KEY_LEFT_ALT) || KEYS.contains(GLFW.GLFW_KEY_RIGHT_ALT)) {
			m |= GLFW.GLFW_MOD_ALT;
		}

		return m;
	}

	/** Сдвиг курсора экрана — ванильным MouseHandler.onMove (оттуда mouseMoved и перетаскивание предметов). */
	private static void cursor(final double dx, final double dy) {
		Minecraft mc = Minecraft.getInstance();
		if (mc.screen == null) {
			return;
		}

		cursorX = Mth.clamp(cursorX + dx, 0.0, mc.getWindow().getWidth() - 1);
		cursorY = Mth.clamp(cursorY + dy, 0.0, mc.getWindow().getHeight() - 1);
		((MouseHandlerAccessor) mc.mouseHandler).mcwc$onMove(window(), cursorX, cursorY);
	}

	/** Открылся экран — курсор в центр, как ванильный releaseMouse. */
	private static void watchScreen(final Minecraft mc) {
		if (mc.screen == lastScreen) {
			return;
		}

		boolean opened = lastScreen == null;
		lastScreen = mc.screen;
		if (mc.screen != null && opened) {
			cursorX = mc.getWindow().getWidth() / 2.0;
			cursorY = mc.getWindow().getHeight() / 2.0;
			((MouseHandlerAccessor) mc.mouseHandler).mcwc$onMove(window(), cursorX, cursorY);
		}
	}

	private static void scroll(final double d) {
		Minecraft mc = Minecraft.getInstance();
		((MouseHandlerAccessor) mc.mouseHandler).mcwc$onScroll(window(), 0.0, d);
	}

	private static void typed(final int c) {
		Minecraft mc = Minecraft.getInstance();
		((KeyboardHandlerAccessor) mc.keyboardHandler).mcwc$charTyped(window(), new net.minecraft.client.input.CharacterEvent(c, 0));
	}

	private static void releaseAll() {
		for (int k : KEYS.toIntArray()) {
			key(k, 0);
		}

		for (int b : BUTTONS.toIntArray()) {
			button(b, 0);
		}
	}

	// ------------------------------------------------------------------------------------------------ поза
	private static void pose(final JsonObject m) {
		JsonArray p = m.getAsJsonArray("p");
		double x = p.get(0).getAsDouble();
		double y = p.get(1).getAsDouble();
		double z = p.get(2).getAsDouble();
		if (!Double.isFinite(x) || !Double.isFinite(y) || !Double.isFinite(z)) {
			throw new IllegalArgumentException("pose.p");
		}

		target = new Vec3(x, y, z);
		follow = true;
		look(m.get("yaw").getAsFloat(), m.get("pitch").getAsFloat());
	}

	private static void look(final float yaw, final float pitch) {
		if (!Float.isFinite(yaw) || !Float.isFinite(pitch)) {
			throw new IllegalArgumentException("yaw/pitch");
		}

		targetYaw = yaw;
		targetPitch = Mth.clamp(pitch, -90.0F, 90.0F);
		LocalPlayer pl = Minecraft.getInstance().player;
		if (pl != null) {
			// курс без скачка через ±180: игроку MC курс не нормализуют, интерполяция камеры шла бы через полкруга
			pl.setYRot(pl.getYRot() + Mth.wrapDegrees(targetYaw - pl.getYRot()));
			pl.setXRot(targetPitch);
			pl.setYHeadRot(pl.getYRot());
		}
	}

	private static void setFollow(final boolean on) {
		follow = on;
		LocalPlayer pl = Minecraft.getInstance().player;
		if (pl != null) {
			pl.setNoGravity(false);
			pl.setDeltaMovement(Vec3.ZERO);
		}
	}

	/**
	 * До тика игрока. Следует за мостом — поставить его в точку моста: LocalPlayer.tick пошлёт серверу ход уже отсюда.
	 * Ходит сам — страховка: под ногами пустота на FLOOR_SCAN блоков (рельеф Ведьмака ещё не пришёл, мир MC — void) —
	 * без гравитации и без падения, иначе он улетел бы под мир.
	 */
	static void startTick(final Minecraft mc) {
		LocalPlayer pl = mc.player;
		if (pl == null || mc.level == null) {
			return;
		}

		if (!follow) {
			// верхом ходит лошадь (её физику клиент считает сам), игрок едет на ней: страховка — ей. Без этого лошадь на
			// полном скаку (14 м/с) выбегала на колонны, рельеф которых ещё не пришёл, и падала в пустоту вместе с игроком
			// (2026-10-04)
			Entity v = pl.getVehicle();
			if (v != null && v.getControllingPassenger() == pl) {
				pl.setNoGravity(false);
				guard(mc, v, VEHICLE_SINK);
				snapDown(v);
				vehicleGuarded = v;
			} else {
				if (vehicleGuarded != null) {
					vehicleGuarded.setNoGravity(false); // слез: гравитация лошади — снова её
					vehicleGuarded = null;
				}

				guard(mc, pl, 0.0);
			}

			return;
		}

		if (target == null) {
			return;
		}

		pl.setPos(target.x, target.y, target.z);
		pl.setDeltaMovement(Vec3.ZERO);
		pl.setOnGround(true); // в воздухе Player.getDestroySpeed режет копание в 5 раз
		pl.resetFallDistance();
	}

	/**
	 * Страховка того, что ходит по физике клиента (игрок или его лошадь). Ноги внутри земли или ступени (после входа,
	 * телепорта, на коллизии, которая пришла позже): MC не сталкивает с формой, в которой сущность уже стоит, и она
	 * провалилась бы сквозь неё — на поверхность. Под ногами пустота на FLOOR_SCAN блоков (рельеф Ведьмака ещё не
	 * пришёл, мир MC — void) — без гравитации и без падения, иначе улетела бы под мир.
	 */
	private static void guard(final Minecraft mc, final Entity e, final double sink) {
		BlockPos.MutableBlockPos bp = new BlockPos.MutableBlockPos();
		int x = Mth.floor(e.getX());
		int z = Mth.floor(e.getZ());
		double h = Solid.active(e) ? Solid.pushUp(e.getX(), e.getY(), e.getZ()) : Double.NaN;
		if (!Double.isNaN(h) && h - e.getY() > sink) {
			e.setPos(e.getX(), h, e.getZ());
			Vec3 d = e.getDeltaMovement();
			e.setDeltaMovement(d.x, Math.max(0.0, d.y), d.z);
		}

		int y0 = Mth.floor(e.getY());
		// опора — земля Ведьмака в этой колонне или блок со столкновением для игрока (barrier при Terrain — нет)
		boolean floor = Solid.known(x, z);
		CollisionContext ctx = CollisionContext.of(e);
		for (int y = y0; y >= y0 - FLOOR_SCAN && !floor; y--) {
			bp.set(x, y, z);
			floor = !mc.level.getBlockState(bp).getCollisionShape(mc.level, bp, ctx).isEmpty();
		}

		e.setNoGravity(!floor);
		if (!floor && e.getDeltaMovement().y < 0.0) {
			e.setDeltaMovement(e.getDeltaMovement().multiply(1.0, 0.0, 1.0));
		}
	}

	/**
	 * Лошадь под игроком сошла со ступеньки земли Ведьмака (колонны 1×1 и подколонны 25 см — лестница) и не прыгает —
	 * сразу вниз до опоры, ванильным move со всеми столкновениями, если опора не дальше SNAP. Иначе на скаку она тик-два
	 * летела с каждой ступеньки и камня, а в воздухе лошадь MC почти не разгоняется (LivingEntity.travel: скорость в
	 * воздухе — flyingSpeed): скорость падала, картинка тряслась (2026-10-04). Опоры ближе нет (обрыв) — назад, падает
	 * как обычно.
	 */
	private static void snapDown(final Entity v) {
		if (v.onGround() || v.getDeltaMovement().y > 0.0 || v.isInWater()) {
			return;
		}

		Vec3 before = v.position();
		v.move(MoverType.SELF, new Vec3(0.0, -SNAP, 0.0));
		if (v.onGround()) {
			v.setDeltaMovement(v.getDeltaMovement().multiply(1.0, 0.0, 1.0));
		} else {
			v.setPos(before);
		}
	}

	/** Каждый кадр (GameRendererMixin): пока ходит сам — глаз мосту, тем же положением, что в кадре клиента. */
	public static void frame(final float partial) {
		Link.Peer b = bridge;
		Minecraft mc = Minecraft.getInstance();
		LocalPlayer pl = mc.player;
		long now = System.nanoTime();
		if (b == null || pl == null || mc.level == null || now - lastEye < EYE_PERIOD_NS) {
			return;
		}

		lastEye = now;
		horses(b, mc, pl, partial);
		arrows(b, mc, pl, partial);
		if (follow) {
			return;
		}

		Vec3 e = pl.getEyePosition(partial);
		JsonObject m = msg("eye");
		m.add("p", vec(e.x, e.y, e.z));
		// скорость — по фактическому сдвигу за тик (после столкновений), в блоках в секунду
		m.add("v", vec((pl.getX() - pl.xo) * 20.0, (pl.getY() - pl.yo) * 20.0, (pl.getZ() - pl.zo) * 20.0));
		m.addProperty("h", round(pl.getEyeHeight()));
		m.addProperty("ground", pl.onGround());
		b.send(m.toString());
	}

	/**
	 * Лошади рядом (до HORSES, ближние) — мосту на этот кадр: Ведьмак рисует их коробками (mcw_models.ws). Поза — как у
	 * рендера MC: позиция и поворот тела с интерполяцией кадра, наклон головы, фаза и сила шага (WalkAnimationState).
	 *   horses{h:[[id, x, y, z, yaw тела, наклон, фаза шага, сила шага, flags, dx?, dy?, dz?], ...]}   flags: 1 видна,
	 *   2 седло, 4 на ней игрок (тогда ещё ноги лошади от глаза игрока dx, dy, dz)
	 * Пустой список — один раз, когда лошадей не стало.
	 */
	private static void horses(final Link.Peer b, final Minecraft mc, final LocalPlayer pl, final float partial) {
		List<AbstractHorse> near = mc.level.getEntitiesOfClass(AbstractHorse.class, pl.getBoundingBox().inflate(HORSE_RANGE),
			h -> h.isAlive() && !h.isBaby());
		if (near.isEmpty() && !horsesSent) {
			return;
		}

		near.sort(Comparator.comparingDouble(pl::distanceToSqr));
		JsonArray list = new JsonArray();
		for (int i = 0; i < near.size() && i < HORSES; i++) {
			AbstractHorse h = near.get(i);
			Vec3 p = h.getPosition(partial);
			JsonArray a = new JsonArray();
			a.add(h.getId());
			a.add(round(p.x));
			a.add(round(p.y));
			a.add(round(p.z));
			a.add(round(Mth.rotLerp(partial, h.yBodyRotO, h.yBodyRot)));
			a.add(round(h.getXRot(partial)));
			a.add(round(h.walkAnimation.position(partial)));
			a.add(round(h.walkAnimation.speed(partial)));
			a.add(1 | (h.isSaddled() ? 2 : 0) | (pl.getVehicle() == h ? 4 : 0));
			if (pl.getVehicle() == h) {
				// под всадником: ноги лошади от глаза игрока в этом же кадре — Ведьмак ставит её от своей камеры (тот же
				// глаз), и лошадь не дрожит относительно неё на скаку
				Vec3 eye = pl.getEyePosition(partial);
				a.add(round(p.x - eye.x));
				a.add(round(p.y - eye.y));
				a.add(round(p.z - eye.z));
			}

			list.add(a);
		}

		JsonObject m = msg("horses");
		m.add("h", list);
		b.send(m.toString());
		horsesSent = !list.isEmpty();
	}

	/**
	 * Стрелы рядом (до ARROWS, ближние) — мосту на этот кадр: мир клиент не рисует, их показывает Ведьмак ванильной
	 * стрелой лучников (mcw_combat.ws). Позиция с интерполяцией кадра, направление — по повороту стрелы (у снарядов MC
	 * курс atan2(x, z) и наклон atan2(y, гориз.), Projectile.updateRotation), скорость — блоков в секунду.
	 *   arrows{a:[[id, x, y, z, dx, dy, dz, vx, vy, vz, flags], ...]}   flags: 1 воткнулась
	 * Пустой список — один раз, когда стрел не стало.
	 */
	private static void arrows(final Link.Peer b, final Minecraft mc, final LocalPlayer pl, final float partial) {
		List<AbstractArrow> near = mc.level.getEntitiesOfClass(AbstractArrow.class, pl.getBoundingBox().inflate(ARROW_RANGE), Entity::isAlive);
		if (near.isEmpty() && !arrowsSent) {
			return;
		}

		near.sort(Comparator.comparingDouble(pl::distanceToSqr));
		JsonArray list = new JsonArray();
		for (int i = 0; i < near.size() && i < ARROWS; i++) {
			AbstractArrow a = near.get(i);
			Vec3 p = a.getPosition(partial);
			float yaw = a.getYRot(partial) * Mth.DEG_TO_RAD;
			float pitch = a.getXRot(partial) * Mth.DEG_TO_RAD;
			boolean stuck = ((AbstractArrowAccessor) a).mcwc$inGround();
			Vec3 v = stuck ? Vec3.ZERO : a.getDeltaMovement().scale(20.0);
			JsonArray e = new JsonArray();
			e.add(a.getId());
			e.add(round(p.x));
			e.add(round(p.y));
			e.add(round(p.z));
			e.add(round(Mth.sin(yaw) * Mth.cos(pitch)));
			e.add(round(Mth.sin(pitch)));
			e.add(round(Mth.cos(yaw) * Mth.cos(pitch)));
			e.add(round(v.x));
			e.add(round(v.y));
			e.add(round(v.z));
			e.add(stuck ? 1 : 0);
			list.add(e);
		}

		JsonObject m = msg("arrows");
		m.add("a", list);
		b.send(m.toString());
		arrowsSent = !list.isEmpty();
	}

	private static JsonArray vec(final double x, final double y, final double z) {
		JsonArray a = new JsonArray();
		a.add(round(x));
		a.add(round(y));
		a.add(round(z));
		return a;
	}

	static void endTick(final Minecraft mc) {
		ticks++;
		watchScreen(mc);
		Link.Peer b = bridge;
		if (b == null) {
			return;
		}

		JsonObject m = msg("state");
		m.addProperty("tick", ticks);
		LocalPlayer pl = mc.player;
		m.addProperty("world", mc.level != null && pl != null);
		if (pl != null) {
			JsonArray p = new JsonArray();
			p.add(round(pl.getX()));
			p.add(round(pl.getY()));
			p.add(round(pl.getZ()));
			m.add("p", p);
			m.addProperty("yaw", round(pl.getYRot()));
			m.addProperty("pitch", round(pl.getXRot()));
			m.addProperty("hp", pl.getHealth());
			m.addProperty("food", pl.getFoodData().getFoodLevel());
			m.addProperty("sel", pl.getInventory().getSelectedSlot());
			m.addProperty("swing", pl.swinging);
			m.addProperty("block", pl.isBlocking()); // щит поднят: кукла Ведьмака в защите (mcwf_guard)
		}

		m.addProperty("screen", mc.screen == null ? null : mc.screen.getClass().getSimpleName());
		m.addProperty("follow", follow);
		m.addProperty("terrain", Terrain.size());
		m.addProperty("solid", Solid.size());
		m.addProperty("fps", mc.getFps());
		m.add("win", win(mc));
		b.send(m.toString());
	}

	private static double round(final double v) {
		return Math.round(v * 1000.0) / 1000.0;
	}

	private static JsonElement win(final Minecraft mc) {
		JsonArray a = new JsonArray();
		a.add(mc.getWindow().getWidth());
		a.add(mc.getWindow().getHeight());
		return a;
	}

	private static JsonObject msg(final String t) {
		JsonObject m = new JsonObject();
		m.addProperty("t", t);
		return m;
	}
}
