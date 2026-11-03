"""Neon/PostgreSQL catalog, atomic repository snapshots, and match evidence."""
import os
from pathlib import Path
import sys

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

from .sources import normalize_homepage


SCHEMA = """
CREATE SCHEMA IF NOT EXISTS pix;
CREATE TABLE IF NOT EXISTS pix.repositories (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    distro text NOT NULL, release text NOT NULL, repo text NOT NULL,
    arch text NOT NULL, url text NOT NULL,
    synced_at timestamptz, package_count integer NOT NULL DEFAULT 0,
    UNIQUE(distro, release, repo, arch)
);
CREATE TABLE IF NOT EXISTS pix.packages (
    repository_id bigint NOT NULL REFERENCES pix.repositories(id),
    name text NOT NULL, version text NOT NULL, arch text NOT NULL,
    summary text NOT NULL, homepage text NOT NULL, origin text NOT NULL,
    PRIMARY KEY(repository_id, name, version, arch)
);
CREATE INDEX IF NOT EXISTS packages_name_idx ON pix.packages(name);
CREATE TABLE IF NOT EXISTS pix.aliases (
    distro text NOT NULL, name text NOT NULL, project text NOT NULL,
    evidence text NOT NULL, PRIMARY KEY(distro, name)
);
CREATE INDEX IF NOT EXISTS aliases_name_idx ON pix.aliases(name);
CREATE INDEX IF NOT EXISTS aliases_project_idx ON pix.aliases(project);
CREATE OR REPLACE VIEW pix.equivalents AS
SELECT DISTINCT a.name AS source_package, ra.distro AS source_distro,
    ra.release AS source_release, ra.repo AS source_repo, a.arch AS source_arch,
    b.name AS target_package, rb.distro AS target_distro,
    rb.release AS target_release, rb.repo AS target_repo, b.arch AS target_arch,
    CASE WHEN aa.project = ab.project AND aa.project LIKE 'auto:%' THEN 'metadata'
         WHEN aa.project = ab.project THEN 'curated'
         WHEN a.homepage <> '' AND a.homepage = b.homepage THEN 'name_and_homepage'
         ELSE 'name_only' END AS evidence,
    COALESCE(aa.project = ab.project AND aa.project NOT LIKE 'auto:%', false) AS verified,
    CASE WHEN aa.project = ab.project AND aa.project LIKE 'auto:%' THEN 0.9
         WHEN aa.project = ab.project THEN 1.0
         WHEN a.homepage <> '' AND a.homepage = b.homepage THEN 0.85
         ELSE 0.5 END AS confidence
FROM pix.packages a
JOIN pix.repositories ra ON ra.id = a.repository_id
LEFT JOIN pix.aliases aa ON aa.distro = ra.distro AND aa.name = a.name
JOIN pix.packages b ON b.name = a.name OR b.name IN (
    SELECT name FROM pix.aliases WHERE project = aa.project
)
JOIN pix.repositories rb ON rb.id = b.repository_id AND rb.distro <> ra.distro
LEFT JOIN pix.aliases ab ON ab.distro = rb.distro AND ab.name = b.name
WHERE a.name = b.name OR aa.project = ab.project;
"""

# Deliberately small: binaries split out from a source package are not interchangeable.
# Automatic matches remain candidates until somebody reviews their package contents.
ALIASES = {
    "libxrandr": ("https://gitlab.freedesktop.org/xorg/lib/libxrandr", {
        "*": "xlibrandr2",
        "arch": "libxrandr", "debian": "libxrandr2", "fedora": "libXrandr",
        "centos": "libXrandr", "redhat": "libXrandr", "alpine": "libxrandr",
        "void": "libXrandr", "suse": "libXrandr2"}),
    "fd": ("https://github.com/sharkdp/fd", {
        "arch": "fd", "debian": "fd-find", "fedora": "fd-find",
        "alpine": "fd", "void": "fd", "suse": "fd"}),
    "bat": ("https://github.com/sharkdp/bat", {
        "arch": "bat", "debian": "bat", "fedora": "bat",
        "alpine": "bat", "void": "bat", "suse": "bat"}),
    "ripgrep": ("https://github.com/BurntSushi/ripgrep", {
        "arch": "ripgrep", "debian": "ripgrep", "fedora": "ripgrep",
        "alpine": "ripgrep", "void": "ripgrep", "suse": "ripgrep"}),
}


def connect():
    # A frozen executable must not depend on PyInstaller's temporary extraction path.
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    load_dotenv(base / ".env")
    load_dotenv(Path.cwd() / ".env")
    url = os.getenv("DATABASE_URL")
    if not url:
        raise ValueError("Set DATABASE_URL in the environment or .env beside pix")
    return psycopg.connect(url, sslmode="require", connect_timeout=20, row_factory=dict_row)


def initialize(conn):
    with conn.transaction():
        conn.execute(SCHEMA)
        for project, (evidence, names) in ALIASES.items():
            for distro, name in names.items():
                conn.execute("""INSERT INTO pix.aliases VALUES (%s, %s, %s, %s)
                    ON CONFLICT (distro, name) DO UPDATE
                    SET project = EXCLUDED.project, evidence = EXCLUDED.evidence""",
                             (distro, name, project, evidence))


def save_snapshot(conn, source, packages):
    # Stage the whole index before touching the last successful snapshot.
    with conn.transaction():
        row = conn.execute("""INSERT INTO pix.repositories (distro, release, repo, arch, url)
            VALUES (%s, %s, %s, %s, %s) ON CONFLICT (distro, release, repo, arch)
            DO UPDATE SET url = EXCLUDED.url RETURNING id""",
                           (source.distro, source.release, source.repo, source.arch, source.url)).fetchone()
        repo_id = row["id"]
        conn.execute("""CREATE TEMP TABLE pix_stage
            (LIKE pix.packages INCLUDING DEFAULTS) ON COMMIT DROP""")
        with conn.cursor().copy("COPY pix_stage FROM STDIN") as copy:
            count = 0
            for p in packages:
                if not p.name or not p.version or not p.arch:
                    raise ValueError("Invalid package record")
                copy.write_row((repo_id, p.name, p.version, p.arch, p.summary,
                                normalize_homepage(p.homepage), p.origin))
                count += 1
        if not count:
            raise ValueError("Empty index; previous snapshot was preserved")
        conn.execute("DELETE FROM pix.packages WHERE repository_id = %s", (repo_id,))
        conn.execute("INSERT INTO pix.packages SELECT DISTINCT * FROM pix_stage ON CONFLICT DO NOTHING")
        count = conn.execute("SELECT count(*) AS n FROM pix.packages WHERE repository_id = %s", (repo_id,)).fetchone()["n"]
        conn.execute("UPDATE pix.repositories SET synced_at=now(), package_count=%s WHERE id=%s", (count, repo_id))
    return count


def equivalents(conn, name, source, target, source_release=None, target_release=None):
    return conn.execute("""SELECT DISTINCT target_package, target_distro, target_release,
        target_repo, target_arch, evidence, verified, confidence::float8 AS confidence FROM pix.equivalents
        WHERE source_package=%s AND source_distro=%s AND target_distro=%s
        AND (%s::text IS NULL OR source_release=%s)
        AND (%s::text IS NULL OR target_release=%s)
        ORDER BY confidence DESC, target_package, target_release, target_repo, target_arch""",
                        (name, source, target, source_release, source_release,
                         target_release, target_release)).fetchall()
