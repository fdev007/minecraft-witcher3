package mcwc.mixin;

import com.mojang.blaze3d.buffers.GpuBufferSlice;
import com.mojang.blaze3d.resource.GraphicsResourceAllocator;
import com.mojang.blaze3d.systems.RenderSystem;
import com.mojang.blaze3d.vertex.PoseStack;
import mcwc.Control;
import mcwc.NpcProxies;
import mcwc.Outline;
import net.minecraft.client.Camera;
import net.minecraft.client.DeltaTracker;
import net.minecraft.client.renderer.GameRenderer;
import net.minecraft.client.renderer.LevelRenderer;
import net.minecraft.client.renderer.LightTexture;
import net.minecraft.client.Minecraft;
import net.minecraft.client.renderer.MultiBufferSource;
import net.minecraft.client.renderer.rendertype.RenderTypes;
import net.minecraft.client.renderer.state.BlockOutlineRenderState;
import net.minecraft.client.resources.model.ModelBakery;
import net.minecraft.core.BlockPos;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.util.ARGB;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;
import net.minecraft.world.phys.shapes.Shapes;
import net.minecraft.world.phys.shapes.VoxelShape;
import net.minecraft.client.renderer.entity.EntityRenderDispatcher;
import net.minecraft.client.renderer.state.LevelRenderState;
import net.minecraft.world.entity.Entity;
import org.joml.Matrix4f;
import org.joml.Matrix4fStack;
import org.joml.Vector4f;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.Redirect;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * Мир не рисуем: в GameRenderer.renderLevel вместо LevelRenderer.renderLevel (небо, чанки, сущности, частицы) — только
 * рамка блока под прицелом, ванильными извлечением и отрисовкой, с той же матрицей вида. Блок рисует Ведьмак, рамка
 * ложится на него, когда камеры совпадают: глаз Ведьмака в глазу MC (мост, mcwf_mc), взгляд из Ведьмака, FOV — fov{v}.
 * Остаётся всё, что после рендера мира: луч прицела (pick), рука с предметом, эффекты экрана, дальше — HUD.
 */
@Mixin(GameRenderer.class)
abstract class GameRendererMixin {
	/** Верхняя грань блока толщиной 1 мм: рамка клетки рельефа. */
	private static final VoxelShape TOP = Shapes.box(0.0, 0.999, 0.0, 1.0, 1.0, 1.0);

	@Redirect(method = "renderLevel", at = @At(value = "INVOKE", target = "Lnet/minecraft/client/renderer/LevelRenderer;renderLevel(Lcom/mojang/blaze3d/resource/GraphicsResourceAllocator;Lnet/minecraft/client/DeltaTracker;ZLnet/minecraft/client/Camera;Lorg/joml/Matrix4f;Lorg/joml/Matrix4f;Lorg/joml/Matrix4f;Lcom/mojang/blaze3d/buffers/GpuBufferSlice;Lorg/joml/Vector4f;Z)V"))
	private void mcwc$level(final LevelRenderer lr, final GraphicsResourceAllocator alloc, final DeltaTracker dt, final boolean outline,
		final Camera camera, final Matrix4f view, final Matrix4f proj, final Matrix4f cull, final GpuBufferSlice fog, final Vector4f fogColor,
		final boolean sky) {
		if (!Control.hideWorld) {
			lr.renderLevel(alloc, dt, outline, camera, view, proj, cull, fog, fogColor, sky);
			return;
		}

		LevelRendererAccessor acc = (LevelRendererAccessor) lr;
		mcwc$cracks(acc, camera, view, fog);
		if (!outline || !Control.outline) {
			return;
		}

		LevelRenderState state = acc.mcwc$levelRenderState();
		acc.mcwc$extractBlockOutline(camera, state);
		if (state.blockOutlineRenderState == null) {
			return;
		}

		// как главный проход LevelRenderer.renderLevel: вид — в стек model-view, туман мира, рамка — линиями, только видимые
		// рёбра (Outline). Не через renderBlockOutline: туда встроен Fabric (BEFORE_BLOCK_OUTLINE), а его
		// контекст готовится в пропущенном renderLevel — NPE. Цвет плотнее ванильного (ARGB.black(102)): рамка ложится
		// на картинку Ведьмака, а не на тусклые блоки MC
		BlockOutlineRenderState o = state.blockOutlineRenderState;
		Vec3 cam = state.cameraRenderState.pos;
		Matrix4fStack mv = RenderSystem.getModelViewStack();
		mv.pushMatrix();
		mv.mul(view);
		RenderSystem.setShaderFog(fog);
		MultiBufferSource.BufferSource buffers = acc.mcwc$renderBuffers().bufferSource();
		PoseStack.Pose pose = new PoseStack().last();
		// клетка рельефа Ведьмака (barrier): только верх — куб ниже уходит под землю, которую рисует Ведьмак
		VoxelShape shape = Minecraft.getInstance().level.getBlockState(o.pos()).is(Blocks.BARRIER) ? TOP : o.shape();
		double ox = o.pos().getX() - cam.x;
		double oy = o.pos().getY() - cam.y;
		double oz = o.pos().getZ() - cam.z;
		if (Minecraft.getInstance().hitResult instanceof BlockHitResult hit && hit.getBlockPos().equals(o.pos()) && !shape.isEmpty()) {
			Outline.face(pose, buffers.getBuffer(RenderTypes.debugQuads()), shape.bounds(), ox, oy, oz, hit.getDirection(), ARGB.white(48));
			buffers.endBatch();
		}

		Outline.render(pose, buffers.getBuffer(RenderTypes.lines()), shape, ox, oy, oz, ARGB.black(200),
			Minecraft.getInstance().getWindow().getAppropriateLineWidth() * 1.5F);
		buffers.endBatch();
		mv.popMatrix();
	}

