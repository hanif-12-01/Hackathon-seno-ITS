import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import warnings


def backup(source, destination):
    source, destination = Path(source).absolute(), Path(destination).absolute()
    for path in (source, destination):
        if any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction())
               for p in (path, *path.parents)):
            raise ValueError('Symlinks/junctions are not permitted in database paths')
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if not source.is_file() or source == destination:
        raise ValueError('Source must be an existing file; destination must be different')
    if any(Path(str(destination) + suffix).exists() or Path(str(destination) + suffix).is_symlink()
           for suffix in ('', '-wal', '-shm', '-journal')):
        raise FileExistsError('Destination or SQLite sidecar exists; choose a new filename')
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as origin:
        origin.execute('PRAGMA schema_version').fetchone()
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        try:
            destination.chmod(0o600)
        except OSError as exc:
            warnings.warn(f'Cannot restrict backup permissions: {exc}; configure owner-only access')
        if os.name == 'nt':
            warnings.warn('Windows chmod does not restrict ACL access; configure owner-only directory/file ACLs')
        with closing(sqlite3.connect(destination.as_uri() + '?mode=rw', uri=True)) as target:
            origin.backup(target)
            if target.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError('Backup integrity_check failed; destination retained, not a verified backup')
            if target.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('Backup foreign_key_check failed; destination retained, not a verified backup')
    return destination


def main():
    parser = argparse.ArgumentParser(description='SQLite online backup to a new private file; failures retain destination for investigation')
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    try:
        path = backup(args.source, args.destination)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f'Backup failed: {exc}. No files removed; do not use an incomplete destination.', file=sys.stderr)
        return 1
    print(f'Verified backup: {path}; integrity_check=ok; foreign_key_check=clean')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
