import gzip
import io
import lzma
import plistlib
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from pix.sources import (normalize_homepage, parse_alpine, parse_arch, parse_debian,
                         parse_rpm, parse_void, read_source, Source, unpack)


def archive(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


class Readers(unittest.TestCase):
    def test_debian_continuations_and_source(self):
        data = b"Package: fd-find\nVersion: 10.2-1\nArchitecture: amd64\nDescription: Find files\n extended description\nHomepage: https://github.com/sharkdp/fd\nSource: rust-fd-find (10.2-1)\n\n"
        p, = parse_debian(lzma.compress(data))
        self.assertEqual((p.name, p.summary, p.origin), ("fd-find", "Find files", "rust-fd-find"))

    def test_arch(self):
        p, = parse_arch(archive({"fd-1/desc": b"%NAME%\nfd\n\n%VERSION%\n1-1\n\n%ARCH%\nx86_64\n\n%BASE%\nfd\n\n%URL%\nhttps://github.com/sharkdp/fd\n"}))
        self.assertEqual((p.name, p.version, p.origin), ("fd", "1-1", "fd"))

    def test_alpine_signed_archive(self):
        p, = parse_alpine(archive({".SIGN.signature": b"sig", "APKINDEX": b"P:fd\nV:1-r0\nA:x86_64\nT:Find files\nU:https://github.com/sharkdp/fd\no:fd\n\n"}))
        self.assertEqual((p.name, p.summary), ("fd", "Find files"))

    def test_void(self):
        data = plistlib.dumps({"fd": {"pkgver": "fd-1_1", "architecture": "x86_64", "short_desc": "Find files"}})
        p, = parse_void(archive({"index.plist": data}))
        self.assertEqual((p.name, p.version), ("fd", "1_1"))

    def test_rpm_namespaces_and_epoch(self):
        xml = b'''<metadata xmlns="http://linux.duke.edu/metadata/common" xmlns:rpm="http://linux.duke.edu/metadata/rpm"><package type="rpm"><name>fd-find</name><arch>x86_64</arch><version epoch="1" ver="10" rel="2"/><summary>Find files</summary><url>https://github.com/sharkdp/fd</url><format><rpm:sourcerpm>fd-find-10-2.src.rpm</rpm:sourcerpm></format></package></metadata>'''
        p, = parse_rpm(gzip.compress(xml))
        self.assertEqual((p.name, p.version, p.origin), ("fd-find", "1:10-2", "fd-find-10-2.src.rpm"))

    def test_rpm_primary_resolution(self):
        source = Source("fedora", "44", "main", "x86_64", "rpm", "https://example.org/os/")
        metadata = b'<repomd xmlns="http://linux.duke.edu/metadata/repo"><data type="primary"><location href="repodata/primary.xml.gz"/></data></repomd>'
        with patch("pix.sources.fetch", side_effect=[metadata, b'<metadata/>']) as fetch:
            self.assertEqual(list(read_source(source)), [])
            self.assertEqual(fetch.call_args.args[0], "https://example.org/os/repodata/primary.xml.gz")

    def test_zstd_unknown_content_size(self):
        import zstandard
        self.assertEqual(unpack(zstandard.ZstdCompressor(write_content_size=False).compress(b"metadata")), b"metadata")

    def test_homepage_identity_keeps_project_path(self):
        self.assertEqual(normalize_homepage("http://www.example.org/project/?ref=repo"), "https://example.org/project")
        self.assertNotEqual(normalize_homepage("https://github.com/a/one"), normalize_homepage("https://github.com/a/two"))
        self.assertEqual(normalize_homepage("https://github.com/"), "")
        self.assertEqual(normalize_homepage("not a URL"), "")


class CLI(unittest.TestCase):
    def test_distro_identity_before_ancestry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "os-release"
            for identity, ancestry, expected in [("centos", "rhel fedora", "centos"),
                                                  ("rhel", "fedora", "redhat"),
                                                  ("ubuntu", "debian", "debian")]:
                path.write_text(f'ID={identity}\nID_LIKE="{ancestry}"\n')
                self.assertEqual(main.check_distro(path), expected)

    def test_install_uses_resolved_package(self):
        with patch("main.check_distro", return_value="arch"), patch("main.connect"), \
                patch("main.resolve", return_value="libxrandr"), \
                patch("main.os.geteuid", return_value=1000), patch("main.subprocess.run") as run:
            run.return_value.returncode = 0
            self.assertEqual(main.main(["-i", "libxrandr2"]), 0)
            run.assert_called_once_with(["sudo", "pacman", "-S", "libxrandr"], check=False)

    def test_remove_resolves_name_and_propagates_exit_status(self):
        with patch("main.check_distro", return_value="arch"), patch("main.connect"), \
                patch("main.resolve", return_value="libxrandr"), \
                patch("main.os.geteuid", return_value=0), patch("main.subprocess.run") as run:
            run.return_value.returncode = 3
            self.assertEqual(main.main(["-r", "libxrandr2"]), 3)
            run.assert_called_once_with(["pacman", "-R", "libxrandr"], check=False)

    def test_missing_mapping_never_runs_package_manager(self):
        with patch("main.check_distro", return_value="arch"), patch("main.connect"), \
                patch("main.resolve", side_effect=ValueError("No mapping")), \
                patch("main.subprocess.run") as run:
            self.assertEqual(main.main(["-i", "unknown"]), 1)
            run.assert_not_called()

    def test_only_install_and_remove(self):
        for args in (["--sync"], ["--status"], ["--install", "fd"], ["-i", "fd", "-r", "fd"]):
            with self.assertRaises(SystemExit):
                main.main(args)

    def test_resolution_checks_native_architecture(self):
        from unittest.mock import MagicMock
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [("libxrandr", 1)]
        with patch("main.platform.machine", return_value="x86_64"):
            self.assertEqual(main.resolve(conn, "libxrandr2", "arch"), "libxrandr")
        self.assertEqual(conn.execute.call_args.args[1],
                         ("libxrandr2", "libxrandr2", "arch", "arch", ["x86_64", "amd64", "any", "all", "noarch"]))

    def test_resolution_rejects_ambiguity(self):
        from unittest.mock import MagicMock
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [("one", 1), ("two", 1)]
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            main.resolve(conn, "name", "arch")


if __name__ == "__main__":
    unittest.main()
