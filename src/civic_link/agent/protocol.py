"""The small, explicit interface between Qwen and the controller."""
from __future__ import annotations

import json
from .graph_access import graph_arguments

ACTIONS = ('search', 'graph', 'respond')
CHECK_STATUSES = ('미확인', '일부 확인', '확인', '충돌')


class InvalidDecision(ValueError):
    """A model response is not an executable request."""


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise InvalidDecision(f'{field}: nonempty string required')
    return value


def _references(value, known_ids):
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise InvalidDecision('evidence_ids: string list required')
    if len(value) != len(set(value)) or not set(value) <= known_ids:
        raise InvalidDecision('evidence_ids: duplicate or not previously supplied to Qwen')
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidDecision(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def parse_decision(raw, known_ids):
    """Validate format and provenance, not semantic truth or answer sufficiency."""
    if not isinstance(raw, str):
        raise InvalidDecision('Qwen response must be JSON text')
    try:
        decision = json.loads(raw, object_pairs_hook=_unique_object,
                              parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except (ValueError, TypeError) as exc:
        raise InvalidDecision('invalid JSON object') from exc
    if not isinstance(decision, dict) or set(decision) != {'checks', 'action', 'arguments', 'reason'}:
        raise InvalidDecision('required fields: checks, action, arguments, reason')
    action = decision['action']
    if action not in ACTIONS:
        raise InvalidDecision('action must be search, graph or respond')
    _text(decision['reason'], 'reason')
    checks = decision['checks']
    if not isinstance(checks, list):
        raise InvalidDecision('checks must be a list')
    items = set()
    for check in checks:
        if not isinstance(check, dict) or set(check) != {'item', 'status', 'evidence_ids'}:
            raise InvalidDecision('check fields: item, status, evidence_ids')
        item = _text(check['item'], 'check.item')
        if item in items:
            raise InvalidDecision('duplicate check item')
        items.add(item)
        if check['status'] not in CHECK_STATUSES:
            raise InvalidDecision('unsupported check status')
        refs = _references(check['evidence_ids'], known_ids)
        if check['status'] in ('확인', '일부 확인', '충돌') and not refs:
            raise InvalidDecision('evidence-backed check requires evidence_ids')
    arguments = decision['arguments']
    if not isinstance(arguments, dict):
        raise InvalidDecision('arguments must be an object')
    if action == 'graph':
        try:
            graph_arguments(arguments)
        except ValueError as exc:
            raise InvalidDecision(str(exc)) from exc
        return decision
    fields = {'search': {'query'},
              'respond': {'text', 'evidence_ids'}}[action]
    if set(arguments) != fields:
        raise InvalidDecision(f'{action} arguments must be {sorted(fields)}')
    for name in fields - {'evidence_ids'}:
        _text(arguments[name], f'arguments.{name}')
    if action == 'respond':
        _references(arguments['evidence_ids'], known_ids)
    return decision
