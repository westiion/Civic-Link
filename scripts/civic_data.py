
from __future__ import annotations
import hashlib, json, re, sqlite3, unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / 'data/graphability-audit/2026-09-24'
BENCH = ROOT / 'benchmark'
AS_OF = '2026-09-24'
HEADINGS = ['서비스 개요','기본정보','신청 방법 및 절차','제출 서류','부가정보','정보 변경내역']

def digest(value):
    if isinstance(value,str): value=value.encode('utf-8')
    return hashlib.sha256(value).hexdigest()

def now(): return datetime.now(timezone.utc).isoformat()
def read_jsonl(path): return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]
def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def write_jsonl(path,rows):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
def normalize(s):
    s=unicodedata.normalize('NFC',s).replace('\xa0',' ')
    return '\n'.join(re.sub(r'[ \t]+',' ',x).strip() for x in s.splitlines() if x.strip()).strip()

class HTMLText(HTMLParser):

    def __init__(self):
        super().__init__(convert_charrefs=True);self.parts=[];self.skip=0
        self.tables=[];self.table=None;self.row=-1;self.cell=None;self.caption=False
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag in ('script','style','noscript'): self.skip+=1
        if self.skip:return
        if tag in ('p','div','br','li','tr','h1','h2','h3','h4','dt','dd'): self.parts.append('\n')
        if tag=='table' and self.table is None:self.table={'caption':'','cells':[]};self.row=-1
        if self.table is not None:
            if tag=='caption':self.caption=True
            if tag=='tr':self.row+=1
            if tag in ('td','th'):
                self.cell={'row':max(0,self.row),'text':'','rowspan':int(attrs.get('rowspan','1') or 1),'colspan':int(attrs.get('colspan','1') or 1),'header':tag=='th'}
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript'):
            self.skip=max(0,self.skip-1);return
        if self.skip:return
        if tag in ('td','th') and self.cell is not None:
            self.cell['text']=normalize(self.cell['text']);self.table['cells'].append(self.cell);self.cell=None
        if tag=='caption':self.caption=False
        if tag=='table' and self.table is not None:
            occupied={};grid=[]
            for c in self.table['cells']:
                row=c['row'];col=0
                while (row,col) in occupied:col+=1
                c['col']=col
                for ri in range(row,row+max(1,c['rowspan'])):
                    for ci in range(col,col+max(1,c['colspan'])):occupied[ri,ci]=c['text']
            if occupied:
                grid=[[occupied.get((r,c),'') for c in range(max(c for _,c in occupied)+1)] for r in range(max(r for r,_ in occupied)+1)]
            self.table['grid']=grid;self.tables.append(self.table);self.table=None
        if tag in ('p','div','li','tr','h1','h2','h3','h4','dt','dd'):self.parts.append('\n')
    def handle_data(self,data):
        if self.skip:return
        self.parts.append(data)
        if self.cell is not None:self.cell['text']+=data
        if self.caption and self.table is not None:self.table['caption']+=data
    @property
    def text(self):return normalize(''.join(self.parts))

def decode_html(raw,encoding='utf-8'):
    for enc in [encoding,'utf-8','cp949']:
        try:return raw.decode(enc)
        except (UnicodeError,LookupError):pass
    return raw.decode('utf-8',errors='replace')

def guide_body(text):
    text=normalize(text)
    start=text.find('서비스 개요\n')
    if start<0:return ''
    text=text[start:]

    for marker in ['\n유의사항\n','\n이 정보는','\n정보 변경내역\n']:
        if marker in text:text=text.split(marker,1)[0]
    return text

def law_body(text):





    text=normalize(text)
    supplement=re.search(r'(?m)^부\s*칙(?:\s|$)',text)
    if supplement:text=text[:supplement.start()].rstrip()
    navigation={'판례','연혁','위임행정규칙','규제','한눈보기'}
    lines=text.splitlines()
    while lines and lines[0] in navigation:lines.pop(0)
    return '\n'.join(lines)

def services():

    return read_jsonl(ROOT / 'data/services.jsonl')


