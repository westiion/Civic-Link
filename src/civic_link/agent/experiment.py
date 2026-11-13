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


def comparison_config(queries, conditions, configuration):
    if not conditions or len(conditions) != len(set(conditions)) or set(conditions) - CONDITIONS.keys():
        raise ValueError('invalid comparison conditions')
    return dict(version=1, conditions=list(conditions), queries=queries, settings=configuration,
                query_sha256=identity(queries), protocol='shared_original_question_search_then_condition',
                gold_in_generation=False, benchmark_scope='development_comparison_not_independent_holdout')


def prepare_comparison(directory, config):
    """Caller must hold the directory's run_lock."""
    path = Path(directory)/'config.json'
    if path.exists():
        if read_json(path) != config:
            raise ValueError('resume configuration, code, corpus or queries changed; use a new output directory')
    else:
        if set(p.name for p in Path(directory).iterdir()) - {'.running'}:
            raise ValueError('nonempty directory without comparison configuration')
        atomic_json(path, config)


def cached_question(directory, query, conditions):
    """Validate all existing files even for a partially completed question."""
    qid, question = query['query_id'], query['question']
    path = result_path(directory, 'initial', qid)
    first = read_json(path) if path.exists() else None
    if first is not None and (first['query_id'] != qid or first['question'] != question or
                              first['sha256'] != identity(first['result'])):
        raise ValueError('initial search cache identity mismatch')
    completed = set()
    for condition in conditions:
        path = result_path(directory, condition, qid)
        if not path.exists():
            continue
        row = read_json(path)
        if (first is None or row['query_id'] != qid or row['condition'] != condition or
                row['question'] != question or row['initial_sha256'] != first['sha256'] or
                row['result_sha256'] != identity(row['result']) or
                row['response_sha256'] != identity(row['result']['text'])):
            raise ValueError('stored response identity mismatch')
        completed.add(condition)
    return first, completed


def run_question(index, query, directory, conditions, search, agent_factory, *, progress=None, execution=None):
    """One owner per question; A/B/C share one persisted first search.

    The global query index controls condition rotation regardless of GPU scheduling.
    Initial search time is charged to every condition, but is computed only once.
    """
    qid, question = query['query_id'], query['question']
    first, completed = cached_question(directory, query, conditions)
    if first is None:
        started = time.perf_counter()
        try:
            result = normalize_result(search.run({'query': question}), 'search')
        except Exception as exc:
            result = dict(tool='search', status='error', evidence=[], relations=[],
                          error=dict(type=type(exc).__name__, message=str(exc)))
        first = dict(query_id=qid, question=question, result=result, sha256=identity(result),
                     seconds=time.perf_counter()-started)
        if execution is not None:
            first['execution'] = execution
        atomic_json(result_path(directory, 'initial', qid), first)
    order = list(conditions[index % len(conditions):]) + list(conditions[:index % len(conditions)])
    for condition in order:
        if condition in completed:
            if progress:
                progress(condition, qid, 'cached')
            continue
        started = time.perf_counter()
        result = agent_factory(condition).run(question, initial_result=first['result'])
        row = dict(query_id=qid, question=question, condition=condition, result=result,
                   initial_sha256=first['sha256'], initial_search_seconds=first['seconds'],
                   elapsed_seconds=time.perf_counter()-started+first['seconds'],
                   result_sha256=identity(result), response_sha256=identity(result['text']))
        if execution is not None:
            row['execution'] = execution
        atomic_json(result_path(directory, condition, qid), row)
        if progress:
            progress(condition, qid, result['status'])


def run_comparison(queries, directory, conditions, configuration, search, agent_factory, *, progress=print):
    """Persist complete conditions atomically; resume using the frozen identity."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    config = comparison_config(queries, conditions, configuration)
    with run_lock(directory):
        prepare_comparison(directory, config)
        completed = 0
        def report(condition, qid, status):
            nonlocal completed
            completed += 1
            if status != 'cached':
                progress(f'{completed}/{len(queries)*len(conditions)} {condition} {qid}: {status}')
        for index, query in enumerate(queries):
            run_question(index, query, directory, conditions, search, agent_factory, progress=report)
        atomic_json(directory/'run.json', dict(status='completed', questions=len(queries),
                    conditions=list(conditions), completed=completed))
    return config
