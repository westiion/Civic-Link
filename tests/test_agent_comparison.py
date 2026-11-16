import json
from copy import deepcopy
from pathlib import Path

import pytest

from civic_link.agent import RetrievalAgent
from civic_link.agent.experiment import CONDITIONS, load_queries, run_comparison, read_json, result_path, identity
from civic_link.agent.evaluation import covers, evaluate_comparison, validate_judgment


class Search:
    def __init__(self): self.calls=[]
    def run(self,args):
        self.calls.append(args)
        return dict(tool='search',status='ok',relations=[],evidence=[dict(id='C1',document_id='D',title='source',text='evidence',source='https://example.org')])


class Client:
    def complete(self,messages):
        system=json.loads(messages[0]['content'])
        self.allowed=system['출력 양식']['fields']['action']['allowed_values']
        return json.dumps(dict(checks=[],action='respond',arguments=dict(text='answer [C1]',evidence_ids=['C1']),reason='done'))


def factory(search):
    return lambda condition: RetrievalAgent(Client(),{'search':search,'graph':object()},allowed_tools=CONDITIONS[condition])


def question(qid='Q1',text='question'):
    return dict(question_id=qid,question=text,expected_action='answer',question_type='direct',
                evidence_requirements=[dict(evidence_id='E1',document_id='D',acceptable_chunk_ids=['C1'],start_char=0,end_char=8)])


def test_shared_initial_search_resume_and_changed_config(tmp_path):
    search=Search(); queries=[dict(query_id='Q1',question='question')]
    run_comparison(queries,tmp_path,list(CONDITIONS),{'model':'test'},search,factory(search),progress=lambda x:None)
    assert len(search.calls)==1
    rows=[read_json(result_path(tmp_path,c,'Q1')) for c in CONDITIONS]
    assert len({r['initial_sha256'] for r in rows})==1
    assert all(r['result']['tool_calls']==1 for r in rows)
    single=json.loads(rows[0]['result']['events'][1]['messages'][0]['content'])
    assert single['출력 양식']['fields']['action']['allowed_values']==['respond']
    assert single['출력 양식']['example']['action']=='respond'
    run_comparison(queries,tmp_path,list(CONDITIONS),{'model':'test'},search,factory(search),progress=lambda x:None)
    assert len(search.calls)==1
    with pytest.raises(ValueError,match='changed'):
        run_comparison(queries,tmp_path,list(CONDITIONS),{'model':'different'},search,factory(search))
    report=evaluate_comparison(tmp_path,{'Q1':question()})
    assert report['paired_questions']==1
    assert all(s['final_complete_rate']==1 for s in report['summaries'].values())
    assert all(s['content_pass_rate'] is None for s in report['summaries'].values())


def test_partial_runs_only_compare_paired_ids_and_resume(tmp_path):
    search=Search(); queries=[dict(query_id='Q1',question='question')]
    class Interrupted:
        def run(self,*a,**k): raise RuntimeError('interruption')
    def make(condition):
        return Interrupted() if condition=='iterative_search' else factory(search)(condition)
    with pytest.raises(RuntimeError):
        run_comparison(queries,tmp_path,list(CONDITIONS),{},search,make,progress=lambda x:None)
    report=evaluate_comparison(tmp_path,{'Q1':question()})
    assert report['paired_questions']==0 and report['missing']
    run_comparison(queries,tmp_path,list(CONDITIONS),{},search,factory(search),progress=lambda x:None)
    assert len(search.calls)==1 and evaluate_comparison(tmp_path,{'Q1':question()})['paired_questions']==1


def test_gold_rows_never_enter_generation_inputs(tmp_path):
    path=tmp_path/'queries.jsonl'
    path.write_text(json.dumps(dict(query_id='Q',question='q',gold_answer='leak')),encoding='utf-8')
    with pytest.raises(ValueError,match='only'):
        load_queries(path)


def test_partial_chunk_overlap_is_not_full_evidence_credit():
    req=dict(document_id='D',start_char=5,end_char=15,acceptable_chunk_ids=['C'])
    a=dict(id='span1',document_id='D',start_char=0,end_char=10,chunk_ids=['C'])
    b=dict(id='span2',document_id='D',start_char=10,end_char=20,chunk_ids=['C'])
    assert not covers([a],req)
    assert covers([a,b],req)
    assert not covers([{**a,'end_char':8},b],req)