GUIDE_SERVICES={4:[1,2],6:[3,4],10:[7],15:[15,16],16:[17],17:[18],18:[19],21:[20],22:[21],23:[22],24:[23],25:[24],26:[25],27:[26],30:[28],31:[29],32:[30,31],33:[32],34:[33],35:[34],36:[35],37:[36],43:[5],44:[27],45:[6],47:[9,10]}
LAW_SERVICES={
'주민등록':[1,2],'공간정보':[3,4],'지적업무':[3,4],'토지이용규제':[5],'건축':[6],
'부동산 가격':[7],'부동산등기':[8],'등기사항':[8],'인터넷에 의한 등기':[8],
'자동차':[9,10],'가족관계':[11,12,13,14,15,16],'국민기초생활':[17,37,38],
'장애인':[18],'한부모':[19],'지방세기본':[20],'지방세징수':[21],
'국세청':[22,23,24,25,26,27,28,29],'국세징수':[25],'소득세':[27],'부가가치세':[22,24,28,29],
'학교':[30,31,32,33,34],'교육':[30,31,32,33,34],'초ㆍ중등':[30,31,32,33,34],
'병역':[35],'병적':[35],'농지':[36],'주거급여':[38],'긴급복지':[39],'기초연금':[40],
'민법':[40],'민원 처리':[22,23,24,25,26,28,29], '성남시 제증명':[1,2,5,7,20]}

def effective_date_from_source(value, text):

    if value:
        return '-'.join([value[:4], value[4:6], value[6:8]]) if re.fullmatch(r'\d{8}', value) else value
    match = re.search(r'^\[시행\s+(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.\s*\]', '\n'.join(text.splitlines()[:5]), re.M)
    if match:
        year, month, day = map(int, match.groups())
        return datetime(year, month, day).date().isoformat()
    return None


