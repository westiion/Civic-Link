"""CPU replay of early-stop policies on the archived 608-question turns (no model calls).

A policy cuts a run at the first turn where its rule fires: that turn's tool request is skipped
and the model is assumed to answer with what it already has. Later turns are counted as saved.
Loss = required evidence that the skipped turns would have newly covered (uses the benchmark's
requirement IDs for evaluation only; the rules themselves use only controller-visible state).
"""

import csv
import json
from collections import defaultdict

import _bootstrap  # noqa: F401
from run_week9_probes import WORK

OUT = WORK / 'feedback'


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

CONDITIONS = ('iterative_search', 'iterative_graph')


def usage_tokens(text):
    try:
        return int(json.loads(text).get('total_tokens', 0))
    except (ValueError, AttributeError):
        return 0


def all_confirmed(turn):
    checks = json.loads(turn['input_checks'] or '[]')
    return bool(checks) and all(c.get('status') == '확인' for c in checks)


def duplicate_before(turns, index):
    """The request was already executed earlier in the same run (cached result)."""
    return turns[index]['tool_cached'] == 'True'


POLICIES = {
    'cached_duplicate': lambda turns, i: duplicate_before(turns, i),
    'all_checks_confirmed': lambda turns, i: all_confirmed(turns[i]),
    'either': lambda turns, i: duplicate_before(turns, i) or all_confirmed(turns[i]),
}


def firing_turn(policy, turns):
    for i, turn in enumerate(turns):
        if turn['action'] == 'respond':
            return None
        # all_checks_confirmed and the duplicate rule both inspect the turn's own state.
        check = policy(turns, i)
        if check:
            return i
    return None


def main():
    questions = {(r['condition'], r['query_id']): r for r in read_csv(WORK / 'trace-608/questions.csv')}
    by_run = defaultdict(list)
    for row in read_csv(WORK / 'trace-608/turns.csv'):
        if row['condition'] in CONDITIONS:
            by_run[row['condition'], row['query_id']].append(row)
    rows = []
    for (condition, qid), turns in by_run.items():
        question = questions[condition, qid]
        turns.sort(key=lambda t: int(t['turn']))
        for name, policy in POLICIES.items():
            cut = firing_turn(policy, turns)
            if cut is None:
                continue
            skipped = turns[cut:]
            later = turns[cut + 1:]
            lost = [rid for t in skipped if t['action'] != 'respond'
                    for rid in json.loads(t['newly_covered_requirement_ids'] or '[]')]
            rows.append(dict(
                condition=condition, query_id=qid, expected_action=question['expected_action'], policy=name, cut_turn=int(turns[cut]['turn']),
                total_turns=len(turns), saved_model_calls=len(later),
                saved_seconds=round(sum(float(t['model_seconds'] or 0) for t in later), 2),
                saved_tokens=sum(usage_tokens(t['usage']) for t in later),
                lost_requirements=len(lost), final_complete_before=question['final_complete'],
                content_pass=question['content_pass']))
    summary = []
    for condition in CONDITIONS:
        n_questions = sum(1 for (c, _) in questions if c == condition)
        total_calls = sum(int(q['model_calls']) for (c, _), q in questions.items() if c == condition)
        total_seconds = sum(float(q['elapsed_seconds']) for (c, _), q in questions.items() if c == condition)
        for name in POLICIES:
            sel = [r for r in rows if r['condition'] == condition and r['policy'] == name]
            summary.append(dict(
                condition=condition, policy=name, all_questions=n_questions,
                fired_questions=len(sel), saved_model_calls=sum(r['saved_model_calls'] for r in sel),
                saved_model_calls_pct=round(100 * sum(r['saved_model_calls'] for r in sel) / total_calls, 1),
                saved_seconds=round(sum(r['saved_seconds'] for r in sel)),
                saved_seconds_pct=round(100 * sum(r['saved_seconds'] for r in sel) / total_seconds, 1),
                saved_tokens=sum(r['saved_tokens'] for r in sel),
                questions_losing_required_evidence=sum(r['lost_requirements'] > 0 for r in sel),
                lost_requirements=sum(r['lost_requirements'] for r in sel),
                fired_by_expected_action=str({a: sum(r['expected_action'] == a for r in sel) for a in ('answer', 'clarify', 'abstain')})))
    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / 'cost-policy-per-question.csv', rows)
    write_csv(OUT / 'cost-policy-summary.csv', summary)
    for row in summary:
        print(row)
    print(f'Results: {OUT / "cost-policy-summary.csv"}')


if __name__ == '__main__':
    main()
