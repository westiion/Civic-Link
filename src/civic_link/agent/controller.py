"""Central program: input → Qwen → dispatch → observations → Qwen → respond."""
from __future__ import annotations

import copy
import json
import time
from dataclasses import asdict, dataclass

from .client import ModelError
from .prompts import describe_tools, messages
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
    def __init__(self, client, tools, *, scope=None, tool_descriptions=None, config=None, allowed_tools=None):
        if set(tools) != {'search', 'graph'}:
            raise ValueError('exactly search and graph tools are required')
        self.client, self.tools = client, tools
        self.scope = scope or {}
        self.tool_descriptions = tool_descriptions if tool_descriptions is not None else describe_tools()
        self.config = config or AgentConfig()
        self.allowed_tools = tuple(('search', 'graph') if allowed_tools is None else allowed_tools)
        if set(self.allowed_tools) - {'search', 'graph'}:
            raise ValueError('unsupported allowed tool')
        self.tool_descriptions = {k:v for k,v in self.tool_descriptions.items()
                                  if k not in ('search', 'graph') or k in self.allowed_tools}

    def initial_messages(self, question):
        state = self._state(question)
        return self._messages(state)

    @staticmethod
    def _state(question):
        if not isinstance(question, str) or not question.strip():
            raise ValueError('question must be nonempty original user text')
        return {'question': question, 'checks': [], 'evidence': {}, 'seen_ids': set(),
                'supplied_ids': set(),
                'latest_result': None, 'history': [], 'events': [], 'cache': {},
                'model_calls': 0, 'tool_calls': 0, 'feedback': None,
                'graph_candidates': [], 'document_candidates': {}, 'graph_pages': []}

    def _messages(self, state):
        candidates = []
        latest = state['latest_result']
        can_search = (state['tool_calls'] < self.config.max_tool_calls
                      and state['model_calls'] < self.config.max_model_calls - 1)
        check_ids = [eid for check in state['checks'] for eid in check['evidence_ids']]
        if not can_search:
            candidates.extend(check_ids)
        if latest:
            candidates.extend(row['id'] for row in latest['evidence'])
        candidates.extend(check_ids)
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
        state['supplied_ids'] = set(selected_ids)
        state['seen_ids'].update(selected_ids)
        if 'graph' in self.allowed_tools and hasattr(self.tools['graph'], 'observe'):
            state['graph_candidates'] = self.tools['graph'].observe(
                state['question'], selected, state['graph_candidates'], state['checks'], latest)
            for row in selected:
                if row.get('document_id'):
                    state['document_candidates'][row['document_id']] = {'id': row['document_id'], 'title': row['title']}
            for row in state['graph_candidates']:
                if row.get('document_id'):
                    state['document_candidates'].setdefault(row['document_id'], {'id': row['document_id']})
        observation = None
        if latest:
            observation = {k: v for k, v in latest.items() if k != 'evidence'}
            observation['evidence_ids'] = [row['id'] for row in latest['evidence']]
        payload = {
            '원래 질문': state['question'],
            '현재 실행 상태': {
                'phase': 'initial' if not state['history'] else 'followup',
                'model_calls_completed': state['model_calls'],
                'tool_calls_completed': state['tool_calls'],
                'checks': state['checks'], 'latest_result': observation, 'evidence': selected,
                'context': {'supplied_evidence_ids': selected_ids, 'omitted_evidence_ids': omitted,
                            'stored_evidence_count': len(state['evidence'])},
                'history': state['history'], 'controller_feedback': state['feedback'],
                'graph_candidates': state['graph_candidates'],
                'document_candidates': list(state['document_candidates'].values()),
                'graph_pages': state['graph_pages'],
            },
            '실행 규칙': {
                'allowed_actions': [*self.allowed_tools, 'respond'] if can_search else ['respond'],
                'remaining_model_calls': self.config.max_model_calls - state['model_calls'],
                'remaining_tool_calls': self.config.max_tool_calls - state['tool_calls'],
            },
        }
        result = messages(payload, self.scope, self.tool_descriptions)
        state['input_chars'] = sum(len(row['content']) for row in result)
        return result

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

    def _execute(self, state, action, arguments, initial_result=None):
        key = json.dumps([action, arguments], sort_keys=True, ensure_ascii=False)
        cached = key in state['cache']
        if cached:
            result = copy.deepcopy(state['cache'][key])
        else:
            state['tool_calls'] += 1
            try:
                raw = initial_result if initial_result is not None else self.tools[action].run(copy.deepcopy(arguments))
                result = normalize_result(raw, action)
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
            state['evidence'].setdefault(row['id'], {k:copy.deepcopy(v) for k,v in row.items()
                                                    if k not in ('rank', 'metadata')})
        state['latest_result'] = result
        if action == 'graph' and result['status'] != 'error':
            state['graph_pages'].append({'query': {k:v for k,v in arguments.items() if k != 'cursor'},
                'cursor': arguments.get('cursor'), 'next_cursor': result.get('next_cursor'),
                'target': result.get('target'), 'metadata': result.get('metadata', {})})
        state['history'].append({'tool': action, 'arguments': arguments, 'status': result['status'],
                                'evidence_ids': [r['id'] for r in result['evidence']], 'cached': cached})
        state['events'].append({'stage': 'tool_result', 'cached': cached, 'result': copy.deepcopy(result)})

    def run(self, question, *, initial_result=None):
        state = self._state(question)
        if initial_result is not None:
            if self.config.max_tool_calls < 1:
                raise ValueError('initial search requires a tool budget')
            self._execute(state, 'search', {'query': question}, initial_result)
        invalid = 0
        while state['model_calls'] < self.config.max_model_calls:
            request_messages = self._messages(state)
            state['model_calls'] += 1
            state['events'].append({'stage': 'qwen_input', 'messages': request_messages, 'input_chars': state['input_chars']})
            started = time.perf_counter()
            try:
                raw = self.client.complete(request_messages)
            except (ModelError, OSError) as exc:
                state['events'].append({'stage': 'model_error', 'error': str(exc)})
                return self._finish(state, 'model_error', '모델 호출에 실패해 답변을 완료하지 못했습니다.')
            state['events'].append({'stage': 'qwen_output', 'raw': raw, 'seconds': time.perf_counter()-started,
                                    'usage': copy.deepcopy(getattr(self.client, 'last_usage', None))})
            try:
                decision = parse_decision(raw, state['seen_ids'])
                if decision['action'] != 'respond' and decision['action'] not in self.allowed_tools:
                    raise InvalidDecision('tool is disabled in this experiment condition')
                if decision['action'] == 'graph':
                    arguments = decision['arguments']
                    if arguments.get('cursor') and not any(
                        page['next_cursor'] == arguments['cursor'] and page['query'] == {k:v for k,v in arguments.items() if k != 'cursor'}
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

            self._execute(state, action, arguments)
        return self._finish(state, 'budget_exhausted', '설정된 판단 한도에 도달해 답변을 완료하지 못했습니다.')
