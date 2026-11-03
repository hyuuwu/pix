import argparse
import os
from pathlib import Path
import platform
import subprocess
import sys

import psycopg
from dotenv import load_dotenv

COMMANDS = {
    "arch": (["pacman", "-S"], ["pacman", "-R"]),
    "debian": (["apt", "install"], ["apt", "remove"]),
    "fedora": (["dnf", "install"], ["dnf", "remove"]),
    "centos": (["dnf", "install"], ["dnf", "remove"]),
    "redhat": (["dnf", "install"], ["dnf", "remove"]),
    "suse": (["zypper", "install"], ["zypper", "remove"]),
    "alpine": (["apk", "add"], ["apk", "del"]),
    "void": (["xbps-install", "-S"], ["xbps-remove"]),
}


def check_distro(path="/etc/os-release"):
    info = {}
    for line in Path(path).read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            info[key] = value.strip('"\'')
    aliases = {"ubuntu": "debian", "rhel": "redhat", "opensuse-tumbleweed": "suse",
               "opensuse-leap": "suse", "opensuse": "suse", "sles": "suse"}
    for name in [info.get("ID", ""), *info.get("ID_LIKE", "").split()]:
        name = aliases.get(name, name)
        if name in COMMANDS:
            return name
    raise ValueError("Unsupported distribution")


def connect():
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    load_dotenv(base / ".env")
    load_dotenv(Path.cwd() / ".env")
    url = os.getenv("DATABASE_URL")
    if not url:
        raise ValueError("DATABASE_URL is not set")
    return psycopg.connect(url, sslmode="require", connect_timeout=15)


def resolve(conn, name, distro):
    machine = platform.machine().lower()
    arch = {"x86_64": ["x86_64", "amd64"], "amd64": ["x86_64", "amd64"],
            "aarch64": ["aarch64", "arm64"], "arm64": ["aarch64", "arm64"]}.get(machine, [machine])
    rows = conn.execute("""
        SELECT p.name, min(c.priority)
        FROM (
            SELECT %s::text AS name, 0 AS priority
            UNION
            SELECT target.name, 1 FROM pix.aliases source
            JOIN pix.aliases target ON target.project = source.project
            WHERE source.name = %s AND target.distro = %s
        ) c
        JOIN pix.packages p ON p.name = c.name
        JOIN pix.repositories r ON r.id = p.repository_id
        WHERE r.distro = %s AND p.arch = ANY(%s)
        GROUP BY p.name ORDER BY min(c.priority), p.name
        """, (name, name, distro, distro, arch + ["any", "all", "noarch"])).fetchall()
    if not rows:
        raise ValueError(f"No package mapping for {name} on {distro}")
    if rows[0][1] != 0 and len(rows) > 1:
        raise ValueError(f"Ambiguous package: {', '.join(row[0] for row in rows)}")
    return rows[0][0]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pix", add_help=False, allow_abbrev=False)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("-i", metavar="PACKAGE")
    action.add_argument("-r", metavar="PACKAGE")
    args = parser.parse_args(argv)
    name = args.i or args.r
    if not name or name.startswith("-") or any(c.isspace() for c in name):
        parser.error("Invalid package name")
    try:
        distro = check_distro()
        with connect() as conn:
            package = resolve(conn, name, distro)
        command = COMMANDS[distro][0 if args.i else 1]
        if os.geteuid() != 0:
            command = ["sudo", *command]
        print(f"{name} -> {package} ({distro})", flush=True)
        return subprocess.run([*command, package], check=False).returncode
    except ValueError as exc:
        print(f"pix: {exc}", file=sys.stderr)
    except psycopg.Error:
        print("pix: Database unavailable", file=sys.stderr)
    except OSError:
        print("pix: Cannot read system information or run the package manager", file=sys.stderr)
    except KeyboardInterrupt:
        return 130
    return 1


if __name__ == "__main__":
    sys.exit(main())
