"""Copy an offline legacy workspace into a new private product data directory.

Run with both servers stopped. Never overwrites a destination or modifies source.
"""
import argparse
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path


def migrate(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists():
        raise ValueError("Destination must not exist; preserve existing user data.")
    if destination == source or source in destination.parents:
        raise ValueError("Destination must be outside the legacy workspace.")
    if not (source / '.local/reviews.sqlite3').is_file():
        raise ValueError("Source has no saved review database.")
    destination.mkdir(parents=True)
    checks = {}
    for name in ('knowledge', 'sources', 'protocols', 'criteria'):
        origin = source / name
        if origin.exists():
            shutil.copytree(origin, destination / 'workspace' / name)
    for name in ('living-stories', 'uploads'):
        origin = source / '.local' / name
        if origin.exists():
            shutil.copytree(origin, destination / name)
    for original in sorted((source / '.local').glob('*.sqlite3')):
        copied = destination / original.name
        with sqlite3.connect(original.as_uri() + '?mode=ro', uri=True) as old, sqlite3.connect(copied) as new:
            old.backup(new)
            if new.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Copied database integrity check failed: ' + original.name)
            tables = old.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
            counts = {}
            for (table,) in tables:
                quoted = '"' + table.replace('"', '""') + '"'
                before = old.execute('SELECT COUNT(*) FROM ' + quoted).fetchone()[0]
                after = new.execute('SELECT COUNT(*) FROM ' + quoted).fetchone()[0]
                if before != after:
                    raise ValueError('Database row count changed during migration')
                counts[table] = after
            checks[original.name] = counts
    files = 0
    for base, target in [(source / n, destination / 'workspace' / n) for n in ('knowledge', 'sources', 'protocols', 'criteria')] + [(source / '.local' / n, destination / n) for n in ('living-stories', 'uploads')]:
        if not base.exists():
            continue
        for original in base.rglob('*'):
            if original.is_file():
                copied = target / original.relative_to(base)
                if hashlib.sha256(original.read_bytes()).digest() != hashlib.sha256(copied.read_bytes()).digest():
                    raise ValueError('Copied source checksum differs')
                files += 1
    report = {'verified_files': files, 'databases': checks, 'source_retained': True}
    (destination / 'migration-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, args.destination), indent=2))
