import argparse,json
from pathlib import Path
from civic_data import ROOT,BENCH,connect,digest,read_jsonl,write_json

def validate(db):
    with connect(db,True) as c:
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
        docs={r['document_id']:dict(r) for r in c.execute('SELECT * FROM documents')}
        chunks={r['chunk_id']:dict(r) for r in c.execute('SELECT * FROM chunks')}
        services={r[0] for r in c.execute('SELECT service_id FROM services')}
        raw={r['raw_hash']:r['payload'] for r in c.execute('SELECT * FROM raw_sources')}
        assert all(digest(payload)==key for key,payload in raw.items())
        for d in docs.values():
            assert digest(d['body'])==d['content_hash'];assert digest((ROOT/d['raw_path']).read_bytes())==d['raw_hash']
            assert raw[d['raw_hash']]==(ROOT/d['raw_path']).read_bytes()
            assert d['source_type'] in ('law','service_guide','municipal_table')
            assert not d['effective_date'] or d['effective_date']<='2026-09-24'
        for ch in chunks.values():
            assert ch['text']==docs[ch['document_id']]['body'][ch['start_char']:ch['end_char']]
            assert digest(ch['text'])==ch['content_hash']
            assert len(ch['text'])<=1200
        corpus_hash=digest('\n'.join(ch['chunk_id']+':'+ch['content_hash'] for ch in c.execute('SELECT * FROM chunks ORDER BY rowid')))
        assert corpus_hash==json.loads(c.execute("SELECT value FROM metadata WHERE key='corpus_hash'").fetchone()[0])
        report={'valid':True,'services':len(services),'documents':len(docs),'chunks':len(chunks),'raw_hashes_and_offsets_verified':True,'database_integrity':'ok','foreign_keys':'ok','corpus_hash':corpus_hash}
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--db',type=Path,required=True);a=p.parse_args();r=validate(a.db);print(json.dumps(r,ensure_ascii=False,indent=2))
