package mcw;

import it.unimi.dsi.fastutil.ints.Int2IntOpenHashMap;
import it.unimi.dsi.fastutil.ints.Int2ObjectOpenHashMap;
import it.unimi.dsi.fastutil.longs.Long2IntOpenHashMap;
import it.unimi.dsi.fastutil.longs.Long2ObjectMap;
import it.unimi.dsi.fastutil.longs.Long2ObjectOpenHashMap;
import it.unimi.dsi.fastutil.longs.LongArrayList;
import it.unimi.dsi.fastutil.longs.LongIterator;
import it.unimi.dsi.fastutil.longs.LongOpenHashSet;
import java.util.ArrayList;
import java.util.List;
import net.minecraft.commands.arguments.blocks.BlockStateParser;
import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.ChunkPos;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.chunk.LevelChunk;
import net.minecraft.world.level.chunk.LevelChunkSection;

/**
 * Зеркало того, что знает мост: для каждого отслеживаемого чанка — позиция → id состояния блока (воздух и barrier
 * не храним: barrier — это рельеф Ведьмака, мосту он не новость). Изменения ловит LevelMixin (RETURN
 * Level.setBlock): здесь копятся только позиции, итог читаем при сбросе и сравниваем с зеркалом — setBlock
 * возвращает true и тогда, когда onPlace тут же поменял состояние ещё раз.
 *
 * Только поток сервера, кроме {@link #onSetBlock}, который сам перекидывает чужой поток в очередь сервера.
 */
final class BlockSync {
	private static final int NONE = -1;

	/** Чанк → (позиция → id состояния). Ключ есть = чанк отслеживается и мосту отправлен. */
	private final Long2ObjectOpenHashMap<Long2IntOpenHashMap> chunks = new Long2ObjectOpenHashMap<>();
	private final LongOpenHashSet dirty = new LongOpenHashSet();
	/** Имена состояний ("minecraft:oak_log[axis=y]") по id: сериализация не бесплатная, а состояний немного. */
	private final Int2ObjectOpenHashMap<String> names = new Int2ObjectOpenHashMap<>();
	private ServerLevel level;
	private int radius;
	private ChunkPos center;
	private boolean active;

	boolean active() {
		return this.active;
	}

	ServerLevel level() {
		return this.level;
	}

	int radius() {
		return this.radius;
	}

	int trackedChunks() {
		return this.chunks.size();
	}

	static boolean visible(final BlockState s) {
		return !s.isAir() && !s.is(Blocks.BARRIER);
	}

	/** Из LevelMixin; любой поток. */
	void onSetBlock(final ServerLevel l, final BlockPos pos) {
		if (!this.active || l != this.level) {
			return;
		}

		if (l.getServer().isSameThread()) {
			this.dirty.add(pos.asLong());
		} else {
			long p = pos.asLong();
			Mcw.later(() -> this.dirty.add(p));
		}
	}

	/**
	 * snap, смена измерения (и hello после stop): снапшот всех загруженных чанков в радиусе за один тик. Мост держит
	 * чанки прошлого снапшота; те, что в новый не войдут (другой уровень, меньший радиус), снимаем явным unchunk —
	 * иначе они остались бы у моста навсегда, без обновлений. Попавшие в новый снапшот придут chunk'ом заново.
	 */
	void start(final ServerLevel l, final int r, final ChunkPos c, final int tick) {
		LongArrayList gone = new LongArrayList();
		for (long key : this.chunks.keySet()) {
			if (l != this.level || Math.max(Math.abs(ChunkPos.getX(key) - c.x), Math.abs(ChunkPos.getZ(key) - c.z)) > r) {
				gone.add(key);
			}
		}

		for (int k = 0; k < gone.size(); k++) {
			untrack(gone.getLong(k), tick);
		}

		this.chunks.clear();
		this.dirty.clear();
		this.level = l;
		this.radius = r;
		this.center = c;
		this.active = true;
		int[] sent = retrack(c, tick);
		Mcw.link().send("{\"t\":\"snap_end\",\"tick\":" + tick + ",\"chunks\":" + sent[0] + ",\"blocks\":" + sent[1] + "}");
	}

