"""Agent that can pause for a clarifying question and continue from the user's single answer.

Flow::

    start()  -> first search -> [gate: is a user fact missing?] -> Qwen loop (ask_user available)
                                   |                                   |
                                   +---- asks ----> waiting_for_user <-+   (state saved in SessionStore, 5 min)
    reply()  -> claim the one allowed answer -> mask personal identifiers -> follow-up search with the answer
             -> Qwen loop (no further questions) -> final result

Outcomes: ``responded`` / ``invalid_output`` / ``budget_exhausted`` / ``model_error`` as before, plus
``waiting_for_user`` (paused) and ``needs_information`` (the user could not supply the missing fact, so the
agent says it cannot answer from that alone instead of guessing).
Model-call and tool-call budgets keep counting across the pause.
"""
from __future__ import annotations

import json

from .clarify import (MASK, NOTICE, RESUME_RULE, check_gate, is_unknown_reply, mask_personal,
                      needs_information_text, question_violations)
from .controller import RetrievalAgent
from .session import SessionStore

_SETS = ('seen_ids', 'supplied_ids')


def _dehydrate(state):
    out = dict(state)
    for name in _SETS:
        out[name] = sorted(out[name])
    return out


def _hydrate(stored):
    state = dict(stored)
    for name in _SETS:
        state[name] = set(state[name])
    return state


class InteractiveAgent(RetrievalAgent):
    def __init__(self, client, tools, *, store: SessionStore, use_gate=False, allow_ask=False, **kwargs):
        super().__init__(client, tools, allow_ask=allow_ask, **kwargs)
        if (use_gate or allow_ask) and 'search' not in self.allowed_tools:
            raise ValueError('asking the user needs the search tool for the follow-up search')
        self.store, self.use_gate = store, use_gate

    # prompt ----------------------------------------------------------------
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

    # pause / start / reply --------------------------------------------------
    def _pause(self, state, question, source='model'):
        state['pending'] = {'question': question, 'source': source}
        state['events'].append({'stage': 'paused', 'question': question, 'source': source})
        expires = self.store.put_waiting(state['session_id'], _dehydrate(state), question)
        return {'status': 'waiting_for_user', 'session_id': state['session_id'], 'question': question,
                'message': f'{question}\n\n({NOTICE})', 'expires_at': expires,
                'model_calls': state['model_calls'], 'tool_calls': state['tool_calls'],
                'events': state['events']}

    def start(self, question, *, session_id, initial_result=None):
        masked, count = mask_personal(question)
        state = self._state(masked)
        state['session_id'] = session_id
        if count:
            state['events'].append({'stage': 'personal_info_masked', 'where': 'question', 'count': count})
        self.store.purge_expired()
        if initial_result is not None or self.use_gate:
            if self.config.max_tool_calls < 1:
                raise ValueError('the first search requires a tool budget')
            self._execute(state, 'search', {'query': masked}, initial_result)
        state['ask_allowed'] = self.allow_ask
        if self.use_gate:
            decision, raw = check_gate(self.client, masked, list(state['evidence'].values()))
            state['events'].append({'stage': 'gate', 'decision': decision, 'raw': raw})
            if decision is not None and not decision['can_answer_now']:
                if question_violations(decision['question']):
                    state['events'].append({'stage': 'gate_question_rejected',
                                            'reason': question_violations(decision['question'])})
                else:
                    return self._pause(state, decision['question'], source='gate')
        return self._loop(state)

    def reply(self, session_id, text):
        """Continue a paused run with the user's one answer. Raises a SessionError subclass if it cannot."""
        state = _hydrate(self.store.claim(session_id))
        try:
            question = state.pop('pending')['question']
            masked, count = mask_personal(text)
            state['ask_allowed'] = False
            state.setdefault('clarifications', []).append(
                {'assistant_clarification': question, 'user_reply': masked})
            state['events'].append({'stage': 'clarification', 'question': question, 'reply': masked,
                                    'personal_info_masked': count})
            if is_unknown_reply(masked) or not masked.replace(MASK, '').strip():
                result = self._finish(state, 'needs_information', needs_information_text(question))
            else:
                query = f"{state['question']} {masked}"
                state['events'].append({'stage': 'follow_up_search', 'query': query})
                if state['tool_calls'] < self.config.max_tool_calls:
                    self._execute(state, 'search', {'query': query})
                result = self._loop(state)
        except BaseException:
            self.store.release(session_id)
            raise
        self.store.finish(session_id)
        return result
