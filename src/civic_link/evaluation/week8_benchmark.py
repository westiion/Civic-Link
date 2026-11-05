"""Reproducible, source-grounded composition of Week 7 development questions.

No new legal facts, user replies, model responses or independent gold are inferred.
The original 608 JSONL lines are retained verbatim. New tasks explicitly concern
two independent cases, so one person's facts must not fill the other's slots.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from copy import deepcopy
import csv
from itertools import combinations
import json
from pathlib import Path
import unicodedata

from civic_link.common import ROOT, connect, digest, read_jsonl, write_json, write_jsonl
from civic_link.graph.build import corpus_identity

VERSION = 'week8-composition1000-v1'
TARGETS = {'answer': 312, 'clarify': 40, 'abstain': 40}
SOURCE = ROOT / 'data/benchmark/questions.jsonl'
DB = ROOT / 'data/corpus/civic.sqlite'
OUTPUT = ROOT / 'data/benchmark/week8'
MODULE = Path(__file__)


def payload_hash(row):
    return digest(json.dumps(row, ensure_ascii=False, sort_keys=True))


def chunk_ids(row):
    return {cid for e in row['evidence_requirements'] for cid in e['acceptable_chunk_ids']}


def strict_spans(row, chunks):
    return all(any(chunks[cid]['start_char'] <= e['start_char']
                   and chunks[cid]['end_char'] >= e['end_char']
                   for cid in e['acceptable_chunk_ids']) for e in row['evidence_requirements'])


def select_pairs(rows, chunks):
    """Fixed greedy diversity policy, chosen without predictions or model scores."""
    answers = sorted((q for q in rows if q['expected_action'] == 'answer'
                      and q['evidence_requirements'] and strict_spans(q, chunks)),
                     key=lambda q: q['question_id'])
    ids = {q['question_id']: chunk_ids(q) for q in answers}
    candidates = []
    for a, b in combinations(answers, 2):
        shared = sorted(set(a['service_ids']) & set(b['service_ids']))
        # Each case must add a retrieval target and use a different question type.
        if (not shared or a['question_type'] == b['question_type']
                or not ids[a['question_id']] - ids[b['question_id']]
                or not ids[b['question_id']] - ids[a['question_id']]
                or len({e['document_id'] for q in (a, b)
                        for e in q['evidence_requirements']}) < 2):
            continue
        candidates.append((a, b, shared))

    selected, uses, services, type_pairs = [], Counter(), Counter(), Counter()
    for action, count in TARGETS.items():
        pool = list(candidates) if action == 'answer' else [
            (a, b, sorted(set(a['service_ids']) & set(b['service_ids'])))
            for a in rows if a['expected_action'] == action and strict_spans(a, chunks)
            and a.get('acceptable_actions', [action]) == [action]
            for b in answers if set(a['service_ids']) & set(b['service_ids'])]
        chosen_behavior = set()
        for _ in range(count):
            eligible = [p for p in pool if action == 'answer'
                        or p[0]['question_id'] not in chosen_behavior]
            if not eligible:
                raise ValueError(f'insufficient grounded pairs for {action}')

            def score(pair):
                a, b, shared = pair
                keys = (a['question_id'], b['question_id'])
                types = tuple(sorted((a['question_type'], b['question_type'])))
                return (min(services[s] for s in shared), max(uses[k] for k in keys),
                        sum(uses[k] for k in keys), type_pairs[types],
                        digest(VERSION + '|'.join(keys)))

            pair = min(eligible, key=score)
            pool.remove(pair)
            a, b, shared = pair
            service = min(shared, key=lambda s: (services[s], s))
            services[service] += 1
            uses.update((a['question_id'], b['question_id']))
            type_pairs[tuple(sorted((a['question_type'], b['question_type'])))] += 1
            chosen_behavior.add(a['question_id'])
            # Balance where the nonanswer case appears; IDs are not in model input.
            if len(selected) % 2:
                a, b = b, a
            selected.append((action, a, b, service))
    return selected


def subtask(parent, label, evidence_ids):
    rubric = parent.get('gold_rubric', {})
    action_rubric = parent.get('action_rubric', {})
    return dict(subtask_id=label, parent_question_id=parent['question_id'],
        expected_action=parent['expected_action'],
        acceptable_actions=parent.get('acceptable_actions', [parent['expected_action']]),
        evidence_ids=evidence_ids, expected_response=parent['gold_answer'],
        required=deepcopy(action_rubric.get('required', rubric.get('must', [parent['gold_answer']]))),
        must_not=deepcopy(action_rubric.get('must_not', rubric.get('must_not', []))),
        clarification=deepcopy(parent.get('clarification')),
        action_rubric=deepcopy(action_rubric))


def compose(index, action, a, b, service, source_hash):
    qid = f'W8-COMP-{index:04d}'
    question = ('서로 다른 두 사람의 민원 상담을 함께 정리하려고 합니다. '
                'A와 B의 사정은 별개이며, 한쪽의 정보를 다른 쪽의 사실로 사용하지 마세요. '
                '각 문의에 대해 A, B로 나누어 안내해 주세요.\n\n'
                f'A 문의: {a["question"]}\n\nB 문의: {b["question"]}')
    evidence, subtasks, seen = [], [], {}
    for label, parent in zip(('A', 'B'), (a, b)):
        keys = []
        for e in parent['evidence_requirements']:
            key = (e['document_id'], e['start_char'], e['end_char'],
                   tuple(sorted(e['acceptable_chunk_ids'])))
            if key not in seen:
                item = deepcopy(e)
                item['evidence_id'] = f'{qid}-E{len(evidence)+1}'
                item['derived_from'] = []
                seen[key] = item
                evidence.append(item)
            item = seen[key]
            item['derived_from'].append(dict(subtask_id=label,
                parent_question_id=parent['question_id'], evidence_id=e['evidence_id']))
            keys.append(item['evidence_id'])
        subtasks.append(subtask(parent, label, keys))
    answer = '\n'.join(f'{label}: {q["gold_answer"]}' for label, q in zip(('A', 'B'), (a, b)))
    required = [f'{s["subtask_id"]}: {text}' for s in subtasks for text in s['required']]
    forbidden = [f'{s["subtask_id"]}: {text}' for s in subtasks for text in s['must_not']]
    forbidden += ['A의 조건·신원·정보를 B에 적용하거나 반대로 적용하지 않는다.',
                  '실제 발급·접수·조회·지급을 수행했다고 주장하지 않는다.']
    missing = [f'{s["subtask_id"]}:{slot}' for s in subtasks
               for slot in (s['clarification'] or {}).get('required_slots', [])]
    return dict(question_id=qid, dataset_id=VERSION, question=question,
        canonical_question=question,
        question_type={'answer': 'composite_answer', 'clarify': 'mixed_clarification',
                       'abstain': 'mixed_abstention'}[action],
        expected_action=action,
        expected_answerability='answerable' if action == 'answer' else 'unanswerable',
        acceptable_actions=[action], gold_answer=answer, evidence_requirements=evidence,
        service_ids=sorted(set(a['service_ids']) | set(b['service_ids'])),
        composition_service_id=service, parent_question_ids=[a['question_id'], b['question_id']],
        parent_question_types=[a['question_type'], b['question_type']],
        gold_rubric=dict(must=required, must_not=forbidden),
        action_rubric=dict(expected_action=action, expected_response=answer,
            required=required, must_not=forbidden, missing_slots=missing,
            conditional_general_information_allowed=True,
            action_boundary='Task label is the blocking action on the unresolved case; answer the other case separately.'),
        clarification=(dict(required_slots=missing, first_turn_retrieval_scored=False,
            gold_followup_question=next(s['expected_response'] for s in subtasks
                                       if s['expected_action'] == 'clarify'),
            resolved_question_status='not_fabricated_or_scored') if action == 'clarify' else None),
        unanswerable_reason=None if action == 'answer' else 'PARTIAL_TASK_REQUIRES_' + action.upper(),
        document_hops=None, gold_document_paths=[], reasoning_classification='parallel_composition_not_inferred_graph_hops',
        entry_source_ids=sorted({e['source_alias'] for e in evidence}),
        row_origin='week8_composed', split='diagnostic_development',
        split_group_id=qid, synthetic=True, human_verified=False, independent_gold=False,
        independent_holdout=False, eligible_for_official_metrics=False,
        retrieval_metric_eligible=action == 'answer', diagnostic_metric_eligible=True,
        review_status='pending_human_review', agent_review_status='composition_and_spans_checked_not_independent_adjudication',
        human_review_status='not_reviewed', tone='not_annotated',
        temporal_scope='frozen_local_corpus; current_law_not_verified',
        provenance=dict(source='data/benchmark/questions.jsonl', source_sha256=source_hash,
            authoring='deterministic_composition_of_two_existing_cases',
            parent_payload_sha256={p['question_id']: payload_hash(p) for p in (a, b)},
            independent_authoring_verified=False),
        agentic=dict(schema_version='civic-link-composition-v1', subtasks=subtasks,
            task_action_policy='answer iff both cases answerable; otherwise the one blocking clarify/abstain action',
            partial_answer_required=action != 'answer',
            response_content_review='pending; action labels and retrieved IDs do not establish correct prose',
            trajectory_policy='No mandatory tool order or invented user continuation; score observed outputs only.'))


def leakage_groups(rows):
    """Conservative connected components; not a train/test split."""
    parent = {q['question_id']: q['question_id'] for q in rows}

    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        a, b = root(a), root(b)
        parent[max(a, b)] = min(a, b)

    seen = {}
    for q in rows:
        qid = q['question_id']
        linked = q.get('parent_question_ids', []) + q.get('related_question_ids', [])
        if q.get('parent_question_id'):
            linked += [q['parent_question_id']]
        for other in linked:
            if other in parent:
                union(qid, other)
        tokens = [('chunk', cid) for cid in chunk_ids(q)]
        tokens += [(k, q[k]) for k in ('split_group_id', 'intent_family_id', 'rule_family_id') if q.get(k)]
        for token in tokens:
            if token in seen:
                union(qid, seen[token])
            else:
                seen[token] = qid
    members = defaultdict(list)
    for qid in sorted(parent):
        members[root(qid)].append(qid)
    mapping = {qid: 'W8-LEAK-' + digest('|'.join(ids))[:16]
               for ids in members.values() for qid in ids}
    return [dict(question_id=q['question_id'], leakage_group_id=mapping[q['question_id']],
                 split='diagnostic_development', independent_holdout=False) for q in rows]


def validate(rows, base, docs, chunks):
    if len(base) != 608 or len(rows) != 1000 or rows[:608] != base:
        raise ValueError('expected unchanged 608 parents plus 392 tasks')
    lookup = {q['question_id']: q for q in rows}
    if len(lookup) != len(rows):
        raise ValueError('duplicate question ID')
    added = rows[608:]
    if Counter(q['expected_action'] for q in added) != TARGETS:
        raise ValueError('new task action counts differ from declared design')
    if [q['question_id'] for q in added] != [f'W8-COMP-{i:04d}' for i in range(1, 393)]:
        raise ValueError('unexpected new task IDs/order')
    pairs = [tuple(sorted(q['parent_question_ids'])) for q in added]
    if len(set(pairs)) != len(pairs):
        raise ValueError('repeated parent pair')
    normalized = [unicodedata.normalize('NFKC', ''.join(q['question'].split())).casefold() for q in rows]
    if len(set(normalized)) != len(rows):
        raise ValueError('duplicate normalized question')
    evidence_count, wide = 0, []
    for q in rows:
        if q['human_verified'] or q['independent_holdout'] or q['eligible_for_official_metrics']:
            raise ValueError('unreviewed development data promoted to independent gold')
        if q['expected_action'] == 'answer' and not q['evidence_requirements']:
            raise ValueError('answer without evidence')
        for e in q['evidence_requirements']:
            evidence_count += 1
            d = docs[e['document_id']]
            if (type(e['start_char']) is not int or type(e['end_char']) is not int
                    or not 0 <= e['start_char'] < e['end_char'] <= len(d['body'])
                    or d['body'][e['start_char']:e['end_char']] != e['quote']):
                raise ValueError('quote span mismatch: ' + q['question_id'])
            if d['source_url'] != e['source_url'] or d['alias'] != e['source_alias']:
                raise ValueError('evidence provenance mismatch')
            if e.get('source_content_hash', d['content_hash']) != d['content_hash']:
                raise ValueError('source content hash mismatch')
            ids = e['acceptable_chunk_ids']
            if not ids or len(ids) != len(set(ids)) or any(
                    cid not in chunks or chunks[cid]['document_id'] != e['document_id'] for cid in ids):
                raise ValueError('unknown/duplicate evidence chunk')
            if not any(chunks[c]['start_char'] <= e['start_char']
                       and chunks[c]['end_char'] >= e['end_char'] for c in ids):
                if q['question_id'].startswith('W8-'):
                    raise ValueError('new evidence spans beyond accepted chunks')
                wide.append(dict(question_id=q['question_id'], evidence_id=e['evidence_id']))
        if 'agentic' in q:
            subtasks = q['agentic']['subtasks']
            if len(subtasks) != 2 or {s['subtask_id'] for s in subtasks} != {'A', 'B'}:
                raise ValueError('invalid subtask schema')
            expected = ['answer', q['expected_action']]
            if sorted(s['expected_action'] for s in subtasks) != sorted(expected):
                raise ValueError('task action disagrees with subtask actions')
            for s in subtasks:
                p = lookup[s['parent_question_id']]
                if payload_hash(p) != q['provenance']['parent_payload_sha256'][p['question_id']]:
                    raise ValueError('changed parent')
                if s['expected_action'] != p['expected_action'] or s['expected_response'] != p['gold_answer']:
                    raise ValueError('changed subtask gold')
                if not set(s['evidence_ids']) <= {e['evidence_id'] for e in q['evidence_requirements']}:
                    raise ValueError('unresolved subtask evidence')
            rebuilt = compose(int(q['question_id'].rsplit('-', 1)[1]), q['expected_action'],
                lookup[q['parent_question_ids'][0]], lookup[q['parent_question_ids'][1]],
                q['composition_service_id'], q['provenance']['source_sha256'])
            if q != rebuilt:
                raise ValueError('composition differs from declared parents')
    groups = leakage_groups(rows)
    sizes = Counter(g['leakage_group_id'] for g in groups)
    uses = Counter(p for q in added for p in q['parent_question_ids'])
    return dict(status='passed', question_count=len(rows), preserved_questions=len(base),
        added_questions=len(added), actions=dict(Counter(q['expected_action'] for q in rows)),
        question_types=dict(sorted(Counter(q['question_type'] for q in rows).items())),
        added_actions=dict(Counter(q['expected_action'] for q in added)),
        evidence_count=evidence_count, historical_wide_evidence=wide,
        historical_wide_question_count=len({e['question_id'] for e in wide}),
        new_wide_evidence_count=0, normalized_duplicate_count=0,
        distinct_parent_questions_used=len(uses), max_parent_reuse=max(uses.values()),
        composition_services=dict(sorted(Counter(q['composition_service_id'] for q in added).items())),
        leakage_group_count=len(sizes), largest_leakage_group=max(sizes.values()),
        distinct_evidence_signatures=len({tuple(sorted(chunk_ids(q))) for q in rows}),
        independent_holdout=False, human_verified=False, legal_currentness_verified=False,
        semantic_label_validation='inherited_from_parents; composition_requires_human_review',
        action_and_response_performance='unmeasured')


def build(source=SOURCE, db=DB, output=OUTPUT):
    output = Path(output)
    if output.exists():
        raise ValueError('output exists; choose a new directory')
    raw = Path(source).read_bytes()
    if not raw.endswith(b'\n'):
        raise ValueError('base must end with a newline to preserve its bytes')
    base = [json.loads(line) for line in raw.decode('utf-8').splitlines()]
    with closing(connect(db, True)) as connection:
        docs = {r['document_id']: dict(r) for r in connection.execute('SELECT * FROM documents')}
        chunks = {r['chunk_id']: dict(r) for r in connection.execute('SELECT * FROM chunks')}
        corpus_hash = corpus_identity(connection)
    chosen = select_pairs(base, chunks)
    new = [compose(i, action, a, b, service, digest(raw))
           for i, (action, a, b, service) in enumerate(chosen, 1)]
    rows = base + new
    audit = validate(rows, base, docs, chunks)
    output.mkdir(parents=True, exist_ok=False)
    write_jsonl(output/'new_questions.jsonl', new)
    (output/'questions.jsonl').write_bytes(raw + (output/'new_questions.jsonl').read_bytes())
    queries = [dict(query_id=q['question_id'], question=q['question']) for q in rows]
    write_jsonl(output/'queries.jsonl', queries)
    write_jsonl(output/'new_queries.jsonl', queries[608:])
    write_jsonl(output/'leakage_groups.jsonl', leakage_groups(rows))
    write_jsonl(output/'selection.jsonl', [dict(question_id=q['question_id'],
        expected_action=q['expected_action'], parent_question_ids=q['parent_question_ids'],
        composition_service_id=q['composition_service_id']) for q in new])
    with (output/'questions.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['question_id', 'question_type', 'expected_action', 'question', 'gold_answer', 'parents', 'review_status'])
        for q in rows:
            writer.writerow([q['question_id'], q['question_type'], q['expected_action'], q['question'],
                             q['gold_answer'], '|'.join(q.get('parent_question_ids', [])), q['review_status']])
    write_json(output/'validation.json', audit)
    write_json(output/'dataset.json', dict(dataset_id=VERSION, question_count=1000,
        base_dataset='week7-master608', preserved_questions=608, composed_questions=392,
        actions=audit['actions'], independent_holdout=False, human_verified=False,
        temporal_scope='existing_frozen_corpus; no_web_refresh',
        inference_input='queries.jsonl: query_id and question only; all other files are evaluator/review inputs',
        legacy_rows='608 original rows retained byte-for-byte, including their week7 dataset_id',
        selection_policy='shared service, distinct evidence contributions and question types for answer pairs; deterministic greedy diversity; one nonanswer parent per mixed task',
        split_policy='all diagnostic development; leakage_groups.jsonl supersedes per-row split_group_id for any future partition',
        mixed_task_policy='Task expected_action refers to unresolved case; other case still requires an answer. Use subtask evaluation.',
        limitations=['392 compositions are not 392 newly sourced independent intents.',
                     'Two independent cases in one prompt; no multi-turn user simulator or agent runtime.',
                     'No inferred minimum graph hop count or mandatory chain-of-thought/tool order.',
                     'Human semantic review remains pending. Model evaluation and agent implementation are outside this benchmark-only task.']))
    inputs = dict(base_sha256=digest(raw), corpus_identity=corpus_hash,
                  generator_sha256=digest(MODULE.read_bytes()))
    write_json(output/'manifest.json', dict(dataset_id=VERSION, inputs=inputs,
        outputs={p.name: digest(p.read_bytes()) for p in sorted(output.iterdir()) if p.is_file()},
        api_calls=0, model_inferences=0, deterministic=True))
    return audit


def verify(output=OUTPUT, source=SOURCE, db=DB):
    output = Path(output)
    manifest = json.loads((output/'manifest.json').read_text(encoding='utf-8'))
    if manifest['inputs']['base_sha256'] != digest(Path(source).read_bytes()):
        raise ValueError('base checksum differs')
    if manifest['inputs']['generator_sha256'] != digest(MODULE.read_bytes()):
        raise ValueError('generator checksum differs; rebuild in a fresh directory')
    for name, sha in manifest['outputs'].items():
        if digest((output/name).read_bytes()) != sha:
            raise ValueError('artifact checksum differs: ' + name)
    base, rows = read_jsonl(source), read_jsonl(output/'questions.jsonl')
    if not (output/'questions.jsonl').read_bytes().startswith(Path(source).read_bytes()):
        raise ValueError('base bytes not preserved')
    expected_queries = [dict(query_id=q['question_id'], question=q['question']) for q in rows]
    if read_jsonl(output/'queries.jsonl') != expected_queries:
        raise ValueError('query-only export differs')
    with closing(connect(db, True)) as connection:
        if corpus_identity(connection) != manifest['inputs']['corpus_identity']:
            raise ValueError('corpus identity differs')
        docs = {r['document_id']: dict(r) for r in connection.execute('SELECT * FROM documents')}
        chunks = {r['chunk_id']: dict(r) for r in connection.execute('SELECT * FROM chunks')}
    report = validate(rows, base, docs, chunks)
    if report != json.loads((output/'validation.json').read_text(encoding='utf-8')):
        raise ValueError('stored validation differs')
    if read_jsonl(output/'leakage_groups.jsonl') != leakage_groups(rows):
        raise ValueError('leakage groups differ')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('build', 'verify'))
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--db', type=Path, default=DB)
    parser.add_argument('--output-dir', type=Path, default=OUTPUT)
    args = parser.parse_args()
    report = (build if args.command == 'build' else verify)(source=args.source, db=args.db, output=args.output_dir)
    print(json.dumps({k: report[k] for k in ('status', 'question_count', 'added_questions', 'actions',
                                           'leakage_group_count', 'largest_leakage_group')}, indent=2))


if __name__ == '__main__':
    main()
