



from __future__ import annotations
import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
import networkx as nx
from civic_data import ROOT, connect, digest, read_jsonl, write_json

VERSION = 'week3-restricted-v2'
ARTICLE = r'제\s*\d+\s*조(?:\s*의\s*\d+)?'
NODE_REQUIRED = {
    'Service': ['label', 'category', 'registry_sha256', 'aliases', 'entry_source_document_id'],
    'Document': ['label', 'document_kind'],
    'Agency': ['label', 'agency_scope'],
    'Article': ['label', 'source_document_id', 'locator'],
    'Law': ['label', 'source_document_id', 'effective_date'],
}
RELATIONS = {'REQUIRES': ('Service','Document'), 'HANDLED_BY': ('Service','Agency'),
             'GROUNDED_BY': ('Service','Article'), 'PART_OF': ('Article','Law'),
             'REFERENCES': ('Article','Article')}


def compact(text): return re.sub(r'[^가-힣a-zA-Z0-9]', '', text).lower()
def stable(prefix, *parts): return prefix+'-'+digest('\n'.join(parts))[:20]
def article_id(did, locator): return stable('ART',did,compact(locator))


def corpus_identity(c):
    return digest(json.dumps({t:[list(r) for r in c.execute(f'SELECT * FROM {t} ORDER BY 1,2')]
                             for t in ['services','documents','document_services','chunks']},
                            ensure_ascii=False,sort_keys=True))


def service_aliases(name):
    values={name, re.sub(r'\s*(신청|요청)$','',name)}

    values.add(name.replace('주민등록표 ', '주민등록'))
    values.update(word for word in name.split() if len(word)>=4)
    return sorted(x for x in values if x)


class EvidenceBuilder:
    def __init__(self, docs, chunks, fingerprint):
        self.docs=docs; self.chunks=chunks
        self.by_doc=defaultdict(list)
        for c in chunks.values(): self.by_doc[c['document_id']].append(c)
        self.data=dict(schema_version=VERSION,corpus_fingerprint=fingerprint,
                       records={},node_bindings={},edge_bindings={})

    def span(self,did,start,end,rule,role='source_statement'):
        d=self.docs[did]; quote=d['body'][start:end]
        cs=sorted((c for c in self.by_doc[did] if c['start_char']<end and c['end_char']>start),key=lambda c:c['start_char'])
        covered=start
        for c in cs:
            if c['start_char']>covered: break
            covered=max(covered,c['end_char'])
        if not quote.strip() or covered<end: raise ValueError('uncovered evidence span')
        record=dict(source_document_id=did,source_alias=d['alias'],chunk_ids=sorted(c['chunk_id'] for c in cs),
                    quote=quote,start_char=start,end_char=end,source_url=d['source_url'],
                    source_content_hash=d['content_hash'],effective_date=d['effective_date'],
                    collected_at=d['collected_at'],last_checked_at=d['last_checked_at'],
                    extraction_rule=rule,role=role,review_status='machine_extracted_pending_human_review')
        eid=stable('EV',json.dumps(record,ensure_ascii=False,sort_keys=True))
        self.data['records'][eid]=record
        return eid

    def bind(self,kind,owner,eids):
        bs=self.data[kind+'_bindings'].setdefault(owner,[])
        bs[:]=sorted(set(bs)|set(eids))


