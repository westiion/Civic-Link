"""Offline, gold-free ranking policies and graph diagnostics on frozen Week 7 data.

This is an exploratory experiment, not a change to the production defaults.
No model inference, network requests, or writes to existing inputs are made.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401
import argparse
from collections import Counter, defaultdict
import csv
import gzip
import json
import math
from fractions import Fraction
from pathlib import Path
import random
import time

from civic_link.common import ROOT, digest, input_bytes, input_path, read_json, read_jsonl, write_json
from civic_link.reranking.policies import rank_metrics, stored_order
from week7_hypothesis_support import BASELINE, SOURCE, load_frozen


def json_lines(path):
    path = input_path(path)
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def features(row):
    """Only observed retrieval features; no question labels or gold accepted."""
    retained = [c for c in row['candidates'] if c['fusion_rank'] is not None]
    result = {}
    for c in retained:
        coverage = c['rerank_features']['chunk_coverage']
        graph = c['graph_rank'] is not None and not c['graph_excluded_by_cap']
        paths = c['paths'] if graph else []
        native = max((p['score'] for p in paths), default=0.)
        uniform = max(((.45 + coverage) / (1 + .12*p['graph_hops'])
                       * p['score_components']['condition_factor'] for p in paths), default=0.)
        structural = max((coverage / (1 + .12*p['graph_hops'])
                          * .25**sum(e['relation'] == 'PART_OF' for e in p['path_edges'])
                          for p in paths), default=0.)
        quote = c['rerank_features']['quote_coverage'] if graph else 0.
        # Suppress broad parent-law support and retain entry confidence once.
        evidence = max((quote / (1 + .5*p['graph_hops'])
                        * .1**sum(e['relation'] == 'PART_OF' for e in p['path_edges'])
                        * math.sqrt(p['score_components']['seed_score'])
                        for p in paths), default=0.)
        result[c['chunk_id']] = dict(
            hybrid=61/(60+c['hybrid_rank']) if c['hybrid_rank'] else 0.,
            coverage=coverage, native=native, uniform=uniform, structural=structural,
            evidence=evidence, graph_rank=c['graph_rank'] if graph else None)
    for name in ('native', 'uniform'):
        maximum = max((r[name] for r in result.values()), default=0.) or 1.
        for r in result.values():
            r[name] /= maximum
    return result


def policy_orders(row, scores, f):
    """Full permutations of the frozen candidate set; BGE raw logits preserved."""
    base = sorted(scores, key=lambda c: (-scores[c], c))
    assert set(f) == set(base)
    orders = dict(bge=base, h3=stored_order(row))
    ranks = {c: i for i, c in enumerate(base, 1)}
    for pct in (5, 10, 15, 20, 30, 40, 100):
        # Exact fractions preserve the existing script's stable tie policy.
        values = {c: Fraction(100-pct, 60+ranks[c]) +
                  (Fraction(pct, 60+f[c]['graph_rank']) if f[c]['graph_rank'] else 0) for c in base}
        orders[f'rrf_{pct:02}'] = sorted(base, key=lambda c: -values[c])
    for name in ('native', 'uniform', 'structural', 'evidence'):
        for strength in (.25, .5, 1., 2., 4.):
            values = {c: scores[c] + strength*f[c][name] for c in base}
            orders[f'logit_{name}_{strength:g}'] = sorted(base, key=lambda c: (-values[c], c))
    # Protect the first five results while exploring evidence near the cutoff.
    for strength in (1., 2., 4.):
        tail = sorted(base[5:], key=lambda c: (-(scores[c]+strength*f[c]['structural']), c))
        orders[f'protect5_structural_{strength:g}'] = base[:5] + tail
    # No neural model: simplex sweep, no overlap penalty (a separate ablation).
    for h in range(0, 11, 2):
        for g in range(0, 11-h, 2):
            c = 10-h-g
            values = {cid: h*f[cid]['hybrid'] + c*f[cid]['coverage'] + g*f[cid]['structural']
                      for cid in base}
            orders[f'linear_h{h}_c{c}_g{g}'] = sorted(base, key=lambda cid: (-values[cid], cid))
    return orders


def aggregate(records, name):
    n = len(records)
    values = [r['metrics'][name] for r in records]
    return dict(n=n, complete10=sum(v['10']['all_evidence'] for v in values),
                recall10=sum(v['10']['recall'] for v in values)/n,
                mrr10=sum(v['10']['mrr'] for v in values)/n,
                complete100=sum(v['100']['all_evidence'] for v in values),
                pool_complete=sum(v['pool_recall'] == 1 for v in values))


def paired(records, name, baseline='bge'):
    wins, losses = [], []
    differences = []
    for r in records:
        delta = r['metrics'][name]['10']['all_evidence'] - r['metrics'][baseline]['10']['all_evidence']
        differences.append(delta)
        if delta > 0:
            wins.append(r['query_id'])
        elif delta < 0:
            losses.append(r['query_id'])
    discordant = len(wins)+len(losses)
    p = min(1., 2*sum(math.comb(discordant, i) for i in range(min(len(wins), len(losses))+1))
            / 2**discordant) if discordant else 1.
    rng = random.Random(20261103)
    boot = sorted(sum(rng.choices(differences, k=len(differences)))/len(differences)
                  for _ in range(2000))
    return dict(wins=wins, losses=losses, exact_mcnemar_p=p,
                paired_bootstrap_delta95=[boot[49], boot[1949]],
                uncertainty_note='Exploratory, unadjusted for multiple comparisons; not independent validation.')


def select(records, names):
    # Fixed lexicographic objective: complete@10, recall@10, MRR@10, name.
    return max(names, key=lambda n: (aggregate(records, n)['complete10'],
                                    aggregate(records, n)['recall10'],
                                    aggregate(records, n)['mrr10'], n))


def cross_validate(records, names):
    """Group near paraphrases by existing intent and split groups (transitively)."""
    parents = {r['query_id']: r['query_id'] for r in records}
    def root(q):
        while parents[q] != q:
            parents[q] = parents[parents[q]]
            q = parents[q]
        return q
    owners = {}
    for r in records:
        for kind in ('intent_component_id', 'split_group_id'):
            identity = (kind, r[kind])
            if identity in owners:
                a, b = root(r['query_id']), root(owners[identity])
                parents[max(a, b)] = min(a, b)
            owners[identity] = r['query_id']
    fold = {r['query_id']: int(digest(root(r['query_id']))[:8], 16) % 5 for r in records}
    details, complete, recall, mrr = [], 0, 0., 0.
    for i in range(5):
        train = [r for r in records if fold[r['query_id']] != i]
        test = [r for r in records if fold[r['query_id']] == i]
        choice = select(train, names)
        metrics = aggregate(test, choice)
        complete += metrics['complete10']
        recall += metrics['recall10'] * len(test)
        mrr += metrics['mrr10'] * len(test)
        details.append(dict(fold=i, train_n=len(train), test_n=len(test), selected=choice,
                            test=metrics, test_baseline=aggregate(test, 'bge')))
    return dict(complete10=complete, recall10=recall/len(records), mrr10=mrr/len(records),
                n=len(records), groups=len({root(q) for q in parents}), folds=details,
                note='Internal grouped sensitivity analysis on repeatedly inspected synthetic development data; no holdout claim.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError('choose a new --out; existing results are preserved')
    started = time.perf_counter()
    queries, pools, protocol = load_frozen()
    questions_path = ROOT/'data/benchmark/questions.jsonl'
    if digest(input_bytes(questions_path)) != protocol['questions_sha256']:
        raise ValueError('gold changed since source experiment')
    questions = {q['question_id']: q for q in read_jsonl(questions_path)}
    graph_path = ROOT/'data/graph/graph.json'
    graph, evidence = read_json(graph_path), read_json(graph_path.with_suffix('.evidence.json'))
    run = read_json(SOURCE/'h3.run.json')
    for path, field in ((graph_path, 'graph_sha256'),
                        (graph_path.with_suffix('.evidence.json'), 'evidence_sha256')):
        if digest(input_bytes(path)) != run[field]:
            raise ValueError('graph or evidence differs from source run')
    coverage = {r['query_id']: stored_order(r) for r in json_lines(SOURCE/'coverage.jsonl')}
    nodes = {n['id']: n for n in graph['nodes']}
    outgoing = Counter(e['source'] for e in graph['edges'])
    incoming = Counter(e['target'] for e in graph['edges'])
    topology = dict(nodes=len(nodes), edges=len(graph['edges']),
                    node_types=dict(Counter(n['type'] for n in nodes.values())),
                    relations=dict(Counter(e['relation'] for e in graph['edges'])),
                    law_sinks=sum(n['type'] == 'Law' and not outgoing[nid] for nid, n in nodes.items()),
                    incoming_hubs=[dict(node_id=n, label=nodes[n]['label'], node_type=nodes[n]['type'],
                                        incoming=c, outgoing=outgoing[n]) for n, c in incoming.most_common(10)])
    all_bound = {cid for e in evidence['records'].values() for cid in e['chunk_ids']}
    records, diag = [], []
    original = {r['query_id']: r['metrics'] for r in read_jsonl(BASELINE.parent/'per_question.jsonl')}
    seen = set()
    for row in json_lines(SOURCE/'h3.jsonl'):
        qid = row['query_id']
        if qid in seen or qid not in queries:
            raise ValueError('unknown or duplicate query')
        seen.add(qid)
        q = questions[qid]
        assert q['question'] == queries[qid]['question']
        f = features(row)
        assert set(f) == set(pools[qid])
        diag.append(dict(query_id=qid, seed_count=len(row['seeds']),
                         distinct_entry_chunks=len({s['entry_chunk_id'] for s in row['seeds']}),
                         seed_limit=sum(d['reason'] == 'seed_limit' for d in row['seed_decisions']),
                         per_chunk_limit=sum(d['reason'] == 'per_chunk_limit' for d in row['seed_decisions']),
                         **row['diagnostics']))
        if q['expected_action'] != 'answer':
            continue
        orders = policy_orders(row, pools[qid], f)
        orders['coverage'] = coverage[qid]
        groups = [r['acceptable_chunk_ids'] for r in q['evidence_requirements']]
        metrics = {name: rank_metrics(groups, order) for name, order in orders.items()}
        for k in ('10', '100'):
            for field in ('all_evidence', 'recall', 'mrr'):
                assert metrics['bge'][k][field] == original[qid][k][field]
        records.append(dict(query_id=qid, question_type=q['question_type'], split=q['split'],
                            split_group_id=q['split_group_id'], intent_component_id=q['intent_component_id'],
                            all_groups_graph_bound=all(set(g) & all_bound for g in groups), metrics=metrics))
    assert seen == set(queries) and len(records) == 488
    names = tuple(records[0]['metrics'])
    overall = {n: aggregate(records, n) for n in names}
    families = dict(rrf=['bge']+[n for n in names if n.startswith('rrf_')],
                    logit=['bge']+[n for n in names if n.startswith('logit_') or n.startswith('protect5_')],
                    nonneural=['coverage', 'h3']+[n for n in names if n.startswith('linear_')])
    selection = {family: dict(best_full=select(records, options),
                              grouped_cv=cross_validate(records, options)) for family, options in families.items()}
    best_names = set(['bge', 'h3', 'coverage', 'rrf_05', 'rrf_10', 'rrf_15', 'rrf_40',
                      'logit_structural_1', 'logit_evidence_1'])
    best_names.update(s['best_full'] for s in selection.values())
    summary = dict(overall=overall, selection=selection, topology=topology,
                   comparisons={n: paired(records, n) for n in sorted(best_names)},
                   by_type={t: {n: aggregate([r for r in records if r['question_type'] == t], n)
                                for n in sorted(best_names)} for t in sorted({r['question_type'] for r in records})},
                   all_gold_groups_graph_bound=sum(r['all_groups_graph_bound'] for r in records),
                   queries=608, eligible=488, ranking_policies=len(names), api_calls=0,
                   new_model_inferences=0, runtime_seconds=time.perf_counter()-started,
                   limitations=['Synthetic, repeatedly inspected development benchmark.',
                                'All fixed-pool candidates and BGE scores are from the 512-token run.',
                                'Complete@10 measures evidence recall, not generated-answer quality.',
                                'Grouped cross-validation does not remove prior analyst exposure.'])
    args.out.mkdir(parents=True)
    write_json(args.out/'summary.json', summary)
    write_json(args.out/'diagnostics.json', diag)
    # Compact, machine-readable per-query evidence ranks without duplicating text.
    with gzip.open(args.out/'per_question.jsonl.gz', 'wt', encoding='utf-8') as stream:
        for r in records:
            stream.write(json.dumps(r, ensure_ascii=False, separators=(',', ':'))+'\n')
    with (args.out/'metrics.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=['policy', *next(iter(overall.values())).keys(), 'wins', 'losses'])
        writer.writeheader()
        for name, values in overall.items():
            wins = sum(r['metrics'][name]['10']['all_evidence'] > r['metrics']['bge']['10']['all_evidence'] for r in records)
            losses = sum(r['metrics'][name]['10']['all_evidence'] < r['metrics']['bge']['10']['all_evidence'] for r in records)
            writer.writerow(dict(policy=name, **values, wins=wins, losses=losses))
    paths = [questions_path, ROOT/'data/benchmark/queries.jsonl', graph_path,
             graph_path.with_suffix('.evidence.json'), BASELINE, SOURCE/'h3.jsonl', SOURCE/'coverage.jsonl', Path(__file__)]
    write_json(args.out/'manifest.json', dict(
        inputs=[dict(path=str(p.relative_to(ROOT)), logical_sha256=digest(input_bytes(p))) for p in paths],
        outputs=[dict(path=p.name, sha256=digest(p.read_bytes())) for p in sorted(args.out.iterdir())],
        selection_uses_gold=False, hyperparameter_selection_uses_gold=True,
        independent_holdout=False, api_calls=0, new_model_inferences=0))
    print(json.dumps(dict(best={f: s['best_full'] for f, s in selection.items()},
                          cv={f: s['grouped_cv']['complete10'] for f, s in selection.items()},
                          baseline=overall['bge'], seconds=summary['runtime_seconds']), indent=2))


if __name__ == '__main__':
    main()
