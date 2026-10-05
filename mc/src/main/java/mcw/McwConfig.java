package mcw;

import java.io.IOException;
import java.io.Reader;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Properties;

/**
 * config/mcw.properties в папке сервера. Нет файла — пишем дефолты. -Dmcw.&lt;ключ&gt;=... перекрывает файл
 * (так smoke-тест и мост могут сменить порт, не трогая конфиг).
 */
public final class McwConfig {
	/** Только loopback: протокол без аутентификации. */
	public final String host;
	public final int port;
	/** Радиус снапшота по умолчанию, в чанках (hello.want.radius перекрывает). */
	public final int radius;
	/** Имя ServerPlayer'а на месте Геральта; uuid — офлайновый от имени. */
	public final String name;
	/** Где Геральт появляется, если в playerdata его ещё нет. */
	public final double spawnX, spawnY, spawnZ;
	/** Столько строк может ждать отправки; больше — мост не читает, рвём соединение (он переподключится и получит снапшот). */
	public final int queueLimit;
	/** Поток ent: мобы и предметы не дальше ent_radius блоков от Геральта, раз в ent_every тиков, не больше ent_max ближних. */
	public final double entRadius;
	public final int entEvery;
	public final int entMax;

	private McwConfig(final Properties p) {
		this.host = p.getProperty("host", "127.0.0.1").trim();
		this.port = Integer.parseInt(p.getProperty("port", "25600").trim());
		this.radius = Integer.parseInt(p.getProperty("radius", "4").trim());
		this.name = p.getProperty("name", "Geralt").trim();
		String[] s = p.getProperty("spawn", "0.5,70,0.5").split(",");
		this.spawnX = Double.parseDouble(s[0].trim());
		this.spawnY = Double.parseDouble(s[1].trim());
		this.spawnZ = Double.parseDouble(s[2].trim());
		this.queueLimit = Integer.parseInt(p.getProperty("queue_limit", "200000").trim());
		this.entRadius = Double.parseDouble(p.getProperty("ent_radius", "32").trim());
		this.entEvery = Math.max(1, Integer.parseInt(p.getProperty("ent_every", "2").trim()));
		this.entMax = Math.max(0, Integer.parseInt(p.getProperty("ent_max", "64").trim()));
	}

	static McwConfig load(final Path configDir) {
		Path file = configDir.resolve("mcw.properties");
		Properties p = new Properties();
		if (Files.isRegularFile(file)) {
			try (Reader r = Files.newBufferedReader(file, StandardCharsets.UTF_8)) {
				p.load(r);
			} catch (IOException e) {
				Mcw.LOG.warn("mcw: не прочитал {}, беру дефолты", file, e);
			}
		} else {
			try {
				Files.createDirectories(configDir);
				try (Writer w = Files.newBufferedWriter(file, StandardCharsets.UTF_8)) {
					w.write("# MCW: связь с мостом (JSON-строки по TCP) и ServerPlayer на месте Геральта\n"
						+ "host=127.0.0.1\nport=25600\nradius=4\nname=Geralt\nspawn=0.5,70,0.5\nqueue_limit=200000\n"
						+ "ent_radius=32\nent_every=2\nent_max=64\n");
				}
			} catch (IOException e) {
				Mcw.LOG.warn("mcw: не записал дефолтный {}", file, e);
			}
		}

		for (String key : new String[] {"host", "port", "radius", "name", "spawn", "queue_limit", "ent_radius", "ent_every", "ent_max"}) {
			String v = System.getProperty("mcw." + key);
			if (v != null) {
				p.setProperty(key, v);
			}
		}

		return new McwConfig(p);
	}
}
