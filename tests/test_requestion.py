import copy
import json

import pytest

from civic_link.agent import AgentConfig, InteractiveAgent, RetrievalAgent, SessionStore
from civic_link.agent.session import AlreadyAnswered, SessionExists, SessionExpired, SessionNotFound


def reply_json(action, arguments, checks=None):
    return json.dumps({'checks': checks or [], 'action': action, 'arguments': arguments, 'reason': '테스트'},
                      ensure_ascii=False)


def respond(text='답변', ids=('c1',)):
    return reply_json('respond', {'text': text, 'evidence_ids': list(ids)})


def gate_json(ask, question='인터넷인가요, 무인발급기인가요?'):
    fact = {'fact': '신청 채널', 'blocks_core_answer': ask, 'changes_answer': '결론이 달라짐'}
    return json.dumps({'core_conclusion': '가능 여부', 'missing_facts': [fact], 'can_answer_now': not ask,
                       'question': question if ask else ''}, ensure_ascii=False)


def evidence(eid):
    return {'id': eid, 'title': '자료 제목', 'text': '실제 제출서류 근거', 'source': 'https://example.org/source'}


def search_result(*ids):
    return {'tool': 'search', 'status': 'ok', 'evidence': [evidence(i) for i in ids], 'relations': []}


class Scripted:
    def __init__(self, replies):
        self.replies, self.last_usage, self.sent = list(replies), None, []

    def complete(self, messages):
        self.sent.append(messages)
        value = self.replies.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class Tool:
    def __init__(self, results=()):
        self.results, self.calls = list(results), []

    def run(self, arguments):
        self.calls.append(arguments)
        return copy.deepcopy(self.results.pop(0))


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(tmp_path, replies, results=(), *, clock=None, ttl=300, **options):
    store = SessionStore(tmp_path / 'sessions.sqlite', ttl_seconds=ttl, clock=clock or Clock())
    client, search = Scripted(replies), Tool(results)
    agent = InteractiveAgent(client, {'search': search, 'graph': Tool()}, scope={}, tool_descriptions={},
                             allowed_tools=('search',), store=store, **options)
    return agent, client, search, store


def rows(store):
    import sqlite3
    with sqlite3.connect(store.path) as db:
        return db.execute('SELECT session_id, state, status FROM sessions').fetchall()


# --- the gate -------------------------------------------------------------------------------------------
def test_gate_pauses_and_a_new_process_resumes_with_a_follow_up_search(tmp_path):
    agent, client, _, store = make(tmp_path, [gate_json(True)], use_gate=True)
    waiting = agent.start('이 화면에서 발급되나요?', session_id='u1', initial_result=search_result('c1'))
    assert waiting['status'] == 'waiting_for_user' and waiting['question'] == '인터넷인가요, 무인발급기인가요?'
    assert '개인정보' in waiting['message'] and waiting['expires_at'] == 1300.0
    assert [r[0] for r in rows(store)] == ['u1']

    # new store object and new agent: only the SQLite file carries the state
    agent2, client2, search2, _ = make(tmp_path, [respond('무인발급기는 본인만 가능합니다', ('c2',))],
                                       [search_result('c2')], use_gate=True)
    done = agent2.reply('u1', '무인발급기예요')
    assert done['status'] == 'responded' and {e['id'] for e in done['evidence']} == {'c1', 'c2'}
    assert search2.calls == [{'query': '이 화면에서 발급되나요? 무인발급기예요'}]
    sent = client2.sent[0][1]['content']
    assert '무인발급기예요' in sent and '대화 이력' in sent
    assert done['tool_calls'] == 2 and rows(store) == []


def test_gate_that_finds_nothing_missing_goes_straight_to_the_answer(tmp_path):
    agent, _, _, store = make(tmp_path, [gate_json(False), respond()], use_gate=True)
    done = agent.start('질문', session_id='u2', initial_result=search_result('c1'))
    assert done['status'] == 'responded' and rows(store) == []


def test_unusable_gate_output_does_not_block_the_answer(tmp_path):
    agent, _, _, _ = make(tmp_path, ['not json', respond()], use_gate=True)
    assert agent.start('질문', session_id='u3', initial_result=search_result('c1'))['status'] == 'responded'


def test_a_gate_question_that_asks_for_an_identifier_is_never_shown(tmp_path):
    agent, _, _, store = make(tmp_path, [gate_json(True, '주민등록번호를 알려 주세요'), respond()], use_gate=True)
    done = agent.start('질문', session_id='u4', initial_result=search_result('c1'))
    assert done['status'] == 'responded' and rows(store) == []
    assert any(e['stage'] == 'gate_question_rejected' for e in done['events'])


