"""Read-only direct source access and one-hop graph traversal with body paging.

The old target/relation interface remains available to historical callers;
the Qwen protocol uses mode/target_type/target_id and issued cursors.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from copy import deepcopy
from pathlib import Path
import re
import unicodedata

import networkx as nx

from civic_link.common import ROOT, connect
from civic_link.graph.build import RELATIONS, load_graph
from .graph_access import GraphAccessMixin


def _name(value: str) -> str:
    """Normalize only Unicode, case, and whitespace; do not guess entities."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


class GraphTool(GraphAccessMixin):
    """A bounded one-hop lookup with explicit target-resolution outcomes.

    ``direction`` and limits belong to application configuration, not LLM args.
    Defaults are implementation starting points, not performance-selected values.
    """

    name = "graph"
    supported_relations = tuple(RELATIONS)

    def __init__(self, graph, evidence, chunks, documents, *,
                 max_relations: int = 20, max_evidence: int = 50,
                 direction: str = "outgoing", max_body_chars: int = 12000):
        if not isinstance(graph, nx.MultiDiGraph):
            raise ValueError("GraphTool requires a directed multigraph")
        if direction not in {"outgoing", "incoming", "both"}:
            raise ValueError("direction must be outgoing, incoming, or both")
        if any(type(n) is not int or n < 1 for n in (max_relations, max_evidence)):
            raise ValueError("Graph result limits must be positive integers")
        self.graph = graph
        self.evidence = evidence
        self.chunks = chunks
        self.documents = documents
        self.max_relations = max_relations
        self.max_evidence = max_evidence
        self.direction = direction
        self._names = defaultdict(set)
        law_names = defaultdict(set)
        for _, node in graph.nodes(data=True):
            if node.get("type") == "Law":
                law_names[node.get("source_document_id")].add(node["label"])
        for node_id, node in graph.nodes(data=True):
            aliases = [node.get("label", ""), *node.get("aliases", [])]
            if node.get("type") == "Article":
                locator = node.get("locator", "")
                aliases.append(locator)
                # These names use existing document/locator properties; they do
                # not create a new graph relationship or infer a legal citation.
                for law_name in law_names[node.get("source_document_id")]:
                    aliases.extend((f"{law_name} {locator}",
                                    f"{law_name} {node.get('label', '')}"))
            for alias in aliases:
                if isinstance(alias, str) and _name(alias):
                    self._names[_name(alias)].add(node_id)
        self._init_access(max_body_chars)

    @classmethod
    def from_files(cls, db=ROOT / "data/corpus/civic.sqlite",
                   graph_path=ROOT / "data/graph/graph.json", **options):
        """Load verified graph bindings and the matching read-only corpus."""
        graph, evidence = load_graph(Path(graph_path), Path(db))
        with closing(connect(Path(db), True)) as connection:
            chunks = {r["chunk_id"]: dict(r) for r in connection.execute(
                "SELECT * FROM chunks")}
            documents = {r["document_id"]: dict(r) for r in connection.execute(
                "SELECT document_id, title, body, source_url, source_type FROM documents")}
        return cls(graph, evidence, chunks, documents, **options)

    def _target(self, node_id):
        node = self.graph.nodes[node_id]
        return {"id": node_id, "label": node["label"], "type": node["type"]}

    def _records(self, kind, owner):
        ids = self.evidence.get(f"{kind}_bindings", {}).get(owner, [])
        return [(eid, self.evidence["records"][eid]) for eid in sorted(ids)]

    def _chunk_ids(self, records):
        ids = []
        for _, record in records:
            for cid in record["chunk_ids"]:
                chunk = self.chunks[cid]
                document = self.documents[chunk["document_id"]]
                if chunk["document_id"] != record["source_document_id"]:
                    raise ValueError("Graph evidence points to the wrong document")
                if not document.get("source_url"):
                    raise ValueError("Graph evidence has no registered source")
                if cid not in ids:
                    ids.append(cid)
        return ids

    def _edge_rows(self, target, relation):
        rows = {}
        if self.direction in {"outgoing", "both"}:
            for u, v, key, edge in self.graph.out_edges(target, keys=True, data=True):
                if edge["relation"] == relation:
                    rows[u, v, key] = (u, v, key, edge, v)
        if self.direction in {"incoming", "both"}:
            for u, v, key, edge in self.graph.in_edges(target, keys=True, data=True):
                if edge["relation"] == relation:
                    rows[u, v, key] = (u, v, key, edge, u)
        return [rows[key] for key in sorted(rows)]

    def run(self, arguments: dict) -> dict:
        if isinstance(arguments, dict) and 'mode' in arguments:
            return self.read(arguments)
        # Kept for historical callers; the Qwen protocol only accepts the new schema.
        return self._legacy_run(arguments)

    def _legacy_run(self, arguments: dict) -> dict:
        result = {"tool": self.name, "status": "error", "evidence": [],
                  "relations": [], "metadata": {"direction": self.direction,
                  "max_hops": 1, "max_relations": self.max_relations,
                  "max_evidence": self.max_evidence, "truncated": False}}
        if (not isinstance(arguments, dict) or set(arguments) != {"target", "relation"}
                or any(not isinstance(arguments[k], str) or not arguments[k].strip()
                       for k in ("target", "relation"))):
            result["error"] = {"code": "invalid_arguments",
                               "message": "graph requires non-empty target and relation strings"}
            return result
        target, relation = arguments["target"].strip(), arguments["relation"].strip()
        if relation not in RELATIONS:
            result["error"] = {"code": "unsupported_relation",
                               "message": "Use one of: " + ", ".join(RELATIONS)}
            return result
        matches = [target] if target in self.graph else sorted(self._names.get(_name(target), []))
        if not matches:
            result.update(status="not_found", candidates=[])
            return result
        if len(matches) > 1:
            result.update(status="ambiguous", candidates=[self._target(n) for n in matches[:self.max_relations]])
            result["metadata"].update(candidate_count=len(matches),
                                      truncated=len(matches) > self.max_relations)
            return result
        root = matches[0]
        result["target"] = self._target(root)
        rows = self._edge_rows(root, relation)
        selected = rows[:self.max_relations]
        result["metadata"].update(relation_count=len(rows), returned_relation_count=len(selected))
        if not selected:
            result.update(status="no_results", reason="no_matching_relation")
            return result
        try:
            plans, all_ids = [], []
            for u, v, key, edge, neighbor in selected:
                edge_records = self._records("edge", key)
                neighbor_records = self._records("node", neighbor)
                if self.graph.nodes[neighbor]["type"] in {"Document", "Agency"}:
                    # Merged requirement/agency labels may belong to several
                    # services. Never pull another service's document binding.
                    edge_docs = {record["source_document_id"] for _, record in edge_records}
                    neighbor_records = [(eid, record) for eid, record in neighbor_records
                                        if record["source_document_id"] in edge_docs]
                edge_ids = self._chunk_ids(edge_records)
                neighbor_ids = self._chunk_ids(neighbor_records)
                plans.append((u, v, key, edge, neighbor, edge_records, edge_ids, neighbor_ids))
                for cid in edge_ids + neighbor_ids:
                    if cid not in all_ids:
                        all_ids.append(cid)
            kept_ids = all_ids[:self.max_evidence]
            kept = set(kept_ids)
            for cid in kept_ids:
                chunk = self.chunks[cid]
                result["evidence"].append({"id": cid, "document_id": chunk["document_id"],
                                           "title": chunk["title"], "text": chunk["text"],
                                           "source": self.documents[chunk["document_id"]]["source_url"]})
            for u, v, key, edge, neighbor, records, edge_ids, neighbor_ids in plans:
                returned_edge_ids = [cid for cid in edge_ids if cid in kept]
                result["relations"].append({
                    "from": u, "to": v, "from_label": self.graph.nodes[u]["label"],
                    "to_label": self.graph.nodes[v]["label"], "relation": relation,
                    "edge_id": key, "path": [root, neighbor],
                    "evidence_ids": returned_edge_ids,
                    "connected_evidence_ids": [cid for cid in neighbor_ids if cid in kept],
                    "evidence_status": ("bound" if returned_edge_ids else
                                        "not_returned" if edge_ids else "no_bound_evidence"),
                    "qualifiers": deepcopy(edge.get("qualifiers", {})),
                    "review_status": edge.get("review_status"),
                    "provenance": [dict(id=eid, **deepcopy(record)) for eid, record in records],
                })
            result["metadata"].update(evidence_count=len(all_ids), returned_evidence_count=len(kept_ids),
                                      truncated=len(selected) < len(rows) or len(kept_ids) < len(all_ids))
            result["status"] = "ok" if result["evidence"] else "no_results"
            if not result["evidence"]:
                result["reason"] = "relations_exist_without_bound_corpus_evidence"
            return result
        except (KeyError, TypeError, ValueError) as exc:
            result.update(status="error", evidence=[], relations=[],
                          error={"code": "invalid_graph_evidence", "message": str(exc)})
            return result
