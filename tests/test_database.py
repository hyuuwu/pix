"""Opt-in checks against the configured catalog after a real import."""
import os
import unittest

from pix.database import connect, equivalents, save_snapshot
from pix.sources import Package, default_sources


@unittest.skipUnless(os.getenv("PIX_LIVE_TEST") == "1", "set PIX_LIVE_TEST=1 after importing the catalog")
class NeonIntegration(unittest.TestCase):
    def test_curated_cross_name_match(self):
        with connect() as conn:
            rows = equivalents(conn, "fd", "arch", "debian")
            self.assertTrue(any(r["target_package"] == "fd-find" and r["verified"] for r in rows))

    def test_failed_imports_preserve_snapshot(self):
        source = default_sources()[0]
        with connect() as conn:
            before = conn.execute("SELECT package_count, synced_at FROM pix.repositories WHERE distro='arch' AND repo='core'").fetchone()
            self.assertIsNotNone(before)
            conn.commit()
            with self.assertRaises(ValueError):
                save_snapshot(conn, source, [])

            def broken_reader():
                yield Package("pix-test-incomplete", "1", "x86_64")
                raise ValueError("Truncated metadata")

            with self.assertRaises(ValueError):
                save_snapshot(conn, source, broken_reader())
            after = conn.execute("SELECT package_count, synced_at FROM pix.repositories WHERE distro='arch' AND repo='core'").fetchone()
            self.assertEqual(before, after)
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM pix.packages WHERE name='pix-test-incomplete'").fetchone()["n"], 0)


if __name__ == "__main__":
    unittest.main()
