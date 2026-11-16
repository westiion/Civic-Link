"""Re-read the 60-question content scoring (180 judged answers) without any model call.

1. Why do answers fail? Tabulate the judge's failed checks per condition and expected action.
2. List the questions whose verdict differs across conditions, with the failing judge reason and a raw
   character-similarity number for reference only. The answer-or-judge classification of these questions is
   done by reading them (week9/review/CONTENT_REVIEW.md): similarity cannot tell a different answer from an
   inconsistently applied criterion, e.g. two answers with the same kind of extra claim, one passed, one failed.
"""

import csv
import json
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher

from run_week9_probes import WORK

SRC = WORK / 'trace-sample60' / 'questions.csv'
OUT = WORK / 'feedback'
CONDITIONS = ('single_search', 'iterative_search', 'iterative_graph')
SHORT = {'single_search': 'A', 'iterative_search': 'B', 'iterative_graph': 'C'}


def normalize(text):
    text = re.sub(r'\[근거[^\]]*\]|DOC-[0-9a-f]+-C\d+', '', text)
    return re.sub(r'\s+', ' ', re.sub(r'[*#`>\-]', '', text)).strip()


def failed_checks(row):
    checks = json.loads(row['judge_failures'] or '{}')
    return sorted({name for names in checks.values() for name in names})


def cause(row):
    names = failed_checks(row)
    if 'required_handling_ok' in names:
        return '필수 처리 미충족'
    if {'unsupported_claim', 'grounded_in_provided_passages', 'factual_correct'} & set(names):
        return '근거 밖 주장·사실 오류'
    if 'citations_supported' in names:
        return '인용 문제'
    return '기타'


def main():
    with SRC.open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    by_question = defaultdict(dict)
    for row in rows:
        by_question[row['query_id']][row['condition']] = row

    failing = [r for r in rows if r['content_pass'] != 'True']
    table = Counter((r['expected_action'], cause(r)) for r in failing)
    print(f'judged answers={len(rows)} failing={len(failing)}')
    for action in ('answer', 'clarify', 'abstain'):
        total = sum(1 for r in rows if r['expected_action'] == action)
        bad = sum(1 for r in failing if r['expected_action'] == action)
        detail = {c: table[(action, c)] for c in ('필수 처리 미충족', '근거 밖 주장·사실 오류', '인용 문제', '기타') if table[(action, c)]}
        print(f'  {action}: {bad}/{total} fail {detail}')

    flips = []
    for qid, conditions in sorted(by_question.items()):
        verdicts = {c: conditions[c]['content_pass'] == 'True' for c in CONDITIONS if c in conditions}
        if len(set(verdicts.values())) < 2:
            continue
        passing = [c for c, ok in verdicts.items() if ok]
        failing_c = [c for c, ok in verdicts.items() if not ok]
        best = max(((SequenceMatcher(None, normalize(conditions[p]['answer']), normalize(conditions[f]['answer'])).ratio(), p, f)
                    for p in passing for f in failing_c))
        ratio, p, f = best
        same_citations = set(json.loads(conditions[p]['cited_ids'] or '[]')) == set(json.loads(conditions[f]['cited_ids'] or '[]'))
        flips.append(dict(query_id=qid, expected_action=conditions[p]['expected_action'],
                          verdicts=' '.join(f'{SHORT[c]}={"통과" if ok else "미통과"}' for c, ok in verdicts.items()),
                          closest_pair=f'{SHORT[p]}(통과)-{SHORT[f]}(미통과)', similarity_reference_only=round(ratio, 2),
                          same_citations=same_citations, failing_cause=cause(conditions[f]),
                          failing_reason=json.loads(conditions[f]['judge_reasons'] or '{}').get('main', '')[:300]))
    OUT.mkdir(parents=True, exist_ok=True)
    if flips:
        with (OUT / 'judge-vs-answer-flips.csv').open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(flips[0]))
            writer.writeheader()
            writer.writerows(flips)
    (OUT / 'judge-vs-answer.json').write_text(json.dumps(dict(
        judged=len(rows), failing=len(failing),
        failing_by_action_cause={f'{a}|{c}': n for (a, c), n in sorted(table.items())},
        flip_questions=len(flips)), ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'questions whose verdict differs across conditions={len(flips)}')
    for flip in flips:
        print(f"  {flip['query_id']} {flip['verdicts']} failing_cause={flip['failing_cause']}")
    print(f'Results: {OUT / "judge-vs-answer.json"}')


if __name__ == '__main__':
    main()