def build_graph(db, registry):
    services=read_jsonl(registry); registry_hash=digest(Path(registry).read_bytes())
    with connect(db,True) as c:
        docs={r['document_id']:dict(r) for r in c.execute('SELECT * FROM documents ORDER BY document_id')}
        chunks={r['chunk_id']:dict(r) for r in c.execute('SELECT * FROM chunks ORDER BY chunk_id')}
        dbservices={r['service_id']:dict(r) for r in c.execute('SELECT * FROM services')}
        fingerprint=corpus_identity(c)
        corpus_hash=json.loads(c.execute("SELECT value FROM metadata WHERE key='corpus_hash'").fetchone()[0])
    if {s['service_id'] for s in services}!=set(dbservices): raise ValueError('registry mismatch')
    g=nx.MultiDiGraph(schema_version=VERSION,corpus_hash=corpus_hash,corpus_fingerprint=fingerprint,
                      registry_sha256=registry_hash,networkx_version=nx.__version__,
                      builder_sha256=digest(Path(__file__).read_bytes()))
    eb=EvidenceBuilder(docs,chunks,fingerprint); unresolved=[]
    sections=defaultdict(list)
    for ch in chunks.values(): sections[ch['document_id'],ch['locator']].append(ch)
    aliases={d['alias']:did for did,d in docs.items()}
    laws={compact(d['title']):did for did,d in docs.items() if d['source_type']=='law'}
    articles={}

    def add_edge(u,v,rel,eids,**qualifiers):
        key=stable('EDGE',u,v,rel,json.dumps(qualifiers,ensure_ascii=False,sort_keys=True))
        a=g.nodes[u].get('source_document_id'); b=g.nodes[v].get('source_document_id')
        g.add_edge(u,v,key=key,relation=rel,document_hop=int(bool(a and b and a!=b)),
                   qualifiers=qualifiers,review_status='machine_extracted_pending_human_review')
        eb.bind('edge',key,eids)


    for did in sorted(laws.values()):
        d=docs[did]; lid=stable('LAW',did)
        g.add_node(lid,type='Law',label=d['title'],source_document_id=did,effective_date=d['effective_date'])
        first=min(eb.by_doc[did],key=lambda c:c['start_char'])
        ev=eb.span(did,first['start_char'],min(first['end_char'],first['start_char']+200),'law_source_identity','identity_context')
        eb.bind('node',lid,[ev])
    for (did,loc),cs in sorted(sections.items()):
        if docs[did]['source_type']!='law' or not re.fullmatch(ARTICLE,loc): continue
        aid=article_id(did,loc); articles[did,compact(loc)]=aid
        start=min(c['start_char'] for c in cs); end=max(c['end_char'] for c in cs)
        line=docs[did]['body'][start:end].splitlines()[0]
        heading=re.match('('+ARTICLE+r'\([^\n)]*\))',line)
        g.add_node(aid,type='Article',label=heading.group(1) if heading else loc,source_document_id=did,locator=loc)
        eids=[eb.span(did,c['start_char'],min(c['end_char'],c['start_char']+200),'article_chunk_anchor','article_context') for c in cs]
        eb.bind('node',aid,eids)
        add_edge(aid,stable('LAW',did),'PART_OF',[eids[0]],basis='article_in_source_law')

    names=sorted((docs[d]['title'] for d in laws.values()),key=len,reverse=True)
    named=re.compile(r'「(?P<quoted>[^」\n]{2,100})」|(?P<known>'+('|'.join(r'\s*'.join(map(re.escape,t.split())) for t in names) or r'(?!)')+')')
    def citations(did,loc,cs):
        start=min(c['start_char'] for c in cs); end=max(c['end_char'] for c in cs)
        body=docs[did]['body'][start:end]; found=[]; occupied=[]
        def record(target,article,a,b,rule):
            dest=articles.get((target,compact(article)))
            if dest: found.append((dest,eb.span(did,start+a,start+b,rule)))
            else: unresolved.append(dict(source_document_id=did,locator=loc,target_document_id=target,
                                          target_article=article,quote=body[a:b],reason='article_not_indexed'))
        for m in named.finditer(body):
            target=laws.get(compact(m.group('quoted') or m.group('known')))
            if not target:
                unresolved.append(dict(source_document_id=did,locator=loc,quote=m.group(),reason='named_law_not_in_corpus')); continue
            tail=body[m.end():]
            lead=re.match(r'\s*(?:\(이하[^)\n]{1,70}\)\s*)?\(?\s*('+ARTICLE+')',tail)
            if not lead:
                unresolved.append(dict(source_document_id=did,locator=loc,quote=m.group(),reason='law_mention_without_article'));continue
            pos=lead.end();record(target,lead.group(1),m.start(),m.end()+pos,'explicit_law_article')
            while True:
                extra=re.match(r'[\s,ㆍ·)(]*(?:및|또는)?\s*('+ARTICLE+')',tail[pos:])
                if not extra:break
                pos+=extra.end();record(target,extra.group(1),m.start(),m.end()+pos,'explicit_law_article')
            occupied.append((m.start(),m.end()+pos))
        declared={}
        for m in re.finditer(r'「([^」]+)」\s*\(이하\s*[“"「]([^”"」]+)[”"」]이라?\s*한다\)',docs[did]['body']):
            td=laws.get(compact(m.group(1)))
            if td:declared[m.group(2)]=td
        for alias,target in declared.items():
            for m in re.finditer(r'(?<![가-힣])'+re.escape(alias)+r'\s+('+ARTICLE+')',body):
                if not any(a<=m.start()<b for a,b in occupied):record(target,m.group(1),m.start(),m.end(),'document_declared_alias')
        return found

    for (did,loc),cs in sorted(sections.items()):
        aid=articles.get((did,compact(loc)))
        if aid:
            for target,eid in citations(did,loc,cs):
                if aid!=target:add_edge(aid,target,'REFERENCES',[eid],basis='explicit_article_reference')

    for s in services:
        sid=s['service_id']; d=docs[aliases[s['entry_alias']]]; did=d['document_id']
        if any(s[k]!=dbservices[sid][k] for k in ['name','category']):raise ValueError('registry identity mismatch')
        g.add_node(sid,type='Service',label=s['name'],category=s['category'],registry_sha256=registry_hash,aliases=service_aliases(s['name']),entry_source_document_id=did)
        for (src,loc),cs in sorted(sections.items()):
            if src!=did:continue
            if loc in ('서비스 개요','기본정보'):
                for ch in cs:eb.bind('node',sid,[eb.span(did,ch['start_char'],min(ch['end_char'],ch['start_char']+200),'entry_guide_context','service_context')])
            if loc in ('기본정보','부가정보') and d['source_type']=='service_guide':
                for target,eid in citations(did,loc,cs):add_edge(sid,target,'GROUNDED_BY',[eid],basis='entry_guide_explicit_citation')
            if d['source_type']=='law' and (did,compact(loc)) in articles:
                start=min(c['start_char'] for c in cs);end=max(c['end_char'] for c in cs)
                for name in g.nodes[sid]['aliases']:
                    at=d['body'].find(name,start,end)
                    if at>=0:
                        eid=eb.span(did,at,at+len(name),'service_name_in_entry_law','service_mention')
                        eb.bind('node',sid,[eid]);add_edge(sid,articles[did,compact(loc)],'GROUNDED_BY',[eid],basis='explicit_service_mention_not_eligibility');break

        a=d['body'].find('민원인이 제출해야하는 서류')
        if a>=0:
            stop=min([p for token in ['민원인이 제출하지 않아도','QR코드','부가정보'] if (p:=d['body'].find(token,a+1))>=0] or [len(d['body'])])
            condition=None; parent_condition=None; offset=a
            for raw in d['body'][a:stop].splitlines(keepends=True):
                line=raw.strip();begin=offset+len(raw)-len(raw.lstrip());offset+=len(raw)
                if not line or line.startswith(('민원인이','※','「','QR','참고','유형 [','*')):continue
                if line.endswith('경우') or re.fullmatch(r'(국문|영문)\s*증명',line):
                    condition=(line,begin,begin+len(line))
                    if not re.match(r'^\d+-\d+',line): parent_condition=condition
                    continue
                if '없음' in line or '서식없음' in line or '같음' in line or len(line)>500:continue
                if not any(w in line for w in ['신분증','위임장','증명','신청서','확인서','여권','등록증','자료','허가서','동의서','서류']):continue
                label=re.split(r'\s*(?:제시|제출|\()',re.sub(r'^[-ㆍ·\d.\s]+','',line))[0].strip()
                if not label or len(label)>100:continue
                dn=stable('REQ',label);g.add_node(dn,type='Document',label=label,document_kind='application_requirement')
                ev=eb.span(did,begin,begin+len(line),'applicant_required_document');eids=[ev]
                conditions=list(dict.fromkeys(c for c in [parent_condition,condition] if c))
                for cond in conditions:eids.append(eb.span(did,cond[1],cond[2],'applicant_condition','condition'))
                eb.bind('node',dn,[ev]);add_edge(sid,dn,'REQUIRES',eids,condition=' / '.join(c[0] for c in conditions) or '원문 조건 확인',
                                               statement=line,requirement_mode='conditional_statement_not_atomic_obligation')

        for m in re.finditer(r'(?:^|\n)(접수|처리)\n([^\n]+)',d['body']):
            label=m.group(2).strip()
            if label.isdigit() or label=='참고':continue
            an=stable('AGENCY',label);g.add_node(an,type='Agency',label=label,agency_scope='as_written_office_or_category')
            ev=eb.span(did,m.start(1),m.end(2),'guide_reception_processing');eb.bind('node',an,[ev])
            add_edge(sid,an,'HANDLED_BY',[ev],role=m.group(1),local_office_resolution='not_inferred')

        for ch in chunks.values():
            if docs[ch['document_id']]['source_type']=='municipal_table' and any(name in ch['text'] for name in g.nodes[sid]['aliases']):
                eb.bind('node',sid,[eb.span(ch['document_id'],ch['start_char'],min(ch['end_char'],ch['start_char']+200),'local_service_row','service_context')])
    g.graph['unresolved_reference_count']=len(unresolved)

    g.graph['nodes_without_evidence']=[n for n in g if n not in eb.data['node_bindings']]
    validate_graph(g,eb.data,docs,chunks)
    return g,eb.data,unresolved