def archived_documents():
    docs=[];checks=[]
    manifest=json.loads((ARCHIVE/'manifest.json').read_text())
    for row in manifest['files']:
        p=ARCHIVE/row['path']
        if not p.exists() or digest(p.read_bytes())!=row['sha256']:raise ValueError('archive checksum mismatch: '+row['path'])
    for folder in ['sources','laws']:
        for meta_file in sorted((ARCHIVE/folder).glob('*.json')):
            m=json.loads(meta_file.read_text());alias=('L'+m['law_name']) if folder=='laws' else 'G'+meta_file.stem[-3:]
            raw=meta_file.with_suffix('.html');txt=meta_file.with_suffix('.txt')
            status='ok';reason=None
            if m.get('error') or not raw.exists() or not txt.exists():status='excluded';reason='acquisition_failed_or_missing_raw'
            elif folder=='sources' and 'gov.kr' not in urlparse(m.get('source_url','')).netloc:status='excluded';reason='auxiliary_or_legal_shell_replaced_by_full_law'
            elif '오류가 발생' in m.get('title',''):status='excluded';reason='error_page_http_200'
            text=normalize(txt.read_text()) if txt.exists() else ''
            if folder=='laws':text=law_body(text)
            if folder=='sources' and status=='ok':
                bf=meta_file.with_suffix('.body.txt');text=guide_body(bf.read_text() if bf.exists() else text)
                if not text:status='excluded';reason='missing_service_body'
            ef=effective_date_from_source(m.get('effective_date'),text) if folder=='laws' else m.get('effective_date')
            if ef and ef>AS_OF:status='excluded';reason='future_effective_version'
            checks.append({'alias':alias,'source_url':m.get('body_url',m.get('source_url',m.get('requested_url'))),'checked_at':m.get('collected_at'),'http_status':m.get('status',200 if status=='ok' else None),'status':status,'reason':reason,'raw_hash':digest(raw.read_bytes()) if raw.exists() else None})
            if status!='ok':continue
            title=m.get('law_name',m.get('title','').split('|')[0].strip());raw_bytes=raw.read_bytes()
            parser=HTMLText();parser.feed(decode_html(raw_bytes,m.get('encoding','utf-8')))
            svc=GUIDE_SERVICES.get(int(alias[1:]),[]) if folder=='sources' else sorted({n for key,ns in LAW_SERVICES.items() if key in title for n in ns})
            docs.append({'alias':alias,'title':title,'body':text,'source_url':m.get('body_url',m.get('source_url')),'source_type':'law' if folder=='laws' else 'service_guide','organization':('국가법령정보센터' if folder=='laws' else '정부24'),'law_name':title if folder=='laws' else None,'article_number':None,'effective_date':ef,'collected_at':m['collected_at'],'last_checked_at':m.get('last_checked_at',m['collected_at']),'raw_path':str(raw.relative_to(ROOT)),'raw_hash':digest(raw_bytes),'parent_document_id':None,'tables':parser.tables,'service_ids':[f'SJ-SVC-{n:03}' for n in svc],'metadata':{'archive_metadata':str(meta_file.relative_to(ROOT)),'temporal_policy':'frozen_snapshot_as_of_2026-09-24'}})
    extra=ROOT/'data/raw/2026-09-26'
    if extra.exists():
        for mf in sorted(extra.glob('*.json')):
            m=json.loads(mf.read_text());raw=extra/m['raw_filename'];p=HTMLText();p.feed(decode_html(raw.read_bytes()))
            checks.append({'alias':'L'+m['law_name'] if m.get('source_type')=='law' else 'C01','source_url':m['source_url'],'checked_at':m.get('last_checked_at',m['collected_at']),'http_status':m.get('http_status',200),'status':'ok','reason':None,'raw_hash':digest(raw.read_bytes())})
            if m.get('source_type')=='law':
                name=m['law_name']
                if not re.search(r'(?m)^제41조',p.text):raise ValueError('supplemental law body missing')
                docs.append({'alias':'L'+name,'title':name,'body':law_body(p.text),'source_url':m['source_url'],'source_type':'law','organization':'국세청','law_name':name,'article_number':None,'effective_date':m['effective_date'],'collected_at':m['collected_at'],'last_checked_at':m.get('last_checked_at',m['collected_at']),'raw_path':str(raw.relative_to(ROOT)),'raw_hash':digest(raw.read_bytes()),'parent_document_id':None,'tables':p.tables,'service_ids':[f'SJ-SVC-{n:03}' for n in range(22,30)],'metadata':{'temporal_policy':'fixed_2025-08-12_version_recaptured'}})
                continue

            tables=[t for t in p.tables if any('발급' in c['text'] or '증명' in c['text'] for c in t['cells']) and any('본인확인' in c['text'] or '주민등록등본' in c['text'] or '국세청' in c['text'] for c in t['cells'])]
            text_parts=[]
            for idx,t in enumerate(tables):
                head=[' / '.join(dict.fromkeys(row[ci] for row in t['grid'][:2] if ci<len(row))) for ci in range(len(t['grid'][0]))] if t['grid'] else []
                for ri,row in enumerate(t['grid'][2:],2):
                    text_parts.append(f'표 {idx+1} 행 {ri}: '+' | '.join(f'{head[ci] if ci<len(head) else ci}: {v}' for ci,v in enumerate(row)))

            lines=p.text.splitlines()
            for li,line in enumerate(lines):
                if line in ('본인확인이 필요한 민원','본인확인이 필요 없는 민원'):
                    text_parts.append('주석: '+line+' — '+lines[li+1])
                elif '이해관계' in line:text_parts.append('주석: '+line)
            if not text_parts:raise ValueError('city issuance table not found')
            docs.append({'alias':'C01','title':'성남시 무인민원발급 — 발급 민원과 본인확인','body':'\n'.join(text_parts),'source_url':m['source_url'],'source_type':'municipal_table','organization':'성남시','law_name':None,'article_number':None,'effective_date':None,'collected_at':m['collected_at'],'last_checked_at':m.get('last_checked_at',m['collected_at']),'raw_path':str(raw.relative_to(ROOT)),'raw_hash':digest(raw.read_bytes()),'parent_document_id':None,'tables':tables,'service_ids':[f'SJ-SVC-{n:03}' for n in range(1,37)],'metadata':{'temporal_policy':'current_capture_not_retroactive_2026-09-24','notes':'local service list; per-device availability out of scope'}})
    for d in docs:
        if d['source_type']=='law':d['metadata']['index_scope']='main_provisions_only; supplementary_provisions_annexes_and_download_UI_excluded; raw_retained'
        d['content_hash']=digest(d['body']);d['document_id']='DOC-'+digest(d['source_url']+'\n'+d['content_hash'])[:20]
    return docs,checks

