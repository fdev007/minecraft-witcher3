package mcw;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.SocketException;
import java.nio.charset.StandardCharsets;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import net.minecraft.server.MinecraftServer;

/**
 * TCP-сервер JSON-строк (UTF-8, одно сообщение на строку). Соединений может быть сколько угодно; мостом становится
 * то, что прислало hello (прежний мост при этом вытесняется). Остальные — отладочные клиенты (fake_bridge.py cmd,
 * ping, pos, act): их запросы выполняются, ответы уходят им же, а события мира — только мосту.
 *
 * Потоки — демоны: приём, чтение и запись на каждое соединение. С миром работает только поток сервера:
 * входящие сообщения уходят в очередь задач сервера, исходящие сервер кладёт в очередь, пишет их поток записи.
 */
final class Link {
	private final MinecraftServer server;
	private final McwConfig cfg;
	private final AtomicInteger ids = new AtomicInteger();
	private final Set<Peer> peers = ConcurrentHashMap.newKeySet();
	private volatile ServerSocket listen;
	/** Соединение, приславшее последний hello; события мира — ему. */
	private volatile Peer bridge;

	Link(final MinecraftServer server, final McwConfig cfg) {
		this.server = server;
		this.cfg = cfg;
	}

	void start() throws IOException {
		InetAddress addr = InetAddress.getByName(this.cfg.host);
		if (!addr.isLoopbackAddress()) {
			Mcw.LOG.warn("mcw: host={} не loopback — протокол без пароля, мир открыт всей сети", this.cfg.host);
		}

		ServerSocket s = new ServerSocket();
		s.setReuseAddress(true);
		s.bind(new InetSocketAddress(addr, this.cfg.port));
		this.listen = s;
		Thread t = new Thread(this::acceptLoop, "mcw-accept");
		t.setDaemon(true);
		t.start();
		Mcw.LOG.info("mcw: жду мост на {}:{}", this.cfg.host, this.cfg.port);
	}

	void stop() {
		ServerSocket s = this.listen;
		this.listen = null;
		if (s != null) {
			try {
				s.close();
			} catch (IOException ignored) {
			}
		}

		for (Peer p : this.peers) {
			p.close("server stopping");
		}
	}

	/** Событие мира — мосту, из любого потока. Моста нет — строка теряется (на hello он всё равно получит снапшот). */
	void send(final String line) {
		Peer b = this.bridge;
		if (b != null) {
			b.send(line);
		}
	}

	/** hello с этого соединения: теперь мост — оно, прежний мост закрываем. Поток сервера (из Bridge.hello). */
	void promote(final Peer p) {
		Peer old = this.bridge;
		this.bridge = p;
		if (old != null && old != p) {
			old.evicted = true;
			old.close("replaced by #" + p.id);
		}
	}

	private void acceptLoop() {
		while (true) {
			ServerSocket s = this.listen;
			if (s == null) {
				return;
			}

			try {
				Socket sock = s.accept();
				sock.setTcpNoDelay(true);
				Peer n = new Peer(sock, this.ids.incrementAndGet());
				this.peers.add(n);
				Mcw.LOG.info("mcw: подключился #{} с {}", n.id, sock.getRemoteSocketAddress());
				n.start();
			} catch (IOException e) {
				if (this.listen != null) {
					Mcw.LOG.warn("mcw: accept", e);
				}
			}
		}
	}

	/** Одно соединение. send — ответ именно ему (ack, cmd_out, pong, error...). */
	final class Peer {
		final Socket sock;
		final int id;
		final LinkedBlockingQueue<String> out = new LinkedBlockingQueue<>();
		volatile boolean closed;
		/**
		 * Вытеснен новым мостом. Строки, прочитанные до вытеснения, ещё лежат в очереди сервера — их не выполняем.
		 * Просто закрытое соединение (EOF) не вытеснено: «cmd stop и сразу отключиться» должно сработать.
		 */
		volatile boolean evicted;

		Peer(final Socket sock, final int id) {
			this.sock = sock;
			this.id = id;
		}

		void start() {
			Thread r = new Thread(this::readLoop, "mcw-read-" + this.id);
			r.setDaemon(true);
			r.start();
			Thread w = new Thread(this::writeLoop, "mcw-write-" + this.id);
			w.setDaemon(true);
			w.start();
		}

		void send(final String line) {
			if (this.closed) {
				return;
			}

			if (this.out.size() >= Link.this.cfg.queueLimit) {
				close("send queue overflow (" + this.out.size() + " lines): peer is not reading");
				return;
			}

			this.out.offer(line);
		}

		void close(final String why) {
			if (this.closed) {
				return;
			}

			this.closed = true;
			Link.this.peers.remove(this);
			Mcw.LOG.info("mcw: соединение #{} закрыто: {}", this.id, why);
			try {
				this.sock.close();
			} catch (IOException ignored) {
			}

			// всегда в очередь задач: close() бывает вызван из send() посреди сброса blocks на потоке сервера
			if (Link.this.server.isRunning()) {
				Mcw.later(() -> Bridge.onDisconnect(this));
			}
		}

		private void readLoop() {
			try (BufferedReader in = new BufferedReader(new InputStreamReader(this.sock.getInputStream(), StandardCharsets.UTF_8))) {
				String line;
				while (!this.closed && (line = in.readLine()) != null) {
					if (line.isBlank()) {
						continue;
					}

					JsonObject m;
					try {
						JsonElement e = JsonParser.parseString(line);
						if (!e.isJsonObject()) {
							throw new IllegalArgumentException("not an object");
						}

						m = e.getAsJsonObject();
					} catch (RuntimeException e) {
						send("{\"t\":\"error\",\"tick\":" + Link.this.server.getTickCount() + ",\"why\":\"bad_json\",\"text\":"
							+ Json.q(String.valueOf(e.getMessage())) + "}");
						continue;
					}

					if (!Link.this.server.isRunning()) {
						break;
					}

					Mcw.later(() -> Bridge.handle(this, m));
				}

				close("eof");
			} catch (SocketException e) {
				close("socket: " + e.getMessage());
			} catch (IOException e) {
				close("read: " + e);
			}
		}

		private void writeLoop() {
			try (BufferedWriter w = new BufferedWriter(new OutputStreamWriter(this.sock.getOutputStream(), StandardCharsets.UTF_8), 1 << 16)) {
				while (!this.closed) {
					String line = this.out.poll(250, TimeUnit.MILLISECONDS);
					if (line == null) {
						continue;
					}

					w.write(line);
					w.write('\n');
					if (this.out.isEmpty()) {
						w.flush();
					}
				}
			} catch (IOException e) {
				close("write: " + e.getMessage());
			} catch (InterruptedException e) {
				Thread.currentThread().interrupt();
				close("interrupted");
			}
		}
	}
}
