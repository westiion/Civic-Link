"""Measure the archived decisions, Graph use, and fixed-pool reranking on CPU."""

import csv
import json
import math
from collections import Counter, defaultdict
from time import perf_counter

import _bootstrap
from civic_link.agent.evaluation import covers, subtasks
from civic_link.agent.experiment import atomic_json, identity, read_json, result_path
from civic_link.agent.graph import GraphTool
from civic_link.agent.index import corpus_fingerprint, load_corpus, passage
from civic_link.tokenization import token_set
from run_week9_probes import ARCHIVE, BENCHMARK, DB, WORK, load_questions


CONDITIONS = ('single_search', 'iterative_search', 'iterative_graph')
OUT = WORK / 'feedback'
TOP_K = 10
RRF_K = 60


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def inspect_traces():
    question_rows = read_csv(WORK / 'trace-608/questions.csv')
    questions = {condition: {r['query_id']: r for r in question_rows if r['condition'] == condition}
                 for condition in CONDITIONS}
    turns = defaultdict(list)
    for row in read_csv(WORK / 'trace-608/turns.csv'):
        turns[row['condition'], row['query_id']].append(row)
    answer_ids = {qid for qid, row in questions[CONDITIONS[0]].items()
                  if row['expected_action'] == 'answer'}
    stages, reviewed = {}, []
    for condition, by_id in questions.items():
        answer_turns = [turn for qid in answer_ids for turn in turns[condition, qid]]
        flags = Counter(turn['review_flag'] for turn in answer_turns if turn['review_flag'])
        all_turns = [turn for qid in by_id for turn in turns[condition, qid]]
        stages[condition] = {
            'complete_by_stage': {
                key: sum(by_id[qid][key] == 'True' for qid in answer_ids)
                for key in ('first_returned_complete', 'retrieved_complete', 'supplied_complete',
                            'final_complete', 'cited_complete')
            },
            'any_context_omission_answer_questions': len({r['query_id'] for r in answer_turns
                                                         if json.loads(r['omitted_ids'])}),
            'final_requirements_lost_after_retrieval': sum(
                bool(set(json.loads(by_id[qid]['final_missing_ids'])) -
                     set(json.loads(by_id[qid]['retrieved_missing_ids']))) for qid in answer_ids),
            'review_flags': flags,
            'first_decision': Counter(
                (turns[condition, qid][0]['action'] or 'no_decision') for qid in answer_ids
                if turns[condition, qid]
            ),
            'first_decision_given_coverage': {
                'complete': Counter(turns[condition, qid][0]['action'] or 'no_decision'
                                    for qid in answer_ids if turns[condition, qid]
                                    and by_id[qid]['first_returned_complete'] == 'True'),
                'incomplete': Counter(turns[condition, qid][0]['action'] or 'no_decision'
                                      for qid in answer_ids if turns[condition, qid]
                                      and by_id[qid]['first_returned_complete'] == 'False'),
            },
            'calls_that_added_required_evidence': Counter(
                r['action'] for r in answer_turns if r['newly_covered_requirement_ids']
                and json.loads(r['newly_covered_requirement_ids'])),
            'max_input_chars': max(int(r['input_chars']) for r in all_turns),
        }
        for r in answer_turns:
            if r['review_flag']:
                reviewed.append({key: r[key] for key in (
                    'query_id', 'condition', 'turn', 'action', 'review_flag', 'allowed_actions',
                    'input_missing_requirement_ids', 'newly_covered_requirement_ids', 'decision_reason')})
    write_csv(OUT / 'decision-review-candidates.csv',
              sorted(reviewed, key=lambda r: (r['query_id'], r['condition'], int(r['turn']))))

    before, after = CONDITIONS[1:]
    graph = [turn for qid in questions[after] for turn in turns[after, qid] if turn['action'] == 'graph']
    graph_ids = {row['query_id'] for row in graph}
    changes = []
    for qid in sorted(answer_ids):
        b, c = (questions[condition][qid]['final_complete'] == 'True' for condition in (before, after))
        if b != c:
            changes.append(dict(query_id=qid, change='improved' if c else 'regressed',
                                used_graph=qid in graph_ids,
                                actions_c=[r['action'] for r in turns[after, qid]]))
    graph_ids_with_candidates = {
        qid for qid in questions[after] if any(json.loads(r['graph_candidate_ids']) for r in turns[after, qid])
    }
    return dict(answer_questions=len(answer_ids), stages=stages,
                graph=dict(calls=len(graph), questions=len(graph_ids),
                           questions_shown_candidates=len(graph_ids_with_candidates),
                           status=Counter(r['tool_status'] or 'not_executed' for r in graph),
                           modes=Counter(json.loads(r['arguments'])['mode'] for r in graph),
                           final_complete_changes=changes,
                           calls_adding_required_evidence=[
                               dict(query_id=r['query_id'], new=json.loads(r['newly_covered_requirement_ids']))
                               for r in graph if r['newly_covered_requirement_ids']
                               and json.loads(r['newly_covered_requirement_ids'])],
                           calls_detail=[{key: r[key] for key in (
                               'query_id', 'turn', 'tool_status', 'tool_cached', 'arguments',
                               'newly_covered_requirement_ids', 'tool_next_cursor')} for r in graph]))


