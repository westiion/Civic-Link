"""Central program: input → Qwen → dispatch → observations → Qwen → respond."""
from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass

from .client import ModelError
from .prompts import messages
from .protocol import InvalidDecision, parse_decision


@dataclass(frozen=True)
class AgentConfig:
    # Initial implementation choices, not calibrated performance parameters.
    max_model_calls: int = 8
    max_tool_calls: int = 6
    max_invalid_responses: int = 2
    max_evidence_chars: int = 60000

    def __post_init__(self):
        for name, value in asdict(self).items():
            minimum = 0 if name == 'max_tool_calls' else 1
            if type(value) is not int or value < minimum:
                raise ValueError(f'{name} must be an integer >= {minimum}')


def normalize_result(raw, tool):
    """Copy real tool fields; never generate a source or semantic assessment."""
    if not isinstance(raw, dict) or raw.get('tool') != tool:
        raise ValueError('tool result identity mismatch')
    result = copy.deepcopy(raw)
    if result.get('status') not in ('ok', 'no_results', 'error', 'ambiguous', 'not_found'):
        raise ValueError('unknown tool status')
    evidence, relations = result.get('evidence'), result.get('relations')
    if not isinstance(evidence, list) or not isinstance(relations, list):
        raise ValueError('evidence and relations must be lists')
    seen = set()
    for row in evidence:
        if not isinstance(row, dict):
            raise ValueError('invalid evidence row')
        for name in ('id', 'title', 'text', 'source'):
            if not isinstance(row.get(name), str) or not row[name].strip():
                raise ValueError(f'evidence.{name} must exist in source data')
        if row['id'] in seen:
            raise ValueError('duplicate evidence ID in one tool result')
        seen.add(row['id'])
    json.dumps(result, ensure_ascii=False, allow_nan=False)
    return result


