"""Build diagnostic facts and collapsible appendices from offline replay artifacts.

REPORT.md contains reviewed prose and is intentionally not regenerated here.
No model API is called. All baseline snapshots must match the run manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import subprocess
from collections import Counter, defaultdict, deque
from pathlib import Path

import networkx as nx

METHODS = ['bm25', 'dense', 'hybrid', 'graph_h0', 'graph_h1', 'graph_h2', 'graph_h3']
NAMES = dict(zip(METHODS, ['BM25', 'Dense', 'Hybrid', 'Graph h0', 'Graph h1', 'Graph h2', 'Graph h3']))
STATUS = {'success': '완전', 'partial': '부분', 'failure': '미회수', 'not_scored_unanswerable': '진단'}
TYPE = {'cross_reference': '법령 상호참조', 'direct_qa': '직접 질의', 'handling_agency': '처리기관',
        'legal_grounding': '법적 근거', 'multi_source_multi_hop': '다중 출처·복합 질의',
        'required_documents': '구비서류', 'requirement_condition': '자격·조건', 'service_discovery': '서비스 탐색'}
STAGE = {'retrieved': 'Top-10 회수', 'ranking': '후보 내 순위 탈락', 'no_seed': '시작점 없음',
         'unbound': '전역 바인딩 없음', 'seed_selection': '평가 서비스로 바꾸면 도달 가능',
         'reachability': '현재 시작점·방향·깊이에서 미도달', 'rrf_ranking': 'RRF 순위 탈락',
         'outside_top100_union': 'BM25/Dense Top-100 합집합 밖', 'no_lexical_match': '토큰 일치 없음'}


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def raw_git(root, ref, filename):
    return subprocess.check_output(['git', 'show', ref+':data/processed/'+filename], cwd=root)


def matched(raw, expected):
    lf = raw.replace(b'\r\n', b'\n')
    return any(hashlib.sha256(b).hexdigest() == expected for b in [raw, lf, lf.replace(b'\n', b'\r\n')])


def candidate_function(graph, evidence):
    nodes = {n['id']: n for n in graph['nodes']}
    outgoing = defaultdict(list)
    for e in graph['edges']:
        outgoing[e['source']].append(e)
    for es in outgoing.values():
        es.sort(key=lambda e: (e['target'], e['key']))

    def candidates(seeds, hop):
        cids = set()
        def collect(kind, owner, source_docs=None):
            for eid in evidence[kind+'_bindings'].get(owner, []):
                ev = evidence['records'][eid]
                if source_docs is None or ev['source_document_id'] in source_docs:
                    cids.update(ev['chunk_ids'])
        for seed in seeds:
            sid = seed['node_id']
            seen = {sid}
            queue = deque([(sid, 0, None)])
            while queue:
                node, depth, last = queue.popleft()
                allowed = None
                if last and nodes[node]['type'] in ('Document', 'Agency'):
                    allowed = {evidence['records'][x]['source_document_id'] for x in evidence['edge_bindings'][last]}
                collect('node', node, allowed)
                if depth >= hop:
                    continue
                for e in outgoing[node]:
                    collect('edge', e['key'])
                    if e['target'] not in seen:
                        seen.add(e['target'])
                        queue.append((e['target'], depth+1, e['key']))
        return cids
    return candidates


def facts(root, old, new, ref, traces, summary):
    manifests = {label: read(root/'results/week4_run'/run/'run_manifest.json') for label, run in [('old', old), ('new', new)]}
    old_raw = {k: raw_git(root, ref, f) for k, f in [('graph', 'graph.json'), ('graph_evidence', 'graph.evidence.json')]}
    assert all(matched(b, manifests['old']['source_sha256'][k]) for k,b in old_raw.items()), 'Baseline Git snapshot does not match run hashes, including line-ending variants.'
    old_graph, old_ev = json.loads(old_raw['graph']), json.loads(old_raw['graph_evidence'])
    graph, ev = read(root/'data/processed/graph.json'), read(root/'data/processed/graph.evidence.json')
    for key, filename in [('graph', 'graph.json'), ('graph_evidence', 'graph.evidence.json')]:
        assert hashlib.sha256((root/'data/processed'/filename).read_bytes()).hexdigest() == manifests['new']['source_sha256'][key]
    preds = {m: {p['query_id']:p for p in rows(root/'results/week4_run'/new/f'{m}.predictions.jsonl')} for m in METHODS}
    old_candidates, new_candidates = candidate_function(old_graph, old_ev), candidate_function(graph, ev)
    byqid = {t['query_id']: t for t in traces}
    result = {'baseline_git_ref': subprocess.check_output(['git', 'rev-parse', ref], cwd=root).decode().strip(),
              'baseline_graph_hashes_match_with_line_endings': True, 'fixed_seed_graph_diff': {},
              'hop_transitions': [], 'topology': {}, 'group_counts': {}, 'diagnostic': {}}
    for hop in range(4):
        method = f'graph_h{hop}'
        counter, changed = Counter(), []
        for t in traces:
            pred = preds[method][t['query_id']]
            assert not pred['traversal_truncated']
            ca, cb = old_candidates(pred['seeds'], hop), new_candidates(pred['seeds'], hop)
            assert cb == set(pred['candidate_chunk_ids'])
            counter['questions_candidate_set_changed'] += ca != cb
            counter['added_candidates'] += len(cb-ca)
            counter['removed_candidates'] += len(ca-cb)
            if t['expected_action'] == 'answer':
                for group in t['evidence_groups']:
                    gold = set(group['chunk_ids'])
                    before, after = bool(gold&ca), bool(gold&cb)
                    counter['newly_reachable_gold_groups'] += not before and after
                    counter['lost_gold_groups'] += before and not after
                    if before != after:
                        changed.append({'query_id':t['query_id'], 'group':group['group'], 'before':before, 'after':after})
        result['fixed_seed_graph_diff'][method] = {'counts':dict(counter), 'changed_gold_groups': changed}
    compare = lambda hits: [(h['chunk_id'], h['score'], h['path_nodes'], h['path_edges'], h['evidence_binding']) for h in hits]
    for transition in summary['hop_transitions']:
        h = transition['hop']
        fixed = json.loads(json.dumps(transition))
        fixed['counts']['questions_paths_or_scores_changed'] = sum(
            compare(preds[f'graph_h{h-1}'][qid]['hits']) != compare(preds[f'graph_h{h}'][qid]['hits']) for qid in byqid)
        result['hop_transitions'].append(fixed)
    nodes = {n['id']: n for n in graph['nodes']}
    G = nx.DiGraph()
    G.add_nodes_from(nodes)
    for e in graph['edges']:
        G.add_edge(e['source'], e['target'])
    article_owners = defaultdict(set)
    for node, eids in ev['node_bindings'].items():
        if nodes[node]['type'] != 'Article':
            continue
        for eid in eids:
            for cid in ev['records'][eid]['chunk_ids']:
                article_owners[cid].add(node)
    distance_cache = {}
    for t in traces:
        seeds = t['methods']['graph_h1']['seeds']
        distances = {}
        for seed in seeds:
            sid = seed['node_id']
            if sid not in distance_cache:
                distance_cache[sid] = nx.single_source_shortest_path_length(G, sid)
            for node, depth in distance_cache[sid].items():
                distances[node] = min(depth, distances.get(node, depth))
        groups = []
        for group in t['evidence_groups']:
            arts = set().union(*(article_owners[cid] for cid in group['chunk_ids']))
            ds = [distances[a] for a in arts if a in distances]
            incoming = [e for e in graph['edges'] if e['target'] in arts and e['relation'] == 'REFERENCES']
            groups.append({'group': group['group'], 'article_nodes': sorted(arts),
                           'min_directed_distance_to_article': min(ds) if ds else None,
                           'incoming_reference_sources': sorted({e['source'] for e in incoming})})
        result['topology'][t['query_id']] = groups
    scored = [t for t in traces if t['expected_action'] == 'answer']
    result['group_counts'] = {'answerable_groups':sum(len(t['evidence_groups']) for t in scored),
                             'globally_unbound_groups':sum(not g['globally_bound'] for t in scored for g in t['evidence_groups'])}
    result['diagnostic'] = {action:{'questions':sum(t['expected_action']==action for t in traces),
        'methods_with_nonempty_hits':{m:sum(bool(preds[m][t['query_id']]['hits']) for t in traces if t['expected_action']==action) for m in METHODS}}
        for action in ['clarify','abstain']}
    result['changed_question_ids'] = [t['query_id'] for t in scored if any(d['old_status'] != d['status'] for d in t['methods'].values())]
    result['every_method_failure_ids'] = [t['query_id'] for t in scored if all(d['status']=='failure' for d in t['methods'].values())]
    result['graph_h1_vs_hybrid'] = dict(Counter(('H_complete' if t['methods']['hybrid']['status']=='success' else 'H_incomplete')+'__'+('G_complete' if t['methods']['graph_h1']['status']=='success' else 'G_incomplete') for t in scored))
    result['code_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return result


# Case-specific conclusions are deliberately separate from the automatic stage labels.
# They are hypotheses/next checks, not automatically proven root causes.
NOTES = {
 'SJ-N-039': '실직 사유·사후조사·재산기준은 서로 다른 근거다. 현재 서비스 시작점에서는 세 조문 모두 미도달한다. Hybrid의 7위 사후조사 조문과 14·16위 보조 근거를 시작점 후보로 쓰고, 시행령→법률의 실제 참조를 따라 세 요건을 함께 회수하는 실험이 필요하다.',
 'SJ-N-040': '기초연금법 제10조는 회수하지만 시행규칙 제6조→법 제10조 방향의 참조만 존재한다. 서비스에서 정방향으로 hop만 늘려서는 규칙 제6조와 민법 제777조에 도달하지 못한다. Hybrid 2위인 시행규칙 제6조를 Article 시작점으로 바인딩해 기존 REFERENCES를 따라가는 실험이 우선이다.',
 'SJ-N-041': '등본의 수수료 면제와 수급자/수급권자 정의를 함께 찾아야 한다. h1에서는 평가 서비스로 교체하면 면제 조문, h3에서는 두 근거 모두 후보화된다. 시작 서비스 선택부터 검증하고, 회수 후에도 두 용어의 정의를 같은 뜻으로 합치지 않는지 확인한다.',
 'SJ-N-043': '서비스명을 쓰지 않은 평면도 공개 예외 질문에서 Graph 시작점이 없다. Hybrid 36위의 관리규칙 제11조를 후보로 삼을 여지는 있지만, 다른 정의 조문은 양쪽 Top-100 밖이다. 건물/평면도 표현의 서비스·조문 연결과 제11조의 참조 확장을 순서대로 검증한다.',
 'SJ-N-044': '지침 제10·11조는 Hybrid 1·5위지만 상위 규칙 제28조가 22위다. Graph는 인터넷 등기 표현으로 시작점을 잡지 못한다. 검색된 지침을 조문 시작점으로 삼는 방식과 상위 규정 포함 정책을 검증한다. 검색 실패가 세 근거 전부의 실패는 아니다.',
 'SJ-N-045': '가족관계증명서라는 서비스는 잡아도 면제 규칙 제28조와 정의 조문은 h3까지 후보가 아니다. 정의는 Hybrid 8위, 면제 규칙은 20위다. 수수료·면제 의도에 맞는 규칙을 추가 시작점으로 삼고 연결된 정의를 보존하는 실험이 필요하다.',
 'SJ-N-046': '발급 채널별 요금을 묻는데 등록규칙 제28조가 Graph 후보에 없다. Dense 18위 근거가 Hybrid에서는 53위다. 채널별 수수료 규정을 시작점 후보로 보완하고 온라인/창구/무인기 조건을 나눠 재정렬한다.',
 'SJ-N-047': '면제 규칙은 Hybrid 7위지만 수급자 정의는 62위이며 Graph에서는 둘 다 미도달한다. 회수한 면제 규칙에서 인용된 정의 조문을 확장하는 실험 대상이다. 정의가 Dense 2249위라는 점에서 유사도 검색만 늘리는 접근의 한계도 보인다.',
 'SJ-N-050': 'h2에서는 세 근거가 모두 후보이나 5·15·16위다. 이전 h2는 6·8·9위로 이미 완전 회수했다. 연결을 새로 만든 성공 사례로 해석하지 말고, GROUNDED_BY→REFERENCES 근거 묶음을 Top-10에 유지하는 재정렬 실험에 사용한다.',
 'SJ-N-052': '현 서비스 시작점으로는 발급시기 조문이 미도달하지만 평가 서비스로 바꾸면 도달한다. Dense 99위, Hybrid 190위라 단일 검색 결과도 약하다. 부가세 늦은 신고→해당 증명서비스 연결과 기한후신고/결정 후 발급 표현을 각각 검증해야 한다.',
 'SJ-N-055': '발급과 열람의 두 요금을 비교해야 한다. Hybrid는 10·25위로 한쪽만 회수하고 Graph는 둘 다 후보가 없다. 발급/열람을 두 하위 요구로 분리해 수수료규칙의 두 조문을 함께 남기는 실험이 필요하다.',
 'SJ-N-057': '정확한 두 서비스와 GROUNDED_BY 경로는 존재한다. Graph h1의 두 근거가 22·24위인 순위 문제다. 국세/지방세별 최소 근거를 확보하고 근거법 질문에는 안내 페이지보다 조문을 우선하는지 비교한다.',
 'SJ-N-060': '재열람 지침은 Hybrid 2위지만 상위 규칙은 16위다. Graph 시작점이 없어 hop 증가는 무효다. 검색된 지침에서 상위 규정을 회수하는 연결 정책과 서비스 종료라는 시간 조건의 보존을 확인한다.',
 'SJ-A-003': '정부24 기본 수수료와 성남시 무인발급 표/창구 예외의 세 근거가 필요하다. Dense는 지역 표 두 근거만, Hybrid는 한 근거만 Top-10에 남긴다. Graph 시작점도 없다. 채널·지역별 근거를 따로 유지해 상충처럼 보이는 요금을 적용 범위로 구분해야 한다.',
 'SJ-A-021': '법률상 청구권과 규칙상 위임서류를 동시에 찾아야 한다. Graph가 찾는 법 제14조는 입양관계증명서 경유이며 규칙 제19조는 미도달한다. 제19조→제14조의 기존 역방향 참조를 확인했고, Hybrid 16위 규칙을 추가 시작점으로 쓰는 비교가 필요하다.',
 'SJ-A-023': '제적등본/초본의 채널별 요금 근거인 규칙 제28조가 Graph 후보에 없고 Hybrid 20위다. N-046과 같은 규칙의 연결·요금 의도 문제를 공유한다. 두 질문을 별개 패치로 고치지 말고 같은 규칙/의도 묶음으로 검증한다.',
 'SJ-A-024': '2000년 사망 기록은 가족관계등록부보다 제적등본 안내가 필요하다. 평가 서비스로 시작하면 후보화되므로 서비스 선택이 확인할 지점이다. 연도/사망/구기록 조건을 활용한 서비스 연결을 시험하되 질문 문구 자체를 별칭으로 하드코딩하지 않는다.',
 'SJ-B-010': '소득금액증명 서비스는 선택되지만 발급매수라는 공통 절차 조문에는 경로가 없다. 양쪽 Top-100 밖이라 현재 Hybrid 후보에도 없다. 서비스별 근거만으로 공통 처리규정이 충분히 연결되는지 해당 조문의 적용 범위를 검수하고 후보 진입을 먼저 해결한다.',
 'SJ-B-011': '서비스 개요 대신 신청 변경·취하의 공통 처리규정이 필요하다. BM25 84위가 Hybrid 151위이고 Graph는 미도달한다. 신청 단계/변경/취하 의도로 공통 절차 조문을 찾는 실험 대상으로 삼는다.',
 'SJ-B-014': '거부 이유·구제절차 조문은 BM25 13위, Hybrid 21위로 가까우나 Graph 후보에는 없다. 먼저 후보 조문 재정렬과 공통 처리규정 연결을 분리해 비교한다. 수집 확대가 선행되어야 할 증거는 없다.',
 'SJ-B-019': '급여 변경 통지와 수급자의 신고 의무는 다른 조문이다. Dense 8·21위, Hybrid 15·56위이며 Graph는 둘 다 미도달한다. 두 의무의 주체를 분리해 후보를 회수하고, 한 조문만으로 신고 불필요라고 답하지 않도록 완전 근거 회수를 평가한다.',
 'SJ-B-032': '주거급여 신청 시작점이 있어도 하위 항목인 수선유지비 조문에는 유향 경로가 없다. Dense 17위, Hybrid 32위 조문을 직접 시작점으로 삼는 방식을 시험한다. 서비스명 일치만으로 급여의 세부 종류까지 해결되지 않는다.',
 'SJ-B-034': '거주지 변경과 조사 생략의 예외를 연결해야 한다. 두 규칙은 Hybrid 43·24위이고 Graph h3까지 미도달한다. 규칙 조문을 직접 후보화한 뒤 실제 참조를 따라 예외까지 회수하는 실험이 필요하다.',
 'SJ-A-002': '시작 서비스가 제적등본에서 주민등록표 등본 등으로 바뀌어 h1에서 제출서류 청크를 1위로 회수했다. 저장 경로는 REQUIRES→위임장이므로, 여권 유효기간까지 그 경로 자체가 증명한 것으로 설명해서는 안 된다. 청크 안의 신분증 항목을 별도 확인한다.',
 'SJ-A-022': '새 결과는 입양관계증명서의 기존 법 제14조 바인딩을 통해 회수한다. 성남시 가족관계 표의 별칭 보완 효과가 아니다. 대상 서비스가 아닌 관련 서비스에 의존하는 회수를 안정적인 설명 경로로 바꿀지 검토한다.',
 'SJ-N-026': 'BM25 8위와 Dense 3위 근거가 RRF에서 1위로 합쳐졌다. Graph도 납부내역증명을 시작점에 포함해 회수한다. 다만 납세증명서도 시작점에 남아 있어 부정 의도를 이해했다고 단정할 수 없다.',
 'SJ-N-029': '같은 실행에서 h0 3·6위 → h1 3·10위 → h2 3·12위다. 두 서비스 기본정보는 이미 있었으며 깊이를 늘리며 두 번째 근거가 탈락했다. 비교 대상별 기본 근거를 보호하는 실험 대상이다.',
 'SJ-A-058': '농지대장/토지대장 자격 비교에서 Hybrid가 4·10위로 완전 회수한다. Graph는 h0 1·3위, h1 1·10위, h2 1·13위이므로 깊은 확장이 비교 근거를 잃게 한다. 서비스별 근거 보존을 평가한다.',
 'SJ-N-004': 'h0 5위 → h1 10위 → h2 19위 → h3 24위로 같은 근거가 밀린다. 저장된 회수 경로는 건축물대장 서비스이고 허용 gold 청크 중 하나에 적중했다. 청크 적중과 질문을 뒷받침하는 정확한 표 행/경로는 구분한다.',
 'SJ-N-008': 'Dense 2위 근거가 BM25 Top-100 밖(122위)이라 Hybrid 18위로 밀렸다. Graph는 시작점이 없다. 단일 검색 방식의 강한 후보 보존과 등기기록 표현의 서비스/조문 연결을 각각 비교한다.',
 'SJ-A-025': '처리기관 질문 1건은 BM25/Dense/Hybrid 모두 성공하나 Graph 실패다. 평가 서비스를 시작점으로 바꾸면 근거에 도달하므로 기관 노드 추가보다 서비스 선택을 먼저 확인한다. 1건으로 기관 질문 전체의 성능을 일반화하지 않는다.',
}


def cell(value):
    return html.escape(str(value), quote=False).replace('|', '&#124;').replace('\n', ' ')


def link(qid):
    return f'[{qid}](ALL_QUESTIONS.md#{qid.lower()})'


def rank_list(groups, old=False):
    return ', '.join(f"E{g['group']}={g.get('old_rank') if old else g.get('rank')}".replace('None', '—' if old else '∅') for g in groups) or '참고 근거 없음'


def next_check(t):
    if t['query_id'] in NOTES:
        return NOTES[t['query_id']]
    if t['expected_action'] != 'answer':
        return '검색 결과 존재 여부와 clarify/abstain 판단은 별개다. 아래 참고 근거 순위로 정답률을 계산하지 않는다. 확인 질문 또는 답변 유보가 필요한 이유를 평가 답변 요지와 대조해야 한다.'
    graph = t['methods']['graph_h1']
    missing = {g['stage'] for g in graph['groups'] if g['stage'] != 'retrieved'}
    if not missing:
        if t['methods']['hybrid']['status'] != 'success':
            return 'Graph h1은 모든 필수 근거를 회수하지만 Hybrid는 불완전하다. 아래 경로로 얻은 근거를 Hybrid의 최종 Top-10에 넣을 때 기존 성공 근거를 밀어내지 않는지 비교한다. 단순 합집합의 회수를 최종 성능으로 간주하지 않는다.'
        return 'Hybrid와 Graph h1 모두 근거를 완전히 회수한다. 현재 회수 경로와 근거별 순위를 회귀 기준으로 보존한다. 완전 회수는 답변 생성 또는 근거 문장 적합성의 검증을 대신하지 않는다.'
    points = []
    if 'no_seed' in missing: points.append('시작점이 없어 확장이 실행되지 않는다. 검색 청크→서비스/조문 바인딩으로 후보 진입부터 비교한다')
    if 'seed_selection' in missing: points.append('평가 서비스로 바꾸면 도달하는 근거가 있어 시작점 선택을 우선 비교한다')
    if 'reachability' in missing: points.append('표시된 근거가 현재 경로에서 미도달한다. 조문 시작점·참조 방향·깊이를 확인한 뒤 연결 보완 여부를 결정한다')
    if 'ranking' in missing: points.append('후보에 있는 근거가 10위 밖이다. 질문 의도와 근거별 역할을 반영한 재정렬을 비교한다')
    return ' / '.join(points)+'. 아래 수치는 단계 진단이며 구체적인 의미적 원인은 추가 대조가 필요하다.'


def appendix(traces, summary):
    incomplete = set(summary['all_methods_incomplete_ids'])
    changed = {t['query_id'] for t in traces if any(m['old_status']!=m['status'] for m in t['methods'].values())}
    groups = [
        ('모든 방법에서 완전 회수하지 못한 질문', [t for t in traces if t['query_id'] in incomplete]),
        ('적어도 한 방법에서 완전 회수하며 전후 상태가 달라진 질문', [t for t in traces if t['expected_action']=='answer' and t['query_id'] not in incomplete and t['query_id'] in changed]),
        ('적어도 한 방법에서 완전 회수하며 전후 상태가 유지된 질문', [t for t in traces if t['expected_action']=='answer' and t['query_id'] not in incomplete and t['query_id'] not in changed]),
        ('확인 질문·답변 유보 진단용 질문', [t for t in traces if t['expected_action']!='answer'])]
    out = ['# 204개 질문의 검색 과정과 근거별 결과', '', '[핵심 분석 보고서](REPORT.md) · [상태 변화 37개 질문](STATUS_CHANGES.md) · [전체 정밀 추적 JSONL](question_traces.jsonl)', '',
           '**읽는 법:** 전=20261026_232637, 후=20261027_015454. 완전=필수 근거 그룹 전부 Top-10, 부분=일부, 미회수=0개다. 어느 한 방법이 완전하다는 뜻이지 모든 방법의 성공을 뜻하지 않는다.', '',
           'E1·E2는 아래에 적은 필수 근거 그룹이다. 여러 허용 청크가 있으면 가장 높은 순위를 표시한다. 이전 `—`는 저장된 Top-10에 없음을 뜻하며 정확한 이전 순위는 모른다. 현재 `∅`는 해당 방법의 후보에 없음을 뜻한다. BM25/Dense는 전체 corpus 순위, Hybrid는 각 Top-100의 RRF 합집합 순위, Graph는 각 hop의 전체 후보 순위다.', '',
           '**공통 과정:** BM25는 같은 Kiwi 규칙으로 질문·문서를 토큰화해 점수화한다. Dense는 캐시된 회사 모델 임베딩과 문서 벡터의 유사도로 정렬한다. Hybrid는 두 Top-100을 RRF(c=60)로 합친다. Graph는 아래 시작점→유향 관계 탐색→바인딩 근거 수집→점수 정렬을 수행한다. 법명이 명시된 경우 Law도 시작점이 될 수 있다. 이 실행에는 Hybrid+Graph 최종 결합이 없다.', '',
           '현재 Top-10 1,428개 결과 목록을 오프라인에서 재현했다. 아래의 성공/실패는 청크 수준 검색 판정이며, 법률 상담이나 생성 답변 정확도 판정이 아니다. 평가 답변은 저장된 동결 데이터의 요약이다.', '']
    included = []
    for title, items in groups:
        out += [f'## {title} — {len(items)}개', '']
        for t in sorted(items, key=lambda t:t['query_id']):
            qid=t['query_id'];included.append(qid)
            complete=[NAMES[m] for m,d in t['methods'].items() if d['status']=='success']
            label='완전 회수: '+', '.join(complete) if complete else '완전 회수 없음' if t['expected_action']=='answer' else t['expected_action']+' 진단'
            out += [f'<a id="{qid.lower()}"></a>', '<details>', f'<summary><strong>{qid}</strong> · {cell(label)} · {cell(t["question"])}</summary>', '',
                    f'**질문 유형:** {TYPE.get(t["question_type"], t["question_type"])} · **평가 동작:** {t["expected_action"]}', '',
                    '**평가 답변 요지:** '+cell(t['gold_answer']), '',
                    '**필요 근거**'+(' (진단용 참고 자료; 검색 성공률에서 제외)' if t['expected_action']!='answer' else ''), '']
            for e in t['evidence_groups']:
                representative = t['methods']['dense']['groups'][e['group']-1].get('best_title')
                title = representative.split(' / ')[0] if representative else e['source_alias']
                out.append(f"- E{e['group']}: {cell(title)} / {cell(e['locator'])} (`{e['source_alias']}`)")
            out += ['', '| 방식 | 전 → 후 | 이전 Top-10 근거 순위 | 현재 전체 후보에서 근거 순위 |', '|---|---|---|---|']
            for m,d in t['methods'].items():
                out.append(f"| {NAMES[m]} | {STATUS[d['old_status']]} → {STATUS[d['status']]} | {rank_list(d['groups'], True)} | {rank_list(d['groups'])} |")
            gh=t['methods']['graph_h1']
            out += ['', '**Graph 도출 과정**', '',
                    '- 시작점(점수 순): '+(' · '.join(cell(s['label'])+f"({s['score']:.3f})" for s in gh['seeds']) if gh['seeds'] else '없음')+'.',
                    '- h0/h1/h2/h3 후보 수: '+ ' / '.join(str(t['methods'][f'graph_h{h}']['candidates']) for h in range(4))+'개. 노드 탐색 한도에 걸린 결과는 없다.']
            for g in gh['groups']:
                chosen=g
                label='h1'
                if not g.get('path'):
                    for h in [2,3]:
                        candidate=t['methods'][f'graph_h{h}']['groups'][g['group']-1]
                        if candidate.get('path'):
                            chosen=candidate;label=f'h{h}';break
                if chosen.get('path'):
                    out.append(f"- E{g['group']}: {label} 경로 `{chosen['path']}` → {chosen['rank']}위, {STAGE[chosen['stage']]}. 바인딩 규칙 `{chosen['evidence_rule']}`.")
                else:
                    out.append(f"- E{g['group']}: h1 {STAGE[g['stage']]}; h3도 후보 없음.")
            if t['expected_action']!='answer' or not complete:
                for m in ['hybrid','graph_h1']:
                    top=t['methods'][m]['top3']
                    out.append(f"- {NAMES[m]} 상위 결과: "+('; '.join(f"{h['rank']}위 {cell(h['title'])}" for h in top) if top else '없음')+'.')
            out += ['', '**판단·다음 확인:** '+next_check(t), '', '</details>', '']
    assert len(included)==len(set(included))==204
    return '\n'.join(out)


def changes(traces):
    order={'failure':0,'partial':1,'success':2,'not_scored_unanswerable':-1}
    out=['# 전후 상태가 달라진 37개 질문', '', '[핵심 보고서](REPORT.md) · [204개 질문 상세](ALL_QUESTIONS.md)', '',
         'Top-10 상태(완전/부분/미회수)가 달라진 질문을 전부 표시했다. 상태가 같아도 순위는 달라질 수 있으므로 순위 변화 전체 목록이라는 뜻은 아니다. Dense는 모든 질문에서 동일하다.', '',
         '| 질문 | 개선된 방법 | 악화된 방법 |', '|---|---|---|']
    for t in sorted(traces,key=lambda t:t['query_id']):
        up,down=[],[]
        for m,d in t['methods'].items():
            a,b=order[d['old_status']],order[d['status']]
            text=f"{NAMES[m]} {STATUS[d['old_status']]}→{STATUS[d['status']]}"
            if a<b:up.append(text)
            if a>b:down.append(text)
        if up or down:out.append(f"| {link(t['query_id'])} {cell(t['question'])} | {'<br>'.join(up) or '—'} | {'<br>'.join(down) or '—'} |")
    return '\n'.join(out)+'\n'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--old', default='20261026_232637')
    p.add_argument('--new', default='20261027_015454')
    p.add_argument('--baseline-ref', default='135de8b')
    a = p.parse_args()
    dest = a.root/'results/week4_run'/a.new/'analysis'
    ts, summary = rows(dest/'question_traces.jsonl'), read(dest/'analysis_summary.json')
    extra = facts(a.root, a.old, a.new, a.baseline_ref, ts, summary)
    write(dest/'report_facts.json', extra)
    summary['hop_transitions'] = extra['hop_transitions']
    summary['graph_build_diff']['old_graph_matches_baseline'] = True
    summary['graph_build_diff']['old_graph_evidence_matches_baseline'] = True
    summary['postprocess'] = {'code_sha256': extra['code_sha256'],
        'changes': ['Exclude retriever method label when comparing paths/scores across hops.',
                    'Match Git snapshot hashes using original/LF/CRLF variants.'],
        'replay_unchanged': True}
    write(dest/'analysis_summary.json', summary)
    (dest/'ALL_QUESTIONS.md').write_text(appendix(ts, summary), encoding='utf-8')
    (dest/'STATUS_CHANGES.md').write_text(changes(ts), encoding='utf-8')
    print(json.dumps({k:extra[k] for k in ['fixed_seed_graph_diff','group_counts','graph_h1_vs_hybrid','diagnostic']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    main()