def validate_graph(g,evidence,docs=None,chunks=None):
    if not isinstance(g,nx.MultiDiGraph) or g.graph.get('schema_version')!=VERSION:raise ValueError('unsupported graph schema')
    if evidence.get('schema_version')!=VERSION or evidence.get('corpus_fingerprint')!=g.graph['corpus_fingerprint']:raise ValueError('evidence identity mismatch')
    for nid,n in g.nodes(data=True):
        if n.get('type') not in NODE_REQUIRED or any(k not in n for k in NODE_REQUIRED[n['type']]):raise ValueError('missing required node property')
        if any(n[k] is None or n[k]=='' for k in NODE_REQUIRED[n['type']] if k!='effective_date'):raise ValueError('null/empty required node property')
        if not evidence['node_bindings'].get(nid):raise ValueError('node without evidence')
    edge_ids=set()
    for u,v,k,e in g.edges(keys=True,data=True):
        if e.get('relation') not in RELATIONS or (g.nodes[u]['type'],g.nodes[v]['type'])!=RELATIONS[e['relation']]:raise ValueError('disallowed relation')
        if k in edge_ids:raise ValueError('duplicate edge ID')
        edge_ids.add(k)
        if not evidence['edge_bindings'].get(k):raise ValueError('edge without evidence')
        if 'qualifiers' not in e or not e.get('review_status'):raise ValueError('missing required edge property')
        source_doc=g.nodes[u].get('source_document_id',g.nodes[u].get('entry_source_document_id'))
        if any(evidence['records'][eid]['source_document_id']!=source_doc for eid in evidence['edge_bindings'][k]):raise ValueError('edge evidence outside source')
        a=g.nodes[u].get('source_document_id');b=g.nodes[v].get('source_document_id')
        if e.get('document_hop')!=int(bool(a and b and a!=b)):raise ValueError('wrong document hop')
        if e['relation']=='PART_OF' and a!=b:raise ValueError('article belongs to a different law')
    for kind,owners in [('node',set(g)),('edge',edge_ids)]:
        for owner,ids in evidence[kind+'_bindings'].items():
            if owner not in owners or not ids or any(e not in evidence['records'] for e in ids):raise ValueError('invalid evidence binding')
    for eid,e in evidence['records'].items():
        required=['source_document_id','source_alias','chunk_ids','quote','start_char','end_char','source_url','source_content_hash','effective_date','collected_at','last_checked_at','extraction_rule','role','review_status']
        if any(k not in e for k in required) or not e['chunk_ids'] or not e['quote']:raise ValueError('incomplete evidence')
        a,b=e['start_char'],e['end_char'];did=e['source_document_id']
        if a<0 or b<=a:raise ValueError('invalid source span')
        if docs and (did not in docs or docs[did]['body'][a:b]!=e['quote'] or docs[did]['content_hash']!=e['source_content_hash']):raise ValueError('quote/source mismatch')
        if docs and any(e[k]!=docs[did][k] for k in ['source_url','effective_date','collected_at','last_checked_at']):raise ValueError('source metadata mismatch')
        if chunks:
            covered=a
            for cid in sorted(e['chunk_ids'],key=lambda x:chunks[x]['start_char'] if x in chunks else -1):
                if cid not in chunks:raise ValueError('unknown evidence chunk')
                ch=chunks[cid]
                if ch['document_id']!=did or ch['start_char']>=b or ch['end_char']<=a:raise ValueError('wrong source chunk')
                x,y=max(a,ch['start_char']),min(b,ch['end_char'])
                if ch['text'][x-ch['start_char']:y-ch['start_char']]!=e['quote'][x-a:y-a]:raise ValueError('quote differs from chunk')
                if ch['start_char']>covered:raise ValueError('evidence gap')
                covered=max(covered,ch['end_char'])
            if covered<b:raise ValueError('evidence not covered')
    return dict(valid=True,nodes=len(g),edges=g.number_of_edges(),evidence_records=len(evidence['records']),
                node_types=dict(sorted(Counter(n['type'] for _,n in g.nodes(data=True)).items())),
                relations=dict(sorted(Counter(e['relation'] for *_,e in g.edges(data=True)).items())))