	void stop() {
		this.active = false;
		this.chunks.clear();
		this.dirty.clear();
		this.level = null;
	}

	/** Геральт сменил чанк (или проверка раз в секунду): новые чанки в радиусе — снапшот, вышедшие — unchunk. */
	int[] retrack(final ChunkPos c, final int tick) {
		this.center = c;
		int chunksSent = 0;
		int blocksSent = 0;
		LongArrayList gone = new LongArrayList();
		for (Long2ObjectMap.Entry<Long2IntOpenHashMap> e : this.chunks.long2ObjectEntrySet()) {
			long key = e.getLongKey();
			if (Math.max(Math.abs(ChunkPos.getX(key) - c.x), Math.abs(ChunkPos.getZ(key) - c.z)) > this.radius) {
				gone.add(key);
			}
		}

		for (int k = 0; k < gone.size(); k++) {
			untrack(gone.getLong(k), tick);
		}

		for (int dx = -this.radius; dx <= this.radius; dx++) {
			for (int dz = -this.radius; dz <= this.radius; dz++) {
				int x = c.x + dx;
				int z = c.z + dz;
				if (this.chunks.containsKey(ChunkPos.asLong(x, z))) {
					continue;
				}

				LevelChunk ch = this.level.getChunkSource().getChunkNow(x, z);
				if (ch != null) {
					blocksSent += sendChunk(ch, tick);
					chunksSent++;
				}
			}
		}

		return new int[] {chunksSent, blocksSent};
	}

	/** ServerChunkEvents.CHUNK_LOAD: чанк в радиусе догрузился после снапшота. */
	void onChunkLoad(final ServerLevel l, final LevelChunk ch, final int tick) {
		if (!this.active || l != this.level || this.center == null) {
			return;
		}

		ChunkPos p = ch.getPos();
		if (p.getChessboardDistance(this.center) <= this.radius && !this.chunks.containsKey(p.toLong())) {
			sendChunk(ch, tick);
		}
	}

	void onChunkUnload(final ServerLevel l, final LevelChunk ch, final int tick) {
		if (this.active && l == this.level && this.chunks.containsKey(ch.getPos().toLong())) {
			untrack(ch.getPos().toLong(), tick);
		}
	}

	private void untrack(final long key, final int tick) {
		this.chunks.remove(key);
		Mcw.link().send("{\"t\":\"unchunk\",\"tick\":" + tick + ",\"c\":[" + ChunkPos.getX(key) + "," + ChunkPos.getZ(key) + "]}");
	}

	/** Все видимые блоки чанка одним сообщением chunk{c,pal,b:[x,y,z,i,...]}; возвращает число блоков. */
	private int sendChunk(final LevelChunk ch, final int tick) {
		ChunkPos cp = ch.getPos();
		Long2IntOpenHashMap mirror = new Long2IntOpenHashMap();
		mirror.defaultReturnValue(NONE);
		Palette pal = new Palette();
		StringBuilder b = new StringBuilder();
		LevelChunkSection[] sections = ch.getSections();
		int x0 = cp.getMinBlockX();
		int z0 = cp.getMinBlockZ();
		for (int si = 0; si < sections.length; si++) {
			LevelChunkSection sec = sections[si];
			if (sec == null || sec.hasOnlyAir()) {
				continue;
			}

			int y0 = ch.getSectionYFromSectionIndex(si) << 4;
			for (int y = 0; y < 16; y++) {
				for (int z = 0; z < 16; z++) {
					for (int x = 0; x < 16; x++) {
						BlockState s = sec.getBlockState(x, y, z);
						if (!visible(s)) {
							continue;
						}

						int id = Block.getId(s);
						int wx = x0 + x;
						int wy = y0 + y;
						int wz = z0 + z;
						mirror.put(BlockPos.asLong(wx, wy, wz), id);
						if (!b.isEmpty()) {
							b.append(',');
						}

						b.append(wx).append(',').append(wy).append(',').append(wz).append(',').append(pal.index(id));
					}
				}
			}
		}

		this.chunks.put(cp.toLong(), mirror);
		Mcw.link().send("{\"t\":\"chunk\",\"tick\":" + tick + ",\"c\":[" + cp.x + "," + cp.z + "],\"pal\":" + pal.json() + ",\"b\":[" + b + "]}");
		return mirror.size();
	}