# --- ask_user as a model action ----------------------------------------------------------------------------
def test_model_can_choose_ask_user_and_is_refused_a_second_question(tmp_path):
    agent, _, _, _ = make(tmp_path, [reply_json('ask_user', {'question': '어떤 서류인가요?'})], allow_ask=True)
    waiting = agent.start('서류 알려줘', session_id='a1', initial_result=search_result('c1'))
    assert waiting['status'] == 'waiting_for_user' and waiting['question'] == '어떤 서류인가요?'
    agent2, client2, _, _ = make(tmp_path, [reply_json('ask_user', {'question': '한 번 더 묻기'}), respond()],
                                 [search_result('c2')], allow_ask=True)
    done = agent2.reply('a1', '등본이요')
    assert done['status'] == 'responded'
    assert any(e['stage'] == 'request_rejected' for e in done['events'])
    assert 'ask_user' not in client2.sent[0][0]['content']
    assert 'ask_user' not in json.loads(client2.sent[0][1]['content'])['실행 규칙']['allowed_actions']


def test_ask_user_question_asking_for_an_identifier_is_rejected_then_corrected(tmp_path):
    agent, _, _, _ = make(tmp_path, [reply_json('ask_user', {'question': '주민등록번호가 뭔가요?'}),
                                     reply_json('ask_user', {'question': '어떤 서류인가요?'})], allow_ask=True)
    waiting = agent.start('질문', session_id='a2', initial_result=search_result('c1'))
    assert waiting['question'] == '어떤 서류인가요?'
    assert any(e['stage'] == 'request_rejected' for e in waiting['events'])


def test_ask_user_is_not_available_unless_enabled(tmp_path):
    client = Scripted([reply_json('ask_user', {'question': '질문'}), respond()])
    agent = RetrievalAgent(client, {'search': Tool([search_result('c1')]), 'graph': Tool()}, scope={},
                           tool_descriptions={}, allowed_tools=('search',))
    out = agent.run('질문', initial_result=search_result('c1'))
    assert out['status'] == 'responded' and any(e['stage'] == 'request_rejected' for e in out['events'])
    assert 'ask_user' not in client.sent[0][0]['content']


# --- one answer, expiry, privacy ---------------------------------------------------------------------------
def test_only_one_answer_is_accepted(tmp_path):
    agent, _, _, store = make(tmp_path, [gate_json(True)], use_gate=True)
    agent.start('질문', session_id='o1', initial_result=search_result('c1'))
    store.claim('o1')
    with pytest.raises(AlreadyAnswered):
        store.claim('o1')


def test_a_finished_session_cannot_be_answered_again(tmp_path):
    agent, _, _, _ = make(tmp_path, [gate_json(True)], use_gate=True)
    agent.start('질문', session_id='o2', initial_result=search_result('c1'))
    agent2, _, _, _ = make(tmp_path, [respond()], [search_result('c2')], use_gate=True)
    agent2.reply('o2', '무인발급기예요')
    with pytest.raises(SessionNotFound):
        agent2.reply('o2', '다시 답할게요')


def test_expired_session_is_rejected_and_deleted(tmp_path):
    clock = Clock()
    agent, _, _, store = make(tmp_path, [gate_json(True)], clock=clock, ttl=300, use_gate=True)
    agent.start('질문', session_id='e1', initial_result=search_result('c1'))
    clock.now += 301
    with pytest.raises(SessionExpired):
        agent.reply('e1', '무인발급기예요')
    assert rows(store) == []


def test_purge_removes_expired_sessions_and_their_evidence(tmp_path):
    clock = Clock()
    agent, _, _, store = make(tmp_path, [gate_json(True)], clock=clock, use_gate=True)
    agent.start('질문', session_id='e2', initial_result=search_result('c1'))
    clock.now += 301
    assert store.purge_expired() == 1 and rows(store) == []


def test_duplicate_session_id_is_refused(tmp_path):
    agent, _, _, store = make(tmp_path, [gate_json(True)], use_gate=True)
    agent.start('질문', session_id='d1', initial_result=search_result('c1'))
    with pytest.raises(SessionExists):
        store.put_waiting('d1', {}, '질문')


def test_a_failure_while_resuming_gives_the_answer_back(tmp_path):
    agent, _, _, store = make(tmp_path, [gate_json(True)], use_gate=True)
    agent.start('질문', session_id='f1', initial_result=search_result('c1'))
    broken, _, _, _ = make(tmp_path, [RuntimeError('unexpected')], [search_result('c2')], use_gate=True)
    with pytest.raises(RuntimeError):
        broken.reply('f1', '무인발급기예요')
    assert [r[2] for r in rows(store)] == ['waiting']


