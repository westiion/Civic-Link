"""Prepare, validate and score the standalone citizen-question benchmark."""
from __future__ import annotations
import argparse,copy,json,re
from collections import Counter
from pathlib import Path
from civic_data import ROOT,connect,digest,now,read_jsonl,write_json,write_jsonl
from evaluate import evaluate

BENCH = ROOT / 'benchmark'
INPUT_FILES = ['questions.jsonl','queries.jsonl','canonical_queries.jsonl',
               'clarified_queries.jsonl','decision_inputs.jsonl']

def assert_no_lineage(value):
    """Dataset records must stand on their own without prior-release links."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {'parent_question_id','parent_dataset','family_id','dataset_version',
                       'parent_sha256','original_40_sha256'}:
                raise ValueError('question lineage field is not allowed: ' + key)
            assert_no_lineage(item)
    elif isinstance(value, list):
        for item in value: assert_no_lineage(item)
    elif isinstance(value, str) and re.search(r'(?<![A-Za-z0-9])v0\.[1-4](?![0-9])', value):
        raise ValueError('question contains a prior dataset release reference')

def prepare(db):
    questions = read_jsonl(BENCH / 'questions.jsonl')
    assert_no_lineage(questions)
    if len(questions) != 100:
        raise ValueError('expected 100 questions')
    outputs = {
        'queries.jsonl': [{'query_id':q['question_id'],'question':q['question']} for q in questions],
        'canonical_queries.jsonl': [{'query_id':q['question_id'],'question':q['canonical_question']} for q in questions if q['expected_action']=='answer'],
        'clarified_queries.jsonl': [{'query_id':q['question_id'],'question':q['clarification']['resolved_question']} for q in questions if q['clarification']],
        'decision_inputs.jsonl': [{'query_id':q['question_id'],'question':q['question'],'context_mode':q['task_track'],'provided_evidence':q['provided_evidence']} for q in questions],
    }
    # Finished experiment inputs cannot silently change under saved responses.
    for name,rows in outputs.items():
        path=BENCH/name
        if path.exists() and read_jsonl(path)!=rows and (BENCH/'results').exists():
            raise ValueError('saved experiment input differs: '+name)
    for name,rows in outputs.items():write_jsonl(BENCH/name,rows)
    with connect(db,True) as c:
        corpus_hash=json.loads(c.execute("SELECT value FROM metadata WHERE key='corpus_hash'").fetchone()[0])
    report={
        'created_at':now(),'development_only':True,'total':len(questions),
        'real_citizen_rows':0,'human_verified':0,
        'by_action':dict(Counter(q['expected_action'] for q in questions)),
        'by_track':dict(Counter(q['task_track'] for q in questions)),
        'answer_hops':dict(Counter(str(q['document_hops']) for q in questions if q['expected_action']=='answer')),
        'clarified_hops':dict(Counter(str(q['clarification']['resolved_document_hops']) for q in questions if q['clarification'])),
        'by_style':dict(Counter(q['language_style'] for q in questions)),
        'by_evidence_condition':dict(Counter(q['evidence_condition'] for q in questions)),
        'by_challenge':dict(Counter(t for q in questions for t in q['challenge_tags'])),
        'service_coverage':len({s for q in questions for s in q['service_ids']}),
        'limitations':['Synthetic development set; human review pending.',
            'Recorded reference hops are not independently proven minimum hops.',
            'Three hop-3 questions share the same evidence.',
            'Controlled fixtures do not imply conflicts in official sources.'],
        'corpus_hash':corpus_hash,
        'files':{name:digest((BENCH/name).read_bytes()) for name in INPUT_FILES},
    }
    write_json(BENCH/'manifest.json',report)
    return report


def validate(db):
    m = json.loads((BENCH / 'manifest.json').read_text())
    for name, sha in m['files'].items():
        if digest((BENCH / name).read_bytes()) != sha:
            raise ValueError('changed benchmark file: ' + name)
    qs = read_jsonl(BENCH / 'questions.jsonl')
    assert_no_lineage(qs)
    assert len(qs) == len({q['question_id'] for q in qs}) == len({q['question'] for q in qs}) == 100
    assert Counter(q['expected_action'] for q in qs) == {'answer': 64, 'clarify': 20, 'abstain': 16}
    assert len({s for q in qs for s in q['service_ids']}) == 40
    assert read_jsonl(BENCH / 'queries.jsonl') == [{'query_id': q['question_id'], 'question': q['question']} for q in qs]
    with connect(db, True) as c:
        docs = {r['document_id']: dict(r) for r in c.execute('SELECT * FROM documents')}
        aliases = {d['alias'] for d in docs.values()}
        chunks = {r['chunk_id']: dict(r) for r in c.execute('SELECT * FROM chunks')}
        assert json.loads(c.execute("SELECT value FROM metadata WHERE key='corpus_hash'").fetchone()[0]) == m['corpus_hash']
        for q in qs:
            assert q['review_status'] == 'pending_human_review' and not q['provenance']['real_citizen_quote']
            es = q['evidence_requirements'] + q['abstention_basis']
            if q['expected_action'] == 'answer':
                assert es and q['document_hops'] == max(len(p) - 1 for p in q['gold_document_paths'])
            else:
                assert not q['evidence_requirements'] and q['document_hops'] is None
            if q['clarification']:
                cl = q['clarification']
                assert cl['required_slots'] and cl['gold_followup_question'] and cl['simulated_user_response']
                assert cl['resolved_evidence_requirements']
                assert cl['resolved_document_hops'] == max(len(p) - 1 for p in cl['resolved_document_paths'])
                es += cl['resolved_evidence_requirements']
            for path in q['gold_document_paths'] + (q['clarification']['resolved_document_paths'] if q['clarification'] else []):
                assert set(path) <= aliases and len(path) == len(set(path))
            for e in es:
                if e['source_alias'] == 'POLICY':
                    assert e['quote']
                    continue
                doc = docs[e['document_id']]
                assert e['quote'] == doc['body'][e['start_char']:e['end_char']]
                assert e['acceptable_chunk_ids'] and set(e['acceptable_chunk_ids']) <= set(chunks)
                assert all(chunks[cid]['document_id'] == e['document_id'] for cid in e['acceptable_chunk_ids'])
            if q['task_track'] == 'provided_evidence_only':
                assert q['provenance']['controlled_fixture'] and q['provided_evidence'] is not None
                assert not q['evidence_requirements'] and not q['abstention_basis']
    inputs = read_jsonl(BENCH / 'decision_inputs.jsonl')
    assert len(inputs) == 100
    for row, q in zip(inputs, qs):
        assert set(row) == {'query_id', 'question', 'context_mode', 'provided_evidence'}
        assert row['query_id'] == q['question_id'] and row['question'] == q['question']
        assert row['provided_evidence'] == q['provided_evidence']
    assert read_jsonl(BENCH / 'canonical_queries.jsonl') == [
        {'query_id': q['question_id'], 'question': q['canonical_question']} for q in qs if q['expected_action'] == 'answer']
    assert read_jsonl(BENCH / 'clarified_queries.jsonl') == [
        {'query_id': q['question_id'], 'question': q['clarification']['resolved_question']} for q in qs if q['clarification']]
    return {'valid': True, 'total': 100, 'answerable': 64, 'clarify': 20, 'abstain': 16,
            'source_quotes_offsets_and_chunk_ids': 'verified', 'no_gold_in_retrieval_input': True,
            'human_review': 'pending', 'minimum_hops': 'not_independently_verified', 'corpus_hash': m['corpus_hash']}


def select_track(qs, track):
    qs = copy.deepcopy(qs)
    if track == 'canonical':
        qs = [q for q in qs if q['expected_action'] == 'answer']
        for q in qs:
            q['question'] = q['canonical_question']
    elif track == 'clarified':
        qs = [q for q in qs if q['clarification']]
        for q in qs:
            cl = q['clarification']
            q.update(question=cl['resolved_question'], expected_answerability='answerable',
                     evidence_requirements=cl['resolved_evidence_requirements'],
                     document_hops=cl['resolved_document_hops'], abstention_basis=[])
    elif track != 'conversational':
        raise ValueError('unknown track')
    return qs


def score(db, predictions_path, track):
    validate(db)
    qs = select_track(read_jsonl(BENCH / 'questions.jsonl'), track)
    queryfile = {'conversational': 'queries.jsonl', 'canonical': 'canonical_queries.jsonl',
                 'clarified': 'clarified_queries.jsonl'}[track]
    metadata = json.loads(predictions_path.with_suffix('.run.json').read_text())
    manifest = json.loads((BENCH / 'manifest.json').read_text())
    if metadata['queries_sha256'] != digest((BENCH / queryfile).read_bytes()) or metadata['corpus_hash'] != manifest['corpus_hash']:
        raise ValueError('prediction input mismatch')
    if metadata['predictions_sha256'] != digest(predictions_path.read_bytes()):
        raise ValueError('prediction output mismatch')
    predictions = read_jsonl(predictions_path)
    if any(r['retriever'] != metadata['method'] for r in predictions):
        raise ValueError('mixed methods')
    with connect(db, True) as c:
        ids = {r[0] for r in c.execute('SELECT chunk_id FROM chunks')}
    report, cases = evaluate(qs, predictions, ids)
    report['track'] = track
    report['method'] = metadata['method']
    report['nonanswer_cases_are_not_retrieval_failures'] = True
    report['decision_accuracy'] = None
    report['input_hashes'] = {'gold': digest((BENCH / 'questions.jsonl').read_bytes()),
                            'queries': metadata['queries_sha256'], 'predictions': metadata['predictions_sha256'],
                            'corpus': metadata['corpus_hash']}
    stem = metadata['method'] + '_' + track
    write_json(BENCH / 'results' / (stem + '_metrics.json'), report)
    write_jsonl(BENCH / 'results' / (stem + '_cases.jsonl'), cases)
    return report['retrieval']['overall']


def main():
    global BENCH
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['prepare', 'validate', 'evaluate'])
    p.add_argument('--db', type=Path, default=ROOT / 'data/processed/civic.sqlite')
    p.add_argument('--predictions', type=Path)
    p.add_argument('--track', choices=['conversational', 'canonical', 'clarified'], default='conversational')
    p.add_argument('--benchmark-dir', type=Path, default=BENCH)
    a = p.parse_args()
    BENCH = a.benchmark_dir.resolve()
    if a.command == 'prepare':
        r = prepare(a.db)
    elif a.command == 'validate':
        r = validate(a.db)
        write_json(BENCH / 'validation.json', r)
    else:
        if not a.predictions:
            p.error('evaluate requires --predictions')
        r = score(a.db, a.predictions, a.track)
    print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
