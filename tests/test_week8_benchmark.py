from contextlib import closing

import pytest

from civic_link.common import ROOT, connect, read_jsonl
from civic_link.evaluation.week8_benchmark import build, verify, validate, leakage_groups


@pytest.fixture(scope='module')
def benchmark(tmp_path_factory):
    directory = tmp_path_factory.mktemp('week8')
    one, two = directory/'one', directory/'two'
    report = build(output=one)
    build(output=two)
    assert {p.name: p.read_bytes() for p in one.iterdir()} == {p.name: p.read_bytes() for p in two.iterdir()}
    return one, report


def test_real_corpus_reproducible_preserved_grounded_and_leakage_grouped(benchmark):
    directory, report = benchmark
    assert verify(output=directory) == report
    assert report['question_count'] == 1000
    assert report['actions'] == {'answer': 800, 'clarify': 100, 'abstain': 100}
    assert report['new_wide_evidence_count'] == 0
    assert len(report['composition_services']) == 40
    base = (ROOT/'data/benchmark/questions.jsonl').read_bytes()
    assert (directory/'questions.jsonl').read_bytes()[:len(base)] == base
    rows = read_jsonl(directory/'questions.jsonl')
    queries = read_jsonl(directory/'queries.jsonl')
    assert all(set(q) == {'query_id', 'question'} for q in queries)
    mapping = {r['question_id']: r['leakage_group_id'] for r in leakage_groups(rows)}
    for q in rows[608:]:
        assert all(mapping[p] == mapping[q['question_id']] for p in q['parent_question_ids'])
        if q['expected_action'] != 'answer':
            assert q['agentic']['partial_answer_required']
            assert sorted(s['expected_action'] for s in q['agentic']['subtasks']) == sorted(['answer', q['expected_action']])
    with pytest.raises(ValueError, match='output exists'):
        build(output=directory)


def test_rejects_modified_new_quote_and_existing_artifact(benchmark, tmp_path):
    directory, _ = benchmark
    rows = read_jsonl(directory/'questions.jsonl')
    base = read_jsonl(ROOT/'data/benchmark/questions.jsonl')
    with closing(connect(ROOT/'data/corpus/civic.sqlite', True)) as db:
        docs = {r['document_id']: dict(r) for r in db.execute('SELECT * FROM documents')}
        chunks = {r['chunk_id']: dict(r) for r in db.execute('SELECT * FROM chunks')}
    rows[-1]['evidence_requirements'][0]['quote'] += 'invented'
    with pytest.raises(ValueError, match='quote span mismatch'):
        validate(rows, base, docs, chunks)
    for p in directory.iterdir():
        (tmp_path/p.name).write_bytes(p.read_bytes())
    (tmp_path/'queries.jsonl').write_text('{}\n')
    with pytest.raises(ValueError, match='artifact checksum'):
        verify(output=tmp_path)


def test_leakage_connections_are_transitive_without_forcing_unrelated_rows():
    rows = [
        dict(question_id='a', evidence_requirements=[dict(acceptable_chunk_ids=['c1'])]),
        dict(question_id='b', evidence_requirements=[dict(acceptable_chunk_ids=['c1', 'c2'])]),
        dict(question_id='c', evidence_requirements=[dict(acceptable_chunk_ids=['c2'])]),
        dict(question_id='d', evidence_requirements=[], parent_question_ids=['c']),
        dict(question_id='e', evidence_requirements=[]),
    ]
    groups = {r['question_id']: r['leakage_group_id'] for r in leakage_groups(rows)}
    assert len({groups[qid] for qid in 'abcd'}) == 1
    assert groups['e'] != groups['a']
