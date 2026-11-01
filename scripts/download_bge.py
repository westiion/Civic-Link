"""Download only the pinned official BAAI reranker inference files."""
import argparse
import json
from pathlib import Path
from huggingface_hub import snapshot_download

MODEL='BAAI/bge-reranker-v2-m3'
REVISION='953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e'
FILES=['config.json','model.safetensors','tokenizer.json','tokenizer_config.json',
       'special_tokens_map.json','sentencepiece.bpe.model']

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir',type=Path,default=Path('.cache/reranker-models'))
    args=parser.parse_args()
    path=snapshot_download(MODEL,revision=REVISION,cache_dir=args.cache_dir,allow_patterns=FILES,max_workers=2)
    print(json.dumps(dict(model=MODEL,revision=REVISION,local_path=str(Path(path).resolve())),indent=2))
