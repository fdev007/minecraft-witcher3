package mcwc;

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
import java.nio.charset.StandardCharsets;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import net.minecraft.client.Minecraft;

/**
 * TCP-сервер JSON-строк для моста на 127.0.0.1 (UTF-8, одно сообщение на строку), как у серверного мода (..\mc,
 * Link.java), но соединение одно: новое вытесняет старое. Входящие уходят в очередь задач Minecraft — тем же путём,
 * что события GLFW (MouseHandler.setup), и выполняются в начале кадра на потоке рендера. Исходящие пишет свой поток.
 */
final class Link {
	/** Строк в очереди отправки, дальше — обрыв: мост не читает. */
	private static final int QUEUE_LIMIT = 10000;
	private final int port;
	private volatile ServerSocket listen;
	private volatile Peer peer;

	Link(final int port) {
		this.port = port;
	}

	void start() throws IOException {
		ServerSocket s = new ServerSocket();
		s.setReuseAddress(true);
		s.bind(new InetSocketAddress(InetAddress.getLoopbackAddress(), this.port));
		this.listen = s;
		Thread t = new Thread(this::acceptLoop, "mcwc-accept");
		t.setDaemon(true);
		t.start();
		Mcwc.LOG.info("mcwc: жду мост на 127.0.0.1:{}", this.port);
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

		Peer p = this.peer;
		if (p != null) {
			p.close("client stopping");
		}
	}

	boolean up() {
		Peer p = this.peer;
		return p != null && !p.closed;
	}

	/** Из любого потока. Моста нет — строка теряется. */
	void send(final JsonObject m) {
		Peer p = this.peer;
		if (p != null) {
			p.send(m.toString());
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
				Peer n = new Peer(sock);
				Peer old = this.peer;
				this.peer = n;
				if (old != null) {
					old.close("replaced");
				}

				Mcwc.LOG.info("mcwc: мост подключился с {}", sock.getRemoteSocketAddress());
				n.start();
				Minecraft.getInstance().execute(() -> Control.onBridgeUp(n));
			} catch (IOException e) {
				if (this.listen != null) {
					Mcwc.LOG.warn("mcwc: accept", e);
				}
			}
		}
	}

	final class Peer {
		private final Socket sock;
		private final LinkedBlockingQueue<String> out = new LinkedBlockingQueue<>();
		volatile boolean closed;

		Peer(final Socket sock) {
			this.sock = sock;
		}

		void start() {
			Thread r = new Thread(this::readLoop, "mcwc-read");
			r.setDaemon(true);
			r.start();
			Thread w = new Thread(this::writeLoop, "mcwc-write");
			w.setDaemon(true);
			w.start();
		}

		void send(final String line) {
			if (this.closed) {
				return;
			}

			if (this.out.size() >= QUEUE_LIMIT) {
				this.close("send queue overflow");
				return;
			}

			this.out.add(line);
		}

		void close(final String why) {
			if (this.closed) {
				return;
			}

			this.closed = true;
			try {
				this.sock.close();
			} catch (IOException ignored) {
			}

			Mcwc.LOG.info("mcwc: мост отключён ({})", why);
			if (Link.this.peer == this) {
				Minecraft.getInstance().execute(Control::onBridgeDown);
			}
		}

		private void readLoop() {
			try (BufferedReader in = new BufferedReader(new InputStreamReader(this.sock.getInputStream(), StandardCharsets.UTF_8))) {
				String line;
				while ((line = in.readLine()) != null) {
					if (line.isBlank()) {
						continue;
					}

					JsonObject m;
					try {
						JsonElement e = JsonParser.parseString(line);
						if (!e.isJsonObject()) {
							continue;
						}

						m = e.getAsJsonObject();
					} catch (RuntimeException e) {
						Mcwc.LOG.warn("mcwc: не JSON: {}", line);
						continue;
					}

					Minecraft.getInstance().execute(() -> Control.handle(this, m));
				}

				this.close("eof");
			} catch (IOException e) {
				this.close(e.toString());
			}
		}

		private void writeLoop() {
			try (BufferedWriter w = new BufferedWriter(new OutputStreamWriter(this.sock.getOutputStream(), StandardCharsets.UTF_8))) {
				while (!this.closed) {
					String line = this.out.poll(200, TimeUnit.MILLISECONDS);
					if (line == null) {
						continue;
					}

					w.write(line);
					w.write('\n');
					// всё, что уже в очереди, — одной пачкой
					while ((line = this.out.poll()) != null) {
						w.write(line);
						w.write('\n');
					}

					w.flush();
				}
			} catch (IOException | InterruptedException e) {
				this.close(e.toString());
			}
		}
	}
}
