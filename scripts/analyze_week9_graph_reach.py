"""Offline check: could the Graph candidates shown to C have supplied the evidence C finally lacked?

For each answer question whose final C input missed a required evidence item, every shown Graph
candidate is read directly (following cursors) and traversed one hop along each relation, in the
current outgoing direction and in the incoming direction (the reverse-reference follow-up plan).
No model is called; required-evidence IDs are used only to score reachability.
"""

import csv
import json
from collections import Counter

import _bootstrap  # noqa: F401
from civic_link.agent.evaluation import covers
from civic_link.agent.experiment import atomic_json
from civic_link.agent.graph import GraphTool
from run_week9_probes import BENCHMARK, DB, WORK, load_questions

OUT = WORK / 'feedback'
RELATIONS = ('GROUNDED_BY', 'REFERENCES', 'REQUIRES', 'HANDLED_BY', 'PART_OF')
MAX_PAGES = 6


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def collect(tool, arguments):
    rows, pages = [], 0
    args = dict(arguments)
    while pages < MAX_PAGES:
        result = tool.run(args)
        pages += 1
        rows.extend(result.get('evidence', []))
        cursor = result.get('next_cursor')
        if result.get('status') != 'ok' or not cursor:
            break
        args = dict(arguments, cursor=cursor)
    return rows


def node_type(tool, node_id):
    return tool.graph.nodes[node_id]['type'] if node_id in tool.graph.nodes else None


def main():
    questions = {q['question_id']: q for q in
                 [*load_questions(BENCHMARK / 'questions.jsonl'), *load_questions(BENCHMARK / 'week8/new_questions.jsonl')]}
    q_rows = {r['query_id']: r for r in read_csv(WORK / 'trace-608/questions.csv')
              if r['condition'] == 'iterative_graph' and r['expected_action'] == 'answer'}
    turns = {}
    for row in read_csv(WORK / 'trace-608/turns.csv'):
        if row['condition'] == 'iterative_graph':
            turns.setdefault(row['query_id'], []).append(row)
    tools = {d: GraphTool.from_files(db=DB, direction=d) for d in ('outgoing', 'incoming')}
    failed = [qid for qid, r in q_rows.items() if r['final_complete'] != 'True']
    print(f'{len(failed)} answer questions where C lacked a required evidence item', flush=True)
    results = []
    for qid in sorted(failed):
        question = questions[qid]
        missing = json.loads(q_rows[qid]['final_missing_ids'])
        reqs = [r for r in question['evidence_requirements'] if r['evidence_id'] in missing]
        candidates = []
        for turn in turns[qid]:
            for node_id in json.loads(turn['graph_candidate_ids'] or '[]'):
                if node_id not in candidates:
                    candidates.append(node_id)
        used_graph = any(t['action'] == 'graph' for t in turns[qid])
        found = {r['evidence_id']: [] for r in reqs}
        for node_id in candidates:
            if node_id not in tools['outgoing'].graph.nodes:
                continue
            attempts = [('direct', 'outgoing', dict(mode='direct', target_type='node', target_id=node_id))]
            for direction in ('outgoing', 'incoming'):
                for relation in RELATIONS:
                    attempts.append((f'traverse:{relation}', direction,
                                     dict(mode='traverse', target_type='node', target_id=node_id, relation=relation)))
            for how, direction, arguments in attempts:
                rows = collect(tools[direction], arguments)
                for req in reqs:
                    if rows and covers(rows, req):
                        found[req['evidence_id']].append(f'{how}@{direction}:{node_id}')
        results.append(dict(
            query_id=qid, used_graph_c=used_graph, candidates_shown=len(candidates), missing=missing,
            first_search_group=q_rows[qid]['first_search_group'],
            reachable={k: v for k, v in found.items() if v},
            unreachable=[k for k, v in found.items() if not v]))
        print(qid, 'reachable' if any(found.values()) else 'unreachable', flush=True)

    def hows(item):
        return [h for v in item['reachable'].values() for h in v]

    summary = dict(
        failed_questions=len(results),
        used_graph=sum(r['used_graph_c'] for r in results),
        any_candidate_reaches_missing=sum(bool(r['reachable']) for r in results),
        reachable_by_direct_read=sum(any(h.startswith('direct') for h in hows(r)) for r in results),
        reachable_by_outgoing_hop=sum(any(h.startswith('traverse') and '@outgoing' in h for h in hows(r)) for r in results),
        reachable_only_by_incoming_hop=sum(
            bool(r['reachable']) and all(h.startswith('traverse') and '@incoming' in h for h in hows(r))
            for r in results),
        no_candidate_reaches=sum(not r['reachable'] for r in results),
        first_search_groups=dict(Counter(r['first_search_group'] for r in results)))
    atomic_json(OUT / 'graph-reach.json', dict(summary=summary, questions=results))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f'Results: {OUT / "graph-reach.json"}')


if __name__ == '__main__':
    main()
