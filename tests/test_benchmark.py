import json
import gzip
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from civic_link.evaluation.benchmark_review import revise, validate


def test_current_review_preserves_search_inputs_and_has_consistent_clarifications():
    path=ROOT/'tests/fixtures/week7_pre_review.jsonl.gz'
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        original=[json.loads(l) for l in stream]
    rows,changes=revise(original)
    assert len(changes)==21
    current=[json.loads(line) for line in (ROOT/'data/benchmark/questions.jsonl').open(encoding='utf-8')]
    assert rows==current
    audit=validate(current,original,ROOT/'data/corpus/civic.sqlite')
    assert audit['new_evidence_count']==590
    for before,after in zip(original,rows):
        for field in ('question_id','question','expected_action','evidence_requirements'):
            assert before[field]==after[field]
        assert 'generation_stage' not in after
        assert after['human_verified'] is False
        if after['expected_action']=='clarify':
            assert after['clarification']['required_slots']==after['action_rubric']['missing_slots']
    lookup={q['question_id']:q for q in rows}
    assert '온라인' not in lookup['SJ-N-032']['canonical_question']
    assert lookup['SJ-A-082']['acceptable_actions']==['clarify','answer']
    assert lookup['W7V2-BH016']['clarification']['slot_policy'].startswith('conditional')
