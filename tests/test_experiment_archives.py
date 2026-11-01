import gzip
import json
import sqlite3

import pytest

from civic_link.common import digest, input_bytes, input_exists, input_path, read_json, read_jsonl
from civic_link.evaluation.runner import run


@pytest.mark.parametrize('suffix', ['json', 'jsonl'])
def test_lossless_archive_resolves_logical_name_and_preserves_digest(tmp_path, suffix):
    logical = tmp_path / ('cache.' + suffix)
    payload = b'{"score":1.0000000000000002}\n'
    archived = logical.with_name(logical.name + '.gz')
    archived.write_bytes(gzip.compress(payload, mtime=0))
    assert input_exists(logical) and input_path(logical) == archived
    assert input_bytes(logical) == payload
    assert digest(input_bytes(archived)) == digest(payload)
    value = read_json(logical) if suffix == 'json' else read_jsonl(logical)[0]
    assert value['score'] == 1.0000000000000002


def test_full_archived_prediction_is_verified_against_original_run_checksum(tmp_path):
    q = dict(question_id='q', question='question', question_type='direct_qa',
        expected_action='answer', expected_answerability='answerable', document_hops=None,
        evidence_requirements=[dict(acceptable_chunk_ids=['gold'], document_id='d',
            source_alias='D', locator='section', source_url='https://example.test')])
    questions = tmp_path/'questions.jsonl'
    questions.write_text(json.dumps(q)+'\n')
    queries = tmp_path/'queries.jsonl'
    queries.write_text(json.dumps(dict(query_id='q', question='question'))+'\n')
    ids = [f'n{i}' for i in range(11)] + ['gold']
    row = dict(query_id='q', retriever='bge', ranked_chunk_ids=ids,
        hits=[dict(query_id='q', retriever='bge', chunk_id=cid,
            document_id='d', rank=i, score=1/i) for i,cid in enumerate(ids[:10], 1)])
    raw = (json.dumps(row)+'\n').encode()
    archived = tmp_path/'bge.jsonl.gz'
    archived.write_bytes(gzip.compress(raw, mtime=0))
    (tmp_path/'bge.run.json').write_text(json.dumps(dict(status='completed',
        queries_sha256=digest(queries.read_bytes()), predictions_sha256=digest(raw))))
    db = tmp_path/'corpus.sqlite'
    with sqlite3.connect(db) as connection:
        connection.execute('CREATE TABLE chunks(chunk_id TEXT, document_id TEXT)')
        connection.executemany('INSERT INTO chunks VALUES (?, ?)', [(cid, 'd') for cid in ids])
    run(questions, tmp_path/'out', benchmark_version='archive-test', db=db,
        retrieval_predictions=archived, retrieval_format='neural', prediction_queries=queries)
    result = read_json(tmp_path/'out/retrieval.metrics.json')['retrieval']['overall']
    assert result['metrics']['10']['all_evidence'] == 0
    assert result['metrics']['100']['all_evidence'] == 1
    assert result['metrics']['100']['mrr'] == 1/12
