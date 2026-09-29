from pathlib import Path
import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'scripts'))


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text())


def no_network(*args, **kwargs):
    raise RuntimeError('Model/network calls are disabled in offline reproduction')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-zip', type=Path, default=ROOT.parent / 'data/corpus-sqlite.zip')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'runs/offline')
    args = parser.parse_args()
    output = args.output_dir.resolve()
    require(not output.exists(), 'Output already exists; choose a new --output-dir')
    for name, expected in read(ROOT / 'files.sha256.json').items():
        require(sha(ROOT / name) == expected, 'Package file changed: ' + name)
    archive_manifest = read(args.data_zip.parent / 'archive_manifest.json')
    require(sha(args.data_zip) == archive_manifest['zip_sha256'], 'Data ZIP checksum mismatch')

    with zipfile.ZipFile(args.data_zip) as z:
        require(set(z.namelist()) == set(archive_manifest['files']), 'Unexpected ZIP entries')
        for name, expected in archive_manifest['files'].items():
            target = (ROOT / name).resolve()
            require(target.is_relative_to(ROOT / 'data'), 'Unsafe archive path')
            if target.exists():
                require(sha(target) == expected, 'Existing data differs: ' + name)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(name) as source, target.open('wb') as dest:
                    shutil.copyfileobj(source, dest)
                require(sha(target) == expected, 'Extracted data checksum mismatch: ' + name)
    urllib.request.urlopen = no_network
    import benchmark
    import generation
    import retrieval
    import validate_corpus
    from civic_data import read_jsonl, write_json, write_jsonl, digest
    db = ROOT / 'data/processed/civic.sqlite'
    corpus = validate_corpus.validate(db)
    subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(ROOT / 'tests'), '-v'], cwd=ROOT, check=True)
    bench = output / 'benchmark'
    shutil.copytree(ROOT / 'benchmark', bench)
    benchmark.BENCH = bench
    benchmark.validate(db)
    result = {'corpus': corpus, 'retrieval_saved_metrics_match': [], 'generation_saved_metrics_match': [],
              'bm25_fresh_predictions_match': [], 'network_calls': 0,
              'limits': 'Dense/Hybrid rankings and LLM outputs are stored artifacts, not fresh model inference.'}
    for track, queryfile in [('conversational', 'queries.jsonl'), ('canonical', 'canonical_queries.jsonl'), ('clarified', 'clarified_queries.jsonl')]:
        for method in ['bm25', 'dense', 'hybrid']:
            stem = method + '_' + track
            metrics = bench / 'results' / (stem + '_metrics.json')
            expected = read(metrics)
            benchmark.score(db, bench / 'results' / (stem + '.jsonl'), track)
            require(read(metrics) == expected, 'Stored retrieval metric mismatch: ' + stem)
            result['retrieval_saved_metrics_match'].append(stem)
        rows, meta = retrieval.run(db, read_jsonl(bench / queryfile), 'bm25')
        expected_rows = read_jsonl(bench / 'results' / ('bm25_' + track + '.jsonl'))
        require(rows == expected_rows, 'Fresh BM25 prediction mismatch: ' + track)
        fresh = output / 'fresh_bm25' / ('bm25_' + track + '.jsonl')
        write_jsonl(fresh, rows)
        meta.update(queries_sha256=digest((bench / queryfile).read_bytes()), predictions_sha256=digest(fresh.read_bytes()))
        write_json(fresh.with_suffix('.run.json'), meta)
        result['bm25_fresh_predictions_match'].append(track)
    generation.BENCH = bench
    generation.RETRIEVAL_RESULTS = bench / 'results'
    generation.DB = db
    for method in ['bm25', 'dense', 'hybrid']:
        generation.METHOD = method
        generation.RESULTS = bench / 'results' / ('generation_' + method)
        expected = read(generation.RESULTS / 'generation_metrics.json')
        actual = generation.evaluate()
        expected.pop('created_at', None)
        actual.pop('created_at', None)
        require(actual == expected, 'Generation metric mismatch: ' + method)
        result['generation_saved_metrics_match'].append(method)
    result['status'] = 'passed'
    write_json(output / 'verification.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
