from copy import deepcopy

import networkx as nx
import pytest

from civic_link.agent.graph import GraphTool
from civic_link.agent.prompts import describe_tools


@pytest.fixture
def graph_data():
    graph = nx.MultiDiGraph()
    graph.add_node("S1", type="Service", label="A 증명서", aliases=["A증명", "공통"])
    graph.add_node("S2", type="Service", label="B 증명서", aliases=["공통"])
    graph.add_node("REQ1", type="Document", label="위임장")
    graph.add_node("LAW1", type="Law", label="가상법", source_document_id="D1")
    graph.add_node("LAW2", type="Law", label="민법", source_document_id="D2")
    graph.add_node("ART1", type="Article", label="제777조(참조)", locator="제777조", source_document_id="D1")
    graph.add_node("ART2", type="Article", label="제777조(친족)", locator="제777조", source_document_id="D2")
    graph.add_edge("S1", "REQ1", key="EDGE1", relation="REQUIRES",
                   qualifiers={"condition": "대리 신청인 경우", "requirement_mode": "conditional_statement_not_atomic_obligation"},
                   review_status="machine_extracted_pending_human_review")
    graph.add_edge("ART1", "ART2", key="REF1", relation="REFERENCES",
                   qualifiers={"basis": "explicit_article_reference"}, review_status="pending")
    chunks = {
        "CS": {"document_id": "DS", "title": "A 안내", "text": "대리 신청인은 위임장을 제출한다."},
        "CO": {"document_id": "DO", "title": "다른 서비스", "text": "다른 서비스의 서류 안내"},
        "C1": {"document_id": "D1", "title": "가상법 제777조", "text": "민법 제777조를 따른다."},
        "C2": {"document_id": "D2", "title": "민법 제777조", "text": "친족의 범위에 관한 원문"},
    }
    documents = {did: {"source_url": f"https://example.org/{did}"} for did in ("DS", "DO", "D1", "D2")}
    records = {}
    for eid, cid in (("ES", "CS"), ("EO", "CO"), ("E1", "C1"), ("E2", "C2")):
        chunk = chunks[cid]
        records[eid] = {"source_document_id": chunk["document_id"], "chunk_ids": [cid],
                        "quote": chunk["text"], "start_char": 0, "end_char": len(chunk["text"]),
                        "source_url": documents[chunk["document_id"]]["source_url"],
                        "role": "source_statement", "review_status": "pending"}
    evidence = {"records": records, "node_bindings": {"REQ1": ["ES", "EO"], "ART1": ["E1"], "ART2": ["E2"]},
                "edge_bindings": {"EDGE1": ["ES"], "REF1": ["E1"]}}
    return graph, evidence, chunks, documents


def test_first_call_exact_alias_returns_scoped_evidence_and_conditions(graph_data):
    graph, evidence, chunks, documents = graph_data
    before = deepcopy((evidence, chunks, documents, dict(graph.edges["S1", "REQ1", "EDGE1"])))
    result = GraphTool(*graph_data).run({"target": " A 증명 ", "relation": "REQUIRES"})
    assert result["status"] == "ok"
    contract = describe_tools()['graph']['returns']
    assert result['status'] in contract['status']
    assert set(result['evidence'][0]) == set(contract['evidence_item'])
    assert set(result['relations'][0]) == set(contract['relation_item'])
    assert set(result['metadata']) <= set(contract['metadata'])
    assert result["target"]["id"] == "S1"
    assert [e["id"] for e in result["evidence"]] == ["CS"]
    assert result["evidence"][0]["source"] == documents["DS"]["source_url"]
    relation = result["relations"][0]
    assert relation["qualifiers"]["condition"] == "대리 신청인 경우"
    assert relation["evidence_ids"] == ["CS"]
    assert relation["connected_evidence_ids"] == ["CS"]
    assert relation["provenance"][0]["quote"] == chunks["CS"]["text"]
    relation["qualifiers"]["condition"] = "changed output"
    relation["provenance"][0]["quote"] = "changed output"
    assert (evidence, chunks, documents, dict(graph.edges["S1", "REQ1", "EDGE1"])) == before


def test_reference_returns_both_citation_and_target_article_body(graph_data):
    result = GraphTool(*graph_data).run({"target": "가상법 제777조", "relation": "REFERENCES"})
    assert result["status"] == "ok"
    assert {e["id"] for e in result["evidence"]} == {"C1", "C2"}
    assert result["relations"][0]["evidence_ids"] == ["C1"]
    assert result["relations"][0]["connected_evidence_ids"] == ["C2"]
    assert result["relations"][0]["path"] == ["ART1", "ART2"]