	/**
	 * Итог тика (или сразу после act): позиции из dirty сравниваем с зеркалом, расхождения — одним blocks.
	 * Позиции вне отслеживаемых чанков пропускаем: они приедут целиком снапшотом чанка.
	 */
	void flush(final int tick) {
		if (!this.active || this.dirty.isEmpty()) {
			return;
		}

		Palette pal = new Palette();
		StringBuilder set = new StringBuilder();
		StringBuilder clear = new StringBuilder();
		BlockPos.MutableBlockPos mp = new BlockPos.MutableBlockPos();
		for (LongIterator it = this.dirty.iterator(); it.hasNext(); ) {
			long p = it.nextLong();
			int x = BlockPos.getX(p);
			int y = BlockPos.getY(p);
			int z = BlockPos.getZ(p);
			Long2IntOpenHashMap mirror = this.chunks.get(ChunkPos.asLong(x >> 4, z >> 4));
			if (mirror == null) {
				continue;
			}

			LevelChunk ch = this.level.getChunkSource().getChunkNow(x >> 4, z >> 4);
			if (ch == null) {
				continue;
			}

			BlockState s = ch.getBlockState(mp.set(x, y, z));
			int now = visible(s) ? Block.getId(s) : NONE;
			int was = mirror.get(p);
			if (now == was) {
				continue;
			}

			if (now == NONE) {
				mirror.remove(p);
				(clear.isEmpty() ? clear : clear.append(',')).append(x).append(',').append(y).append(',').append(z);
			} else {
				mirror.put(p, now);
				(set.isEmpty() ? set : set.append(',')).append(x).append(',').append(y).append(',').append(z).append(',').append(pal.index(now));
			}
		}

		this.dirty.clear();
		if (!set.isEmpty() || !clear.isEmpty()) {
			Mcw.link().send("{\"t\":\"blocks\",\"tick\":" + tick + ",\"pal\":" + pal.json() + ",\"set\":[" + set + "],\"clear\":[" + clear + "]}");
		}
	}

	/** Сколько блоков в зеркале (для pong и логов). */
	int mirrorSize() {
		int n = 0;
		for (Long2IntOpenHashMap m : this.chunks.values()) {
			n += m.size();
		}

		return n;
	}

	String name(final int id) {
		String n = this.names.get(id);
		if (n == null) {
			n = BlockStateParser.serialize(Block.stateById(id));
			this.names.put(id, n);
		}

		return n;
	}

	/** Палитра одного сообщения: id состояния → индекс в "pal". */
	private final class Palette {
		private final Int2IntOpenHashMap index = new Int2IntOpenHashMap();
		private final List<String> list = new ArrayList<>();

		Palette() {
			this.index.defaultReturnValue(NONE);
		}

		int index(final int id) {
			int i = this.index.get(id);
			if (i == NONE) {
				i = this.list.size();
				this.index.put(id, i);
				this.list.add(name(id));
			}

			return i;
		}

		String json() {
			StringBuilder b = new StringBuilder("[");
			for (int i = 0; i < this.list.size(); i++) {
				if (i > 0) {
					b.append(',');
				}

				b.append(Json.q(this.list.get(i)));
			}

			return b.append(']').toString();
		}
	}
}