def units(doc):
    text=doc['body'];kind=doc['source_type'];starts=[]
    if kind=='law':
        starts=[(m.start(),m.group(1)) for m in re.finditer(r'(?m)^(제\d+조(?:의\d+)?)(?=[ (　])',text)]

        supplement=re.search(r'(?m)^부\s*칙(?:\s|$)',text)
        if supplement:starts=[x for x in starts if x[0]<supplement.start()]+[(supplement.start(),'부칙')]
    elif kind=='service_guide':
        starts=[(m.start(),m.group()) for m in re.finditer(r'(?m)^(?:'+'|'.join(map(re.escape,HEADINGS))+r')$',text)]
    elif kind=='municipal_table':starts=[(m.start(),m.group()) for m in re.finditer(r'(?m)^(?:표 \d+ 행 \d+:|주석:)' ,text)]
    if not starts or starts[0][0]>0:starts.insert(0,(0,'문서 머리말'))
    seen={}
    for i,(start,label) in enumerate(starts):
        end=starts[i+1][0] if i+1<len(starts) else len(text)
        if not text[start:end].strip():continue
        seen[label]=seen.get(label,0)+1
        yield label if seen[label]==1 else f'{label}({seen[label]})',start,end

def chunks(doc,max_chars=1200,overlap=150):
    if max_chars<=overlap or overlap<0:raise ValueError('invalid chunk window')
    result=[]
    for label,start,end in units(doc):
        pos=start
        while pos<end:
            stop=min(end,pos+max_chars)
            if stop<end:
                boundary=doc['body'].rfind('\n',pos+max_chars//2,stop)
                if boundary>pos:stop=boundary
            text=doc['body'][pos:stop]
            if text.strip():
                ordinal=len(result);chash=digest(text)
                result.append({'chunk_id':doc['document_id']+f'-C{ordinal:04}','document_id':doc['document_id'],'ordinal':ordinal,'title':doc['title']+' / '+label,'text':text,'locator':label,'start_char':pos,'end_char':stop,'content_hash':chash})
            if stop==end:break
            pos=max(pos+1,stop-overlap)
    return result

SCHEMA='''
PRAGMA foreign_keys=ON;
CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE raw_sources(raw_hash TEXT PRIMARY KEY,media_type TEXT NOT NULL,payload BLOB NOT NULL);
CREATE TABLE services(service_id TEXT PRIMARY KEY,name TEXT NOT NULL,category TEXT NOT NULL,entry_source_id TEXT,max_document_hops INTEGER NOT NULL,audit_classification TEXT NOT NULL);
CREATE TABLE documents(document_id TEXT PRIMARY KEY,alias TEXT UNIQUE NOT NULL,title TEXT NOT NULL,body TEXT NOT NULL,source_url TEXT NOT NULL,source_type TEXT NOT NULL,organization TEXT,law_name TEXT,article_number TEXT,effective_date TEXT,collected_at TEXT NOT NULL,last_checked_at TEXT NOT NULL,content_hash TEXT NOT NULL,raw_hash TEXT NOT NULL,raw_path TEXT NOT NULL,parent_document_id TEXT REFERENCES documents(document_id),acquisition_status TEXT NOT NULL,metadata_json TEXT NOT NULL);
CREATE TABLE document_services(document_id TEXT REFERENCES documents(document_id),service_id TEXT REFERENCES services(service_id),PRIMARY KEY(document_id,service_id));
CREATE TABLE source_checks(check_id INTEGER PRIMARY KEY,alias TEXT,source_url TEXT,checked_at TEXT,http_status INTEGER,status TEXT,reason TEXT,raw_hash TEXT);
CREATE TABLE document_tables(document_id TEXT REFERENCES documents(document_id),table_index INTEGER,caption TEXT,grid_json TEXT,cells_json TEXT,PRIMARY KEY(document_id,table_index));
CREATE TABLE chunks(chunk_id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES documents(document_id),ordinal INTEGER NOT NULL,title TEXT NOT NULL,text TEXT NOT NULL,locator TEXT NOT NULL,start_char INTEGER NOT NULL,end_char INTEGER NOT NULL,content_hash TEXT NOT NULL,UNIQUE(document_id,ordinal),CHECK(end_char>start_char));
CREATE TABLE embeddings(chunk_id TEXT REFERENCES chunks(chunk_id),model TEXT,encoder_fingerprint TEXT,dimension INTEGER NOT NULL,vector_json TEXT NOT NULL,input_hash TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(chunk_id,model,encoder_fingerprint));
CREATE INDEX chunks_document ON chunks(document_id);
CREATE VIEW document_catalog AS SELECT d.*,s.service_id,s.category FROM documents d LEFT JOIN document_services ds USING(document_id) LEFT JOIN services s USING(service_id);
'''

def connect(path,readonly=False):
    p=Path(path).resolve()
    c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) if readonly else sqlite3.connect(p)
    c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');return c