def test_qualified_article_resolves_without_prior_search(graph_data):
    tool = GraphTool(*graph_data)
    result = tool.run({"target": "민법 제 777 조", "relation": "REFERENCES"})
    assert result["target"]["id"] == "ART2"
    assert result["status"] == "no_results"
    assert result["reason"] == "no_matching_relation"
    assert tool.run({"target": "제777조", "relation": "REFERENCES"})["status"] == "ambiguous"


def test_ambiguous_name_does_not_guess_a_node(graph_data):
    result = GraphTool(*graph_data).run({"target": "공통", "relation": "REQUIRES"})
    assert result["status"] == "ambiguous"
    assert {c["id"] for c in result["candidates"]} == {"S1", "S2"}
    assert result["evidence"] == result["relations"] == []


def test_unknown_target_and_absent_relation_are_distinct(graph_data):
    tool = GraphTool(*graph_data)
    assert tool.run({"target": "A 증명서와 비슷한 이름", "relation": "REQUIRES"})["status"] == "not_found"
    result = tool.run({"target": "S1", "relation": "HANDLED_BY"})
    assert result["status"] == "no_results"
    assert result["reason"] == "no_matching_relation"


def test_incoming_is_explicit_and_original_direction_is_retained(graph_data):
    args = {"target": "ART2", "relation": "REFERENCES"}
    assert GraphTool(*graph_data).run(args)["status"] == "no_results"
    result = GraphTool(*graph_data, direction="incoming").run(args)
    assert result["status"] == "ok"
    relation = result["relations"][0]
    assert (relation["from"], relation["to"]) == ("ART1", "ART2")
    assert relation["path"] == ["ART2", "ART1"]


def test_evidence_limit_does_not_claim_unreturned_body_was_read(graph_data):
    result = GraphTool(*graph_data, max_evidence=1).run({"target": "ART1", "relation": "REFERENCES"})
    assert [e["id"] for e in result["evidence"]] == ["C1"]
    assert result["metadata"]["truncated"]
    assert result["metadata"]["evidence_count"] == 2
    assert result["relations"][0]["connected_evidence_ids"] == []


def test_relation_limit_and_ambiguous_candidate_limit_are_explicit(graph_data):
    graph_data[0].add_edge("S1", "REQ1", key="EDGE2", relation="REQUIRES", qualifiers={})
    graph_data[1]["edge_bindings"]["EDGE2"] = ["ES"]
    tool = GraphTool(*graph_data, max_relations=1)
    result = tool.run({"target": "S1", "relation": "REQUIRES"})
    assert len(result["relations"]) == 1
    assert result["metadata"]["relation_count"] == 2
    assert result["metadata"]["truncated"]
    ambiguous = tool.run({"target": "공통", "relation": "REQUIRES"})
    assert len(ambiguous["candidates"]) == 1
    assert ambiguous["metadata"]["candidate_count"] == 2
    assert ambiguous["metadata"]["truncated"]


def test_existing_relation_without_bound_text_is_not_missing_edge(graph_data):
    graph_data[1]["edge_bindings"]["REF1"] = []
    graph_data[1]["node_bindings"]["ART2"] = []
    result = GraphTool(*graph_data).run({"target": "ART1", "relation": "REFERENCES"})
    assert result["status"] == "no_results"
    assert result["reason"] == "relations_exist_without_bound_corpus_evidence"
    assert len(result["relations"]) == 1
    assert result["relations"][0]["evidence_status"] == "no_bound_evidence"


def test_broken_evidence_is_error_not_empty_result(graph_data):
    graph_data[1]["records"]["E1"]["chunk_ids"] = ["MISSING"]
    result = GraphTool(*graph_data).run({"target": "ART1", "relation": "REFERENCES"})
    assert result["status"] == "error"
    assert result["error"]["code"] == "invalid_graph_evidence"
    assert result["evidence"] == result["relations"] == []


@pytest.mark.parametrize("arguments", [None, {}, {"target": "S1", "relation": 7},
    {"target": "S1", "relation": ""}, {"target": "S1", "relation": "REQUIRES", "max_hops": 100},
    {"target": "S1", "relation": "INVENTED_RELATION"}])
def test_invalid_arguments_never_traverse(graph_data, arguments):
    result = GraphTool(*graph_data).run(arguments)
    assert result["status"] == "error"
    assert not result["evidence"]


@pytest.mark.parametrize("options", [{"max_relations": 0}, {"max_evidence": True}, {"direction": "random"}])
def test_invalid_configuration_rejected(graph_data, options):
    with pytest.raises(ValueError):
        GraphTool(*graph_data, **options)
