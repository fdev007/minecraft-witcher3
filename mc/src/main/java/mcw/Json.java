package mcw;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.util.Locale;

/** Мелочи для JSON-строк протокола: исходящие собираем StringBuilder'ом (blocks бывают большими), входящие — Gson. */
final class Json {
	private Json() {
	}

	/** Строка в кавычках с экранированием по RFC 8259. */
	static String q(final String s) {
		StringBuilder b = new StringBuilder(s.length() + 2).append('"');
		for (int i = 0; i < s.length(); i++) {
			char c = s.charAt(i);
			switch (c) {
				case '"' -> b.append("\\\"");
				case '\\' -> b.append("\\\\");
				case '\n' -> b.append("\\n");
				case '\r' -> b.append("\\r");
				case '\t' -> b.append("\\t");
				default -> {
					if (c < 0x20) {
						b.append(String.format(Locale.ROOT, "\\u%04x", (int) c));
					} else {
						b.append(c);
					}
				}
			}
		}

		return b.append('"').toString();
	}

	/** Число с 3 знаками без локали (координаты сущностей); NaN и бесконечность — null (в JSON их нет). */
	static String d(final double v) {
		return Double.isFinite(v) ? String.format(Locale.ROOT, "%.3f", v) : "null";
	}

	static int i(final JsonObject m, final String key, final int def) {
		JsonElement e = m.get(key);
		return e == null || e.isJsonNull() ? def : e.getAsInt();
	}

	static double num(final JsonObject m, final String key, final double def) {
		JsonElement e = m.get(key);
		return e == null || e.isJsonNull() ? def : e.getAsDouble();
	}

	static String s(final JsonObject m, final String key, final String def) {
		JsonElement e = m.get(key);
		return e == null || e.isJsonNull() ? def : e.getAsString();
	}

	/** Массив чисел или null, если поля нет; размер не проверяет. */
	static double[] arr(final JsonObject m, final String key) {
		JsonElement e = m.get(key);
		if (e == null || !e.isJsonArray()) {
			return null;
		}

		JsonArray a = e.getAsJsonArray();
		double[] r = new double[a.size()];
		for (int k = 0; k < r.length; k++) {
			r[k] = a.get(k).getAsDouble();
		}

		return r;
	}

	static int[] ints(final JsonObject m, final String key) {
		JsonElement e = m.get(key);
		if (e == null || !e.isJsonArray()) {
			return null;
		}

		JsonArray a = e.getAsJsonArray();
		int[] r = new int[a.size()];
		for (int k = 0; k < r.length; k++) {
			r[k] = a.get(k).getAsInt();
		}

		return r;
	}
}
