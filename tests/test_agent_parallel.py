"""Real spawned processes, with small fake tools in place of GPUs/model APIs."""
from dataclasses import dataclass
import json
import multiprocessing as mp
import os
from pathlib import Path
import time

import pytest

from civic_link.agent import RetrievalAgent
from civic_link.agent.evaluation import evaluate_comparison
from civic_link.agent.experiment import CONDITIONS, identity, read_json, result_path
from civic_link.agent.parallel import RequestLimited, run_parallel_comparison, validate_devices


class FakeSearch:
    def __init__(self, markers, barrier):
        self.markers, self.barrier, self.first = Path(markers), barrier, True

    def run(self, args):
        if self.first and self.barrier is not None:
            self.first = False
            self.barrier.wait(timeout=20)
        # Exclusive creation detects duplicate initial work across any workers/resume.
        with (self.markers/(identity(args['query'])+'.txt')).open('x') as stream:
            stream.write(str(os.getpid()))
        return dict(tool='search', status='ok', relations=[], evidence=[
            dict(id='C1', document_id='D', title='source', text='evidence', source='https://example.org')])


class FakeClient:
    last_usage = {'total_tokens': 1}
    def __init__(self, counters, lock):
        self.counters, self.lock = counters, lock

    def complete(self, messages):
        if self.counters is not None:
            active, peak = self.counters
            with self.lock:
                active.value += 1
                peak.value = max(peak.value, active.value)
            try:
                time.sleep(.05)
            finally:
                with self.lock:
                    active.value -= 1
        return json.dumps(dict(checks=[], action='respond', arguments=dict(
            text='answer [C1]', evidence_ids=['C1']), reason='done'))


class InterruptingAgent:
    def run(self, *args, **kwargs):
        raise RuntimeError('simulated worker failure')


@dataclass
class FakeFactory:
    markers: str
    barrier: object = None
    counters: object = None
    counter_lock: object = None
    fail: bool = False
    hard_exit: bool = False

    def __call__(self, device, semaphore):
        if self.hard_exit:
            os._exit(17)
        search = FakeSearch(self.markers, self.barrier)
        def factory(condition):
            if self.fail and condition == 'iterative_search':
                return InterruptingAgent()
            client = RequestLimited(FakeClient(self.counters, self.counter_lock), semaphore)
            return RetrievalAgent(client, {'search':search, 'graph':object()}, allowed_tools=CONDITIONS[condition])
        return search, factory


def queries(n):
    return [dict(query_id=f'Q{i}', question=f'question {i}') for i in range(n)]


def gold(qs):
    return {q['query_id']:dict(question_id=q['query_id'], question=q['question'],
        expected_action='answer', question_type='direct', evidence_requirements=[
            dict(evidence_id='E1', document_id='D', acceptable_chunk_ids=['C1'], start_char=0, end_char=8)]) for q in qs}


def no_workers():
    assert not [p for p in mp.active_children() if p.name.startswith('comparison-')]


def test_four_workers_preserve_pairing_limit_api_calls_and_resume(tmp_path):
    ctx = mp.get_context('spawn')
    counters = (ctx.Value('i', 0), ctx.Value('i', 0))
    markers = tmp_path/'markers'; markers.mkdir()
    factory = FakeFactory(str(markers), ctx.Barrier(4), counters, ctx.Lock())
    directory = tmp_path/'run'; qs = queries(8)
    devices = ['cuda:0', 'cuda:1', 'cuda:2', 'cuda:3']
    config = run_parallel_comparison(qs, directory, list(CONDITIONS), {}, factory, devices,
                                     api_concurrency=2, progress=lambda x:None)
    assert config['settings']['execution']['api_concurrency'] == 2
    assert counters[0].value == 0 and counters[1].value == 2
    assert len(list(markers.iterdir())) == 8
    seen_devices, seen_pids, snapshots = set(), set(), {}
    for q in qs:
        rows = [read_json(result_path(directory,c,q['query_id'])) for c in CONDITIONS]
        assert len({r['initial_sha256'] for r in rows}) == 1
        assert len({r['execution']['device'] for r in rows}) == 1
        assert all(r['result']['status'] == 'responded' for r in rows)
        seen_devices.add(rows[0]['execution']['device'])
        seen_pids.add(rows[0]['execution']['pid'])
        for c in CONDITIONS:
            path = result_path(directory,c,q['query_id']); snapshots[path] = path.read_bytes()
    assert seen_devices == set(devices) and len(seen_pids) == 4
    # A completely cached run must not construct workers/models at all.
    run_parallel_comparison(qs, directory, list(CONDITIONS), {}, FakeFactory(str(markers), hard_exit=True),
                            devices, api_concurrency=2, progress=lambda x:None)
    assert all(path.read_bytes() == value for path,value in snapshots.items())
    assert read_json(directory/'run.json')['cached_conditions'] == 24
    assert evaluate_comparison(directory, gold(qs))['paired_questions'] == 8
    assert not (directory/'.running').exists()
    no_workers()


