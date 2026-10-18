"""Collection logic: folder resolution, scan, empty states, selection checks. No Qt."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kde.collection import load_collection, resolve_selection  # noqa: E402


def touch(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class CollectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name) / "home"
        self.home.mkdir()
        # Isolate from the real user: HOME and XDG dirs point into the temp tree.
        env = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config"),
               "XDG_CACHE_HOME": str(self.home / ".cache")}
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def walls(self) -> Path:
        d = self.home / "walls"
        d.mkdir(exist_ok=True)
        return d

    def test_images_sorted_and_shaped_for_qml(self):
        d = self.walls()
        for n in ("b.png", "A.jpg", "c.webp"):
            touch(d / n)
        c = load_collection(str(d))
        self.assertEqual([e["name"] for e in c.entries], ["A.jpg", "b.png", "c.webp"])
        self.assertEqual(c.detail, "")
        for e in c.entries:
            self.assertEqual(set(e), {"name", "path", "url"})
            self.assertTrue(e["url"].startswith("file:///"))

    def test_spaces_and_unicode_survive_the_url(self):
        d = self.walls() / "my pictures é"
        names = ["Закат над морем.png", "a b (1).jpg", "日本語 壁紙.webp", "100% #1.png"]
        for n in names:
            touch(d / n)
        c = load_collection(str(d))
        self.assertEqual(len(c.entries), len(names))
        for e in c.entries:
            self.assertEqual(unquote(urlparse(e["url"]).path), e["path"])
            self.assertTrue(Path(e["path"]).is_file())

    def test_ignores_hidden_subdirs_and_unsupported(self):
        d = self.walls()
        touch(d / "ok.png"); touch(d / ".hidden.png"); touch(d / "notes.txt")
        touch(d / "sub" / "nested.png")
        self.assertEqual([e["name"] for e in load_collection(str(d)).entries], ["ok.png"])

    def test_empty_folder(self):
        c = load_collection(str(self.walls()))
        self.assertEqual(c.entries, [])
        self.assertIn("no supported images", c.detail)
        self.assertTrue(c.hint)

    def test_videos_only_is_empty_but_explained(self):
        d = self.walls()
        touch(d / "a.mp4"); touch(d / "b.webm")
        c = load_collection(str(d))
        self.assertEqual(c.entries, [])
        self.assertIn("2 video files", c.detail)

    def test_missing_folder(self):
        c = load_collection(str(self.home / "nope"))
        self.assertEqual(c.entries, [])
        self.assertIn("folder not found", c.detail)

    def test_path_is_a_file(self):
        f = touch(self.home / "file.txt")
        self.assertIn("not a folder", load_collection(str(f)).detail)

    def test_nothing_configured_and_no_default_folder(self):
        c = load_collection(None)           # empty HOME: no config, no Pictures dir
        self.assertEqual(c.entries, [])
        self.assertIn("no wallpaper folder is configured", c.detail)
        self.assertIn("config.json", c.hint)

    def test_config_json_is_used_and_cli_overrides_it(self):
        cfg_dir = self.home / ".config" / "wallfliper"
        cfg_dir.mkdir(parents=True)
        configured = self.home / "from_config"; touch(configured / "c.png")
        override = self.home / "from_cli"; touch(override / "o.png")
        (cfg_dir / "config.json").write_text(json.dumps({"wallpaper_dir": str(configured)}))
        self.assertEqual([e["name"] for e in load_collection(None).entries], ["c.png"])
        self.assertEqual([e["name"] for e in load_collection(str(override)).entries], ["o.png"])

    def test_tilde_is_expanded_and_not_printed_as_full_home(self):
        touch(self.home / "walls" / "t.png")
        self.assertEqual(len(load_collection("~/walls").entries), 1)
        missing = load_collection("~/absent")
        self.assertIn("~/absent", missing.detail)
        self.assertNotIn(str(self.home), missing.detail)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores permissions")
    def test_unreadable_folder_does_not_raise(self):
        d = self.walls(); touch(d / "a.png")
        d.chmod(0)
        self.addCleanup(d.chmod, 0o755)
        c = load_collection(str(d))
        self.assertEqual(c.entries, [])
        self.assertIn("cannot read folder", c.detail)


class ResolveSelectionTest(unittest.TestCase):
    def test_ok_out_of_range_and_vanished_file(self):
        with tempfile.TemporaryDirectory() as t:
            f = touch(Path(t) / "a.png")
            entries = [{"name": "a.png", "path": str(f), "url": f.as_uri()}]
            self.assertEqual(resolve_selection(entries, 0), (f, ""))
            for bad in (-1, 1, 99):
                self.assertEqual(resolve_selection(entries, bad), (None, "nothing selected"))
            f.unlink()
            self.assertEqual(resolve_selection(entries, 0), (None, "file no longer exists"))
        self.assertEqual(resolve_selection([], 0), (None, "nothing selected"))


if __name__ == "__main__":
    unittest.main()
