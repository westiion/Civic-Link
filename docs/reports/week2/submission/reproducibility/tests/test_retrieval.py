import json,math,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from civic_data import HTMLText,chunks,digest,connect,SCHEMA,law_body
from retrieval import BM25,EmbeddingClient,Dense,normalize_vector,rrf,index_dense,run
from evaluate import evaluate
from validate_corpus import validate

class ParserTests(unittest.TestCase):
    def test_rowspan_colspan_preserved(self):
        p=HTMLText();p.feed('<table><tr><th rowspan="2">국세</th><th colspan="2">수수료</th></tr><tr><td>관내</td><td>관외</td></tr><tr><td>휴업</td><td>무료</td><td>무료</td></tr></table>')
        self.assertEqual(p.tables[0]['grid'],[['국세','수수료','수수료'],['국세','관내','관외'],['휴업','무료','무료']]);self.assertEqual(p.tables[0]['cells'][0]['rowspan'],2)
    def test_spaced_supplement_and_download_controls_not_indexed(self):
        raw='판례\n연혁\n법 제목\n제1조(목적) 원문\n부\xa0\xa0칙 <제1호>\n제1조(시행일) 옛 조문\n[별지] 서식.hwp 아래 을 눌러 주소 복사'
        self.assertEqual(law_body(raw),'법 제목\n제1조(목적) 원문')
        self.assertEqual(law_body('법 제목\n제1조 부칙을 인용한다'),'법 제목\n제1조 부칙을 인용한다')
    def test_article_boundaries_offsets_and_overlap(self):
        text='법 제목\n제1조(목적) '+('가나다라 '*400)+'\n제1조의2(범위) 사항\n제2조(대상) 다음\n부칙\n제1조(시행일) 다음'
        d={'body':text,'source_type':'law','document_id':'D','title':'법'};rows=chunks(d)
        self.assertTrue(any(r['locator']=='제1조의2' for r in rows));self.assertTrue(any(r['locator']=='부칙' for r in rows))
        for r in rows:self.assertEqual(r['text'],text[r['start_char']:r['end_char']]);self.assertLessEqual(len(r['text']),1200)
        self.assertEqual(''.join(set(r['locator'] for r in rows)).count('제1조(2)'),0)

