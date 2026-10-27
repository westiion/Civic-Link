"""Evaluate frozen Week4 results without model calls or gold-guided retrieval."""
import argparse
import json
from collections import Counter
from pathlib import Path

from civic_data import ROOT, connect, digest, read_jsonl, write_json, write_jsonl
from evaluate import evaluate
from graph_construct import load_graph
from prepare_h2 import compare as compare_bindings


def require(condition, message):
    if not condition:
        raise ValueError(message)


def found(requirements, chunk_ids):
    return {i for i, group in enumerate(requirements, 1) if set(group['acceptable_chunk_ids']) & chunk_ids}


def candidate_pool(row):
    if row['retriever'] == 'hybrid':
        return {h['chunk_id'] for h in row['hits']}
    # Diagnostic candidates also contain pruned Graph candidates. They are NOT rerank input.
    return {c['chunk_id'] for c in row['candidates'] if c['hybrid_rank'] is not None or
            (c['graph_rank'] is not None and not c['graph_excluded_by_cap'])}


def graph_pool(row):
    return {c['chunk_id'] for c in row.get('candidates', []) if c['graph_rank'] is not None and not c['graph_excluded_by_cap']}


def validate_pair(a, b, kind):
    if kind == 'baseline':
        require(a == b['hybrid_run'], 'baseline must be the same frozen Hybrid run')
        return
    for key in ('hybrid_run', 'tokenizer', 'tokenizer_package_version', 'score_version', 'rerank_weights', 'source_sha256'):
        require(a.get(key) == b.get(key) and key in a, 'paired provenance differs: '+key)
    ca, cb = dict(a['config']), dict(b['config'])
    if kind == 'h1':
        require(ca.pop('seed_mode') == 'lexical' and cb.pop('seed_mode') == 'hybrid', 'H1 requires lexical -> hybrid')
        require(ca['seed_weight'] == cb['seed_weight'] == 'uniform', 'H1 requires uniform seed weights')
    elif kind == 'relations':
        require(set(ca.pop('relations')) == {'GROUNDED_BY', 'REFERENCES'}, 'relations requires two-relation baseline')
        require(set(cb.pop('relations')) == {'GROUNDED_BY', 'REFERENCES', 'REQUIRES', 'HANDLED_BY', 'PART_OF'}, 'relations requires all five relations')
    elif kind == 'h3':
        require(ca.pop('rerank') == 'none' and cb.pop('rerank') == 'feature', 'H3 requires none -> feature')
    require(ca == cb, 'only the tested policy may differ')
    if kind != 'h2':
        for key in ('graph_sha256', 'evidence_sha256'):
            require(a[key] == b[key], 'paired graph differs')