class RetrievalAgent:
    def __init__(self, client, tools, *, scope=None, tool_descriptions=None, config=None):
        if set(tools) != {'search', 'graph'}:
            raise ValueError('exactly search and graph tools are required')
        self.client, self.tools = client, tools
        self.scope = scope or {}
        self.tool_descriptions = tool_descriptions or {}
        self.config = config or AgentConfig()

    def initial_messages(self, question):
        state = self._state(question)
        return self._messages(state)

    @staticmethod
    def _state(question):
        if not isinstance(question, str) or not question.strip():
            raise ValueError('question must be nonempty original user text')
        return {'question': question, 'checks': [], 'evidence': {}, 'seen_ids': set(),
                'latest_result': None, 'history': [], 'events': [], 'cache': {},
                'model_calls': 0, 'tool_calls': 0, 'feedback': None}

    def _messages(self, state):
        candidates = []
        latest = state['latest_result']
        if latest:
            candidates.extend(row['id'] for row in latest['evidence'])
        for check in state['checks']:
            candidates.extend(check['evidence_ids'])
        candidates.extend(state['evidence'])
        selected, omitted, used = [], [], 0
        for eid in dict.fromkeys(candidates):
            row = state['evidence'].get(eid)
            if row is None:
                continue
            size = len(json.dumps(row, ensure_ascii=False))
            if used + size > self.config.max_evidence_chars:
                omitted.append(eid)
            else:
                selected.append(row)
                used += size
        selected_ids = [row['id'] for row in selected]
        state['seen_ids'].update(selected_ids)
        observation = None
        if latest:
            observation = {k: v for k, v in latest.items() if k != 'evidence'}
            observation['evidence_ids'] = [row['id'] for row in latest['evidence']]
        can_search = (state['tool_calls'] < self.config.max_tool_calls
                      and state['model_calls'] < self.config.max_model_calls - 1)
        payload = {'question': state['question'], 'checks': state['checks'],
                   'state': {'phase': 'initial' if not state['history'] else 'followup',
                             'model_calls_completed': state['model_calls'],
                             'tool_calls_completed': state['tool_calls'],
                             'remaining_model_calls': self.config.max_model_calls - state['model_calls'],
                             'remaining_tool_calls': self.config.max_tool_calls - state['tool_calls'],
                             'allowed_actions': ['search', 'graph', 'respond'] if can_search else ['respond']},
                   'latest_result': observation, 'evidence': selected,
                   'context': {'supplied_evidence_ids': selected_ids, 'omitted_evidence_ids': omitted,
                               'stored_evidence_count': len(state['evidence'])},
                   'history': state['history'], 'controller_feedback': state['feedback']}
        return messages(payload, self.scope, self.tool_descriptions)

    @staticmethod
    def _finish(state, status, text, *, decision=None):
        refs = decision['arguments']['evidence_ids'] if decision else []
        sources = [{'id': eid, 'title': state['evidence'][eid]['title'],
                    'source': state['evidence'][eid]['source']} for eid in refs]
        rendered = text
        if sources:
            rendered += '\n\n출처\n' + '\n'.join(
                f'- [{row["title"]}]({row["source"]}) · {row["id"]}' for row in sources)
        return {'status': status, 'question': state['question'], 'text': text,
                'rendered_response': rendered, 'sources': sources, 'checks': state['checks'],
                'model_calls': state['model_calls'], 'tool_calls': state['tool_calls'],
                'evidence': list(state['evidence'].values()), 'history': state['history'],
                'events': state['events']}

    def run(self, question):
        state = self._state(question)
        invalid = 0
        while state['model_calls'] < self.config.max_model_calls:
            request_messages = self._messages(state)
            state['model_calls'] += 1
            state['events'].append({'stage': 'qwen_input', 'messages': request_messages})
            try:
                raw = self.client.complete(request_messages)
            except (ModelError, OSError) as exc:
                state['events'].append({'stage': 'model_error', 'error': str(exc)})
                return self._finish(state, 'model_error', '모델 호출에 실패해 답변을 완료하지 못했습니다.')
            state['events'].append({'stage': 'qwen_output', 'raw': raw})
            try:
                decision = parse_decision(raw, state['seen_ids'])
                if decision['action'] != 'respond' and (
                    state['tool_calls'] >= self.config.max_tool_calls
                    or state['model_calls'] >= self.config.max_model_calls
                ):
                    raise InvalidDecision('tool budget exhausted; choose respond using available information')
            except InvalidDecision as exc:
                invalid += 1
                state['feedback'] = {'type': 'invalid_request', 'message': str(exc)}
                state['events'].append({'stage': 'request_rejected', **state['feedback']})
                if invalid >= self.config.max_invalid_responses:
                    return self._finish(state, 'invalid_output', '모델의 실행 요청을 해석하지 못해 중단했습니다.')
                continue
            invalid = 0
            state['feedback'] = None
            state['checks'] = decision['checks']
            state['events'].append({'stage': 'decision', **copy.deepcopy(decision)})
            action, arguments = decision['action'], decision['arguments']
            if action == 'respond':
                return self._finish(state, 'responded', arguments['text'], decision=decision)

            key = json.dumps([action, arguments], sort_keys=True, ensure_ascii=False)
            cached = key in state['cache']
            if cached:
                result = copy.deepcopy(state['cache'][key])
            else:
                state['tool_calls'] += 1
                try:
                    result = normalize_result(self.tools[action].run(copy.deepcopy(arguments)), action)
                    for row in result['evidence']:
                        old = state['evidence'].get(row['id'])
                        if old and any(old.get(k) != row.get(k) for k in ('title', 'text', 'source')):
                            raise ValueError('same evidence ID has conflicting source content')
                    if result['status'] != 'error':
                        state['cache'][key] = copy.deepcopy(result)
                except Exception as exc:
                    result = {'tool': action, 'status': 'error', 'evidence': [], 'relations': [],
                              'error': {'type': type(exc).__name__, 'message': str(exc)}}
            for row in result['evidence']:
                # Query-specific scores/ranks stay in the original tool trace.
                # Reusing a source must not present an earlier query's score as current.
                state['evidence'].setdefault(row['id'], {
                    name: copy.deepcopy(row[name])
                    for name in ('id', 'document_id', 'title', 'text', 'source') if name in row
                })
            state['latest_result'] = result
            state['history'].append({'tool': action, 'arguments': arguments, 'status': result['status'],
                                     'evidence_ids': [row['id'] for row in result['evidence']],
                                     'cached': cached})
            state['events'].append({'stage': 'tool_result', 'cached': cached, 'result': copy.deepcopy(result)})
        return self._finish(state, 'budget_exhausted', '설정된 판단 한도에 도달해 답변을 완료하지 못했습니다.')