def test_composite_requires_both_subtasks_and_response_bound_judgment(tmp_path):
    q=question();q['agentic']={'subtasks':[dict(subtask_id=n,expected_action='answer',evidence_ids=['E1']) for n in ('A','B')]}
    def verdict(n,ok):
        return dict(subtask_id=n,observed_action='answer',required_handling_ok=ok,factual_correct=True,
                    grounded_in_provided_passages=True,citations_supported=True,unsupported_claim=False,reason='checked')
    with pytest.raises(ValueError,match='omitted'):
        validate_judgment({'subtasks':[verdict('A',True)]},q)
    search=Search()
    run_comparison([dict(query_id='Q1',question='question')],tmp_path,['single_search'],{},search,factory(search),progress=lambda x:None)
    row=read_json(result_path(tmp_path,'single_search','Q1'))
    review=dict(response_sha256=row['response_sha256'],result_sha256=row['result_sha256'],judgment={'subtasks':[verdict('A',True),verdict('B',False)]})
    report=evaluate_comparison(tmp_path,{'Q1':q},{('single_search','Q1'):review})
    assert report['summaries']['single_search']['content_pass_rate']==0
    review['response_sha256']='other response'
    with pytest.raises(ValueError,match='does not belong'):
        evaluate_comparison(tmp_path,{'Q1':q},{('single_search','Q1'):review})


def test_evaluation_and_blinded_judge_clis_offline(tmp_path,monkeypatch):
    import evaluate_agent_comparison
    import judge_agent_comparison
    search=Search(); run=tmp_path/'run'
    run_comparison([dict(query_id='Q1',question='question')],run,list(CONDITIONS),{},search,factory(search),progress=lambda x:None)
    questions=tmp_path/'questions.jsonl'
    questions.write_text(json.dumps(question())+'\n',encoding='utf-8')
    seen=[]
    class Judge:
        last_usage={'total_tokens':20}
        def __init__(self,*a,**k): pass
        def complete(self,messages):
            data=json.loads(messages[1]['content'])
            assert 'condition' not in data and 'single_search' not in messages[1]['content']
            assert data['provided_passages'][0]['id']=='C1'
            seen.append(data)
            return json.dumps({'subtasks':[dict(subtask_id='main',observed_action='answer',required_handling_ok=True,
                factual_correct=True,grounded_in_provided_passages=True,citations_supported=True,unsupported_claim=False,reason='matched')]})
    monkeypatch.setattr(judge_agent_comparison,'QwenClient',Judge)
    judgments=tmp_path/'judgments'
    args=['--run-dir',str(run),'--questions',str(questions),'--output-dir',str(judgments),
          '--model','test-judge','--endpoint','https://example.org/chat']
    assert judge_agent_comparison.main(args)==0
    assert judge_agent_comparison.main(args)==0
    assert len(seen)==3
    destination=tmp_path/'evaluation'
    assert evaluate_agent_comparison.main(['--run-dir',str(run),'--questions',str(questions),
        '--judgments',str(judgments),'--output-dir',str(destination)])==0
    report=read_json(destination/'report.json')
    assert all(v['content_pass_rate']==1 for v in report['summaries'].values())
    assert report['summaries']['iterative_graph']['subtask_handling']['answer']['required_handling_rate']==1
    assert (destination/'summary.csv').is_file() and (destination/'REPORT.md').is_file()


