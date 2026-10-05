package mcw;

import com.mojang.serialization.Codec;
import com.mojang.serialization.codecs.RecordCodecBuilder;
import it.unimi.dsi.fastutil.ints.IntArrayList;
import it.unimi.dsi.fastutil.longs.LongIterator;
import it.unimi.dsi.fastutil.longs.LongOpenHashSet;
import java.util.Arrays;
import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.chunk.LevelChunk;
import net.minecraft.world.level.chunk.LevelChunkSection;
import net.minecraft.world.level.saveddata.SavedData;
import net.minecraft.world.level.saveddata.SavedDataType;

/**
 * Рельеф Ведьмака как barrier (ground / ground_clear). Позиции своих barrier хранятся в SavedData уровня
 * (world/data/mcw_ground.dat, у Нижнего мира — world/DIM-1/data/): сами блоки сохраняются с чанками, и без этой
 * записи ground_clear после рестарта сервера свой рельеф бы не нашёл. Позиции в незагруженных чанках не теряем:
 * ground сообщает пропущенные колонны, ground_clear оставляет их в наборе до следующего вызова.
 *
 * Только поток сервера.
 */
final class Ground extends SavedData {
	private static final int FLAGS = Block.UPDATE_CLIENTS | Block.UPDATE_KNOWN_SHAPE;
	/** ground_clear{any} обходит box по чанкам; больше 64×64 чанков за раз не берём. */
	private static final int MAX_SWEEP_CHUNKS = 64 * 64;

	static final Codec<Ground> CODEC = RecordCodecBuilder.create(i -> i.group(
		Codec.LONG_STREAM.fieldOf("barriers").forGetter(g -> Arrays.stream(g.set.toLongArray()))
	).apply(i, s -> new Ground(s.toArray())));
	/** DataFixTypes = null: фиксеров у своих данных нет, null пропускает Fabric API (object-builder, DimensionDataStorageMixin). */
	static final SavedDataType<Ground> TYPE = new SavedDataType<>("mcw_ground", Ground::new, CODEC, null);

	private final LongOpenHashSet set;

	Ground() {
		this.set = new LongOpenHashSet();
	}

	private Ground(final long[] saved) {
		this.set = new LongOpenHashSet(saved);
	}

	static Ground of(final ServerLevel level) {
		return level.getDataStorage().computeIfAbsent(TYPE);
	}

	/**
	 * Колонны {x, z, y0, y1, ...} (включительно) как barrier; заменяется только воздух, уже стоящий barrier
	 * становится «своим» (остался от старой версии мода или от прошлой привязки). Колонны в незагруженных чанках
	 * пропускаются и возвращаются в skip — мост пришлёт их снова, когда придёт chunk.
	 */
	static String place(final ServerLevel level, final int[] c, final int tick) {
		Ground g = of(level);
		BlockState barrier = Blocks.BARRIER.defaultBlockState();
		BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
		IntArrayList skip = new IntArrayList();
		int placed = 0;
		int before = g.set.size();
		for (int i = 0; i < c.length; i += 4) {
			int x = c[i];
			int z = c[i + 1];
			if (level.getChunkSource().getChunkNow(x >> 4, z >> 4) == null) {
				skip.add(x);
				skip.add(z);
				continue;
			}

			for (int y = Math.max(c[i + 2], level.getMinY()), y1 = Math.min(c[i + 3], level.getMaxY()); y <= y1; y++) {
				pos.set(x, y, z);
				BlockState s = level.getBlockState(pos);
				if (s.isAir()) {
					level.setBlock(pos, barrier, FLAGS);
					g.set.add(pos.asLong());
					placed++;
				} else if (s.is(Blocks.BARRIER)) {
					g.set.add(pos.asLong());
				}
			}
		}

		if (g.set.size() != before) {
			g.setDirty();
		}

		return "{\"t\":\"ground_ok\",\"tick\":" + tick + ",\"placed\":" + placed + ",\"total\":" + g.set.size() + ",\"unloaded\":" + skip.size() / 2
			+ ",\"skip\":" + ints(skip) + "}";
	}