def compare_files(before, after, output_dir, *, kind, db, questions, queries, before_graph=None, after_graph=None):
    output_dir = Path(output_dir)
    require(not output_dir.exists(), 'comparison output exists; choose a new directory')
    qs, query_rows = read_jsonl(questions), read_jsonl(queries)
    require(query_rows == [dict(query_id=q['question_id'], question=q['question']) for q in qs], 'question projection differs')
    qids = {q['question_id'] for q in qs}
    require(len(qids) == len(qs), 'duplicate evaluation question ID')
    with connect(db, True) as c:
        known = {r['chunk_id'] for r in c.execute('SELECT chunk_id FROM chunks')}
        corpus_hash = json.loads(c.execute("SELECT value FROM metadata WHERE key='corpus_hash'").fetchone()[0])
    manifests, results, reports, cases = {}, {}, {}, {}
    top_k = None
    for name, path in [('before', Path(before)), ('after', Path(after))]:
        manifest = json.loads(path.with_suffix('.run.json').read_text(encoding='utf-8'))
        require(digest(path.read_bytes()) == manifest['predictions_sha256'], 'prediction fingerprint differs')
        source = manifest.get('hybrid_run', manifest)
        require(source['queries_sha256'] == digest(Path(queries).read_bytes()), 'query fingerprint differs')
        require(source['corpus_hash'] == corpus_hash, 'corpus differs')
        rows = read_jsonl(path)
        require(len(rows) == len(qs) and {r['query_id'] for r in rows} == qids, 'prediction IDs differ')
        manifests[name] = manifest
        results[name] = {r['query_id']: r for r in rows}
    validate_pair(manifests['before'], manifests['after'], kind)
    top_k = manifests['after']['config']['top_k']
    require(top_k == 10, 'Week4 comparison reports use Top-10')
    if kind == 'h2':
        require(before_graph is not None and after_graph is not None, 'H2 requires both graph snapshots')
        pair = []
        for name, path in [('before', Path(before_graph)), ('after', Path(after_graph))]:
            require(digest(path.read_bytes()) == manifests[name]['graph_sha256'], 'H2 graph fingerprint differs')
            require(digest(path.with_suffix('.evidence.json').read_bytes()) == manifests[name]['evidence_sha256'], 'H2 evidence fingerprint differs')
            pair.append(load_graph(path, db))
        compare_bindings(*pair)
    details = []
    for q in qs:
        qid = q['question_id']; a, b = results['before'][qid], results['after'][qid]
        if kind == 'h3':
            signature = lambda row: {c['chunk_id']: (c['hybrid_rank'], c['graph_rank'], c['graph_excluded_by_cap'], c['paths']) for c in row['candidates']}
            require(signature(a) == signature(b) and a['seeds'] == b['seeds'], 'H3 upstream candidates or paths differ: '+qid)
            require([h['chunk_id'] for h in a['hits']] == a['rrf_top_chunk_ids'] == b['rrf_top_chunk_ids'], 'H3 RRF baseline differs')
        if kind == 'baseline':
            require(a['hits'][:10] == b['baseline_hits'], 'stored baseline differs')
        else:
            require(a['baseline_hits'] == b['baseline_hits'], 'paired Hybrid baseline differs')
        item = dict(query_id=qid, question=q['question'], question_type=q['question_type'],
                    scored=q['expected_answerability']=='answerable', required_groups=len(q['evidence_requirements']))
        for name, row in [('before', a), ('after', b)]:
            item[name] = dict(found=sorted(found(q['evidence_requirements'], {h['chunk_id'] for h in row['hits'][:10]})),
                pool_found=sorted(found(q['evidence_requirements'], candidate_pool(row))),
                graph_pool_found=sorted(found(q['evidence_requirements'], graph_pool(row))),
                top10=[dict(rank=h['rank'], chunk_id=h['chunk_id'], title=h.get('title')) for h in row['hits'][:10]],
                seeds=row.get('seeds', []), diagnostics=row.get('diagnostics', {}))
        aa, bb = set(item['before']['found']), set(item['after']['found'])
        item['change'] = ('improved' if len(bb)>len(aa) else 'worsened' if len(bb)<len(aa) else
                          'same_count_changed_groups' if aa!=bb else 'unchanged') if item['scored'] else 'diagnostic_only'
        item['gained_groups'], item['lost_groups'] = sorted(bb-aa), sorted(aa-bb)
        item['missed_in_pool'] = sorted(set(item['after']['pool_found'])-bb)
        details.append(item)
    summary = dict(kind=kind, question_count=len(qs), transitions=dict(Counter(d['change'] for d in details)), methods={})
    scored = [d for d in details if d['scored']]
    require(bool(scored), 'no answerable questions')
    for name in ['before', 'after']:
        predictions = [{**row, 'hits': row['hits'][:10]} for row in results[name].values()]
        reports[name], cases[name] = evaluate(qs, predictions, known)
        summary['methods'][name] = dict(metrics=reports[name]['retrieval']['overall']['metrics']['10'],
            status_counts=dict(Counter(c['retrieval_status_at_10'] for c in cases[name])),
            pool_recall=sum(len(d[name]['pool_found'])/d['required_groups'] for d in scored)/len(scored),
            graph_pool_recall=sum(len(d[name]['graph_pool_found'])/d['required_groups'] for d in scored)/len(scored))
    summary['scored_questions'] = len(scored)
    summary['top10_set_changed_queries'] = sum({h['chunk_id'] for h in d['before']['top10']} != {h['chunk_id'] for h in d['after']['top10']} for d in details)
    summary['missed_in_pool_groups'] = sum(len(d['missed_in_pool']) for d in scored)
    write_json(output_dir/'summary.json', summary)
    write_jsonl(output_dir/'per_question.jsonl', details)
    for name in reports:
        write_json(output_dir/f'{name}.metrics.json', reports[name])
        write_jsonl(output_dir/f'{name}.cases.jsonl', cases[name])
    write_json(output_dir/'evaluation_manifest.json', dict(input_runs=manifests,
        questions_sha256=digest(Path(questions).read_bytes()), evaluator_sha256=digest((Path(__file__).parent/'evaluate.py').read_bytes()),
        comparison_sha256=digest(Path(__file__).read_bytes()), api_calls=0))
    lines = [f'# {kind.upper()} 비교 결과', '', '현재 Kiwi·최신 법령 참조 기반의 별도 실행이다. 팀원의 과거 bigram 결과와 구분한다.', '',
             '| 조건 | 완전 근거 회수 | Recall@10 | MRR@10 | 후보 Recall |', '|---|---:|---:|---:|---:|']
    for name, value in summary['methods'].items():
        m=value['metrics']
        lines.append(f"| {name} | {value['status_counts'].get('success',0)}/{len(scored)} | {m['recall']:.2%} | {m['mrr']:.4f} | {value['pool_recall']:.2%} |")
    lines += ['', '변화는 회수한 근거 그룹 수 기준이다. 성공률 증가만으로 모든 지표의 개선을 주장하지 않는다.', '',
              '| 질문 유형 | 수 | 이전 Recall | 이후 Recall |', '|---|---:|---:|---:|']
    for typ, value in reports['before']['retrieval']['by_type'].items():
        other=reports['after']['retrieval']['by_type'][typ]
        lines.append(f"| {typ} | {value['question_count']} | {value['metrics']['10']['recall']:.2%} | {other['metrics']['10']['recall']:.2%} |")
    lines += ['', '| 질문 | 변화 | 근거 회수 전→후 |', '|---|---|---|']
    for d in scored:
        if d['change']!='unchanged':
            lines.append(f"| {d['query_id']} {d['question'].replace('|','/')} | {d['change']} | {len(d['before']['found'])}→{len(d['after']['found'])}/{d['required_groups']} |")
    lines += ['', '[질문별 전체 결과](per_question.jsonl) · [실행 검증 기록](evaluation_manifest.json)', '',
              '이 문서는 자동 집계다. 의미적 원인·개선 가설은 근거 원문과 경로를 확인해 별도로 분석한다. Gold 사람 검수와 답변 생성 평가를 대체하지 않는다.', '']
    (output_dir/'report.md').write_text('\n'.join(lines), encoding='utf-8')
    return summary


def main(kind=None):
    p=argparse.ArgumentParser(description=__doc__)
    if kind is None:p.add_argument('--kind', required=True, choices=['baseline','h1','h2','h3','relations'])
    p.add_argument('--before', type=Path, required=True);p.add_argument('--after', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--db', type=Path, default=ROOT/'data/processed/civic.sqlite')
    p.add_argument('--questions', type=Path, default=ROOT/'data/questions.jsonl')
    p.add_argument('--queries', type=Path, default=ROOT/'data/queries.jsonl')
    p.add_argument('--before-graph', type=Path);p.add_argument('--after-graph', type=Path)
    args=vars(p.parse_args());args['kind']=kind or args['kind']
    print(json.dumps(compare_files(**args), ensure_ascii=False, indent=2))


if __name__=='__main__':main()
