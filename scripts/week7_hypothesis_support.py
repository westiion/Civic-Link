"""Frozen Week 7 inputs shared by Graph ranking and exploration experiments.

Retains the original module path used by the preserved experiment runners.
No gold data is loaded by this module.
"""
import _bootstrap
from civic_link.common import ROOT, digest, input_bytes, read_json, read_jsonl
from evaluate_week7_bge_direct import read_rankings, direct_order

SOURCE = ROOT/'week7/results/experiment2_20261031'
BASELINE = ROOT/'week7/results/experiment2_bge_direct_20261101/rankings.csv'
QUERIES = ROOT/'data/benchmark/queries.jsonl'
QUESTIONS = ROOT/'data/benchmark/questions.jsonl'


def unique(rows, key):
    result = {r[key]: r for r in rows}
    if len(result) != len(rows):
        raise ValueError('duplicate '+key)
    return result


def load_frozen():
    protocol = read_json(SOURCE/'protocol.json')
    if digest(input_bytes(QUERIES)) != protocol['queries_sha256']:
        raise ValueError('questions changed since source run')
    manifest = read_json(BASELINE.parent/'manifest.json')
    expected = next(x['sha256'] for x in manifest['outputs'] if x['path'].endswith('/rankings.csv'))
    if digest(input_bytes(BASELINE)) != expected:
        raise ValueError('baseline ranking checksum differs')
    queries = unique(read_jsonl(QUERIES), 'query_id')
    scores = read_rankings(BASELINE)
    if set(queries) != set(scores) or sum(map(len, scores.values())) != protocol['candidate_pairs']:
        raise ValueError('frozen query/pool mismatch')
    return queries, scores, protocol
