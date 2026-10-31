"""Restore and verify the exact Week 7 Git inputs without network or model calls."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def member(root, name):
    path = root / name
    if Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink():
        raise ValueError('unsafe shared path: ' + name)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('shared path escapes root: ' + name)
    return path


def verify_corpus(path, expected):
    from graph_construct import corpus_identity
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        if db.execute('pragma integrity_check').fetchone()[0] != 'ok':
            raise ValueError('corpus integrity failed')
        if corpus_identity(db) != expected['fingerprint']:
            raise ValueError('corpus contents differ')
        value = db.execute("select value from metadata where key='corpus_hash'").fetchone()
        if value is None or json.loads(value[0]) != expected['corpus_hash']:
            raise ValueError('corpus identity differs')


def restore_asset(root, item):
    source, target = member(root, item['archive']), member(root, item['target'])
    if sha(source) != item['archive_sha256']:
        raise ValueError('compressed input differs')
    if target.exists():
        if item.get('kind') == 'corpus':
            # The full local corpus is also valid; never replace its embeddings.
            verify_corpus(target, item)
        elif sha(target) != item['sha256']:
            raise ValueError('existing input differs; refusing overwrite')
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.restore-', delete=False) as dst:
            temporary = Path(dst.name)
            count = 0
            with gzip.open(source, 'rb') as src:
                while block := src.read(1024 * 1024):
                    count += len(block)
                    if count > item['bytes']:
                        raise ValueError('expanded input exceeds recorded size')
                    dst.write(block)
        if count != item['bytes'] or sha(temporary) != item['sha256']:
            raise ValueError('restored input differs')
        os.link(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return True


def verify(root=ROOT, *, restore=False):
    root = Path(root)
    manifest = json.loads((root / 'week7/manifest.json').read_text())
    for name, expected in manifest['files'].items():
        path = member(root, name)
        if not path.is_file() or path.stat().st_size != expected['bytes'] or sha(path) != expected['sha256']:
            raise ValueError('shared file differs: ' + name)
    restored = 0
    for item in manifest['assets']:
        compressed = member(root,item['archive'])
        value = gzip.decompress(compressed.read_bytes())
        if len(value)!=item['bytes'] or hashlib.sha256(value).hexdigest()!=item['sha256']:
            raise ValueError('compressed input contents differ')
        if restore and 'target' in item:
            restored += restore_asset(root, item)
        if 'target' not in item:
            continue
        target = member(root, item['target'])
        if not target.is_file():
            raise ValueError('run restore_week7.py restore first: ' + item['target'])
        if item.get('kind') == 'corpus':
            verify_corpus(target, item)
        elif sha(target) != item['sha256']:
            raise ValueError('restored input differs: ' + item['target'])
    questions = [json.loads(s) for s in (root/'week7/data/benchmark/questions.jsonl').read_text().splitlines()]
    core = [json.loads(s) for s in (root/'week7/data/benchmark/diagnostic480/questions.jsonl').read_text().splitlines()]
    by_id = {q['question_id']:q for q in questions}
    if len(questions) != 608 or len(by_id) != 608 or len(core) != 480:
        raise ValueError('benchmark count differs')
    if any(q != by_id.get(q['question_id']) for q in core):
        raise ValueError('diagnostic subset differs')
    if any('generation_stage' in q for q in questions):
        raise ValueError('retired generation column returned')
    return dict(status='passed', files=len(manifest['files']), questions=608, subset=480,
                restored=restored, network_calls=0, model_loads=0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('restore', 'verify'))
    args = parser.parse_args()
    print(json.dumps(verify(restore=args.command=='restore'), indent=2))
