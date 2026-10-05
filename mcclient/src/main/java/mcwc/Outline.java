package mcwc;

import com.mojang.blaze3d.vertex.PoseStack;
import com.mojang.blaze3d.vertex.VertexConsumer;
import net.minecraft.client.renderer.LightTexture;
import net.minecraft.client.renderer.texture.OverlayTexture;
import net.minecraft.core.Direction;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.shapes.VoxelShape;
import org.joml.Vector3f;

/**
 * Рамка блока под прицелом — как ShapeRenderer.renderShape, но только видимые рёбра. В Майнкрафте задние рёбра рамки
 * прячет глубина самого блока; у нас мира в буфере глубины нет (блок рисует Ведьмак), и без отбора рамка выходила
 * каркасом из всех 12 рёбер. Ребро видно, если хотя бы одна из двух его граней смотрит на камеру.
 */
public final class Outline {
	private Outline() {
	}

	/** (ox, oy, oz) — угол блока относительно камеры, как в LevelRenderer.renderHitOutline. */
	public static void render(final PoseStack.Pose pose, final VertexConsumer vc, final VoxelShape shape, final double ox, final double oy,
		final double oz, final int color, final float width) {
		for (AABB b : shape.toAabbs()) {
			float x0 = (float) (b.minX + ox);
			float y0 = (float) (b.minY + oy);
			float z0 = (float) (b.minZ + oz);
			float x1 = (float) (b.maxX + ox);
			float y1 = (float) (b.maxY + oy);
			float z1 = (float) (b.maxZ + oz);
			// грань смотрит на камеру (камера в нуле), если камера снаружи её плоскости
			boolean[] fx = {x0 > 0.0F, x1 < 0.0F};
			boolean[] fy = {y0 > 0.0F, y1 < 0.0F};
			boolean[] fz = {z0 > 0.0F, z1 < 0.0F};
			float[] xs = {x0, x1};
			float[] ys = {y0, y1};
			float[] zs = {z0, z1};
			for (int i = 0; i < 2; i++) {
				for (int j = 0; j < 2; j++) {
					if (fy[i] || fz[j]) {
						line(pose, vc, x0, ys[i], zs[j], x1, ys[i], zs[j], color, width);
					}

					if (fx[i] || fz[j]) {
						line(pose, vc, xs[i], y0, zs[j], xs[i], y1, zs[j], color, width);
					}

					if (fx[i] || fy[j]) {
						line(pose, vc, xs[i], ys[j], z0, xs[i], ys[j], z1, color, width);
					}
				}
			}
		}
	}

	/**
	 * Грань, к которой встанет новый блок (сторона попадания луча): полупрозрачный квад чуть снаружи грани (QUADS,
	 * RenderTypes.debugQuads). В Майнкрафте его нет, но там видно сам блок; здесь блок рисует Ведьмак, и сторону
	 * иначе не угадать.
	 */
	public static void face(final PoseStack.Pose pose, final VertexConsumer vc, final AABB box, final double ox, final double oy, final double oz,
		final Direction dir, final int color) {
		final float e = 0.004F;
		float x0 = (float) (box.minX + ox) - e;
		float y0 = (float) (box.minY + oy) - e;
		float z0 = (float) (box.minZ + oz) - e;
		float x1 = (float) (box.maxX + ox) + e;
		float y1 = (float) (box.maxY + oy) + e;
		float z1 = (float) (box.maxZ + oz) + e;
		switch (dir) {
			case DOWN -> quad(pose, vc, x0, y0, z0, x1, y0, z0, x1, y0, z1, x0, y0, z1, color);
			case UP -> quad(pose, vc, x0, y1, z0, x0, y1, z1, x1, y1, z1, x1, y1, z0, color);
			case NORTH -> quad(pose, vc, x0, y0, z0, x0, y1, z0, x1, y1, z0, x1, y0, z0, color);
			case SOUTH -> quad(pose, vc, x0, y0, z1, x1, y0, z1, x1, y1, z1, x0, y1, z1, color);
			case WEST -> quad(pose, vc, x0, y0, z0, x0, y0, z1, x0, y1, z1, x0, y1, z0, color);
			case EAST -> quad(pose, vc, x1, y0, z0, x1, y1, z0, x1, y1, z1, x1, y0, z1, color);
		}
	}

