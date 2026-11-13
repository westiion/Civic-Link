"""Spawn one persistent BGE worker per GPU; schedule whole questions across them."""
from __future__ import annotations

import multiprocessing as mp
import os
from pathlib import Path
from queue import Empty
import re
import time

from .experiment import (atomic_json, cached_question, comparison_config,
                         prepare_comparison, run_lock, run_question)


def validate_devices(devices):
    if (not devices or len(set(devices)) != len(devices) or
            any(not re.fullmatch(r'cuda:(0|[1-9][0-9]*)', device) for device in devices)):
        raise ValueError('use distinct CUDA devices, for example cuda:0 cuda:1 cuda:2 cuda:3')


def cuda_runtime(devices):
    """Fail before writing any results if the wheel/driver cannot run CUDA kernels."""
    validate_devices(devices)
    import platform
    import importlib.metadata as metadata
    import torch
    if not torch.cuda.is_available():
        raise ValueError('CUDA is unavailable; V100 requires a compatible PyTorch build (for example cu126)')
    profiles = []
    for device in devices:
        index = int(device.split(':')[1])
        if index >= torch.cuda.device_count():
            raise ValueError(f'{device} is not visible; check CUDA_VISIBLE_DEVICES')
        try:
            # Allocation alone does not verify that this wheel contains usable kernels.
            value = torch.ones((16, 16), device=device)
            assert (value @ value).sum().item() == 4096
            torch.cuda.synchronize(index)
            del value
        except Exception as exc:
            raise ValueError(f'{device} kernel check failed; V100 needs a build supporting sm_70, such as cu126') from exc
        profiles.append(dict(device=device, name=torch.cuda.get_device_name(index),
                             capability=list(torch.cuda.get_device_capability(index))))
    if len({(p['name'], tuple(p['capability'])) for p in profiles}) != 1:
        raise ValueError('use the same GPU model for all comparison workers')
    return dict(python=platform.python_version(), cuda=torch.version.cuda, devices=profiles,
                packages={name:metadata.version(name) for name in
                          ('torch', 'transformers', 'numpy', 'kiwipiepy', 'tokenizers', 'safetensors')})


class RequestLimited:
    """One shared semaphore bounds generation AND embedding requests across GPUs."""
    def __init__(self, client, semaphore):
        self.client, self.semaphore = client, semaphore

    def __getattr__(self, name):
        return getattr(self.client, name)

    def complete(self, messages):
        with self.semaphore:
            return self.client.complete(messages)

    def embed(self, texts, **kwargs):
        with self.semaphore:
            return self.client.embed(texts, **kwargs)


def _worker(device, factory, semaphore, tasks, events, directory, conditions):
    # CUDA/model objects are created here, never inherited from the parent.
    try:
        search, make_agent = factory(device, semaphore)
        execution = dict(device=device, pid=os.getpid())
        events.put(dict(kind='ready', **execution))
        while True:
            job = tasks.get()
            if job is None:
                return
            index, query = job
            run_question(index, query, directory, conditions, search, make_agent, execution=execution)
            events.put(dict(kind='done', index=index, query_id=query['query_id'], **execution))
    except BaseException as exc:
        events.put(dict(kind='error', device=device, error=f'{type(exc).__name__}: {exc}'))


def _event(events, workers):
    while True:
        try:
            event = events.get(timeout=.25)
        except Empty:
            dead = [p for p in workers if p.exitcode is not None]
            if dead:
                raise RuntimeError(f'comparison worker exited unexpectedly: {[(p.name, p.exitcode) for p in dead]}')
            continue
        if event['kind'] == 'error':
            raise RuntimeError(f"{event['device']} worker failed: {event['error']}")
        return event


def run_parallel_comparison(queries, directory, conditions, configuration, factory, devices,
                            *, api_concurrency=None, progress=print):
    """Write the standard flat output layout, with exclusive ownership per query.

    A parent lock protects the entire run. Children write disjoint query IDs;
    no shard merge is needed. On interruption all owned children stop before unlock.
    """
    validate_devices(devices)
    api_concurrency = len(devices) if api_concurrency is None else api_concurrency
    if type(api_concurrency) is not int or not 1 <= api_concurrency <= len(devices):
        raise ValueError('API concurrency must be between 1 and the number of GPU workers')
    if not queries or len({q['query_id'] for q in queries}) != len(queries):
        raise ValueError('parallel execution requires nonempty, unique queries')
    configuration = dict(configuration, execution=dict(mode='spawn_per_gpu', devices=list(devices),
        api_concurrency=api_concurrency, assignment='dynamic_whole_question', warmup='before_dispatch'))
    config = comparison_config(queries, conditions, configuration)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with run_lock(directory):
        prepare_comparison(directory, config)
        pending, cached_conditions = [], 0
        for index, query in enumerate(queries):
            _, completed = cached_question(directory, query, conditions)
            cached_conditions += len(completed)
            if len(completed) != len(conditions):
                pending.append((index, query))
        progress(f'{cached_conditions}/{len(queries)*len(conditions)} conditions already saved; {len(pending)} questions pending')
        workers = []
        ctx = mp.get_context('spawn')
        tasks, events = ctx.Queue(), ctx.Queue()
        semaphore = ctx.BoundedSemaphore(api_concurrency)
        ready = []
        try:
            for device in devices[:len(pending)]:
                process = ctx.Process(target=_worker, name=f'comparison-{device}',
                    args=(device, factory, semaphore, tasks, events, directory, conditions), daemon=True)
                process.start()
                workers.append(process)
                atomic_json(directory/'workers.json', dict(parent_pid=os.getpid(), workers=[
                    dict(device=devices[i], pid=worker.pid, state='starting') for i,worker in enumerate(workers)]))
            # All models/indices are ready before timed questions are dispatched.
            while len(ready) < len(workers):
                event = _event(events, workers)
                if event['kind'] != 'ready':
                    raise RuntimeError('unexpected worker startup event')
                ready.append(event)
                progress(f"{event['device']} ready (PID {event['pid']})")
            atomic_json(directory/'workers.json', dict(parent_pid=os.getpid(), workers=ready))
            waiting = iter(pending)
            for _ in workers:
                tasks.put(next(waiting))
            remaining = {index for index, _ in pending}
            while remaining:
                event = _event(events, workers)
                if event['kind'] != 'done' or event['index'] not in remaining:
                    raise RuntimeError('unexpected or duplicate worker completion')
                remaining.remove(event['index'])
                progress(f"{len(queries)-len(remaining)}/{len(queries)} questions complete: {event['query_id']} on {event['device']}")
                job = next(waiting, None)
                if job is not None:
                    tasks.put(job)
            for _ in workers:
                tasks.put(None)
            for process in workers:
                process.join(timeout=5)
                if process.is_alive() or process.exitcode != 0:
                    raise RuntimeError(f'{process.name} did not shut down cleanly')
        finally:
            # Stop child computations after Ctrl+C or failure; rental billing is separate.
            for process in workers:
                if process.is_alive():
                    process.terminate()
            for process in workers:
                process.join(timeout=5)
                if process.is_alive():
                    process.kill()
                    process.join()
            for queue in (tasks, events):
                queue.cancel_join_thread()
                queue.close()
        atomic_json(directory/'run.json', dict(status='completed', questions=len(queries),
            conditions=list(conditions), completed=len(queries)*len(conditions),
            cached_conditions=cached_conditions, attempt_wall_seconds=time.perf_counter()-started))
    return config
