"""Тесты поиска путей (w3paths.py) на временных папках: vdf/acf Steam, проверка по exe, JDK по файлу release."""
import os
import tempfile
import unittest
from unittest import mock

import w3paths as P


def touch(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class VdfTest(unittest.TestCase):
    def test_values(self):
        text = '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"C:\\\\Program Files (x86)\\\\Steam"\n\t}\n' \
               '\t"1"\n\t{\n\t\t"path"\t\t"D:\\\\SteamLibrary"\n\t\t"apps"\n\t\t{\n\t\t\t"292030"\t\t"1"\n\t\t}\n\t}\n}\n'
        self.assertEqual(P.vdf_values(text, "path"), [r"C:\Program Files (x86)\Steam", r"D:\SteamLibrary"])
        self.assertEqual(P.vdf_values(text, "nope"), [])


class SteamTest(unittest.TestCase):
    def test_app_dir_and_game_dir(self):
        with tempfile.TemporaryDirectory() as root:
            lib2 = os.path.join(root, "lib2")
            touch(os.path.join(root, "steamapps", "libraryfolders.vdf"),
                  '"libraryfolders" { "1" { "path" "%s" } }' % lib2.replace("\\", "\\\\"))
            touch(os.path.join(lib2, "steamapps", "appmanifest_292030.acf"), '"AppState" { "installdir" "The Witcher 3" }')
            game = os.path.join(lib2, "steamapps", "common", "The Witcher 3")
            os.makedirs(game)
            libs = P.steam_libraries(root)
            self.assertEqual([os.path.normcase(x) for x in libs],
                             [os.path.normcase(os.path.realpath(root)), os.path.normcase(os.path.realpath(lib2))])
            self.assertEqual(os.path.normcase(P.steam_app_dir("292030", libs)), os.path.normcase(os.path.realpath(game)))
            self.assertIsNone(P.steam_app_dir("2684660", libs))
            # папка без witcher3.exe — не игра
            with mock.patch.object(P, "steam_app_dir", return_value=game), mock.patch.object(P, "gog_dir", return_value=None), \
                    mock.patch.dict(os.environ, {"MCW_W3_DIR": ""}):
                self.assertIsNone(P.game_dir())
                touch(os.path.join(game, P.W3_EXE))
                self.assertEqual(P.game_dir(), game)
                self.assertEqual(P.game_store(game), "steam")

    def test_env_override(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"MCW_W3_DIR": d}):
            self.assertIsNone(P.game_dir())                 # переменная задана, но exe нет — не ищем дальше
            touch(os.path.join(d, P.W3_EXE))
            self.assertEqual(P.game_dir(), d)


class JavaTest(unittest.TestCase):
    def jdk(self, base, name, version, modules=None):
        home = os.path.join(base, name)
        rel = f'JAVA_VERSION="{version}"\n' + (f'MODULES="{modules}"\n' if modules is not None else "")
        touch(os.path.join(home, "release"), rel)
        touch(os.path.join(home, "bin", "java.exe"))
        return home

    def test_version_and_choice(self):
        with tempfile.TemporaryDirectory() as d:
            j17 = self.jdk(d, "j17", "17.0.9")
            jre21 = self.jdk(d, "jre21", "21.0.7", "java.base java.desktop")          # без компилятора: Gradle не соберёт
            j25 = self.jdk(d, "j25", "25.0.1")
            j21 = self.jdk(d, "j21", "21.0.7", "java.base jdk.compiler java.desktop")
            self.assertEqual(P.java_version(j17), (17, True))
            self.assertEqual(P.java_version(jre21), (21, False))
            self.assertIsNone(P.java_version(os.path.join(d, "nothing")))
            env = {"MCW_JAVA_HOME": ""}
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(P, "java_candidates", return_value=[j17, jre21, j25, j21]):
                self.assertEqual(P.java_home(), os.path.normpath(j21))        # 21 раньше 25
            with mock.patch.dict(os.environ, env), mock.patch.object(P, "java_candidates", return_value=[j17, j25]):
                self.assertEqual(P.java_home(), os.path.normpath(j25))
            with mock.patch.dict(os.environ, env), mock.patch.object(P, "java_candidates", return_value=[j17, jre21]):
                self.assertIsNone(P.java_home())
            with mock.patch.dict(os.environ, {"MCW_JAVA_HOME": j17}):
                self.assertEqual(P.java_home(), j17)                          # явная переменная — как есть


if __name__ == "__main__":
    unittest.main()
