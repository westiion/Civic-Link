"""Paired, resumable agent comparisons. Generation consumes query-only rows."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import time

from .controller import normalize_result

CONDITIONS = {'single_search': (), 'iterative_search': ('search',),
              'iterative_graph': ('search', 'graph')}


def identity(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


@contextmanager
def run_lock(directory):
    lock = Path(directory)/'.running'
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError('run is locked; if the previous process crashed, verify it stopped before removing .running') from exc
    try:
        os.write(descriptor, str(os.getpid()).encode())
        os.close(descriptor)
        yield
    finally:
        lock.unlink()


def load_queries(path, limit=None):
    rows = [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]
    seen = set()
    for row in rows:
        if set(row) != {'query_id', 'question'} or any(not isinstance(v, str) or not v.strip() for v in row.values()):
            raise ValueError('generation inputs must contain only query_id and question')
        if row['query_id'] in seen:
            raise ValueError('duplicate query ID')
        seen.add(row['query_id'])
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError('limit must be positive')
    if not rows:
        raise ValueError('empty query set')
    return rows[:limit] if limit else rows


def result_path(directory, condition, qid):
    return Path(directory)/condition/(identity(qid)+'.json')


def run_comparison(queries, directory, conditions, configuration, search, agent_factory, *, progress=print):
    """Persist each first search and complete question atomically; resume by identity.

    initial_search_seconds is charged to each condition for comparable latency,
    although that common computation is physically performed only once.
    """
    if not conditions or len(conditions) != len(set(conditions)) or set(conditions) - CONDITIONS.keys():
        raise ValueError('invalid comparison conditions')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    config = dict(version=1, conditions=list(conditions), queries=queries, settings=configuration,
                  query_sha256=identity(queries), protocol='shared_original_question_search_then_condition',
                  gold_in_generation=False, benchmark_scope='development_comparison_not_independent_holdout')
    with run_lock(directory):
        path = directory/'config.json'
        if path.exists():
            if read_json(path) != config:
                raise ValueError('resume configuration, code, corpus or queries changed; use a new output directory')
        else:
            if any(directory.iterdir()) and set(p.name for p in directory.iterdir()) != {'.running'}:
                raise ValueError('nonempty directory without comparison configuration')
            atomic_json(path, config)
        completed = 0
        for index, query in enumerate(queries):
            qid, question = query['query_id'], query['question']
            first_path = result_path(directory, 'initial', qid)
            if first_path.exists():
                first = read_json(first_path)
                if first['query_id'] != qid or first['question'] != question or first['sha256'] != identity(first['result']):
                    raise ValueError('initial search cache identity mismatch')
            else:
                started = time.perf_counter()
                try:
                    result = normalize_result(search.run({'query': question}), 'search')
                except Exception as exc:
                    result = dict(tool='search', status='error', evidence=[], relations=[],
                                  error=dict(type=type(exc).__name__, message=str(exc)))
                first = dict(query_id=qid, question=question, result=result, sha256=identity(result),
                             seconds=time.perf_counter()-started)
                atomic_json(first_path, first)
            # Rotate order to reduce a fixed warm-up/server-load advantage.
            order = list(conditions[index % len(conditions):]) + list(conditions[:index % len(conditions)])
            for condition in order:
                destination = result_path(directory, condition, qid)
                if destination.exists():
                    existing = read_json(destination)
                    if (existing['query_id'] != qid or existing['condition'] != condition or existing['question'] != question
                            or existing['initial_sha256'] != first['sha256'] or
                            existing['result_sha256'] != identity(existing['result'])):
                        raise ValueError('stored response identity mismatch')
                    completed += 1
                    continue
                started = time.perf_counter()
                agent = agent_factory(condition)
                result = agent.run(question, initial_result=first['result'])
                row = dict(query_id=qid, question=question, condition=condition, result=result,
                           initial_sha256=first['sha256'], initial_search_seconds=first['seconds'],
                           elapsed_seconds=time.perf_counter()-started+first['seconds'],
                           result_sha256=identity(result), response_sha256=identity(result['text']))
                atomic_json(destination, row)
                completed += 1
                progress(f'{completed}/{len(queries)*len(conditions)} {condition} {qid}: {result["status"]}')
        atomic_json(directory/'run.json', dict(status='completed', questions=len(queries),
                    conditions=list(conditions), completed=completed))
    return config