	/**
	 * Убрать свой рельеф: в box [x0,z0,x1,z1] или весь. Позиции в незагруженных чанках остаются в наборе (left).
	 * any = true (только с box): ещё и любые barrier в загруженных чанках box — на случай рельефа, поставленного
	 * до того, как набор стал сохраняться, или руками.
	 */
	static String clear(final ServerLevel level, final int[] box, final boolean any, final int tick) {
		Ground g = of(level);
		BlockState air = Blocks.AIR.defaultBlockState();
		BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
		int removed = 0;
		int left = 0;
		int before = g.set.size();
		for (LongIterator it = g.set.iterator(); it.hasNext(); ) {
			long l = it.nextLong();
			pos.set(BlockPos.getX(l), BlockPos.getY(l), BlockPos.getZ(l));
			if (box != null && (pos.getX() < box[0] || pos.getZ() < box[1] || pos.getX() > box[2] || pos.getZ() > box[3])) {
				continue;
			}

			if (level.getChunkSource().getChunkNow(pos.getX() >> 4, pos.getZ() >> 4) == null) {
				left++;
				continue;
			}

			if (level.getBlockState(pos).is(Blocks.BARRIER)) {
				level.setBlock(pos, air, FLAGS);
				removed++;
			}

			it.remove();
		}

		int unloadedChunks = 0;
		if (any && box != null) {
			for (int cx = box[0] >> 4; cx <= box[2] >> 4; cx++) {
				for (int cz = box[1] >> 4; cz <= box[3] >> 4; cz++) {
					LevelChunk ch = level.getChunkSource().getChunkNow(cx, cz);
					if (ch == null) {
						unloadedChunks++;
					} else {
						removed += sweep(level, ch, box, pos, air);
					}
				}
			}
		}

		if (g.set.size() != before) {
			g.setDirty();
		}

		return "{\"t\":\"ground_ok\",\"tick\":" + tick + ",\"removed\":" + removed + ",\"left\":" + left + ",\"total\":" + g.set.size()
			+ (any ? ",\"unloaded_chunks\":" + unloadedChunks : "") + "}";
	}

	/** Размер box для ground_clear{any} в чанках, чтобы не обходить пол-мира по ошибке моста. */
	static boolean sweepTooBig(final int[] box) {
		long w = (long) (box[2] >> 4) - (box[0] >> 4) + 1;
		long h = (long) (box[3] >> 4) - (box[1] >> 4) + 1;
		return w <= 0 || h <= 0 || w * h > MAX_SWEEP_CHUNKS;
	}

	private static int sweep(final ServerLevel level, final LevelChunk ch, final int[] box, final BlockPos.MutableBlockPos pos, final BlockState air) {
		int n = 0;
		int x0 = Math.max(box[0], ch.getPos().getMinBlockX());
		int x1 = Math.min(box[2], ch.getPos().getMaxBlockX());
		int z0 = Math.max(box[1], ch.getPos().getMinBlockZ());
		int z1 = Math.min(box[3], ch.getPos().getMaxBlockZ());
		LevelChunkSection[] sections = ch.getSections();
		for (int si = 0; si < sections.length; si++) {
			LevelChunkSection sec = sections[si];
			if (sec == null || sec.hasOnlyAir() || !sec.maybeHas(s -> s.is(Blocks.BARRIER))) {
				continue;
			}

			int y0 = ch.getSectionYFromSectionIndex(si) << 4;
			for (int y = y0; y < y0 + 16; y++) {
				for (int z = z0; z <= z1; z++) {
					for (int x = x0; x <= x1; x++) {
						if (sec.getBlockState(x & 15, y & 15, z & 15).is(Blocks.BARRIER)) {
							level.setBlock(pos.set(x, y, z), air, FLAGS);
							n++;
						}
					}
				}
			}
		}

		return n;
	}

	private static String ints(final IntArrayList v) {
		StringBuilder b = new StringBuilder("[");
		for (int i = 0; i < v.size(); i++) {
			if (i > 0) {
				b.append(',');
			}

			b.append(v.getInt(i));
		}

		return b.append(']').toString();
	}
}
