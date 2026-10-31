import json
from pathlib import Path
import sys

import pytest

from civic_link.evaluation.runner import retrieval_metrics, run, expand_rankings, verify_prediction_queries
from civic_link.common import digest


def question(qid, category='direct_qa', action='answer', groups=(('c1',),)):
    return dict(question_id=qid, question=qid, question_type=category,
        expected_action=action, expected_answerability='answerable' if action == 'answer' else 'unanswerable',
        document_hops=None, unanswerable_reason=None,
        evidence_requirements=[dict(acceptable_chunk_ids=list(group), document_id='d',
            source_alias='D', locator='section', source_url='https://example.test') for group in groups])


def prediction(qid, chunks):
    return dict(query_id=qid, retriever='test', hits=[dict(query_id=qid, retriever='test',
        chunk_id=cid, document_id='d', rank=i, score=1/i) for i, cid in enumerate(chunks, 1)])


def test_conjunctive_evidence_and_long_rank_are_not_hidden():
    questions = [question('a', groups=(('c1',), ('c2',))), question('b', action='clarify', groups=())]
    chunks = ['c1'] + [f'n{i}' for i in range(10)] + ['c2']
    summary, rows, _ = retrieval_metrics(questions, [prediction('a', chunks)], set(chunks))
    assert summary['behavior_rows_excluded_from_retrieval'] == 1
    assert summary['retrieval']['overall']['question_count'] == 1
    assert rows[0]['metrics']['10']['all_evidence'] == 0
    assert rows[0]['metrics']['10']['recall'] == .5
    assert rows[0]['metrics']['100']['all_evidence'] == 1
    q = question('c', groups=(('c2',),))
    result, _, _ = retrieval_metrics([q], [prediction('c', chunks)], set(chunks))
    assert result['retrieval']['overall']['metrics']['10']['mrr'] == 0
    assert result['retrieval']['overall']['metrics']['100']['mrr'] == 1/12


def test_macro_weights_categories_instead_of_questions():
    qs = [question('a'), question('b'), question('c', 'handling_agency')]
    ps = [prediction('a', ['c1']), prediction('b', ['c1']), prediction('c', [])]
    result, _, _ = retrieval_metrics(qs, ps, {'c1'})
    assert result['retrieval']['overall']['metrics']['10']['recall'] == 2/3
    assert result['retrieval']['category_macro']['metrics']['10']['recall'] == .5


def test_top_ten_failure_explanation_does_not_consume_deeper_documents():
    ids = [f'n{i}' for i in range(11)] + ['c1']
    row = prediction('a', ids)
    for hit in row['hits'][:11]:
        hit['document_id'] = 'other'
    summary, _, cases = retrieval_metrics([question('a')], [row], set(ids))
    assert summary['retrieval']['overall']['metrics']['100']['all_evidence'] == 1
    assert len(cases[0]['top_10']) == 10
    assert 'CHUNKING_ERROR' not in cases[0]['cause_candidates']


def test_missing_retrieval_is_not_a_fabricated_failed_prediction():
    with pytest.raises(ValueError, match='missing answerable'):
        retrieval_metrics([question('a')], [], {'c1'})


def test_no_predictions_preserves_unmeasured_and_fresh_output(tmp_path):
    source = tmp_path/'questions.jsonl'
    source.write_text(json.dumps(question('a'))+'\n'+json.dumps(question('b', action='abstain', groups=()))+'\n')
    out = tmp_path/'out'
    result = run(source, out, benchmark_version='test')
    assert result['retrieval_status'] == result['behavior_status'] == 'unmeasured'
    assert json.loads((out/'behavior.metrics.json').read_text())['accuracy_on_measured'] is None
    with pytest.raises(ValueError, match='output exists'):
        run(source, out, benchmark_version='test')


@pytest.mark.parametrize('kind', ['h3', 'neural'])
def test_pipeline_full_rank_survives_top_ten_display(kind):
    ids = [f'c{i}' for i in range(12)]
    row = prediction('a', ids[:10])
    row['ranked_chunk_ids'] = ids
    row['candidates'] = [dict(chunk_id=cid, fusion_rank=i, fusion_score=1/i)
                         for i, cid in enumerate(ids, 1)]
    converted = expand_rankings([row], {cid: dict(document_id='d') for cid in ids}, kind)
    result, _, _ = retrieval_metrics([question('a', groups=(('c11',),))], converted, set(ids))
    assert result['retrieval']['overall']['metrics']['100']['mrr'] == 1/12
    assert result['retrieval']['overall']['metrics']['10']['mrr'] == 0
    row['hits'][0]['chunk_id'] = 'c11'
    with pytest.raises(ValueError, match='disagree'):
        expand_rankings([row], {cid: dict(document_id='d') for cid in ids}, kind)


def test_exact_query_provenance_allows_subset_but_rejects_edited_text():
    qs = [question('a')]
    queries = [dict(query_id='a', question='a'), dict(query_id='b', question='b')]
    ps = [prediction('a', ['c1']), prediction('b', ['c1'])]
    pb, qb = json.dumps(ps).encode(), json.dumps(queries).encode()
    metadata = dict(predictions_sha256=digest(pb), hybrid_run=dict(queries_sha256=digest(qb)))
    assert len(verify_prediction_queries(qs, ps, queries, metadata, pb, qb)) == 1
    qs[0]['question'] = 'reworded question'
    with pytest.raises(ValueError, match='inference text'):
        verify_prediction_queries(qs, ps, queries, metadata, pb, qb)
    with pytest.raises(ValueError, match='checksum'):
        verify_prediction_queries(qs, ps, queries, metadata, pb+b' ', qb)


def test_nonanswer_only_subset_reports_not_applicable_without_false_completion(tmp_path):
    import sqlite3
    source, preds, db = tmp_path/'questions.jsonl', tmp_path/'predictions.jsonl', tmp_path/'corpus.sqlite'
    source.write_text(json.dumps(question('a', action='clarify', groups=()))+'\n')
    preds.write_text(json.dumps(prediction('a', []))+'\n')
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE chunks(chunk_id TEXT, document_id TEXT)')
    out = tmp_path/'out'
    result = run(source, out, benchmark_version='test', db=db, retrieval_predictions=preds)
    assert result['retrieval_status'] == 'not_applicable_no_answer_rows'
    assert result['behavior_status'] == 'unmeasured'
    assert '| Answerable | 0 | N/A | N/A | N/A |' in (out/'REPORT.md').read_text()
    assert json.loads((out/'manifest.json').read_text())['status'] == 'completed'
