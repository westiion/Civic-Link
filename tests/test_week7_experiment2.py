import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_week7_experiment2 import ordered_result
from compare_week7_experiment2 import cached_bge_order
from explain_week7_cases import failure_stage, priority


def source():
    return dict(query_id='q',candidates=[dict(chunk_id='a',fusion_rank=2),dict(chunk_id='b',fusion_rank=1),dict(chunk_id='excluded',fusion_rank=None)])


def test_full_pool_is_preserved_and_excluded_graph_paths_stay_excluded():
    chunks={cid:dict(document_id='doc') for cid in ('a','b','excluded')}
    result=ordered_result(source(),[1.,2.],chunks)
    assert result['ranked_chunk_ids']==['a','b']
    assert [h['chunk_id'] for h in result['hits']]==['a','b']
    assert {x['chunk_id']:x['score'] for x in result['bge_scores']}=={'b':1.,'a':2.}


def test_exact_ties_preserve_coverage_order_and_scores_are_not_blended():
    chunks={cid:dict(document_id='doc') for cid in ('a','b')}
    assert ordered_result(source(),[2.,2.],chunks)['ranked_chunk_ids']==['b','a']
    assert ordered_result(source(),[-100.,-99.],chunks)['ranked_chunk_ids']==['a','b']


@pytest.mark.parametrize('scores',[[float('nan'),1.],[float('inf'),1.],[1.]])
def test_bad_or_incomplete_model_output_is_rejected(scores):
    with pytest.raises(ValueError):ordered_result(source(),scores,{'a':{},'b':{}})


def test_unknown_candidate_is_rejected():
    with pytest.raises(ValueError):ordered_result(source(),[1.,2.],{'a':{}})


def test_formula_gate_is_applied_before_bge_and_omitted_tail_stays_out():
    scores={'a':1.,'b':2.,'c':100.}
    assert cached_bge_order(['a','b','c'],scores,2)==['b','a']
    assert cached_bge_order(['c','a','b'],scores,2)==['c','a']
    assert cached_bge_order(['a','b','c'],scores)==cached_bge_order(['c','a','b'],scores)


def test_only_exact_bge_ties_depend_on_previous_formula():
    scores={'a':1.,'b':1.,'c':2.}
    assert cached_bge_order(['a','b','c'],scores)==['c','a','b']
    assert cached_bge_order(['b','a','c'],scores)==['c','b','a']


@pytest.mark.parametrize('prior,scores,limit', [(['a','a'],{'a':1.},None),
    (['a','b'],{'a':1.},1),(['a'],{'a':float('nan')},None),(['a'],{'a':1.},0)])
def test_cached_comparisons_reject_incomplete_or_invalid_inputs(prior,scores,limit):
    with pytest.raises(ValueError):cached_bge_order(prior,scores,limit)


def test_failure_stages_separate_absent_candidates_from_low_ranked_evidence():
    assert failure_stage([1,10])=='complete'
    assert failure_stage([1,11])=='ranking_only'
    assert failure_stage([1,None])=='candidate_missing'
    assert failure_stage([None,11])=='mixed'


def test_action_gaps_are_prioritized_without_inventing_failure_predictions():
    assert priority({'expected_action':'abstain'}, {})==1
    assert priority({'expected_action':'clarify'}, {})==2
    ranks={'h3':[1,2],'h3_bge':[1,30],'coverage':[1,3],'bge':[1,30]}
    assert priority({'expected_action':'answer'},ranks)==3


def test_full_case_appendix_is_a_partition_of_current_608_questions():
    import json,re
    root=Path(__file__).resolve().parents[1]
    folder=root/'week7/docs/experiment2_cases'
    if not folder.exists():pytest.skip('case appendix not generated')
    questions=[json.loads(l) for l in (root/'data/benchmark/questions.jsonl').read_text(encoding='utf-8').splitlines()]
    ids=[qid for p in sorted(folder.glob('*.md')) for qid in re.findall(r'^## (\S+) · ',p.read_text(encoding='utf-8'),flags=re.M)]
    assert len(ids)==len(set(ids))==608
    assert set(ids)=={q['question_id'] for q in questions}