def test_personal_identifiers_in_the_answer_are_masked_and_never_stored_or_sent(tmp_path):
    agent, _, _, store = make(tmp_path, [gate_json(True)], use_gate=True)
    agent.start('질문', session_id='p1', initial_result=search_result('c1'))
    assert '901231' not in rows(store)[0][1]
    agent2, client2, search2, _ = make(tmp_path, [respond('답변', ('c2',))], [search_result('c2')], use_gate=True)
    done = agent2.reply('p1', '무인발급기예요 주민번호 901231-1234567 입니다')
    assert '901231' not in json.dumps(client2.sent, ensure_ascii=False)
    assert '901231' not in json.dumps(search2.calls, ensure_ascii=False)
    assert '901231' not in json.dumps(done['events'], ensure_ascii=False)
    clar = next(e for e in done['events'] if e['stage'] == 'clarification')
    assert clar['personal_info_masked'] == 1


def test_personal_identifiers_in_the_original_question_are_masked_too(tmp_path):
    agent, client, _, _ = make(tmp_path, [respond()])
    agent.start('제 번호는 010-1234-5678 이에요. 발급되나요?', session_id='p2', initial_result=search_result('c1'))
    assert '5678' not in json.dumps(client.sent, ensure_ascii=False)


# --- "I don't know" ---------------------------------------------------------------------------------------
def test_unknown_answer_ends_with_a_cannot_answer_message_without_another_model_call(tmp_path):
    agent, _, _, _ = make(tmp_path, [gate_json(True)], use_gate=True)
    agent.start('질문', session_id='k1', initial_result=search_result('c1'))
    agent2, client2, search2, store = make(tmp_path, [], [], use_gate=True)
    done = agent2.reply('k1', '모르겠어요')
    assert done['status'] == 'needs_information'
    assert '정확히 안내드리기 어렵습니다' in done['text'] and '인터넷인가요' in done['text']
    assert client2.sent == [] and search2.calls == [] and rows(store) == []


def test_a_long_answer_that_mentions_not_knowing_is_still_used(tmp_path):
    agent, _, _, _ = make(tmp_path, [gate_json(True)], use_gate=True)
    agent.start('질문', session_id='k2', initial_result=search_result('c1'))
    agent2, client2, _, _ = make(tmp_path, [respond()], [search_result('c2')], use_gate=True)
    done = agent2.reply('k2', '인터넷 등기 열람이요. 오늘이 공휴일인지는 모르겠어요')
    assert done['status'] == 'responded' and len(client2.sent) == 1


def test_budgets_keep_counting_across_the_pause(tmp_path):
    agent, _, _, _ = make(tmp_path, [gate_json(True)], use_gate=True)
    waiting = agent.start('질문', session_id='b1', initial_result=search_result('c1'))
    assert waiting['model_calls'] == 0 and waiting['tool_calls'] == 1
    agent2, _, _, _ = make(tmp_path, [respond()], [search_result('c2')], use_gate=True)
    done = agent2.reply('b1', '무인발급기예요')
    assert done['model_calls'] == 1 and done['tool_calls'] == 2


def test_requires_the_search_tool():
    with pytest.raises(ValueError):
        InteractiveAgent(Scripted([]), {'search': Tool(), 'graph': Tool()}, scope={}, tool_descriptions={},
                         allowed_tools=('graph',), store=None, use_gate=True)


# --- rule-based helpers -------------------------------------------------------------------------------------
@pytest.mark.parametrize('text', ['901231-1234567', '9012311234567', '010-1234-5678', '01012345678',
                                  '02-123-4567', '1234-5678-9012-3456', '1234 5678 9012 3456'])
def test_mask_personal_removes_identifiers(text):
    from civic_link.agent.clarify import MASK, mask_personal
    masked, count = mask_personal(f'제 번호는 {text} 입니다')
    assert count == 1 and MASK in masked and not any(ch.isdigit() for ch in masked)


@pytest.mark.parametrize('text', ['2024년 3월 신청', '수수료 400원', '제9조제1항', '1년에 12번'])
def test_mask_personal_leaves_ordinary_numbers(text):
    from civic_link.agent.clarify import mask_personal
    assert mask_personal(text) == (text, 0)


@pytest.mark.parametrize('question,bad', [('주민등록번호를 알려 주세요', True), ('카드 번호가 뭔가요?', True),
                                          ('계좌번호를 입력해 주세요', True), ('어떤 서류인가요?', False),
                                          ('인터넷인가요, 무인발급기인가요?', False)])
def test_question_violations(question, bad):
    from civic_link.agent.clarify import question_violations
    assert bool(question_violations(question)) is bad


@pytest.mark.parametrize('text,unknown', [('모르겠어요', True), ('잘 몰라요', True), ('기억이 안 나요', True),
                                          ('없어요', False), ('등본이요', False),
                                          ('인터넷 등기 열람이요. 공휴일인지는 모르겠어요', False)])
def test_is_unknown_reply(text, unknown):
    from civic_link.agent.clarify import is_unknown_reply
    assert is_unknown_reply(text) is unknown