def save_graph(g,evidence,path):
    write_json(path,nx.node_link_data(g,edges='edges'))
    write_json(Path(path).with_suffix('.evidence.json'),evidence)


def load_graph(path,db=None):
    g=nx.node_link_graph(json.loads(Path(path).read_text()),edges='edges')
    ev=json.loads(Path(path).with_suffix('.evidence.json').read_text())
    if db:
        with connect(db,True) as c:
            if corpus_identity(c)!=g.graph['corpus_fingerprint']:raise ValueError('graph is stale for SQLite')
            docs={r['document_id']:dict(r) for r in c.execute('SELECT * FROM documents')}
            chunks={r['chunk_id']:dict(r) for r in c.execute('SELECT * FROM chunks')}
        validate_graph(g,ev,docs,chunks)
    else:validate_graph(g,ev)
    return g,ev


def main():
    p=argparse.ArgumentParser(description='Graph construct.')
    p.add_argument('--db',type=Path,default=ROOT/'data/processed/civic.sqlite')
    p.add_argument('--registry',type=Path,default=ROOT/'data/services.jsonl')
    p.add_argument('--output',type=Path,default=ROOT/'data/processed/graph.json')
    a=p.parse_args();g,ev,unresolved=build_graph(a.db,a.registry);save_graph(g,ev,a.output)
    summary={**validate_graph(g,ev),**g.graph,'graph_sha256':digest(a.output.read_bytes())}
    write_json(a.output.with_suffix('.build.json'),summary)
    write_json(ROOT/'benchmark/results/week3/unresolved_references.json',unresolved)
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
