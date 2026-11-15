#!/usr/bin/python3
"""Build source archives and a small self-extracting GUI installer."""
from pathlib import Path
import argparse
import hashlib
import io
import os
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.1.0'
EXCLUDED = {'.git', '__pycache__', 'dist', '.pytest_cache', '.ruff_cache'}


def source_files():
    return sorted(p for p in ROOT.rglob('*') if p.is_file() and not any(part in EXCLUDED for part in p.relative_to(ROOT).parts) and p.suffix != '.pyc')


def archive(files, prefix):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as tar:
        for path in files:
            info = tar.gettarinfo(str(path), arcname=prefix + '/' + path.relative_to(ROOT).as_posix())
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            info.mtime = 1794693600
            with path.open('rb') as source:
                tar.addfile(info, source)
    return stream.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    options = parser.parse_args()
    out = options.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    files = source_files()
    prefix = 'remote-plus-plus-' + VERSION
    source = out / (prefix + '-source.zip')
    with zipfile.ZipFile(source, 'w', compression=zipfile.ZIP_DEFLATED) as zip:
        for path in files:
            zip.write(path, prefix + '/' + path.relative_to(ROOT).as_posix())
    (out / (prefix + '-linux-x86_64.tar.gz')).write_bytes(archive(files, prefix))
    runtime = [p for p in files if p.relative_to(ROOT).parts[0] not in ('tests', '.github', 'docs')]
    payload = archive(runtime, 'remote-plus-plus')
    installer = out / (prefix + '-installer.run')
    header = '''#!/bin/sh
set -eu
if [ "$(id -u)" = 0 ]; then
    printf '%s\\n' 'Запустите установщик без sudo.' >&2
    exit 1
fi
if [ "${1:-}" = "--help" ]; then
    printf '%s\\n' 'Remote++: запустите этот файл для установки. --extract DIR только распаковывает архив.'
    exit 0
fi
line=$(awk '/^__REMOTEPLUS_PAYLOAD__$/{print NR+1;exit}' "$0")
if [ "${1:-}" = "--extract" ]; then
    mkdir -p -- "$2"
    tail -n +"$line" "$0" | tar -xz --no-same-owner -C "$2"
    exit 0
fi
temporary=$(mktemp -d -t remote-plus-plus.XXXXXXXX)
trap 'rm -rf -- "$temporary"' EXIT HUP INT TERM
tail -n +"$line" "$0" | tar -xz --no-same-owner -C "$temporary"
python3 "$temporary/remote-plus-plus/install.py" "$@"
exit 0
__REMOTEPLUS_PAYLOAD__
'''
    installer.write_bytes(header.encode() + payload)
    installer.chmod(0o755)
    outputs = [source, out / (prefix + '-linux-x86_64.tar.gz'), installer]
    sums = '\n'.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name for p in outputs) + '\n'
    (out / 'SHA256SUMS').write_text(sums)
    for path in outputs:
        print(path)


if __name__ == '__main__':
    main()
