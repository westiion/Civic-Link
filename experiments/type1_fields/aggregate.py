"""Aggregate the 14 field-scoring diagnostics from cached logits; no model is needed."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from statistics import mean
from prepare import TYPE1_QUERY_IDS as QUESTIONS


METHODS = ("whole", "fields", "fusion")
DISPLAY = {"whole": "청크 전체", "fields": "항목 최고점", "fusion": "전체·항목 순위 평균"}


def sha256(path):
    with path.open("rb") as stream:
        h = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
        return h.hexdigest()


def json_lines(path):
    with path.open(encoding="utf-8-sig") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def json_text(data):
    return json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def read_frozen(repo):
    experiments = repo / "week7/results/experiment2_bge_direct_20261101"
    ranking_path = experiments / "rankings.csv"
    per_question = experiments / "per_question.jsonl"
    bench_path = repo / "data/benchmark/questions.jsonl"
    frozen = {q: {} for q in QUESTIONS}
    with ranking_path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            q, c = row["query_id"], row["chunk_id"]
            if q in frozen:
                assert c not in frozen[q]
                frozen[q][c] = (int(row["final_rank"]), float(row["bge_raw_score"]))
    for q, rows in frozen.items():
        order = sorted(rows, key=lambda c: (-rows[c][1], c))
        assert len(order) >= 100
        assert [rows[c][0] for c in order] == list(range(1, len(rows) + 1)), q
    benchmark = {row["question_id"]: row for row in json_lines(bench_path) if row["question_id"] in frozen}
    logged = {row["query_id"]: row["metrics"] for row in json_lines(per_question) if row["query_id"] in frozen}
    assert set(benchmark) == set(logged) == set(frozen)
    assert sum(map(len, frozen.values())) == 1568
    return frozen, benchmark, logged, [ranking_path, per_question, bench_path]


def evidence(ranks, requirements):
    group_ranks = [
        min((ranks[c] for c in group["acceptable_chunk_ids"] if c in ranks), default=None)
        for group in requirements
    ]
    metrics = {}
    for k in (10, 100):
        hits = [r for r in group_ranks if r is not None and r <= k]
        metrics[str(k)] = {
            "groups_recovered": len(hits),
            "groups": len(group_ranks),
            "complete": int(len(hits) == len(group_ranks)),
            "recall": len(hits) / len(group_ranks),
            "mrr": 1 / min(hits) if hits else 0.0,
        }
    return {"group_ranks": group_ranks, "metrics": metrics}


def baseline_metrics(frozen, benchmark, logged):
    base = {}
    for q, rows in frozen.items():
        req = benchmark[q]["evidence_requirements"]
        base[q] = evidence({c: rank for c, (rank, _) in rows.items()}, req)
        for k in ("10", "100"):
            m = base[q]["metrics"][k]
            assert m["complete"] == logged[q][k]["all_evidence"]
            assert abs(m["recall"] - logged[q][k]["recall"]) < 1e-12
            assert abs(m["mrr"] - logged[q][k]["mrr"]) < 1e-12
    assert sum(base[q]["metrics"]["10"]["groups"] for q in QUESTIONS) == 27
    assert sum(base[q]["metrics"]["10"]["groups_recovered"] for q in QUESTIONS) == 11
    return base


def summarize(records, method):
    aggregate = {}
    for k in ("10", "100"):
        metrics = [records[q][method]["metrics"][k] for q in QUESTIONS]
        aggregate[k] = {
            "questions": len(metrics),
            "complete": sum(m["complete"] for m in metrics),
            "groups_recovered": sum(m["groups_recovered"] for m in metrics),
            "groups": sum(m["groups"] for m in metrics),
            "macro_recall": mean(m["recall"] for m in metrics),
            "macro_mrr": mean(m["mrr"] for m in metrics),
        }
    return aggregate


def load_plan(candidates, passages, controls_path, frozen, benchmark):
    models = {q: {} for q in QUESTIONS}
    field_keys = set()
    for row in json_lines(candidates):
        q, c = row["query_id"], row["chunk_id"]
        ids = row["field_ids"]
        assert c not in models[q] and len(set(ids)) == len(ids)
        assert (int(row["baseline_rank"]), float(row["baseline_score"])) == frozen[q][c]
        models[q][c] = list(ids)
        field_keys.update((q, i) for i in ids)
    assert all(set(models[q]) == set(frozen[q]) for q in QUESTIONS)
    controls = {}
    for row in json_lines(controls_path):
        q, c = row["query_id"], row["chunk_id"]
        assert q not in controls
        assert (int(row["baseline_rank"]), float(row["baseline_score"])) == frozen[q][c]
        controls[q] = row
    assert set(controls) == set(frozen)
    control_keys = {(q, row["passage_id"]) for q, row in controls.items()}
    available = {}
    for row in json_lines(passages):
        key = row["query_id"], row["passage_id"]
        assert key not in available
        assert row["question"] == benchmark[key[0]]["question"]
        assert isinstance(row["passage"], str) and row["passage"]
        assert hashlib.sha256(row["passage"].encode("utf8")).hexdigest() == key[1]
        available[key] = row["passage"]
    assert field_keys | control_keys == set(available)
    return models, field_keys, controls, available


def load_logits(path, wanted):
    scores = {}
    for row in json_lines(path):
        key = row["query_id"], row["passage_id"]
        assert key not in scores and key in wanted, key
        val = float(row["score"])
        assert math.isfinite(val)
        scores[key] = val
    if set(scores) != wanted:
        raise ValueError(f"Measured {len(scores)}/{len(wanted)} field inputs; resume scoring before aggregation.")
    return scores


def rank_query(candidates, source, q, logits, field_shift=0.0):
    score_and_winner = {}
    for c, field_ids in candidates.items():
        if field_ids:
            winner = min(field_ids, key=lambda f: (-logits[q, f], f))
            score_and_winner[c] = logits[q, winner] + field_shift, winner
        else:
            score_and_winner[c] = source[c][1], ""
    field_order = sorted(candidates, key=lambda c: (-score_and_winner[c][0], source[c][0], c))
    fields = {c: i for i, c in enumerate(field_order, 1)}
    fusion_order = sorted(candidates, key=lambda c: (fields[c] + source[c][0], source[c][0], c))
    fusion = {c: i for i, c in enumerate(fusion_order, 1)}
    for ranks in (fields, fusion):
        assert set(ranks) == set(source) and sorted(ranks.values()) == list(range(1, len(source) + 1))
    return score_and_winner, fields, fusion


def hit_groups(ranks, k):
    return {i for i, v in enumerate(ranks, 1) if v is not None and v <= k}


def q_differences(before, after):
    changes = {}
    for k in (10, 100):
        old = hit_groups(before["group_ranks"], k)
        new = hit_groups(after["group_ranks"], k)
        changes[str(k)] = {"gained_groups": sorted(new - old), "lost_groups": sorted(old - new)}
    return changes


def group_transitions(q, requirements, original, candidates, winners, new_ranks, passages):
    events = []
    for i, req in enumerate(requirements, 1):
        allowed = [c for c in req["acceptable_chunk_ids"] if c in original]
        old = min(allowed, key=lambda c:original[c][0])
        new = min(allowed, key=new_ranks.get)
        was = original[old][0] <= 10
        remains = new_ranks[new] <= 10
        if was == remains:
            continue
        fs, f_winner = winners[new]
        events.append({
            "group":i, "movement_at_10":"gained" if remains else "lost",
            "former_best_id":old, "former_best_rank":original[old][0],
            "new_best_id":new, "rank":new_ranks[new], "new_best_original_rank":original[new][0],
            "score_source":"max_split_field" if candidates[new] else "unchanged_whole",
            "new_best_original_score":original[new][1], "field_score":fs,
            "winning_field_label":passages[q, f_winner].splitlines()[1] if f_winner else None,
        })
    return events


def metadata(path, inputs):
    provenance = json.loads(path.read_bytes())
    assert provenance["inputs_sha256"] == sha256(inputs)
    return provenance


def control_summary(controls, logits):
    errors = {}
    for q, row in controls.items():
        measured = logits[q, row["passage_id"]]
        errors[q] = {
            "chunk_id": row["chunk_id"],
            "reference_rank": row["baseline_rank"],
            "frozen_score": row["baseline_score"],
            "cpu_whole_score": measured,
            "cpu_minus_frozen": measured - row["baseline_score"],
        }
    absolute = [abs(row["cpu_minus_frozen"]) for row in errors.values()]
    return {"count":len(errors), "mean_absolute":mean(absolute), "maximum_absolute":max(absolute),
            "per_query":errors}


def cutoff_diagnostic(candidates, scores, field_rank, original, q, logits, req, max_drift):
    top = sorted(candidates, key=field_rank.get)
    c10, c11 = top[9:11]
    gap = scores[c10][0] - scores[c11][0]
    assert gap >= 0
    outcomes = {}
    for tag, shift in (("minus", -max_drift),("plus",max_drift)):
        _, f, comb = rank_query(candidates, original, q, logits, field_shift=shift)
        outcomes[tag] = {m:evidence(r, req)["group_ranks"] for m,r in (("fields",f),("fusion",comb))}
    return {
        "field_rank_10":c10, "field_rank_11":c11, "field_cutoff_raw_gap":gap,
        "at_least_one_new_field_at_boundary":bool(candidates[c10] or candidates[c11]),
        "field_cutoff_gap_lte_twice_observed_max_drift":gap <= 2*max_drift,
        "changed_scores_shift_sensitivity":outcomes,
    }


def make_report(summary, out):
    by = summary["aggregate"]
    lines = [
        "### 유형 1 · 청크 전체와 항목별 채점 비교", "",
        f"유형 1의 14문항에서 원래 후보 {summary['candidate_pairs']:,}개를 그대로 정렬했다. "
        "항목이 여러 개인 청크에는 항목별 점수 중 최고점을, 나뉘지 않은 청크에는 기존 점수를 사용했다. "
        "결합 조건은 전체 점수 순위와 항목 점수 순위의 평균이 낮은 순서다.", "",
        "| 조건 | 완전회수@10 | Recall@10 | MRR@10 | 완전회수@100 | Recall@100 | MRR@100 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for m in METHODS:
        a, b = by[m]["10"], by[m]["100"]
        lines.append(f"| {DISPLAY[m]} | {a['complete']}/14 | {a['macro_recall']:.2%} | {a['macro_mrr']:.4f} | "
                     f"{b['complete']}/14 | {b['macro_recall']:.2%} | {b['macro_mrr']:.4f} |")
    lines += ["", "| 문항 | 전체 점수의 근거 순위 | 항목 점수의 근거 순위 | 순위 평균의 근거 순위 |",
              "|---|---|---|---|"]
    for q, rec in summary["per_query"].items():
        cells = [" / ".join("—" if v is None else str(v) for v in rec[m]["group_ranks"]) for m in METHODS]
        lines.append(f"| [{q}](#{q.lower()}) | " + " | ".join(cells) + " |")
    target = summary["target_example"]
    lines += ["", f"**W7-PR-012** · 제출할 서류 이름이 있는 ‘서비스 개요’의 순위는 청크 전체 점수에서 "
              f"**{target['whole_rank']}위**, 항목 최고점에서 **{target['field_rank']}위**, "
              f"순위 평균에서 **{target['fusion_rank']}위**였다. 이 청크에서 점수가 가장 높았던 항목은 "
              f"‘{target['winning_field_label']}’이었다."]
    for m in ("fields","fusion"):
        causes = summary["delta_groups"][m]["10"]
        lines.append(f"- {DISPLAY[m]}에서 상위 10개에 새로 든 근거는 {causes['gained_groups']}묶음, "
                     f"기존에 회수했다가 빠진 근거는 {causes['lost_groups']}묶음이었다.")
    lines += [
        "", f"기존에 놓친 {summary['split_gold']['missing_groups']}묶음 중 직접 나눠 채점한 허용 청크가 있는 묶음은 "
        f"{summary['split_gold']['missing_groups_with_split_allowed_candidate']}개다. 나머지 묶음의 순위 변화는 "
        "경쟁 후보의 점수가 달라진 결과다.",
        "", "원래 완전회수@10에 실패한 14문항만 선정했으므로, 기존 성공 문항에서 발생할 손실과 "
        "488문항 전체의 성능 변화는 이 결과로 판단할 수 없다.", "",
    ]
    (out / "report.md").write_text("\n".join(lines), encoding="utf8")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", type=Path, default=Path.cwd())
    ap.add_argument("--candidates", type=Path)
    ap.add_argument("--inputs", type=Path)
    ap.add_argument("--controls", type=Path)
    ap.add_argument("--plan", type=Path)
    ap.add_argument("--scores", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--baseline-only", action="store_true")
    args = ap.parse_args()
    frozen, benchmark, logged, sources = read_frozen(args.repo)
    base = baseline_metrics(frozen, benchmark, logged)
    per_query = {q: {"whole": base[q]} for q in QUESTIONS}
    if args.baseline_only:
        print(json_text(summarize(per_query, "whole")))
        return
    if not all((args.candidates, args.inputs, args.scores, args.out)):
        ap.error("Please specify --candidates, --inputs, --scores and --out.")
    controls_path = args.controls or args.inputs.parent/"controls.jsonl"
    plan_path = args.plan or args.inputs.parent/"plan.json"
    plan = json.loads(plan_path.read_bytes())
    assert plan["query_ids"] == list(QUESTIONS)
    for file in (args.candidates, args.inputs, controls_path):
        assert sha256(file) == plan["output_hashes"][file.name]
    models, field_keys, controls, passages = load_plan(
        args.candidates, args.inputs, controls_path, frozen, benchmark
    )
    logits = load_logits(args.scores, set(passages))
    ctrl_record = control_summary(controls, logits)
    provenance = metadata(args.scores.with_name("provenance.json"), args.inputs)
    assert provenance["input_count"] == len(passages) and provenance["max_length"] == 512
    assert provenance["model"] == "BAAI/bge-reranker-v2-m3" and provenance["revision"] == "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
    assert provenance["scoring"] == "raw_logit" and provenance["truncation"] == "only_second"
    args.out.mkdir(parents=True, exist_ok=True)
    ranked_path = args.out / "candidate-ranks.csv"
    target_example = {}
    split_gold = {"missing_groups":0, "missing_groups_with_split_allowed_candidate":0, "query_ids":[]}
    with ranked_path.open("w", encoding="utf8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(("query_id", "chunk_id", "whole_score", "whole_rank", "field_count", "winning_passage_id",
                         "field_score", "field_rank", "mean_rank", "fusion_rank", "evidence_groups"))
        for q in QUESTIONS:
            candidates, original = models[q], frozen[q]
            winning, field_ranks, fusion_ranks = rank_query(candidates, original, q, logits)
            req = benchmark[q]["evidence_requirements"]
            rec = per_query[q]
            for key, ranks in (("fields", field_ranks), ("fusion", fusion_ranks)):
                rec[key] = evidence(ranks, req)
                rec[key]["delta_from_whole"] = q_differences(rec["whole"], rec[key])
                rec[key]["group_transitions_at_10"]=group_transitions(
                    q, req, original, candidates, winning, ranks, passages
                )
            rec["candidate_count"] = len(candidates)
            rec["split_candidate_count"] = sum(bool(ids) for ids in candidates.values())
            missing = {i for i,rank in enumerate(rec["whole"]["group_ranks"]) if rank is None or rank > 10}
            affected = {i for i in missing if any(candidates.get(c) for c in req[i]["acceptable_chunk_ids"])}
            split_gold["missing_groups"] += len(missing)
            split_gold["missing_groups_with_split_allowed_candidate"] += len(affected)
            if affected:
                split_gold["query_ids"].append(q)
            rec["cutoff_check"] = cutoff_diagnostic(
                candidates, winning, field_ranks, original, q, logits, req, ctrl_record["maximum_absolute"]
            )
            for c in sorted(candidates, key=lambda c: original[c][0]):
                whole_rank, whole_score = original[c]
                field_score, winning_id = winning[c]
                fraction = (field_ranks[c] + whole_rank) / 2
                groups = "|".join(str(i) for i,g in enumerate(req, 1) if c in g["acceptable_chunk_ids"])
                writer.writerow((q, c, whole_score, whole_rank, len(candidates[c]), winning_id, field_score, field_ranks[c],
                                 fraction, fusion_ranks[c], groups))
                if q == "W7-PR-012" and whole_rank == 21:
                    assert c in req[0]["acceptable_chunk_ids"] and winning_id
                    passage = passages[q,winning_id]
                    target_example = {
                        "query_id":q, "chunk_id":c, "whole_rank":whole_rank, "field_rank":field_ranks[c],
                        "fusion_rank":fusion_ranks[c], "winning_field_passage":passage,
                        "winning_field_label":passage.splitlines()[1], "whole_score":whole_score,
                        "winning_field_score":field_score,
                    }
    assert target_example
    net = {m:{k:{
        "gained_groups":sum(len(rec[m]["delta_from_whole"][k]["gained_groups"]) for rec in per_query.values()),
        "lost_groups":sum(len(rec[m]["delta_from_whole"][k]["lost_groups"]) for rec in per_query.values()),
    } for k in ("10","100")} for m in ("fields","fusion")}
    live = summarize(per_query, "whole")
    for m in ("fields","fusion"):
        for k, met in summarize(per_query,m).items():
            assert live[k]["groups_recovered"] + net[m][k]["gained_groups"] - net[m][k]["lost_groups"] == met["groups_recovered"]
    out = {
        "cohort": "14 answerable Week 7 type 1 baseline rank failures",
        "selection": list(QUESTIONS),
        "candidate_pairs": sum(len(v) for v in frozen.values()),
        "changed_candidate_pairs": sum(bool(i) for c in models.values() for i in c.values()),
        "new_scored_field_passages": len(field_keys), "measured_whole_controls":len(controls),
        "unique_measured_pairs_including_controls":len(logits), "controls_512":ctrl_record,
        "precision_note":"Fourteen matched original whole passages check CPU-versus-frozen drift. Uniform +/- max absolute observed control offset is a sensitivity check only, not an error bound; only the newly scored fields are shifted.",
        "new_score_run":provenance, "target_example":target_example, "delta_groups":net, "split_gold":split_gold,
        "tie_policy": "Field logits descending, ties by original rank. Fusion by field rank + whole rank ascending, ties by original rank.",
        "candidate_ranks_sha256": sha256(ranked_path),
        "sources_sha256": {p.name: sha256(p) for p in [*sources, plan_path, args.candidates, args.inputs, controls_path, args.scores]},
        "aggregate": {m: summarize(per_query, m) for m in METHODS},
        "per_query": per_query,
    }
    (args.out / "result.json").write_text(json_text(out), encoding="utf8")
    make_report(out, args.out)
    print(json_text(out["aggregate"]))


if __name__ == "__main__":
    main()