	/**
	 * Трещины копания (ванильные destroy_stage_N) на видимых гранях блока, чуть снаружи них. Ваниль кладёт их на блок
	 * умножением (RenderTypes.crumbling), а у нас под ними ключ magenta — умножение испортило бы ключ. Серые пиксели
	 * трещины полупрозрачно (RenderTypes.entityTranslucent) оверлей снимает точно, как серое стекло, и на блоке Ведьмака
	 * выходит то же затемнение по трещине. UV — координаты в блоке, как у граней полного куба. Нормаль у всех граней —
	 * вверх: освещение граней одинаковое, трещина не темнее на боках.
	 */
	public static void cracks(final PoseStack.Pose pose, final VertexConsumer vc, final VoxelShape shape, final double ox, final double oy,
		final double oz, final int color) {
		final float e = 0.006F;
		for (AABB b : shape.toAabbs()) {
			float x0 = (float) (b.minX + ox) - e;
			float y0 = (float) (b.minY + oy) - e;
			float z0 = (float) (b.minZ + oz) - e;
			float x1 = (float) (b.maxX + ox) + e;
			float y1 = (float) (b.maxY + oy) + e;
			float z1 = (float) (b.maxZ + oz) + e;
			float u0x = (float) b.minX;
			float u1x = (float) b.maxX;
			float u0z = (float) b.minZ;
			float u1z = (float) b.maxZ;
			float v0 = 1.0F - (float) b.maxY;
			float v1 = 1.0F - (float) b.minY;
			if (x0 > 0.0F) {
				tex(pose, vc, color, x0, y0, z0, u0z, v1, x0, y0, z1, u1z, v1, x0, y1, z1, u1z, v0, x0, y1, z0, u0z, v0);
			}

			if (x1 < 0.0F) {
				tex(pose, vc, color, x1, y0, z1, u0z, v1, x1, y0, z0, u1z, v1, x1, y1, z0, u1z, v0, x1, y1, z1, u0z, v0);
			}

			if (z0 > 0.0F) {
				tex(pose, vc, color, x1, y0, z0, u0x, v1, x0, y0, z0, u1x, v1, x0, y1, z0, u1x, v0, x1, y1, z0, u0x, v0);
			}

			if (z1 < 0.0F) {
				tex(pose, vc, color, x0, y0, z1, u0x, v1, x1, y0, z1, u1x, v1, x1, y1, z1, u1x, v0, x0, y1, z1, u0x, v0);
			}

			if (y0 > 0.0F) {
				tex(pose, vc, color, x0, y0, z0, u0x, u0z, x1, y0, z0, u1x, u0z, x1, y0, z1, u1x, u1z, x0, y0, z1, u0x, u1z);
			}

			if (y1 < 0.0F) {
				tex(pose, vc, color, x0, y1, z1, u0x, u1z, x1, y1, z1, u1x, u1z, x1, y1, z0, u1x, u0z, x0, y1, z0, u0x, u0z);
			}
		}
	}

	private static void tex(final PoseStack.Pose pose, final VertexConsumer vc, final int color, final float ax, final float ay, final float az,
		final float au, final float av, final float bx, final float by, final float bz, final float bu, final float bv, final float cx, final float cy,
		final float cz, final float cu, final float cv, final float dx, final float dy, final float dz, final float du, final float dv) {
		vert(pose, vc, color, ax, ay, az, au, av);
		vert(pose, vc, color, bx, by, bz, bu, bv);
		vert(pose, vc, color, cx, cy, cz, cu, cv);
		vert(pose, vc, color, dx, dy, dz, du, dv);
	}

	private static void vert(final PoseStack.Pose pose, final VertexConsumer vc, final int color, final float x, final float y, final float z,
		final float u, final float v) {
		vc.addVertex(pose, x, y, z).setColor(color).setUv(u, v).setOverlay(OverlayTexture.NO_OVERLAY).setLight(LightTexture.FULL_BRIGHT)
			.setNormal(pose, 0.0F, 1.0F, 0.0F);
	}

	private static void quad(final PoseStack.Pose pose, final VertexConsumer vc, final float ax, final float ay, final float az, final float bx,
		final float by, final float bz, final float cx, final float cy, final float cz, final float dx, final float dy, final float dz, final int color) {
		vc.addVertex(pose, ax, ay, az).setColor(color);
		vc.addVertex(pose, bx, by, bz).setColor(color);
		vc.addVertex(pose, cx, cy, cz).setColor(color);
		vc.addVertex(pose, dx, dy, dz).setColor(color);
	}

	private static void line(final PoseStack.Pose pose, final VertexConsumer vc, final float ax, final float ay, final float az, final float bx,
		final float by, final float bz, final int color, final float width) {
		Vector3f n = new Vector3f(bx - ax, by - ay, bz - az).normalize();
		vc.addVertex(pose, ax, ay, az).setColor(color).setNormal(pose, n).setLineWidth(width);
		vc.addVertex(pose, bx, by, bz).setColor(color).setNormal(pose, n).setLineWidth(width);
	}
}