def test_failed_worker_preserves_partial_results_and_can_resume(tmp_path):
    markers = tmp_path/'markers'; markers.mkdir()
    directory = tmp_path/'run'; qs = queries(1)
    with pytest.raises(RuntimeError, match='simulated worker failure'):
        run_parallel_comparison(qs, directory, list(CONDITIONS), {}, FakeFactory(str(markers), fail=True),
                                ['cuda:0'], progress=lambda x:None)
    saved = result_path(directory, 'single_search', 'Q0').read_bytes()
    assert not (directory/'run.json').exists() and not (directory/'.running').exists()
    assert evaluate_comparison(directory, gold(qs))['paired_questions'] == 0
    no_workers()
    run_parallel_comparison(qs, directory, list(CONDITIONS), {}, FakeFactory(str(markers)),
                            ['cuda:0'], progress=lambda x:None)
    assert result_path(directory, 'single_search', 'Q0').read_bytes() == saved
    assert len(list(markers.iterdir())) == 1
    assert evaluate_comparison(directory, gold(qs))['paired_questions'] == 1
    no_workers()


def test_abrupt_worker_exit_does_not_hang_or_leave_lock(tmp_path):
    with pytest.raises(RuntimeError, match='exited unexpectedly'):
        run_parallel_comparison(queries(2), tmp_path, list(CONDITIONS), {},
            FakeFactory(str(tmp_path), hard_exit=True), ['cuda:0', 'cuda:1'], progress=lambda x:None)
    assert not (tmp_path/'.running').exists() and not (tmp_path/'run.json').exists()
    no_workers()


def test_changed_config_and_corrupt_cache_refused_before_startup(tmp_path):
    markers = tmp_path/'markers'; markers.mkdir()
    directory = tmp_path/'run'; qs = queries(1)
    factory = FakeFactory(str(markers))
    run_parallel_comparison(qs, directory, list(CONDITIONS), {}, factory, ['cuda:0'], progress=lambda x:None)
    with pytest.raises(ValueError, match='changed'):
        run_parallel_comparison(qs, directory, list(CONDITIONS), {}, factory,
                                ['cuda:0', 'cuda:1'], progress=lambda x:None)
    path = result_path(directory, 'single_search', 'Q0')
    row = read_json(path); row['response_sha256'] = 'tampered'
    path.write_text(json.dumps(row), encoding='utf-8')
    with pytest.raises(ValueError, match='identity mismatch'):
        run_parallel_comparison(qs, directory, list(CONDITIONS), {}, factory, ['cuda:0'], progress=lambda x:None)
    no_workers()


@pytest.mark.parametrize('devices', [[], ['cuda:0','cuda:0'], ['cpu'], ['cuda'], ['cuda:-1'], ['cuda:01']])
def test_invalid_devices(devices):
    with pytest.raises(ValueError, match='distinct CUDA'):
        validate_devices(devices)


def test_limiter_releases_on_error_and_forwards_client_metadata():
    import threading
    class Client:
        fingerprint = 'test-encoder'
        last_usage = None
        def embed(self, texts, **kwargs):
            raise RuntimeError('network error')
        def complete(self, messages):
            self.last_usage = {'total_tokens':3}
            return 'ok'
    semaphore = threading.BoundedSemaphore(1)
    client = RequestLimited(Client(), semaphore)
    with pytest.raises(RuntimeError):
        client.embed(['x'], query=True)
    assert semaphore.acquire(blocking=False)
    semaphore.release()
    assert client.complete([]) == 'ok'
    assert client.last_usage == {'total_tokens':3} and client.fingerprint == 'test-encoder'


def test_cli_dispatches_gpu_workers_after_preflight(tmp_path, monkeypatch):
    import run_agent_comparison as cli
    query_file = tmp_path/'queries.jsonl'
    query_file.write_text('\n'.join(json.dumps(q) for q in queries(4)), encoding='utf-8')
    model = tmp_path/'model'; model.mkdir(); (model/'config.json').write_text('{}')
    seen = []
    monkeypatch.setattr(cli, 'corpus_scope', lambda _: {})
    monkeypatch.setattr(cli, 'file_hash', lambda _: 'hash')
    def runtime(devices):
        seen.append('cuda')
        return {'devices':list(devices)}
    monkeypatch.setattr(cli, 'cuda_runtime', runtime)
    monkeypatch.setattr(cli, 'check_apis', lambda args:seen.append('api'))
    def parallel(qs, directory, conditions, settings, factory, devices, **kwargs):
        seen.append('parallel')
        assert len(qs) == 4 and list(conditions) == list(CONDITIONS)
        assert devices == ['cuda:0','cuda:1','cuda:2','cuda:3']
        assert kwargs['api_concurrency'] == 2
        assert settings['bge_device'] == 'per_worker' and settings['bge_dtype'] == 'float32'
        assert isinstance(factory, cli.Components)
    monkeypatch.setattr(cli, 'run_parallel_comparison', parallel)
    assert cli.main(['--queries', str(query_file), '--output-dir', str(tmp_path/'run'),
        '--endpoint', 'https://example.org/chat', '--embedding-endpoint', 'https://example.org/embed',
        '--bge-model', str(model), '--bge-devices', 'cuda:0', 'cuda:1', 'cuda:2', 'cuda:3',
        '--api-concurrency', '2']) == 0
    assert seen == ['cuda', 'api', 'parallel']
