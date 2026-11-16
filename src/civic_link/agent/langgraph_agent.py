"""LangGraph version of the retrieval loop (prototype; the original controller is untouched).

Each block of ``RetrievalAgent.run`` becomes a node and the ``while`` loop becomes edges:

    init -> call_model -> decide -+-> tool -> call_model
                 ^                +-> finish (respond / invalid_output)
                 +---- retry ------+
    call_model -> finish (budget_exhausted / model_error)

With ``use_gate=True`` (needs a checkpointer) a clarification gate runs after the first search:

    init -> gate -+-> call_model                      (nothing blocking: continue as above)
                  +-> ask_user -> research -> call_model   (ask, pause, search again with the reply)

``run`` returns ``{'status': 'waiting_for_user', ...}`` when paused and ``resume`` continues the thread,
also from a new process that opens the same checkpoint database.

Prompt building, tool execution and result shaping reuse the original methods, so the two
implementations differ only in control flow. The graph state is JSON-friendly (sets become
lists) so a checkpointer can store it.
"""
from __future__ import annotations

import copy
import json
import time
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .client import ModelError
from .clarify import RESUME_RULE, check_gate
from .controller import RetrievalAgent
from .protocol import InvalidDecision, parse_decision

_SETS = ('seen_ids', 'supplied_ids')


class AgentState(TypedDict, total=False):
    question: str
    initial_result: Any
    agent: dict
    raw: str
    invalid: int
    route: str
    result: dict
    reply: str


def _hydrate(agent_state):
    state = dict(agent_state)
    for name in _SETS:
        state[name] = set(state[name])
    return state


def _dehydrate(state):
    out = dict(state)
    for name in _SETS:
        out[name] = sorted(out[name])
    return out


