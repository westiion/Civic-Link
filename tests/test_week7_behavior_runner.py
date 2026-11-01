import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_week7_behavior import messages, parse
from summarize_week7_behavior import normalize


def test_prompt_contains_only_question_and_ordered_full_passages():
    chunks={'a':{'title':'first','text':'A'*1500,'gold_answer':'SECRET'},
            'b':{'title':'second','text':'second text','expected_action':'abstain'}}
    payload=messages('Which document?', ['b','a'],chunks)
    user=payload[1]['content']
    assert user.index('second text')<user.index('A'*1500)
    assert 'SECRET' not in json.dumps(payload)
    assert 'expected_action' not in json.dumps(payload)
    assert len(payload)==2 and payload[0]['role']=='system'


def test_top10_is_the_only_passage_input():
    chunks={str(i):{'title':f'title-{i}','text':f'body-{i}'} for i in range(12)}
    content=messages('q',list(chunks),chunks)[1]['content']
    assert 'body-9' in content and 'body-10' not in content and 'body-11' not in content


def test_parse_preserves_actual_question_and_citation_output():
    value={'action':'clarify','response':'어느 지역인가요?','questions':['지역?'],'cited_sources':[2]}
    assert parse(json.dumps(value))==value


@pytest.mark.parametrize('patch',[{'action':'unknown'},{'response':''},{'questions':'지역?'},{'cited_sources':[11]},{'cited_sources':[True]}])
def test_invalid_response_is_never_silently_abstain(patch):
    value={'action':'answer','response':'안내','questions':[],'cited_sources':[1]}
    with pytest.raises(ValueError):parse(json.dumps(value|patch))


def test_citation_error_is_separate_from_valid_declared_action():
    value={'action':'answer','response':'안내','questions':[],'cited_sources':[11]}
    result=normalize({'raw_response':json.dumps(value),'parse_error':'invalid citations'})
    assert result['predicted_action']=='answer'
    assert result['citation_syntax_valid'] is False
    assert result['action_parse_error'] is None


def test_truncated_json_is_missing_action_not_abstention():
    result=normalize({'raw_response':'{"action":"abstain",'})
    assert result['predicted_action'] is None
    assert result['action_parse_error']

from judge_week7_responses import payload_for, parse as parse_judgment


def test_posthoc_payload_is_explicitly_separate_and_does_not_change_response():
    q={'question':'질문','expected_action':'answer','gold_answer':'정답','gold_rubric':{'must':['핵심']},'evidence_requirements':[{'quote':'근거'}]}
    response={'predicted_action':'answer','response':'고정 응답','clarification_questions':[],'cited_sources':[1]}
    before=json.dumps(response,sort_keys=True)
    payload=payload_for(q,response,[{'number':1,'text':'제공 원문'}],'Qwen')
    data=json.loads(payload['messages'][1]['content'])
    assert data['expected_response']=='정답' and data['actual_response']=='고정 응답'
    assert data['reference_evidence'][0]['quote']=='근거'
    assert json.dumps(response,sort_keys=True)==before
    assert payload['temperature']==0 and payload['seed']==7


def judgment_value():
    return dict(required_handling_ok=True,unsupported_claim=False,action_response_consistent=True,
        clarification_sufficient=None,factual_correct=True,grounded_in_provided_passages=True,
        reason='근거와 일치',response_excerpt='고정 응답',failed_requirements=[])


def test_judge_false_is_not_confused_with_missing():
    v=judgment_value();v['required_handling_ok']=False
    assert parse_judgment(json.dumps(v),'answer')['required_handling_ok'] is False


@pytest.mark.parametrize('patch',[{'factual_correct':'true'},{'required_handling_ok':1},{'clarification_sufficient':True},{'response_excerpt':''}])
def test_invalid_posthoc_judgment_is_not_a_pass(patch):
    with pytest.raises(ValueError):parse_judgment(json.dumps(judgment_value()|patch),'answer')


def test_clarification_requires_a_measured_policy_verdict():
    with pytest.raises(ValueError):parse_judgment(json.dumps(judgment_value()),'clarify')

from summarize_week7_behavior import content_metrics


def test_unmeasured_content_is_not_reported_as_zero_quality():
    result=content_metrics([({'expected_action':'answer'},None)])
    assert result['measured']==0 and result['missing']==1
    assert result['content_pass'] is None and result['content_pass_rate'] is None


def test_reference_correctness_and_retrieved_grounding_are_separate():
    v=judgment_value();v['grounded_in_provided_passages']=False
    result=content_metrics([({},dict(judgment=v,excerpt_is_verbatim=True))])
    assert result['content_pass']==1
    assert result['grounded_in_provided_passages']==dict(measured=1,true=0,rate=0)


def test_unsupported_extra_claim_prevents_content_pass_even_if_required_answer_present():
    v=judgment_value();v['unsupported_claim']=True
    result=content_metrics([({},dict(judgment=v,excerpt_is_verbatim=False))])
    assert result['content_pass']==0 and result['unsupported_claim']==1
    assert result['nonverbatim_excerpts']==1
    assert result['verbatim_excerpt_subset']['measured']==0

from summarize_week7_behavior import normalize_judgment


def test_missing_slot_verdict_does_not_erase_other_explicit_content_verdicts():
    value=judgment_value();value['required_handling_ok']=False
    row=normalize_judgment({'raw_judgment':json.dumps(value),'judgment':None,'parse_error':'missing clarification result'},'clarify')
    assert row['judgment']['required_handling_ok'] is False
    assert row['judgment']['clarification_sufficient'] is None
    assert row['field_warnings']==['missing_clarification_verdict']
    metrics=content_metrics([({'expected_action':'clarify'},dict(judgment=row['judgment'],excerpt_is_verbatim=True))])
    assert metrics['measured']==1 and metrics['content_pass']==0
    assert metrics['clarification_sufficient']['measured']==0


def test_irrelevant_slot_verdict_is_not_counted_as_clarify_evidence():
    value=judgment_value();value['clarification_sufficient']=True
    row=normalize_judgment({'raw_judgment':json.dumps(value)},'answer')
    metrics=content_metrics([({'expected_action':'answer'},dict(judgment=row['judgment'],excerpt_is_verbatim=True))])
    assert row['field_warnings']==['irrelevant_clarification_verdict']
    assert metrics['clarification_sufficient']['measured']==0