def test_trace_cli_separates_search_rank_context_loss_and_content_change(tmp_path):
    import csv
    import analyze_agent_trace
    from civic_link.agent.controller import AgentConfig
    from civic_link.agent.experiment import atomic_json, file_hash

    class FollowupSearch:
        def run(self, args):
            eid = 'C1' if args['query'] == 'question' else 'C2'
            evidence = dict(id=eid, document_id='D', title='source', text='근거' * 300, source='https://example.org')
            return dict(tool='search', status='ok', relations=[], evidence=[evidence],
                        metadata={'candidate_ids': [eid, 'C2'] if eid == 'C1' else [eid]})

    class FollowupClient:
        def complete(self, messages):
            payload = json.loads(messages[1]['content'])
            state = payload['현재 실행 상태']
            if state['tool_calls_completed'] == 1:
                return json.dumps(dict(checks=[], action='search', arguments={'query': 'followup'}, reason='missing'))
            return json.dumps(dict(checks=[], action='respond',
                                   arguments={'text': 'second answer', 'evidence_ids': ['C2']}, reason='done'))

    def make(condition):
        client = Client() if condition == 'single_search' else FollowupClient()
        return RetrievalAgent(client, {'search': search, 'graph': object()}, allowed_tools=CONDITIONS[condition],
                              config=AgentConfig(max_evidence_chars=900))

    run = tmp_path / 'run'
    search = FollowupSearch()
    conditions = ['single_search', 'iterative_search']
    run_comparison([dict(query_id='Q1', question='question')], run, conditions, {}, search, make,
                   progress=lambda _: None)
    q = question()
    q['evidence_requirements'].append(dict(evidence_id='E2', acceptable_chunk_ids=['C2']))
    questions = tmp_path / 'questions.jsonl'
    questions.write_text(json.dumps(q) + '\n', encoding='utf-8')
    judgments = tmp_path / 'judgments'
    atomic_json(judgments / 'config.json', dict(questions_sha256=file_hash(questions),
                                               generation_config_sha256=file_hash(run / 'config.json')))
    for condition in conditions:
        row = read_json(result_path(run, condition, 'Q1'))
        enough = condition == 'single_search'
        atomic_json(judgments / f'{condition}.json', dict(
            query_id='Q1', condition=condition, result_sha256=row['result_sha256'],
            response_sha256=row['response_sha256'], judgment={'subtasks': [dict(
                subtask_id='main', observed_action='answer', required_handling_ok=enough,
                factual_correct=True, grounded_in_provided_passages=True, citations_supported=True,
                unsupported_claim=False, reason='reviewed')]}))

    output = tmp_path / 'trace'
    assert analyze_agent_trace.main(['--run-dir', str(run), '--questions', str(questions),
                                     '--judgments', str(judgments), '--output-dir', str(output)]) == 0

    def rows(name):
        with (output / name).open(encoding='utf-8-sig', newline='') as stream:
            return list(csv.DictReader(stream))

    single, followup = rows('questions.csv')
    assert single['first_search_group'] == 'candidate_only_complete'
    assert json.loads(single['candidate_accepted_chunk_ranks'])['E2'] == 2
    assert followup['retrieved_complete'] == 'True'
    assert json.loads(followup['final_missing_ids']) == ['E1']
    first, second, third = rows('turns.csv')
    assert first['review_flag'] == 'respond_only_with_missing_coverage'
    assert json.loads(second['newly_covered_requirement_ids']) == ['E2']
    assert json.loads(third['stored_but_not_covered_ids']) == ['E1']
    change, = rows('content_changes.csv')
    assert change['change'] == 'regressed'
    assert json.loads(change['after_judge_failures']) == {'main': ['required_handling_ok']}


def test_parallel_judge_limits_requests_isolates_usage_and_retries_only_failures(tmp_path,monkeypatch):
    import threading
    import time
    import judge_agent_comparison as cli
    from civic_link.agent.client import ModelError
    search=Search(); run=tmp_path/'run'
    run_comparison([dict(query_id='Q1',question='question')],run,list(CONDITIONS),{},search,factory(search),progress=lambda x:None)
    questions=tmp_path/'questions.jsonl'
    questions.write_text(json.dumps(question())+'\n',encoding='utf-8')
    lock=threading.Lock(); barrier=threading.Barrier(2)
    counts={'calls':0,'active':0,'peak':0}
    class Judge:
        def __init__(self,*args,**kwargs): self.last_usage=None
        def complete(self,messages):
            with lock:
                counts['calls']+=1
                number=counts['calls']
                counts['active']+=1
                counts['peak']=max(counts['peak'],counts['active'])
            try:
                if number<=2: barrier.wait(timeout=5)
                time.sleep(.02)
                if number==1: raise ModelError('temporary failure')
                self.last_usage={'total_tokens':number}
                return json.dumps({'subtasks':[dict(subtask_id='main',observed_action='answer',
                    required_handling_ok=True,factual_correct=True,grounded_in_provided_passages=True,
                    citations_supported=True,unsupported_claim=False,reason=str(number))]})
            finally:
                with lock: counts['active']-=1
    monkeypatch.setattr(cli,'QwenClient',Judge)
    output=tmp_path/'judgments'
    args=['--run-dir',str(run),'--questions',str(questions),'--output-dir',str(output),
          '--model','test','--endpoint','https://example.org/chat','--workers','2']
    assert cli.main(args)==0
    saved={p:p.read_bytes() for p in output.glob('*.json') if p.name!='config.json'}
    assert len(saved)==2 and len(list((output/'errors').glob('*.json')))==1
    assert counts=={'calls':3,'active':0,'peak':2}
    assert cli.main(args)==0
    assert counts['calls']==4 and all(p.read_bytes()==data for p,data in saved.items())
    rows=[read_json(p) for p in output.glob('*.json') if p.name!='config.json']
    assert len(rows)==3
    assert all(r['usage']['total_tokens']==int(r['judgment']['subtasks'][0]['reason']) for r in rows)
    assert not (output/'.running').exists()
