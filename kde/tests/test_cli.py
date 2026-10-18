"""Command line: --dry-run exists, is off by default and is documented. No Qt."""
from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kde.main import build_parser  # noqa: E402


class Cli(unittest.TestCase):
    def test_dry_run_is_off_by_default(self):
        self.assertFalse(build_parser().parse_args([]).dry_run)

    def test_dry_run_flag(self):
        args = build_parser().parse_args(["--dry-run", "--dir", "/x"])
        self.assertTrue(args.dry_run)
        self.assertEqual(args.dir, "/x")

    def test_help_documents_that_dry_run_never_changes_the_wallpaper(self):
        buf = io.StringIO()
        with redirect_stdout(buf), self.assertRaises(SystemExit):
            build_parser().parse_args(["--help"])
        text = " ".join(buf.getvalue().split())  # argparse re-wraps lines
        self.assertIn("--dry-run", text)
        self.assertIn("never changes the wallpaper", text)


if __name__ == "__main__":
    unittest.main()