def build_database(output):
    output=Path(output)
    if output.exists():raise FileExistsError('Refusing to overwrite DB; choose a new --db output')
    docs,checks=archived_documents();output.parent.mkdir(parents=True,exist_ok=True)
    with connect(output) as c:
        c.executescript(SCHEMA)
        for s in services():c.execute('INSERT INTO services VALUES(?,?,?,?,?,?)',tuple(s[k] for k in ['service_id','name','category','entry_source_id','max_document_hops','audit_classification']))
        allchunks=[]
        for d in docs:
            c.execute('INSERT OR IGNORE INTO raw_sources VALUES(?,?,?)',(d['raw_hash'],'text/html',(ROOT/d['raw_path']).read_bytes()))
            keys=['document_id','alias','title','body','source_url','source_type','organization','law_name','article_number','effective_date','collected_at','last_checked_at','content_hash','raw_hash','raw_path','parent_document_id']
            c.execute('INSERT INTO documents VALUES('+','.join('?' for _ in range(18))+')',[d[k] for k in keys]+['ok',json.dumps(d['metadata'],ensure_ascii=False)])
            for sid in d['service_ids']:c.execute('INSERT INTO document_services VALUES(?,?)',(d['document_id'],sid))
            for i,t in enumerate(d['tables']):c.execute('INSERT INTO document_tables VALUES(?,?,?,?,?)',(d['document_id'],i,t['caption'],json.dumps(t['grid'],ensure_ascii=False),json.dumps(t['cells'],ensure_ascii=False)))
            for ch in chunks(d):
                c.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?)',tuple(ch.values()));allchunks.append(ch)
        for r in checks:c.execute('INSERT INTO source_checks(alias,source_url,checked_at,http_status,status,reason,raw_hash) VALUES(?,?,?,?,?,?,?)',tuple(r.values()))
        corpus_hash=digest('\n'.join(x['chunk_id']+':'+x['content_hash'] for x in allchunks))
        meta={'created_at':now(),'as_of_date':AS_OF,'corpus_hash':corpus_hash,'chunking':'article/section; 1200 chars; 150 overlap','gold_indexed':False}
        for k,v in meta.items():c.execute('INSERT INTO metadata VALUES(?,?)',(k,json.dumps(v,ensure_ascii=False)))
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
        report={**meta,'documents':len(docs),'chunks':len(allchunks),'tables':c.execute('SELECT count(*) FROM document_tables').fetchone()[0],'services':40,'excluded_acquisitions':[r for r in checks if r['status']!='ok'],'archive_files_verified':len(json.loads((ARCHIVE/'manifest.json').read_text())['files'])}
    write_json(output.with_suffix('.build.json'),report);return report


def repair_source_dates(db, report_path):

    changes=[]
    with connect(db) as c:
        for row in c.execute("SELECT document_id,alias,body FROM documents WHERE source_type='law' AND effective_date IS NULL").fetchall():
            value=effective_date_from_source(None,row['body'])
            if value:
                if value>AS_OF:
                    raise ValueError('future effective date requires a separate corpus rebuild')
                changes.append({'document_id':row['document_id'],'alias':row['alias'],'old':None,'new':value,
                                'source_header':'\n'.join(row['body'].splitlines()[:5])})

        if changes:
            if Path(report_path).exists():raise FileExistsError('date repair report already exists')
            write_json(report_path,{'created_at':now(),'changes':changes,'content_and_chunks_changed':False})
        for row in changes:c.execute('UPDATE documents SET effective_date=? WHERE document_id=?',(row['new'],row['document_id']))
    return {'dates_repaired':len(changes),'ledger':str(report_path)}

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--db',type=Path,required=True);p.add_argument('--repair-source-dates',type=Path);args=p.parse_args()
    result=repair_source_dates(args.db,args.repair_source_dates) if args.repair_source_dates else build_database(args.db)
    print(json.dumps({k:v for k,v in result.items() if k not in ['excluded_acquisitions']},ensure_ascii=False,indent=2))
