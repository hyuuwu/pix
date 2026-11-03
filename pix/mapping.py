import hashlib
import re
from collections import defaultdict
from urllib.parse import urlsplit


def canonical(name):
    name = re.sub(r"^python3-", "python-", name.lower())
    return re.sub(r"[-_]", "", name)


def identity(name):
    for suffixes, role in ((('dev', 'devel'), 'development'),
                           (('doc', 'docs'), 'documentation'),
                           (('dbg', 'dbgsym', 'debuginfo'), 'debug')):
        for suffix in suffixes:
            if name.lower().endswith('-' + suffix):
                return canonical(name[:-len(suffix)-1]), role
    return canonical(name), 'runtime'


def source_name(origin):
    origin = origin.removesuffix('.src.rpm').removesuffix('.nosrc.rpm')
    return canonical(re.sub(r'-\d[^/]*$', '', origin))


def project_url(url):
    parts = urlsplit(url)
    host = parts.hostname or ''
    path = parts.path.rstrip('/')
    if not host:
        return ''
    if host in {'github.com', 'gitlab.com', 'bitbucket.org'} and len(path.strip('/').split('/')) < 2:
        return ''
    if not path and host in {'x.org', 'xorg.freedesktop.org', 'freedesktop.org',
                             'gnu.org', 'gnome.org', 'kde.org', 'kernel.org', 'sourceforge.net',
                             'python.org', 'pypi.org', 'cran.r-project.org'}:
        return ''
    return url


def build_aliases(rows, curated=()):
    parents, buckets = {}, {}

    def find(node):
        parents.setdefault(node, node)
        while parents[node] != node:
            parents[node] = parents[parents[node]]
            node = parents[node]
        return node

    for row in rows:
        node = (row['distro'], row['name'])
        if node in curated:
            continue
        stem, role = identity(row['name'])
        source, url = source_name(row['origin']), project_url(row['homepage'])
        for kind, project in (('source', source), ('url', url)):
            if not project:
                continue
            key = kind, project, stem, role
            other = buckets.setdefault(key, node)
            parents[find(node)] = find(other)

    groups = defaultdict(set)
    for node in parents:
        groups[find(node)].add(node)
    aliases, rejected = [], 0
    for members in groups.values():
        distros = [distro for distro, _ in members]
        if len(set(distros)) < 2 or len({name for _, name in members}) < 2:
            continue
        if len(distros) != len(set(distros)):
            rejected += 1
            continue
        digest = hashlib.sha256(repr(sorted(members)).encode()).hexdigest()[:24]
        for distro, name in sorted(members):
            aliases.append((distro, name, 'auto:' + digest, 'metadata: project and package role'))
    return aliases, rejected


def rebuild(conn):
    with conn.transaction():
        curated = {(r['distro'], r['name']) for r in conn.execute(
            "SELECT distro, name FROM pix.aliases WHERE project NOT LIKE 'auto:%'")}
        rows = conn.execute("""SELECT DISTINCT r.distro, p.name, p.origin, p.homepage
            FROM pix.packages p JOIN pix.repositories r ON r.id=p.repository_id""").fetchall()
        aliases, rejected = build_aliases(rows, curated)
        conn.execute("CREATE TEMP TABLE pix_alias_stage (LIKE pix.aliases) ON COMMIT DROP")
        with conn.cursor().copy('COPY pix_alias_stage FROM STDIN') as copy:
            for alias in aliases:
                copy.write_row(alias)
        conn.execute("DELETE FROM pix.aliases WHERE project LIKE 'auto:%'")
        conn.execute("INSERT INTO pix.aliases SELECT * FROM pix_alias_stage ON CONFLICT DO NOTHING")
    return {'packages_scanned': len({(r['distro'], r['name']) for r in rows}),
            'mapped_names': len(aliases), 'groups': len({r[2] for r in aliases}),
            'ambiguous_groups_skipped': rejected}


if __name__ == '__main__':
    from .database import connect, initialize
    with connect() as conn:
        initialize(conn)
        print(rebuild(conn))
