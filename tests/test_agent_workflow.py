import json

import pytest

from civic_link.agent import AgentConfig, RetrievalAgent
from civic_link.agent.client import ModelError, QwenClient
from civic_link.agent.protocol import InvalidDecision, parse_decision


def decision(action='search', arguments=None, checks=None):
    arguments = arguments or {'query': '첫 질문과 다른 새 검색어'}
    return json.dumps({'checks': checks or [], 'action': action, 'arguments': arguments, 'reason': '필요한 정보 확인'}, ensure_ascii=False)


def evidence(eid='c1', text='실제 제출서류 근거'):
    return {'id': eid, 'title': '자료 제목', 'text': text, 'source': 'https://example.org/source'}


def result(tool='search', rows=None, status='ok'):
    return {'tool': tool, 'status': status, 'evidence': rows if rows is not None else [evidence()], 'relations': []}


class ScriptedQwen:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.messages = []

    def complete(self, messages):
        self.messages.append(messages)
        return next(self.replies)


class Tool:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def run(self, arguments):
        self.calls.append(arguments)
        value = next(self.results)
        if isinstance(value, Exception):
            raise value
        return value


def make_agent(replies, search=None, graph=None, **config):
    client = ScriptedQwen(replies)
    tools = {'search': search or Tool([]), 'graph': graph or Tool([])}
    return RetrievalAgent(client, tools, config=AgentConfig(**config)), client, tools


def respond(ids=None):
    return decision('respond', {'text': '안내 문장', 'evidence_ids': ids or []})


def test_first_action_can_be_graph_and_sources_come_from_tools():
    graph = Tool([result('graph')])
    agent, client, tools = make_agent([
        decision('graph', {'target': '민법 제777조', 'relation': 'REFERENCES'}), respond(['c1'])], graph=graph)
    out = agent.run('  원문 질문을 유지  ')
    first = json.loads(client.messages[0][1]['content'])
    second = json.loads(client.messages[1][1]['content'])
    assert first['question'] == second['question'] == '  원문 질문을 유지  '
    assert first['checks'] == first['evidence'] == first['history'] == []
    assert first['latest_result'] is None
    assert tools['search'].calls == []
    assert graph.calls == [{'target': '민법 제777조', 'relation': 'REFERENCES'}]
    assert second['evidence'][0]['id'] == 'c1'
    assert out['status'] == 'responded'
    assert out['sources'][0]['source'] == 'https://example.org/source'


def test_rewritten_query_and_accumulated_checks_survive_observations():
    checks = [{'item': 'A의 서류', 'status': '확인', 'evidence_ids': ['a']}]
    search = Tool([result(rows=[evidence('a')]), result(rows=[evidence('b')])])
    agent, client, _ = make_agent([
        decision(arguments={'query': 'A 서류'}),
        decision(arguments={'query': 'B 서류'}, checks=checks), respond(['a', 'b'])], search=search)
    out = agent.run('A와 B를 비교해줘')
    last = json.loads(client.messages[-1][1]['content'])
    assert search.calls == [{'query': 'A 서류'}, {'query': 'B 서류'}]
    assert last['checks'] == checks
    assert {x['id'] for x in last['evidence']} == {'a', 'b'}
    assert len(out['sources']) == 2


@pytest.mark.parametrize('bad', [
    'not JSON',
    decision('shell', {'command': 'anything'}),
    decision('respond', {'text': '확정', 'evidence_ids': ['invented']}),
    decision('search', {'query': 'x', 'code': 'extra'}),
    '{"checks":[],"checks":[],"action":"search","arguments":{"query":"x"},"reason":"r"}',
])
def test_bad_model_request_is_rejected_before_any_tool(bad):
    agent, client, tools = make_agent([bad, respond()])
    out = agent.run('질문')
    assert out['status'] == 'responded'
    assert tools['search'].calls == tools['graph'].calls == []
    assert json.loads(client.messages[1][1]['content'])['controller_feedback']['type'] == 'invalid_request'


def test_identical_search_is_cached_but_still_consumes_model_turns():
    search = Tool([result()])
    agent, _, _ = make_agent([decision(), decision(), respond(['c1'])], search=search)
    out = agent.run('질문')
    assert len(search.calls) == out['tool_calls'] == 1
    assert out['model_calls'] == 3
    assert [x['cached'] for x in out['history']] == [False, True]


def test_tool_error_and_empty_results_are_distinct():
    search = Tool([RuntimeError('unavailable'), result(rows=[], status='no_results')])
    agent, client, _ = make_agent([decision(), decision(arguments={'query': '다른 검색'}), respond()], search=search)
    out = agent.run('질문')
    assert [x['status'] for x in out['history']] == ['error', 'no_results']
    assert json.loads(client.messages[1][1]['content'])['latest_result']['status'] == 'error'