class LangGraphAgent(RetrievalAgent):
    """Same inputs and result shape as ``RetrievalAgent.run``."""

    def __init__(self, *args, checkpointer=None, use_gate=False, **kwargs):
        super().__init__(*args, **kwargs)
        if use_gate and checkpointer is None:
            raise ValueError('use_gate needs a checkpointer to pause and resume')
        if use_gate and 'search' not in self.allowed_tools:
            raise ValueError('use_gate needs the search tool for the follow-up search')
        self.use_gate = use_gate
        self.graph = self._build(checkpointer)

    def _messages(self, state):
        result = super()._messages(state)
        clarifications = state.get('clarifications')
        if not clarifications:
            return result
        system = json.loads(result[0]['content'])
        system['판단 지침'] = [*system['판단 지침'], RESUME_RULE]
        payload = json.loads(result[1]['content'])
        payload['대화 이력'] = clarifications
        result = [{'role': 'system', 'content': json.dumps(system, ensure_ascii=False, indent=2)},
                  {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False, indent=2)}]
        state['input_chars'] = sum(len(row['content']) for row in result)
        return result

    # nodes -----------------------------------------------------------------
    def _init(self, data):
        state = self._state(data['question'])
        initial = data.get('initial_result')
        if initial is not None:
            if self.config.max_tool_calls < 1:
                raise ValueError('initial search requires a tool budget')
            self._execute(state, 'search', {'query': data['question']}, initial)
        return {'agent': _dehydrate(state), 'invalid': 0, 'initial_result': None}

    def _gate(self, data):
        state = _hydrate(data['agent'])
        decision, raw = check_gate(self.client, state['question'], list(state['evidence'].values()))
        state['events'].append({'stage': 'gate', 'decision': decision, 'raw': raw})
        ask = decision is not None and not decision['can_answer_now']
        if ask:
            state['pending_question'] = decision['question']
        return {'agent': _dehydrate(state), 'route': 'ask' if ask else 'proceed'}

    def _ask_user(self, data):
        # Nothing before interrupt() may have side effects: the node starts over when the thread resumes.
        reply = interrupt({'question': data['agent']['pending_question']})
        return {'reply': reply}

    def _research(self, data):
        state = _hydrate(data['agent'])
        question, reply = state.pop('pending_question'), data['reply']
        state.setdefault('clarifications', []).append(
            {'assistant_clarification': question, 'user_reply': reply})
        query = f"{state['question']} {reply}"
        state['events'].append({'stage': 'clarification', 'question': question, 'reply': reply,
                                'research_query': query})
        if state['tool_calls'] < self.config.max_tool_calls:
            self._execute(state, 'search', {'query': query})
        return {'agent': _dehydrate(state), 'route': 'call_model'}

    def _call_model(self, data):
        state = _hydrate(data['agent'])
        if state['model_calls'] >= self.config.max_model_calls:
            return {'route': 'budget_exhausted'}
        request_messages = self._messages(state)
        state['model_calls'] += 1
        state['events'].append({'stage': 'qwen_input', 'messages': request_messages,
                                'input_chars': state['input_chars']})
        started = time.perf_counter()
        try:
            raw = self.client.complete(request_messages)
        except (ModelError, OSError) as exc:
            state['events'].append({'stage': 'model_error', 'error': str(exc)})
            return {'agent': _dehydrate(state), 'route': 'model_error'}
        state['events'].append({'stage': 'qwen_output', 'raw': raw, 'seconds': time.perf_counter() - started,
                                'usage': copy.deepcopy(getattr(self.client, 'last_usage', None))})
        return {'agent': _dehydrate(state), 'raw': raw, 'route': 'decide'}

    def _decide(self, data):
        state, invalid = _hydrate(data['agent']), data['invalid']
        try:
            decision = parse_decision(data['raw'], state['seen_ids'])
            if decision['action'] != 'respond' and decision['action'] not in self.allowed_tools:
                raise InvalidDecision('tool is disabled in this experiment condition')
            if decision['action'] == 'graph':
                arguments = decision['arguments']
                if arguments.get('cursor') and not any(
                    page['next_cursor'] == arguments['cursor']
                    and page['query'] == {k: v for k, v in arguments.items() if k != 'cursor'}
                    for page in state['graph_pages']):
                    raise InvalidDecision('use an issued next_cursor with unchanged query conditions')
                if hasattr(self.tools['graph'], 'validate_arguments'):
                    try:
                        self.tools['graph'].validate_arguments(arguments)
                    except ValueError as exc:
                        raise InvalidDecision(str(exc)) from exc
            if decision['action'] == 'respond' and (
                not set(decision['arguments']['evidence_ids']) <= state['supplied_ids']
            ):
                raise InvalidDecision('respond evidence_ids must be supplied in the current input')
            if decision['action'] != 'respond' and (
                state['tool_calls'] >= self.config.max_tool_calls
                or state['model_calls'] >= self.config.max_model_calls
            ):
                raise InvalidDecision('tool budget exhausted; choose respond using available information')
        except InvalidDecision as exc:
            invalid += 1
            state['feedback'] = {'type': 'invalid_request', 'message': str(exc)}
            state['events'].append({'stage': 'request_rejected', **state['feedback']})
            route = 'invalid_output' if invalid >= self.config.max_invalid_responses else 'call_model'
            return {'agent': _dehydrate(state), 'invalid': invalid, 'route': route}
        state['feedback'] = None
        state['checks'] = decision['checks']
        state['events'].append({'stage': 'decision', **copy.deepcopy(decision)})
        state['decision'] = decision  # consumed by the next node, removed before the result
        route = 'respond' if decision['action'] == 'respond' else 'tool'
        return {'agent': _dehydrate(state), 'invalid': 0, 'route': route}

    def _tool(self, data):
        state = _hydrate(data['agent'])
        decision = state.pop('decision')
        self._execute(state, decision['action'], decision['arguments'])
        return {'agent': _dehydrate(state), 'route': 'call_model'}

    def _finish_node(self, status, text=None):
        def node(data):
            state = _hydrate(data['agent'])
            decision = state.pop('decision', None)
            if status == 'responded':
                return {'result': self._finish(state, status, decision['arguments']['text'], decision=decision)}
            return {'result': self._finish(state, status, text)}
        return node

    # wiring ----------------------------------------------------------------
    def _build(self, checkpointer):
        graph = StateGraph(AgentState)
        graph.add_node('init', self._init)
        graph.add_node('call_model', self._call_model)
        graph.add_node('decide', self._decide)
        graph.add_node('tool', self._tool)
        graph.add_node('finish_responded', self._finish_node('responded'))
        graph.add_node('finish_invalid', self._finish_node(
            'invalid_output', '모델의 실행 요청을 해석하지 못해 중단했습니다.'))
        graph.add_node('finish_model_error', self._finish_node(
            'model_error', '모델 호출에 실패해 답변을 완료하지 못했습니다.'))
        graph.add_node('finish_budget', self._finish_node(
            'budget_exhausted', '설정된 판단 한도에 도달해 답변을 완료하지 못했습니다.'))
        graph.add_edge(START, 'init')
        if self.use_gate:
            graph.add_node('gate', self._gate)
            graph.add_node('ask_user', self._ask_user)
            graph.add_node('research', self._research)
            graph.add_edge('init', 'gate')
            graph.add_conditional_edges('gate', lambda s: s['route'], {'ask': 'ask_user', 'proceed': 'call_model'})
            graph.add_edge('ask_user', 'research')
            graph.add_edge('research', 'call_model')
        else:
            graph.add_edge('init', 'call_model')
        graph.add_conditional_edges('call_model', lambda s: s['route'], {
            'decide': 'decide', 'model_error': 'finish_model_error', 'budget_exhausted': 'finish_budget'})
        graph.add_conditional_edges('decide', lambda s: s['route'], {
            'call_model': 'call_model', 'tool': 'tool', 'respond': 'finish_responded',
            'invalid_output': 'finish_invalid'})
        graph.add_edge('tool', 'call_model')
        for name in ('finish_responded', 'finish_invalid', 'finish_model_error', 'finish_budget'):
            graph.add_edge(name, END)
        return graph.compile(checkpointer=checkpointer)

    @staticmethod
    def _outcome(final, thread_id):
        if final.get('__interrupt__'):
            return {'status': 'waiting_for_user', 'thread_id': thread_id,
                    'question': final['__interrupt__'][0].value['question']}
        return final['result']

    def run(self, question, *, initial_result=None, thread_id=None):
        config = {'configurable': {'thread_id': thread_id}} if thread_id else None
        if self.use_gate and not thread_id:
            raise ValueError('use_gate needs a thread_id')
        final = self.graph.invoke({'question': question, 'initial_result': initial_result}, config)
        return self._outcome(final, thread_id)

    def resume(self, thread_id, reply):
        """Continue a paused thread with the user's reply (also from a new process)."""
        final = self.graph.invoke(Command(resume=reply), {'configurable': {'thread_id': thread_id}})
        return self._outcome(final, thread_id)
