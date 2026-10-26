"""Restore compressed experiment outputs without changing their recorded bytes."""
import gzip
import hashlib
import json
import os
import tempfile
from pathlib import Path

from release_support import local_path, require


def file_sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def restore_results(root):
    root = Path(root).resolve()
    manifest = json.loads((root / 'results/compressed_assets.json').read_text(encoding='utf-8'))
    restored = 0
    for item in manifest['assets']:
        archive = local_path(root, item['archive'])
        target = local_path(root, item['path'])
        require(file_sha(archive) == item['archive_sha256'], 'result archive checksum differs')
        if target.exists():
            require(target.stat().st_size == item['bytes'] and file_sha(target) == item['sha256'],
                    'existing result differs; refusing to overwrite: ' + item['path'])
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.restore-', delete=False) as out:
                temp = Path(out.name)
                total = 0
                with gzip.open(archive, 'rb') as source:
                    while block := source.read(1024 * 1024):
                        total += len(block)
                        require(total <= item['bytes'], 'expanded result exceeds recorded size')
                        out.write(block)
            require(total == item['bytes'] and file_sha(temp) == item['sha256'],
                    'restored result checksum differs')
            # Exclusive creation prevents replacement if another process creates the target.
            os.link(temp, target)
            restored += 1
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
    return {'compressed_results': len(manifest['assets']), 'restored_results': restored}


if __name__ == '__main__':
    print(json.dumps(restore_results(Path(__file__).resolve().parents[1])))
