"""Refresh the exact Git-only Week 7 file inventory and frozen input manifest."""
import ast
import gzip
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile

from civic_data import ROOT
from graph_construct import corpus_identity
from restore_week7 import sha

RUNTIME = ['hybrid_graph','evaluate_week7','run_graph_score_revision',
    'analyze_graph_score_revision','restore_week7','refresh_week7_manifest','benchmark_review']
TESTS = ['test_evidence_repairs','test_graph_connection_close','test_hybrid_graph',
    'test_graph_score_revision','test_week7_current','test_week7_evaluation',
    'test_behavior_evaluation','test_overlap_rerank','test_restore_week7']


def members(root):
    names = ['README.md','requirements.txt','requirements-dev.txt','pytest.ini','.gitattributes',
        '.gitignore','data/services.jsonl','data/processed/graph.json','data/processed/graph.evidence.json',
        'tests/fixtures/week7_pre_review.jsonl.gz','week7/README.md','week7/REPORT.md',
        'week7/inputs/hybrid_candidates.run.json','week7/reviews/agent_review.csv',
        'week7/reviews/adjudication_changes.jsonl','week7/assets/corpus.sqlite.gz',
        'week7/assets/hybrid_candidates.jsonl.gz','week7/git_files.txt']
    modules={p.stem:p for p in (root/'scripts').glob('*.py')}
    pending=list(RUNTIME)
    for name in TESTS:
        tree=ast.parse((root/'tests'/(name+'.py')).read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.ImportFrom) and node.module:
                pending += [node.module.split('.')[0]]
    needed=set()
    while pending:
        name=pending.pop()
        if name in needed or name not in modules:continue
        needed.add(name)
        for node in ast.walk(ast.parse(modules[name].read_text())):
            if isinstance(node,ast.ImportFrom) and node.module:pending.append(node.module.split('.')[0])
            elif isinstance(node,ast.Import):pending += [a.name.split('.')[0] for a in node.names]
    names += ['scripts/'+n+'.py' for n in sorted(needed)]
    names += ['tests/'+n+'.py' for n in TESTS]
    for directory in ['week7/data/benchmark','week7/evaluation','week7/docs/assets']:
        names += [str(p.relative_to(root)) for p in (root/directory).rglob('*') if p.is_file()]
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
    (root/'week7/git_files.txt').write_text('\n'.join(sorted(names+['week7/manifest.json']))+'\n')
    names = members(root)
    assets = [describe_asset(root,'week7/assets/corpus.sqlite.gz','data/processed/civic.sqlite','corpus'),
              describe_asset(root,'week7/assets/hybrid_candidates.jsonl.gz',None,'hybrid')]
    assets[1].pop('target')  # Hybrid gzip is read directly; do not expand a second copy.
    with tempfile.TemporaryDirectory(prefix='civic-share-corpus-') as directory:
        path=Path(directory)/'corpus.sqlite'
        with gzip.open(root/assets[0]['archive'],'rb') as stream:path.write_bytes(stream.read())
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
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
    (root/'week7/manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    return dict(files=len(names)+1, bytes=sum(item['bytes'] for item in files.values()), network_calls=0, model_loads=0)


if __name__=='__main__':
    print(json.dumps(build(),ensure_ascii=False,indent=2))
