"""repo reader."""
from dataclasses import dataclass
import bz2
import gzip
import io
import lzma
import plistlib
import re
import tarfile
import time
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


@dataclass(frozen=True)
class Source:
    distro: str
    release: str
    repo: str
    arch: str
    kind: str
    url: str


@dataclass(frozen=True)
class Package:
    name: str
    version: str
    arch: str
    summary: str = ""
    homepage: str = ""
    origin: str = ""


def default_sources():
    sources = []
    for repo in ("core", "extra", "multilib"):
        sources.append(Source("arch", "rolling", repo, "x86_64", "arch",
                              f"https://geo.mirror.pkgbuild.com/{repo}/os/x86_64/{repo}.db"))
    for repo in ("main", "contrib", "non-free", "non-free-firmware"):
        sources.append(Source("debian", "trixie", repo, "amd64", "debian",
                              f"https://deb.debian.org/debian/dists/trixie/{repo}/binary-amd64/Packages.xz"))
    for repo in ("main", "community"):
        sources.append(Source("alpine", "latest-stable", repo, "x86_64", "alpine",
                              f"https://dl-cdn.alpinelinux.org/alpine/latest-stable/{repo}/x86_64/APKINDEX.tar.gz"))
    for repo, path in (("main", ""), ("nonfree", "nonfree/"), ("multilib", "multilib/"),
                       ("multilib-nonfree", "multilib/nonfree/")):
        sources.append(Source("void", "rolling", repo, "x86_64", "void",
                              f"https://repo-default.voidlinux.org/current/{path}x86_64-repodata"))
    for repo in ("BaseOS", "AppStream", "CRB"):
        sources.append(Source("centos", "10-stream", repo, "x86_64", "rpm",
                              f"https://mirror.stream.centos.org/10-stream/{repo}/x86_64/os/"))
    for repo in ("baseos", "appstream", "codeready-builder"):
        sources.append(Source("redhat", "ubi9", repo, "x86_64", "rpm",
                              f"https://cdn-ubi.redhat.com/content/public/ubi/dist/ubi9/9/x86_64/{repo}/os/"))
    for repo in ("oss", "non-oss"):
        sources.append(Source("suse", "tumbleweed", repo, "x86_64", "rpm",
                              f"https://cdn.opensuse.org/tumbleweed/repo/{repo}/"))
    sources.append(Source("fedora", "44", "Everything", "x86_64", "rpm",
                          "https://dl.fedoraproject.org/pub/fedora/linux/releases/44/Everything/x86_64/os/"))
    sources.append(Source("fedora", "44", "updates", "x86_64", "rpm",
                          "https://dl.fedoraproject.org/pub/fedora/linux/updates/44/Everything/x86_64/"))
    return sources


def fetch(url):
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={"User-Agent": "pix-catalog/0.1"}), timeout=120) as response:
                return response.read()
        except OSError:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def unpack(data):
    if data.startswith(b"\x1f\x8b"):
        return gzip.decompress(data)
    if data.startswith(b"\xfd7zXZ"):
        return lzma.decompress(data)
    if data.startswith(b"BZh"):
        return bz2.decompress(data)
    if data.startswith(b"\x28\xb5\x2f\xfd"):
        import zstandard
        with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data)) as reader:
            return reader.read()
    return data


def normalize_homepage(value):
    try:
        parts = urlsplit(value.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return ""
        host = parts.hostname.lower().removeprefix("www.")
        path = parts.path.rstrip("/").removesuffix(".git")
        # A shared hosting service's root is not a project identity.
        if not path and host in {"github.com", "gitlab.com", "sourceforge.net"}:
            return ""
        return urlunsplit(("https", host, path, "", ""))
    except ValueError:
        return ""


def stanzas(text):
    for block in re.split(r"\n\s*\n", text.strip()):
        fields = {}
        for line in block.splitlines():
            if line and not line[0].isspace() and ":" in line:
                key, value = line.split(":", 1)
                fields[key] = value.strip()
        if fields:
            yield fields


def parse_debian(data):
    for p in stanzas(unpack(data).decode("utf-8", "replace")):
        yield Package(p["Package"], p["Version"], p["Architecture"], p.get("Description", ""),
                      p.get("Homepage", ""), p.get("Source", p["Package"]).split()[0])


def parse_arch(data):
    with tarfile.open(fileobj=io.BytesIO(unpack(data)), mode="r:") as archive:
        for member in archive:
            if member.isfile() and member.name.endswith("/desc"):
                fields = {}
                for block in archive.extractfile(member).read().decode("utf-8", "replace").split("\n\n"):
                    lines = block.strip().splitlines()
                    if len(lines) > 1:
                        fields[lines[0].strip("%")] = lines[1]
                yield Package(fields["NAME"], fields["VERSION"], fields["ARCH"],
                              fields.get("DESC", ""), fields.get("URL", ""), fields.get("BASE", ""))


def parse_alpine(data):
    with tarfile.open(fileobj=io.BytesIO(unpack(data)), mode="r:") as archive:
        for member in archive:
            if member.isfile() and member.name == "APKINDEX":
                for p in stanzas(archive.extractfile(member).read().decode("utf-8", "replace")):
                    yield Package(p["P"], p["V"], p["A"], p.get("T", ""), p.get("U", ""), p.get("o", ""))


def parse_void(data):
    with tarfile.open(fileobj=io.BytesIO(unpack(data)), mode="r:") as archive:
        for member in archive:
            if member.isfile() and member.name == "index.plist":
                for name, p in plistlib.loads(archive.extractfile(member).read()).items():
                    yield Package(name, p["pkgver"][len(name) + 1:], p["architecture"],
                                  p.get("short_desc", ""), p.get("homepage", ""), p.get("sourcepkg", name))


def parse_rpm(data):
    ns = {"c": "http://linux.duke.edu/metadata/common", "r": "http://linux.duke.edu/metadata/rpm"}
    stream = io.BytesIO(unpack(data))
    for _, elem in ET.iterparse(stream, events=("end",)):
        if elem.tag != "{" + ns["c"] + "}package":
            continue
        version = elem.find("c:version", ns).attrib
        v = f"{version.get('ver', '')}-{version.get('rel', '')}"
        if version.get("epoch", "0") != "0":
            v = version["epoch"] + ":" + v
        yield Package(elem.findtext("c:name", namespaces=ns), v,
                      elem.findtext("c:arch", namespaces=ns),
                      elem.findtext("c:summary", "", ns), elem.findtext("c:url", "", ns),
                      elem.findtext("c:format/r:sourcerpm", "", ns))
        elem.clear()


def read_source(source):
    if source.kind == "rpm":
        root = ET.fromstring(fetch(urljoin(source.url, "repodata/repomd.xml")))
        ns = {"r": "http://linux.duke.edu/metadata/repo"}
        location = root.find("r:data[@type='primary']/r:location", ns)
        if location is None:
            raise ValueError("Repository has no primary package metadata")
        url = urljoin(source.url, location.attrib["href"])
        if urlsplit(url).scheme != "https":
            raise ValueError("Package metadata must use HTTPS")
        yield from parse_rpm(fetch(url))
    else:
        parser = {"arch": parse_arch, "debian": parse_debian,
                  "alpine": parse_alpine, "void": parse_void}[source.kind]
        yield from parser(fetch(source.url))