	/**
	 * Трещины на блоке, который копает игрок (ванильные стадии MultiPlayerGameMode.getDestroyStage): в Майнкрафте их
	 * рисует проход мира, которого у нас нет, и блок Ведьмака ломался без трещин. Барьер (рельеф Ведьмака) не копается.
	 */
	private static void mcwc$cracks(final LevelRendererAccessor acc, final Camera camera, final Matrix4f view, final GpuBufferSlice fog) {
		Minecraft mc = Minecraft.getInstance();
		if (mc.gameMode == null || mc.level == null || !mc.gameMode.isDestroying()) {
			return;
		}

		int stage = mc.gameMode.getDestroyStage();
		BlockPos pos = ((MultiPlayerGameModeAccessor) mc.gameMode).mcwc$destroyBlockPos();
		if (stage < 0 || stage >= ModelBakery.BREAKING_LOCATIONS.size() || pos == null) {
			return;
		}

		BlockState st = mc.level.getBlockState(pos);
		VoxelShape shape = st.getShape(mc.level, pos);
		if (st.isAir() || st.is(Blocks.BARRIER) || shape.isEmpty()) {
			return;
		}

		Vec3 cam = camera.position();
		Matrix4fStack mv = RenderSystem.getModelViewStack();
		mv.pushMatrix();
		mv.mul(view);
		RenderSystem.setShaderFog(fog);
		MultiBufferSource.BufferSource buffers = acc.mcwc$renderBuffers().bufferSource();
		Outline.cracks(new PoseStack().last(), buffers.getBuffer(RenderTypes.entityTranslucent(ModelBakery.BREAKING_LOCATIONS.get(stage))), shape,
			pos.getX() - cam.x, pos.getY() - cam.y, pos.getZ() - cam.z, ARGB.color(150, 255, 255, 255));
		buffers.endBatch();
		mv.popMatrix();
	}

	/** FOV мира (не руки) — как у камеры Ведьмака, если мост его прислал: без этого рамка блока не легла бы на блок. */
	@Inject(method = "getFov", at = @At("HEAD"), cancellable = true)
	private void mcwc$fov(final Camera camera, final float partial, final boolean useFovSetting, final CallbackInfoReturnable<Float> cir) {
		float v = Control.fov;
		if (useFovSetting && v > 0.0F) {
			cir.setReturnValue(v);
		}
	}

	/**
	 * Перед выбором цели под прицелом (каждый кадр и в тике, Minecraft.tick): прокси NPC Ведьмака — туда, где NPC
	 * сейчас (NpcProxies), чтобы удар MC попадал в того, кого рисует Ведьмак.
	 */
	@Inject(method = "pick(F)V", at = @At("HEAD"))
	private void mcwc$npcs(final float partial, final CallbackInfo ci) {
		NpcProxies.place(Minecraft.getInstance());
	}

	/** Раз в кадр, до рендера: глаз игрока мосту (Control.frame), пока клиент ходит сам. */
	@Inject(method = "render", at = @At("HEAD"))
	private void mcwc$frame(final DeltaTracker dt, final boolean renderLevel, final CallbackInfo ci) {
		Control.frame(dt.getGameTimeDeltaPartialTick(true));
	}

	/**
	 * Рука и предмет — на полной яркости: свет в точке игрока считает мир Майнкрафта (ночь там — когда угодно, сутки с
	 * Ведьмаком не связаны), и рука выходила чёрной посреди дня в Белом Саду. Подстроить под свет Ведьмака — потом.
	 */
	@Redirect(method = "renderItemInHand", at = @At(value = "INVOKE", target = "Lnet/minecraft/client/renderer/entity/EntityRenderDispatcher;getPackedLightCoords(Lnet/minecraft/world/entity/Entity;F)I"))
	private int mcwc$handLight(final EntityRenderDispatcher erd, final Entity e, final float f) {
		return Control.hideWorld ? LightTexture.FULL_BRIGHT : erd.getPackedLightCoords(e, f);
	}
}
