"""Prepare the fixed 14-question whole-versus-field BGE diagnostic offline.

The input cohort is the original Week 7 ranking casebook's type 1 group. It
does not depend on Markdown headings, present-day grouping, or gold labels.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path
from tempfile import NamedTemporaryFile


RANKINGS = "week7/results/experiment2_bge_direct_20261101/rankings.csv"
QUERIES = "data/benchmark/queries.jsonl"
CORPUS = "data/corpus/civic.sqlite"
PROTOCOL = "service-guide-source-fields-v1"
TYPE1_QUERY_IDS = (
    "SJ-A-058", "SJ-N-022", "SJ-N-028", "W7-PR-012", "W7-PR-013",
    "W7-PR-020", "W7-PR-021", "W7-PR-022", "SJ-B-038", "W7-LA-047",
    "SJ-N-051", "SJ-A-003", "W7-MS-033", "W7-MS-045",
)
RANKINGS_SHA256 = "94a3e35fc29b054e2c1a7f71845411c35fff0910e01687d59449d4e5ecd14c6e"
QUERIES_SHA256 = "b951eb6f08d250b5069b0f184733ea079e6b16a76352b5cff501eeb73ad614a0"
# These are printed form headings, shared across the corpus. All eligible
# headings are scored regardless of the user's words. No query routing.
HEADINGS = {
    "서비스 개요": {"신청방법", "신청자격", "발급서류", "처리기간", "신청서", "구비서류", "수수료"},
    "기본정보": {"제공 내용", "ㅇ 신청대상", "ㅇ 신청방법", "ㅇ 제출서류"},
    "신청 방법 및 절차": {"접수", "처리", "참고"},
    "부가정보": {"근거법령", "제도를 담당하는 기관 :", "자주묻는 질문", "정보 변경 내역"},
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def write_atomic(path, text):
    with NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        name = Path(tmp.name)
        tmp.write(text.encode("utf8"))
    try:
        os.replace(name, path)
    finally:
        name.unlink(missing_ok=True)


def heading_at(line, section):
    if section == "제출 서류":
        return (line.startswith("민원인이 제출해야") or
                line.startswith("민원인이 제출하지 않아도 되는 서류") or line in ("QR코드", "참고"))
    return line in HEADINGS.get(section, ())


def fields(source_type, title, locator, text):
    """Return independently scored multi-field passages; [] reuses exact whole input.

    Paragraphs/conditions under a label stay together. Municipal tables are
    already chunked by row; its fee, applicant, and channel may depend on one
    another. Leave rows, legal text, and opaque or partial chunks unchanged.
    """
    if source_type != "service_guide":
        return []
    section = re.sub(r"\(\d+\)$", "", locator).strip()
    lines = text.splitlines()
    if not lines or lines[0].strip() != section:
        return []
    blocks = []
    label = None
    values = []
    prefix = ""
    for i, raw in enumerate(lines[1:], 1):
        key = raw.strip()
        if section == "신청 방법 및 절차" and key.isdecimal() and i + 1 < len(lines) and heading_at(lines[i + 1].strip(), section):
            prefix = key + "\n"
        elif heading_at(key, section):
            if label is not None and any(v.strip() for v in values):
                blocks.append((label, values))
            label, values = prefix + raw, []
            prefix = ""
        elif label is not None:
            values.append(raw)
        elif key:
            # Fragments whose inherited heading cannot be established are
            # scored once as the original chunk, never stripped of context.
            return []
    if label is not None and any(v.strip() for v in values):
        blocks.append((label, values))
    if len(blocks) < 2:
        return []
    return list(dict.fromkeys(title + "\n" + key + "\n" + "\n".join(value).strip() for key, value in blocks))


def prepare(repo, out):
    ids = TYPE1_QUERY_IDS
    cases = set(ids)
    assert len(ids) == len(cases) == 14
    hashes = {path: digest((repo / path).read_bytes()) for path in (RANKINGS, QUERIES)}
    if hashes != {RANKINGS: RANKINGS_SHA256, QUERIES: QUERIES_SHA256}:
        raise ValueError("Expected the frozen Week 7 rankings and original benchmark questions")
    query_rows = map(json.loads, (repo / QUERIES).read_bytes().splitlines())
    qs = {row["query_id"]: row["question"] for row in query_rows if row["query_id"] in cases}
    assert set(qs) == cases
    baseline = defaultdict(list)
    with (repo / RANKINGS).open(encoding="utf8", newline="") as f:
        for row in csv.DictReader(f):
            if row["query_id"] in cases:
                baseline[row["query_id"]].append((int(row["final_rank"]), row["chunk_id"], float(row["bge_raw_score"])))
    with closing(sqlite3.connect((repo / CORPUS).resolve().as_uri()+"?mode=ro", uri=True)) as con:
        corpus = {cid: (did,typ,title,loc,text) for cid,did,typ,title,loc,text in con.execute(
            "SELECT c.chunk_id,c.document_id,d.source_type,c.title,c.locator,c.text "
            "FROM chunks c JOIN documents d USING(document_id)")}
    inputs = {}
    mapping = []
    controls = []
    counts = Counter()
    by_source = Counter()
    by_section = Counter()

    def record_input(q, passage):
        pid = digest(passage.encode("utf8"))
        key = (q, pid)
        row = dict(query_id=q, passage_id=pid, question=qs[q], passage=passage)
        if key in inputs:
            assert inputs[key] == row
        else:
            inputs[key] = row
        return pid

    for q in ids:
        rows = sorted(baseline[q])
        assert [r for r,_,_ in rows] == list(range(1,len(rows)+1))
        assert rows == sorted(rows, key=lambda x: (-x[2],x[1]))
        control = None
        for rank,cid,score in rows:
            did, typ, title, loc, body = corpus[cid]
            parts = fields(typ, title, loc, body)
            by_source[typ] += 1
            counts["candidates"] += 1
            if parts:
                assert len(parts) >= 2 and all(p != title+"\n"+body for p in parts)
                counts["split_candidates"] += 1
                counts["field_occurrences"] += len(parts)
                by_section[loc] += 1
                if control is None:
                    control = (rank, cid, score, title+"\n"+body)
            pids = [record_input(q, p) for p in parts]
            mapping.append(dict(query_id=q, chunk_id=cid, document_id=did, source_type=typ, locator=loc,
                                baseline_rank=rank, baseline_score=score, field_ids=pids))
        if control is None:
            rank, cid, score = rows[0]
            _,_,title,_,body = corpus[cid]
            control = rank,cid,score,title+"\n"+body
        rank,cid,score,passage = control
        pid = record_input(q, passage)
        controls.append(dict(query_id=q,chunk_id=cid,passage_id=pid,baseline_rank=rank,baseline_score=score))

    out.mkdir(parents=True, exist_ok=True)
    output_hashes = {}
    for name, rows in (("inputs.jsonl",list(inputs.values())), ("candidates.jsonl",mapping), ("controls.jsonl",controls)):
        content = "".join(encode_json(row)+"\n" for row in rows)
        write_atomic(out/name, content)
        output_hashes[name]=digest(content.encode("utf8"))
    summary = dict(protocol=PROTOCOL, query_ids=ids, counts=dict(counts),
                   unchanged_candidates=counts["candidates"]-counts["split_candidates"],
                   unique_new_field_inputs=len({(r["query_id"],pid) for r in mapping for pid in r["field_ids"]}),
                   controls=len(controls), unique_inference_inputs=len(inputs), source_counts=dict(by_source),
                   split_section_counts=dict(by_section), pool_sizes={q:len(baseline[q]) for q in ids},
                   input_format="Original chunk title + newline + source label and complete value block",
                   source_hashes=hashes, output_hashes=output_hashes)
    write_atomic(out/"plan.json",json.dumps(summary,ensure_ascii=False,indent=2)+"\n")
    return summary


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args()
    plan=prepare(args.repo,args.out)
    print(json.dumps(plan,ensure_ascii=False,indent=2))