class RankingTests(unittest.TestCase):
    def test_bm25_ties_and_no_match(self):
        docs=[{'chunk_id':i,'title':'주민등록','text':'등본 발급'} for i in ['B','A']];bm=BM25(docs)
        self.assertEqual([x[0] for x in bm.search('등본')],['A','B']);self.assertEqual(bm.search('zzzzz'),[])
    def test_rrf_uses_ranks_not_incommensurate_scores(self):
        result=rrf([[('A',10000),('B',.01)],[('B',.9),('C',.8)]])
        self.assertEqual(result[0][0],'B');self.assertAlmostEqual(result[0][1],1/62+1/61)
        with self.assertRaises(ValueError):rrf([[('A',1),('A',.5)]])
    def test_vectors_reject_bad_data(self):
        for v in [[],[0,0],[math.nan,1],[True,1],[math.inf,2],['1',2]]:
            with self.subTest(v=v),self.assertRaises(ValueError):normalize_vector(v)
        self.assertEqual(normalize_vector([3,4]),[.6,.8])
    def test_embedding_api_preserves_batch_order(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return json.dumps({'data':[{'index':1,'embedding':[0,2]},{'index':0,'embedding':[3,0]}]}).encode()
        cl=EmbeddingClient('http://localhost/v1','unit-test-only',query_prefix='query: ')
        with patch('urllib.request.urlopen',return_value=Response()) as req:
            self.assertEqual(cl.encode(['가','나'],'query'),[[1.,0.],[0.,1.]])
            self.assertEqual(json.loads(req.call_args.args[0].data)['input'],['query: 가','query: 나'])
    def test_malformed_api_indices_rejected(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return b'{"data":[{"index":1,"embedding":[1]}]}'
        with patch('urllib.request.urlopen',return_value=Response()),self.assertRaises(ValueError):EmbeddingClient('http://localhost/v1','test').encode(['x'])
    def test_qwen_vl_template_and_cache_are_distinct(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return b'{"data":[{"index":0,"embedding":[1,0]}]}'
        plain=EmbeddingClient('http://localhost/v1','test')
        client=EmbeddingClient('http://localhost/v1','test',input_format='qwen3-vl')
        self.assertNotEqual(plain.fingerprint,client.fingerprint)
        with patch('urllib.request.urlopen',return_value=Response()) as req:
            client.encode(['등본'],'query')
            payload=json.loads(req.call_args.args[0].data)
        self.assertEqual(payload['input'],["<|im_start|>system\nRepresent the user's input.<|im_end|>\n<|im_start|>user\n등본<|im_end|>\n<|im_start|>assistant\n"])
        self.assertFalse(payload['add_special_tokens'])
        self.assertNotIn('truncate_prompt_tokens',payload)
        self.assertEqual(client.format_input('등본','query'),client.format_input('등본','document'))
    def test_dense_cosine_dimension_and_stale_cache(self):
        with tempfile.TemporaryDirectory() as t:
            db=Path(t)/'test.sqlite';c=connect(db);c.executescript(SCHEMA)
            class Client:
                model='test-only';fingerprint='f';document_prefix=''
                def encode(self,texts,kind='document'):return [[1.,0.] for _ in texts]
            client=Client();docs=[{'chunk_id':'A','title':'t','text':'x'},{'chunk_id':'B','title':'t','text':'y'}]

            c.execute('PRAGMA foreign_keys=OFF')
            for d,v in zip(docs,[[1,0],[0,1]]):c.execute('INSERT INTO embeddings VALUES(?,?,?,?,?,?,?)',(d['chunk_id'],'test-only','f',2,json.dumps(v),digest(d['title']+'\n'+d['text']),'test'))
            c.commit();dense=Dense(c,docs,client);expected=dense.search('x');self.assertEqual(expected[0],('A',1.))
            with patch('retrieval.np',None):
                fallback=Dense(c,docs,client)
                self.assertEqual(fallback.backend,'python_exact')
                self.assertEqual(fallback.search('x'),expected)
            docs[0]['text']='changed'
            with self.assertRaises(ValueError):Dense(c,docs,client)
            c.close()

class EvaluationTests(unittest.TestCase):
    def test_alternative_chunks_partial_and_missing_predictions(self):
        q={'question_id':'Q','question':'q','question_type':'cross_reference','document_hops':1,'challenge_tags':[],'expected_answerability':'answerable','evidence_requirements':[{'acceptable_chunk_ids':['A','A2'],'source_alias':'L1','source_url':'u','locator':'1'},{'acceptable_chunk_ids':['B'],'source_alias':'L2','source_url':'u','locator':'2'}],'abstention_basis':[]}
        pred=[{'query_id':'Q','retriever':'bm25','hits':[{'query_id':'Q','retriever':'bm25','rank':1,'chunk_id':'A2','score':1.,'document_id':'D','title':'t'}]}]
        r,_=evaluate([q],pred,{'A','A2','B'});self.assertEqual(r['retrieval']['overall']['metrics']['10']['recall'],.5);self.assertEqual(r['retrieval']['overall']['metrics']['10']['all_evidence'],0)
        r,_=evaluate([q],[],{'A','A2','B'});self.assertEqual(r['retrieval']['overall']['metrics']['10']['recall'],0)
        pred[0]['hits'][0]['chunk_id']='UNKNOWN'
        with self.assertRaises(ValueError):evaluate([q],pred,{'A','A2','B'})
    def test_no_gold_input_or_mock_fallback(self):
        with self.assertRaises(ValueError):run('unused',[{'query_id':'Q','question':'x','gold':'leak'}])
    def test_live_corpus_integrity(self):
        db=Path(__file__).resolve().parents[1]/'data/processed/civic.sqlite'
        if not db.exists():self.skipTest('build SQLite to enable integration validation')
        self.assertEqual(validate(db)['documents'],91)

if __name__=='__main__':unittest.main()
