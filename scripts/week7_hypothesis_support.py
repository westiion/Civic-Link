"""Shared frozen-input/evaluation helpers. Gold is read only by evaluate_orders."""
import _bootstrap
from contextlib import closing
from pathlib import Path
from collections import defaultdict
from civic_link.common import ROOT, connect, digest, input_bytes, read_json, read_jsonl, write_json, write_jsonl
from civic_link.reranking.policies import rank_metrics
from evaluate_week7_bge_direct import read_rankings, direct_order

SOURCE = ROOT/'week7/results/experiment2_20261031'
BASELINE = ROOT/'week7/results/experiment2_bge_direct_20261101/rankings.csv'
QUERIES = ROOT/'data/benchmark/queries.jsonl'
QUESTIONS = ROOT/'data/benchmark/questions.jsonl'
DB = ROOT/'data/corpus/civic.sqlite'


def unique(rows, key):
    result = {r[key]: r for r in rows}
    if len(result) != len(rows):
        raise ValueError('duplicate '+key)
    return result


def load_frozen():
    protocol = read_json(SOURCE/'protocol.json')
    if digest(input_bytes(QUERIES)) != protocol['queries_sha256']:
        raise ValueError('questions changed since source run')
    manifest = read_json(BASELINE.parent/'manifest.json')
    expected = next(x['sha256'] for x in manifest['outputs'] if x['path'].endswith('/rankings.csv'))
    if digest(input_bytes(BASELINE)) != expected:
        raise ValueError('baseline ranking checksum differs')
    queries = unique(read_jsonl(QUERIES), 'query_id')
    scores = read_rankings(BASELINE)
    if set(queries) != set(scores) or sum(map(len, scores.values())) != protocol['candidate_pairs']:
        raise ValueError('frozen query/pool mismatch')
    return queries, scores, protocol


def corpus(db, protocol):
    from civic_link.graph.build import corpus_identity
    graph = read_json(ROOT/'data/graph/graph.json')
    with closing(connect(db, True)) as connection:
        value = connection.execute("select value from metadata where key='corpus_hash'").fetchone()
        if value is None or __import__('json').loads(value[0]) != protocol['corpus_hash']:
            raise ValueError('corpus metadata differs from source run')
        if corpus_identity(connection) != graph['graph']['corpus_fingerprint']:
            raise ValueError('corpus content fingerprint differs')
        chunks = {r['chunk_id']: dict(r) for r in connection.execute('select * from chunks')}
        documents = {r['document_id']: dict(r) for r in connection.execute('select * from documents')}
    return chunks, documents


def evaluate_orders(rows, destination, baseline_name, questions=QUESTIONS):
    """Full pool permutations, per-query ranks, regressions and type breakdown."""
    queries, pools, source = load_frozen()
    if digest(input_bytes(questions)) != source['questions_sha256']:
        raise ValueError('gold differs from the frozen experiment')
    gold = unique(read_jsonl(questions), 'question_id')
    rows = unique(rows, 'query_id')
    if not set(rows) == set(queries) == set(gold):
        raise ValueError('full evaluation requires all 608 questions')
    names = tuple(next(iter(rows.values()))['orders'])
    if baseline_name not in names:
        raise ValueError('missing baseline')
    records = []
    for qid, row in rows.items():
        if set(row['orders']) != set(names) or gold[qid]['question'] != queries[qid]['question']:
            raise ValueError('condition/question mismatch')
        for order in row['orders'].values():
            if len(order) != len(set(order)) or set(order) != set(pools[qid]):
                raise ValueError('candidate pool changed: '+qid)
        q = gold[qid]
        groups = [r['acceptable_chunk_ids'] for r in q['evidence_requirements']] if q['expected_action']=='answer' else []
        records.append(dict(query_id=qid, question=q['question'], question_type=q['question_type'],
            expected_action=q['expected_action'], metrics={n:rank_metrics(groups,row['orders'][n]) for n in names} if groups else {}))
    scored = [r for r in records if r['metrics']]
    def aggregate(items):
        return {n:{k:dict(n=len(items), complete=sum(r['metrics'][n][k]['all_evidence'] for r in items),
                    recall=sum(r['metrics'][n][k]['recall'] for r in items)/len(items),
                    mrr=sum(r['metrics'][n][k]['mrr'] for r in items)/len(items)) for k in ('10','100')} for n in names}
    changes = {}
    for n in names:
        changes[n] = dict(complete_gained=[], complete_lost=[], recall_improved=[], recall_worsened=[])
        for r in scored:
            a,b = r['metrics'][baseline_name]['10'],r['metrics'][n]['10']
            for label,field,sign in [('complete_gained','all_evidence',1),('complete_lost','all_evidence',-1),
                                      ('recall_improved','recall',1),('recall_worsened','recall',-1)]:
                if (b[field]-a[field])*sign>0: changes[n][label].append(r['query_id'])
    summary = dict(overall=aggregate(scored), changes=changes,
        by_type={t:aggregate([r for r in scored if r['question_type']==t]) for t in sorted({r['question_type'] for r in scored})},
        questions_sha256=digest(input_bytes(questions)), baseline=baseline_name,
        note='Post-hoc development evaluation; no independent holdout claim; 120 non-answer queries diagnostic only.')
    write_json(destination/'summary.json',summary)
    write_jsonl(destination/'per_question.jsonl',records)
    lines=['# Week7 가설 실험 결과', '', '같은 후보·청크 ID, 상위 10개. 답변 생성 평가는 아님. 분석에 사용한 개발셋 결과.', '',
           '| 조건 | 완전회수@10 | Recall@10 | MRR@10 | 완전회수@100 |','|---|---:|---:|---:|---:|']
    for n,m in summary['overall'].items():
        lines.append(f"| {n} | {m['10']['complete']}/{m['10']['n']} | {m['10']['recall']:.2%} | {m['10']['mrr']:.4f} | {m['100']['complete']} |")
    lines += ['', '문항별 순위는 rankings.jsonl, 근거 그룹별 순위는 per_question.jsonl, 유형별 지표·개선/악화 ID는 summary.json에 기록한다.']
    (destination/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return summary