def reverse_reference(question):
    start = 'ART-4bd69c7ec361a150c73a'
    expected = 'ART-fc45303f33854f8c5961'
    arguments = dict(mode='traverse', target_type='node', target_id=start, relation='REFERENCES')
    results = {}
    for direction in ('outgoing', 'incoming', 'both'):
        tool = GraphTool.from_files(db=DB, direction=direction)
        result = tool.run(arguments)
        target = tool.graph.nodes[expected]
        results[direction] = dict(status=result['status'], has_expected_reference=any(
            expected in (r['from'], r['to']) for r in result['relations']
        ), required_covered=[covers(result['evidence'], req) for req in question['evidence_requirements']],
            evidence_ids=[r['id'] for r in result['evidence']], expected_article=target['label'])
    return results


def rank_candidates(questions):
    corpus = load_corpus(DB)
    chunks = {r['id']: r for r in corpus}
    fingerprint = corpus_fingerprint(corpus)
    tokens = {r['id']: token_set(passage(r)) for r in corpus}
    df = Counter(token for vocabulary in tokens.values() for token in vocabulary)
    idf = {term: math.log1p((len(chunks) - freq + .5) / (freq + .5)) for term, freq in df.items()}
    outcomes, selected, durations = [], defaultdict(list), []
    for question in questions:
        if question['expected_action'] != 'answer':
            continue
        qid, text = question['question_id'], question['question']
        base = read_json(result_path(ARCHIVE, 'initial', qid))['result']
        pool = base['metadata']['candidate_ids']
        if base['metadata']['corpus_fingerprint'] != fingerprint:
            raise ValueError(f'{qid}: corpus differs from archived retrieval')
        before = pool[:TOP_K]
        if before != [r['id'] for r in base['evidence']]:
            raise ValueError(f'{qid}: BGE order does not reproduce the archived top 10')
        started = perf_counter()
        query = token_set(text)
        ordinal = {cid: rank for rank, cid in enumerate(pool, 1)}
        lexical = {cid: sum(idf[term] for term in sorted(query & tokens[cid])) for cid in pool}
        ranked = sorted((cid for cid in pool if lexical[cid] > 0),
                        key=lambda cid: (-lexical[cid], ordinal[cid]))
        lexical_rank = {cid: rank for rank, cid in enumerate(ranked, 1)}
        after = sorted(pool, key=lambda cid: (
            -(1 / (RRF_K + ordinal[cid]) +
              (1 / (RRF_K + lexical_rank[cid]) if cid in lexical_rank else 0)),
            ordinal[cid],
        ))[:TOP_K]
        durations.append(perf_counter() - started)
        required = question['evidence_requirements']
        pool_rows, before_rows, after_rows = ([chunks[cid] for cid in ids] for ids in (pool, before, after))
        first = all(covers(before_rows, req) for req in required)
        alternative = all(covers(after_rows, req) for req in required)
        available = all(covers(pool_rows, req) for req in required)
        group = 'existing_success' if first else 'rank_miss' if available else 'pool_miss'
        outcome = ('retained_success' if first else 'still_missing') if first == alternative else (
            'recovered' if alternative else 'regressed'
        )
        outcomes.append(dict(query_id=qid, group=group, outcome=outcome, bge_complete=first,
                             fusion_complete=alternative, bge_top10=json.dumps(before),
                             fusion_top10=json.dumps(after)))
        selected[outcome].append(dict(query_id=qid, question=text, group=outcome,
                                      expected=subtasks(question),
                                      variants={'bge': before_rows, 'rank_fusion': after_rows}))
    write_csv(OUT / 'fixed-pool-rankings.csv', outcomes)
    cases = []
    for outcome in ('recovered', 'regressed', 'retained_success', 'still_missing'):
        ranked = sorted(selected[outcome], key=lambda r: identity(['week9-answer-v1', r['query_id']]))
        cases.extend(ranked[:2])
    atomic_json(OUT / 'answer-cases.json', cases)
    return dict(groups={group: dict(Counter(row['outcome'] for row in outcomes if row['group'] == group))
                        for group in ('existing_success', 'rank_miss', 'pool_miss')},
                archived_bge_complete=sum(row['bge_complete'] for row in outcomes),
                fusion_complete=sum(row['fusion_complete'] for row in outcomes),
                average_incremental_seconds=sum(durations) / len(durations),
                corpus_fingerprint=fingerprint,
                answer_case_ids=[case['query_id'] for case in cases])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    original = load_questions(BENCHMARK / 'questions.jsonl')
    composite = load_questions(BENCHMARK / 'week8/new_questions.jsonl')
    results = inspect_traces()
    results['planning_population'] = dict(
        original=Counter(q.get('reasoning_classification') or 'unclassified' for q in original),
        composite=Counter(q['expected_action'] for q in composite),
    )
    results['reverse_reference'] = reverse_reference(next(q for q in original if q['question_id'] == 'SJ-B-034'))
    print('Archived traces and Graph cases checked; reranking the fixed candidate pools...', flush=True)
    results['reranking'] = rank_candidates(original)
    atomic_json(OUT / 'offline-results.json', results)
    print(f'Results: {OUT / "offline-results.json"}', flush=True)


if __name__ == '__main__':
    main()
