from pix.database import connect, initialize, save_snapshot
from pix.sources import default_sources, read_source
from pix.mapping import rebuild


def main():
    failures = 0
    with connect() as conn:
        initialize(conn)
        for source in default_sources():
            label = f"{source.distro}/{source.repo}"
            try:
                packages = list(read_source(source))
                count = save_snapshot(conn, source, packages)
                print(f"{label}: {count}", flush=True)
            except Exception as exc:
                failures += 1
                print(f"{label}: {type(exc).__name__}", flush=True)
        print(rebuild(conn), flush=True)
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
