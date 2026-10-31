"""Refresh the shared file inventory and frozen input manifest."""

import _bootstrap  # Make the local src package available to script entry points.
import gzip
import hashlib
import json
from contextlib import closing
from pathlib import Path
import re
import sqlite3
import tempfile

from civic_link.common import ROOT
from civic_link.graph.build import corpus_identity
from restore_data import sha

def members(root):
    names = ['README.md', 'requirements.txt', 'requirements-dev.txt', 'pyproject.toml',
        'pytest.ini', '.gitattributes', '.gitignore', 'file_inventory.txt', 'data/services.jsonl',
        'data/graph/graph.json', 'data/graph/graph.evidence.json',
        'data/corpus/corpus.sqlite.gz', 'data/retrieval/hybrid_candidates.jsonl.gz',
        'data/retrieval/hybrid_candidates.run.json', 'tests/fixtures/week7_pre_review.jsonl.gz']
    for directory in ('src', 'scripts', 'tests'):
        names += [p.relative_to(root).as_posix() for p in (root/directory).rglob('*.py')]
    for directory in ('data/benchmark', 'experiments', 'docs'):
        names += [p.relative_to(root).as_posix() for p in (root/directory).rglob('*')
                  if p.is_file() and '__pycache__' not in p.parts]
    return sorted(set(names))


def describe_asset(root, archive, target, kind):
    path = root/archive
    h = hashlib.sha256(); size = 0
    with gzip.open(path,'rb') as stream:
        while block := stream.read(1024*1024):
            h.update(block);size += len(block)
    return dict(archive=archive,target=target,kind=kind,bytes=size,sha256=h.hexdigest(),archive_sha256=sha(path))


def build(root=ROOT):
    root = Path(root)
    names = members(root)
    (root/'file_inventory.txt').write_text('\n'.join(sorted(names+['manifest.json']))+'\n', encoding='utf-8', newline='\n')
    names = members(root)
    assets = [describe_asset(root,'data/corpus/corpus.sqlite.gz','data/corpus/civic.sqlite','corpus'),
              describe_asset(root,'data/retrieval/hybrid_candidates.jsonl.gz',None,'hybrid')]
    assets[1].pop('target')  # Hybrid gzip is read directly; do not expand a second copy.
    with tempfile.TemporaryDirectory(prefix='civic-share-corpus-') as directory:
        path=Path(directory)/'corpus.sqlite'
        with gzip.open(root/assets[0]['archive'],'rb') as stream:path.write_bytes(stream.read())
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
            assets[0].update(fingerprint=corpus_identity(db),corpus_hash=json.loads(db.execute("select value from metadata where key='corpus_hash'").fetchone()[0]))
            assert db.execute('pragma integrity_check').fetchone()[0]=='ok'
            assert {r[0] for r in db.execute("select name from sqlite_master where type='table'")}=={'metadata','services','documents','document_services','chunks'}
    patterns=[rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',rb'\bAKIA[A-Z0-9]{16}\b',
              rb'\bgh[pousr]_[A-Za-z0-9]{30,}\b',rb'\bsk-(?:proj-)?[A-Za-z0-9_-]{35,}\b']
    files={}
    for name in names:
        path=root/name
        if path.is_symlink() or not path.is_file():raise ValueError('missing/linked shared member: '+name)
        if not path.resolve().is_relative_to(root.resolve()):raise ValueError('outside member: '+name)
        data=gzip.decompress(path.read_bytes()) if path.suffix=='.gz' else path.read_bytes()
        if re.search(rb'/(?:Users|home)/[A-Za-z0-9_.-]+/',data) or re.search(rb'/private/var/folders/[A-Za-z0-9]',data):
            raise ValueError('local absolute path: '+name)
        # The scanner's own regex literals are not keys; they do not match these patterns.
        if any(re.search(pattern,data) for pattern in patterns):raise ValueError('possible secret: '+name)
        if path.stat().st_size>25*1024*1024:raise ValueError('unexpected large shared file: '+name)
        files[name]=dict(bytes=path.stat().st_size,sha256=sha(path))
    manifest=dict(format_version=1,scope='Week7 frozen Hybrid onward; no embeddings or BGE required',
        files=files,assets=assets,questions=608,diagnostic_subset=480,
        excluded=['Archive','work','.git','models','virtual environments','query embeddings','raw web payloads'],
        network_calls=0,model_loads=0)
    (root/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n', encoding='utf-8', newline='\n')
    return dict(files=len(names)+1, bytes=sum(item['bytes'] for item in files.values()), network_calls=0, model_loads=0)


if __name__=='__main__':
    print(json.dumps(build(),ensure_ascii=False,indent=2))
