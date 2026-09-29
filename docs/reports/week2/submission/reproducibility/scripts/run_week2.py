import argparse,json,os
from pathlib import Path
import benchmark as benchmark
import generation as generation
from civic_data import ROOT,read_jsonl,write_jsonl,write_json,digest
from retrieval import EmbeddingClient,client_example,run


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--benchmark-dir',type=Path,default=ROOT/'benchmark')
    p.add_argument('--client-example',type=Path)
    p.add_argument('--embedding-url',default=os.environ.get('CIVIC_EMBEDDING_URL','http://10.50.0.50:30004/v1'))
    p.add_argument('--embedding-model',default=os.environ.get('CIVIC_EMBEDDING_MODEL','Qwen3-VL-Embedding-8B'))
    p.add_argument('--chat-model',default=os.environ.get('CIVIC_CHAT_MODEL','Qwen3.8-Flash-Next'))
    p.add_argument('--stage',choices=['retrieval','generation','all'],default='all')
    a=p.parse_args();bench=a.benchmark_dir.resolve();db=ROOT/'data/processed/civic.sqlite'
    benchmark.BENCH=bench;benchmark.validate(db)
    client=EmbeddingClient(a.embedding_url,a.embedding_model,os.environ.get('CIVIC_EMBEDDING_API_KEY'),input_format='qwen3-vl',timeout=120)
    if a.stage in ('retrieval','all'):
        for track,name in [('conversational','queries.jsonl'),('canonical','canonical_queries.jsonl'),('clarified','clarified_queries.jsonl')]:
            for method in ['bm25','dense','hybrid']:
                path=bench/'results'/f'{method}_{track}.jsonl';inputs=bench/name
                print(f'START {method} {track}',flush=True)
                if path.exists():
                    saved=json.loads(path.with_suffix('.run.json').read_text())
                    if saved['method']!=method or saved['top_k']!=10 or saved['candidate_k']!=100 or saved['rrf_constant']!=60:
                        raise ValueError('stored retrieval settings differ from requested run')
                    if saved['bm25']!={'k1':1.2,'b':.75,'tokenizer':'words+Hangul bigrams'}:
                        raise ValueError('stored tokenizer/BM25 settings differ from requested run')
                    if method!='bm25' and saved.get('encoder_fingerprint')!=client.fingerprint:
                        raise ValueError('stored embedding model/input format differs from requested run')
                    benchmark.score(db,path,track)
                else:
                    rows,meta=run(db,read_jsonl(inputs),method,client=client if method!='bm25' else None)
                    meta.update(queries_sha256=digest(inputs.read_bytes()),truncation_enabled=False)
                    write_jsonl(path,rows);meta['predictions_sha256']=digest(path.read_bytes());write_json(path.with_suffix('.run.json'),meta)
                print(json.dumps({'method':method,'track':track,'metrics':benchmark.score(db,path,track)},ensure_ascii=False),flush=True)
    if a.stage in ('generation','all'):
        cfg=client_example(a.client_example) if a.client_example else {}
        base=os.environ.get('CIVIC_CHAT_URL') or cfg.get('base_url')
        if not base:p.error('chat URL is required')
        key=os.environ.get('CIVIC_CHAT_API_KEY') or cfg.get('api_key')
        generator=generation.ChatClient(base,a.chat_model,key,1600)
        judge=generation.ChatClient(base,a.chat_model,key,700)
        generation.BENCH=bench;generation.RETRIEVAL_RESULTS=bench/'results';generation.DB=db
        for method in ['bm25','dense','hybrid']:
            print(f'START generation {method}',flush=True)
            generation.METHOD=method;generation.RESULTS=bench/'results'/('generation_'+method)
            generation.RESULTS.mkdir(parents=True,exist_ok=True)
            generation.generate(generator,2)
            generation.judge(judge,2)
            result=generation.evaluate()
            print(json.dumps({'method':method,'generation':result['overall'],'api_errors':result['api_errors'],'schema_valid':result['schema_valid']},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
