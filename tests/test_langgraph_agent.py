import copy
import json

import pytest

pytest.importorskip('langgraph')

from civic_link.agent import AgentConfig, RetrievalAgent
from civic_link.agent.client import ModelError
from civic_link.agent.langgraph_agent import LangGraphAgent


def reply(action, arguments, checks=None):
    return json.dumps({'checks': checks or [], 'action': action, 'arguments': arguments, 'reason': '테스트'},
                      ensure_ascii=False)


def evidence(eid='c1'):
    return {'id': eid, 'title': '자료 제목', 'text': '실제 제출서류 근거', 'source': 'https://example.org/source'}


def search_result(*ids):
    return {'tool': 'search', 'status': 'ok', 'evidence': [evidence(i) for i in ids], 'relations': []}


class Scripted:
    def __init__(self, replies):
        self.replies, self.last_usage = list(replies), None

    def complete(self, messages):
        value = self.replies.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class Tool:
    def __init__(self, results=()):
        self.results = list(results)

    def run(self, arguments):
        return copy.deepcopy(self.results.pop(0))


def build(cls, replies, results, **config):
    return cls(Scripted(replies), {'search': Tool(results), 'graph': Tool()}, scope={}, tool_descriptions={},
               config=AgentConfig(**config) if config else AgentConfig(), allowed_tools=('search',))


def strip(result):
    result = copy.deepcopy(result)
    for event in result['events']:
        event.pop('seconds', None)
    return result


SCENARIOS = {
    'respond_after_search': ([reply('search', {'query': '다른 검색어'}),
                              reply('respond', {'text': '답변', 'evidence_ids': ['c1']})],
                             [search_result('c1'), search_result('c1')], {}),
    'invalid_twice': (['not json', 'still not json'], [search_result('c1')], {}),
    'invalid_then_valid': (['not json', reply('respond', {'text': '답변', 'evidence_ids': ['c1']})],
                           [search_result('c1')], {}),
    'budget_exhausted': ([reply('search', {'query': 'a'}), reply('search', {'query': 'b'})],
                         [search_result('c1')] * 3, {'max_model_calls': 2}),
    'model_error': ([ModelError('down')], [search_result('c1')], {}),
}


@pytest.mark.parametrize('name', sorted(SCENARIOS))
def test_same_result_as_original_loop(name):
    replies, results, config = SCENARIOS[name]
    out = {}
    for label, cls in (('original', RetrievalAgent), ('langgraph', LangGraphAgent)):
        agent = build(cls, replies, results, **config)
        out[label] = strip(agent.run('원래 질문', initial_result=copy.deepcopy(results[0])))
    assert out['langgraph'] == out['original']
    assert out['original']['status'] in ('responded', 'invalid_output', 'budget_exhausted', 'model_error')


def test_checkpointer_keeps_final_state(tmp_path):
    from langgraph.checkpoint.sqlite import SqliteSaver
    replies, results, _ = SCENARIOS['respond_after_search']
    with SqliteSaver.from_conn_string(str(tmp_path / 'agent.sqlite')) as saver:
        agent = build(LangGraphAgent, replies, results)
        agent.graph = agent._build(saver)
        result = agent.run('원래 질문', initial_result=copy.deepcopy(results[0]), thread_id='t1')
        snapshot = agent.graph.get_state({'configurable': {'thread_id': 't1'}})
    assert result['status'] == 'responded'
    assert snapshot.values['result']['text'] == '답변'
    assert snapshot.next == ()


def gate_reply(ask):
    fact = {'fact': '신청 채널', 'blocks_core_answer': ask, 'changes_answer': '결론이 달라짐'}
    return json.dumps({'core_conclusion': '가능 여부', 'missing_facts': [fact], 'can_answer_now': not ask,
                       'question': '인터넷인가요, 무인발급기인가요?' if ask else ''}, ensure_ascii=False)


def gated(saver, replies, results):
    agent = LangGraphAgent(Scripted(replies), {'search': Tool(results), 'graph': Tool()}, scope={},
                           tool_descriptions={}, allowed_tools=('search',), checkpointer=saver, use_gate=True)
    return agent


def test_gate_pauses_then_resumes_in_a_new_process_with_a_follow_up_search(tmp_path):
    from langgraph.checkpoint.sqlite import SqliteSaver
    db = str(tmp_path / 'agent.sqlite')
    first = search_result('c1')
    with SqliteSaver.from_conn_string(db) as saver:
        agent = gated(saver, [gate_reply(True)], [])
        waiting = agent.run('이 화면에서 발급되나요?', initial_result=first, thread_id='u1')
    assert waiting == {'status': 'waiting_for_user', 'thread_id': 'u1', 'question': '인터넷인가요, 무인발급기인가요?'}
    # New saver and new agent object: only the SQLite file carries the state.
    follow_up = search_result('c2')
    with SqliteSaver.from_conn_string(db) as saver:
        agent = gated(saver, [reply('respond', {'text': '무인발급기는 본인만 가능합니다', 'evidence_ids': ['c2']})],
                      [follow_up])
        done = agent.resume('u1', '무인발급기예요')
    assert done['status'] == 'responded'
    assert {row['id'] for row in done['evidence']} == {'c1', 'c2'}
    stages = [e['stage'] for e in done['events']]
    assert stages.index('gate') < stages.index('clarification') < stages.index('qwen_input')
    sent = next(e for e in done['events'] if e['stage'] == 'qwen_input')['messages'][1]['content']
    assert '무인발급기예요' in sent and '대화 이력' in sent
    assert done['tool_calls'] == 2


def test_gate_that_finds_nothing_missing_continues_without_pausing(tmp_path):
    from langgraph.checkpoint.sqlite import SqliteSaver
    with SqliteSaver.from_conn_string(str(tmp_path / 'agent.sqlite')) as saver:
        agent = gated(saver, [gate_reply(False), reply('respond', {'text': '답변', 'evidence_ids': ['c1']})], [])
        done = agent.run('질문', initial_result=search_result('c1'), thread_id='u2')
    assert done['status'] == 'responded' and done['tool_calls'] == 1


def test_unusable_gate_output_does_not_block_the_answer(tmp_path):
    from langgraph.checkpoint.sqlite import SqliteSaver
    with SqliteSaver.from_conn_string(str(tmp_path / 'agent.sqlite')) as saver:
        agent = gated(saver, ['not json', reply('respond', {'text': '답변', 'evidence_ids': ['c1']})], [])
        done = agent.run('질문', initial_result=search_result('c1'), thread_id='u3')
    assert done['status'] == 'responded'


def test_gate_requires_checkpointer_and_thread():
    with pytest.raises(ValueError):
        LangGraphAgent(Scripted([]), {'search': Tool(), 'graph': Tool()}, scope={}, tool_descriptions={},
                       allowed_tools=('search',), use_gate=True)