def test_limits_stop_repeated_model_requests_without_unbounded_tools():
    search = Tool([result()])
    agent, client, _ = make_agent([decision()] * 10, search=search, max_tool_calls=1, max_model_calls=4)
    out = agent.run('질문')
    assert out['status'] == 'invalid_output'
    assert out['tool_calls'] == 1
    assert out['model_calls'] <= 4
    assert json.loads(client.messages[1][1]['content'])['state']['allowed_actions'] == ['respond']


def test_context_omission_is_explicit_and_unseen_evidence_cannot_be_cited():
    search = Tool([result(rows=[evidence(text='본문' * 1000)])])
    agent, client, _ = make_agent([decision(), respond(['c1']), respond()], search=search, max_evidence_chars=100)
    out = agent.run('질문')
    second = json.loads(client.messages[1][1]['content'])
    assert second['evidence'] == []
    assert second['context']['omitted_evidence_ids'] == ['c1']
    assert out['sources'] == []
    assert any(e['stage'] == 'request_rejected' for e in out['events'])


def test_new_evidence_is_not_starved_by_large_previously_supported_evidence():
    search = Tool([result(rows=[evidence('a', 'A' * 180)]), result(rows=[evidence('b', 'B')])])
    checks = [{'item': 'A 항목', 'status': '확인', 'evidence_ids': ['a']}]
    agent, client, _ = make_agent([
        decision(arguments={'query': 'A'}),
        decision(arguments={'query': 'B'}, checks=checks), respond(['b'])],
        search=search, max_evidence_chars=330)
    out = agent.run('A와 B 비교')
    final_input = json.loads(client.messages[-1][1]['content'])
    assert 'b' in final_input['context']['supplied_evidence_ids']
    assert 'a' in final_input['context']['omitted_evidence_ids']
    assert {row['id'] for row in out['evidence']} == {'a', 'b'}


def test_evidence_id_cannot_change_source_between_tools():
    changed = evidence()
    changed['source'] = 'https://example.org/different'
    agent, client, _ = make_agent([
        decision(), decision('graph', {'target': 'A', 'relation': 'REQUIRES'}), respond(['c1'])],
        search=Tool([result()]), graph=Tool([result('graph', [changed])]))
    out = agent.run('질문')
    assert out['history'][-1]['status'] == 'error'
    assert out['sources'][0]['source'] == 'https://example.org/source'


def test_query_specific_scores_stay_in_trace_not_reused_evidence():
    row = {**evidence(), 'rank': 1, 'metadata': {'bge_relevance': 5.0}}
    agent, client, _ = make_agent([decision(), respond(['c1'])], search=Tool([result(rows=[row])]))
    out = agent.run('질문')
    supplied = json.loads(client.messages[1][1]['content'])['evidence'][0]
    assert 'rank' not in supplied and 'metadata' not in supplied
    trace = next(e for e in out['events'] if e['stage'] == 'tool_result')
    assert trace['result']['evidence'][0]['metadata']['bge_relevance'] == 5.0


def test_check_requires_observed_provenance():
    raw = decision(checks=[{'item': '제출서류', 'status': '확인', 'evidence_ids': []}])
    with pytest.raises(InvalidDecision):
        parse_decision(raw, set())


def test_qwen_client_preserves_messages_and_does_not_force_json_mode():
    captured = {}
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps({'choices': [{'message': {'content': respond()}, 'finish_reason': 'stop'}]}).encode()
    def opener(request, timeout):
        captured.update(json.loads(request.data))
        return Response()
    client = QwenClient('https://example.org/chat/completions', 'configured-model', opener=opener)
    messages = [{'role': 'system', 'content': 'guide'}, {'role': 'user', 'content': '원문'}]
    assert json.loads(client.complete(messages))['action'] == 'respond'
    assert captured['messages'] == messages
    assert 'response_format' not in captured


def test_model_connection_failure_does_not_become_no_evidence_answer():
    class Broken:
        def complete(self, messages): raise ModelError('connection unavailable')
    agent = RetrievalAgent(Broken(), {'search': Tool([]), 'graph': Tool([])})
    out = agent.run('질문')
    assert out['status'] == 'model_error'
    assert out['tool_calls'] == 0


@pytest.mark.parametrize('choice', [None, [], {'message': None}, {'message': []}])
def test_malformed_chat_envelope_is_reported_as_model_error(choice):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps({'choices': [choice]}).encode()
    client = QwenClient('https://example.org/chat/completions', 'model', opener=lambda *a, **k: Response())
    with pytest.raises(ModelError):
        client.complete([])
