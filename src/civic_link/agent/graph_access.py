"""Corpus-backed direct reads, bounded body pages and observation-derived candidates."""
from __future__ import annotations

import base64
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import re


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def graph_arguments(arguments):
    if not isinstance(arguments, dict):
        raise ValueError('graph arguments must be an object')
    required = {'mode', 'target_type', 'target_id'}
    mode = arguments.get('mode')
    if mode == 'traverse':
        required.add('relation')
        optional = {'destination_id', 'cursor'}
        if arguments.get('target_type') != 'node':
            raise ValueError('traverse requires target_type=node')
    elif mode == 'direct':
        optional = {'cursor'}
        if arguments.get('target_type') not in ('node', 'document'):
            raise ValueError('direct target_type must be node or document')
    else:
        raise ValueError('mode must be direct or traverse')
    if not required <= arguments.keys() or arguments.keys() - required - optional:
        raise ValueError('invalid graph fields for the selected mode')
    if any(not isinstance(v, str) or not v.strip() for v in arguments.values()):
        raise ValueError('graph fields must be nonempty strings')


class GraphAccessMixin:
    def _init_access(self, max_body_chars):
        if type(max_body_chars) is not int or max_body_chars < 1:
            raise ValueError('max_body_chars must be a positive integer')
        self.max_body_chars = max_body_chars
        self._doc_chunks = defaultdict(list)
        self._chunk_nodes = defaultdict(set)
        self._doc_articles = defaultdict(list)
        for cid, chunk in self.chunks.items():
            self._doc_chunks[chunk['document_id']].append(cid)
        for ids in self._doc_chunks.values():
            ids.sort(key=lambda cid: (self.chunks[cid].get('start_char', 0), cid))
        for nid, node in self.graph.nodes(data=True):
            if node['type'] == 'Article':
                self._doc_articles[node['source_document_id']].append(nid)
            for _, record in self._records('node', nid):
                for cid in record['chunk_ids']:
                    self._chunk_nodes[cid].add(nid)
        for u, v, key in self.graph.edges(keys=True):
            for _, record in self._records('edge', key):
                for cid in record['chunk_ids']:
                    self._chunk_nodes[cid].update((u, v))

    def _catalog(self, did):
        def start(nid):
            ids = self._article_chunks(nid)
            return min((self.chunks[c].get('start_char', 0) for c in ids), default=0)
        return [self._candidate(nid, []) for nid in sorted(self._doc_articles[did], key=lambda n: (start(n), n))]

    def _article_chunks(self, nid):
        node = self.graph.nodes[nid]
        return [cid for cid in self._doc_chunks[node['source_document_id']]
                if self.chunks[cid].get('locator') == node.get('locator')]

    def _candidate(self, nid, ids):
        row = self._target(nid)
        row.update(evidence_ids=sorted(set(ids)), relations=sorted({e['relation']
                   for _, _, e in self.graph.out_edges(nid, data=True)}))
        did = self.graph.nodes[nid].get('source_document_id')
        if did:
            row['document_id'] = did
        return row

    def observe(self, question, supplied, previous, checks, latest=None):
        """Only question names, bodies actually supplied, and observed neighbors.

        A candidate is navigation metadata, never a claim that its body was read.
        No Top-K filter is applied to this bounded, accumulated observation pool.
        """
        from .graph import _name
        normalized = _name(question)
        explicit = set()
        for alias, matches in self._names.items():
            if len(alias) >= 3 and alias in normalized and len(matches) == 1 and not re.fullmatch(r'제\d+조.*', alias):
                explicit.update(matches)
        pool = {row['id']: set(row['evidence_ids']) for row in previous if row['id'] in self.graph}
        for nid in explicit:
            pool.setdefault(nid, set())
        for row in supplied:
            for cid in row.get('chunk_ids', [row['id']]):
                for nid in self._chunk_nodes.get(cid, ()):
                    pool.setdefault(nid, set()).add(row['id'])
        if latest and latest.get('tool') == 'graph':
            nodes = list(latest.get('article_catalog', [])) + list(latest.get('candidates', []))
            if latest.get('target', {}).get('id') in self.graph:
                nodes.append(latest['target'])
            for edge in latest.get('relations', []):
                nodes.extend([{'id': edge['from']}, {'id': edge['to']}])
            for row in nodes:
                if row['id'] in self.graph:
                    pool.setdefault(row['id'], set())
        unresolved = {eid for check in checks if check['status'] != '확인' for eid in check['evidence_ids']}
        order = lambda nid: (0 if nid in explicit else 1 if pool[nid] & unresolved else 2, nid)
        return [self._candidate(nid, pool[nid]) for nid in sorted(pool, key=order)]

    def _span(self, did, start, end):
        doc = self.documents[did]
        body = doc.get('body')
        if not isinstance(body, str) or not body or not doc.get('source_url'):
            return None
        if not 0 <= start < end <= len(body):
            raise ValueError('invalid stored source range')
        return (did, start, end)

    def _chunk_spans(self, ids):
        spans = []
        for cid in ids:
            chunk = self.chunks[cid]
            start, end = chunk.get('start_char'), chunk.get('end_char')
            if start is None or end is None:
                continue
            span = self._span(chunk['document_id'], start, end)
            if span:
                if self.documents[span[0]]['body'][start:end] != chunk['text']:
                    raise ValueError('chunk body disagrees with source range')
                spans.append(span)
        return spans

    def _node_spans(self, nid):
        node = self.graph.nodes[nid]
        records = self._records('node', nid)
        ids = self._chunk_ids(records)
        if node['type'] == 'Article':
            spans = self._chunk_spans(self._article_chunks(nid))
            return [(spans[0][0], min(s[1] for s in spans), max(s[2] for s in spans))] if spans else []
        spans = self._chunk_spans(ids)
        if node['type'] == 'Service':
            guides = {self.chunks[c]['document_id'] for c in ids
                      if self.documents[self.chunks[c]['document_id']].get('source_type') == 'service_guide'}
            spans = [s for s in spans if s[0] not in guides]
            spans += [(did, 0, len(self.documents[did]['body'])) for did in sorted(guides)
                      if self.documents[did].get('body')]
        return spans

    @staticmethod
    def _merge(spans):
        out = []
        for did, start, end in sorted(set(spans)):
            if out and out[-1][0] == did and start <= out[-1][2]:
                out[-1] = (did, out[-1][1], max(end, out[-1][2]))
            else:
                out.append((did, start, end))
        return out

    def validate_arguments(self, arguments):
        graph_arguments(arguments)
        target = arguments['target_id']
        if arguments['target_type'] == 'document':
            if target not in self.documents:
                raise ValueError('unknown document ID')
        elif target not in self.graph:
            raise ValueError('unknown node ID; use a supplied candidate ID')
        if arguments['mode'] == 'traverse':
            if arguments['relation'] not in self.supported_relations:
                raise ValueError('unsupported relation')
            destination = arguments.get('destination_id')
            if destination and not any(row[4] == destination for row in self._edge_rows(target, arguments['relation'])):
                raise ValueError('destination_id is not connected by the selected relation')

    def _read(self, arguments):
        self.validate_arguments(arguments)
        target = arguments['target_id']
        result = dict(tool='graph', status='ok', evidence=[], relations=[], next_cursor=None,
                      metadata=dict(mode=arguments['mode'], max_body_chars=self.max_body_chars,
                                    direction=self.direction, max_hops=0 if arguments['mode'] == 'direct' else 1))
        spans, missing = [], []
        if arguments['target_type'] == 'document':
            result['target'] = dict(id=target, type='source_document', label=self.documents[target].get('title', target))
            did = target
        else:
            result['target'] = self._target(target)
            did = self.graph.nodes[target].get('source_document_id') if self.graph.nodes[target]['type'] == 'Law' else None
        if arguments['mode'] == 'direct':
            if did and self._doc_articles[did]:
                if 'cursor' in arguments:
                    raise ValueError('article catalogs do not issue a cursor; select an article ID')
                result['article_catalog'] = self._catalog(did)
                result['reason'] = 'select_article'
                return result
            if did:
                body = self.documents[did].get('body', '')
                span = self._span(did, 0, len(body)) if body else None
                spans = [span] if span else []
            else:
                node = self.graph.nodes[target]
                if node['type'] in ('Document', 'Agency'):
                    owners = {u for u, _, e in self.graph.in_edges(target, data=True) if self.graph.nodes[u]['type'] == 'Service'}
                    if len(owners) > 1:
                        result.update(status='ambiguous', reason='select_service_scope',
                                      candidates=[self._candidate(n, []) for n in sorted(owners)])
                        return result
                spans = self._node_spans(target)
            if not spans:
                missing.append(target)
        else:
            rows = self._edge_rows(target, arguments['relation'])
            if arguments.get('destination_id'):
                rows = [r for r in rows if r[4] == arguments['destination_id']]
            if not rows:
                result.update(status='no_results', reason='no_matching_relation')
            for u, v, key, edge, neighbor in rows:
                records = self._records('edge', key)
                edge_spans = self._chunk_spans(self._chunk_ids(records))
                kind = self.graph.nodes[neighbor]['type']
                # Shared requirement/agency nodes: the selected edge supplies
                # the source context, never another service's bound text.
                connected = edge_spans if kind in ('Document', 'Agency') else self._node_spans(neighbor)
                if kind == 'Law':
                    result.setdefault('article_catalog', []).extend(self._catalog(self.graph.nodes[neighbor]['source_document_id']))
                if not connected and kind != 'Law':
                    missing.append(neighbor)
                spans += edge_spans + connected
                result['relations'].append(dict(
                    **{'from': u, 'to': v}, from_label=self.graph.nodes[u]['label'], to_label=self.graph.nodes[v]['label'],
                    relation=edge['relation'], edge_id=key, path=[target, neighbor],
                    qualifiers=deepcopy(edge.get('qualifiers', {})), review_status=edge.get('review_status'),
                    evidence_ids=[], connected_evidence_ids=[],
                    source_ranges=[dict(document_id=d, start_char=a, end_char=b) for d, a, b in edge_spans],
                    connected_ranges=[dict(document_id=d, start_char=a, end_char=b) for d, a, b in connected]))
        spans = self._merge(spans)
        total = sum(b-a for _, a, b in spans)
        identity = digest([ {k:v for k,v in arguments.items() if k != 'cursor'}, self.max_body_chars,
                           self.direction, [(d,a,b,digest(self.documents[d]['body'][a:b])) for d,a,b in spans]])
        offset = 0
        if arguments.get('cursor'):
            try:
                token = json.loads(base64.urlsafe_b64decode(arguments['cursor'].encode()))
                offset = token['offset']
                if token['query'] != identity or type(offset) is not int or not 0 < offset < total:
                    raise ValueError()
            except (ValueError, KeyError, TypeError, UnicodeError) as exc:
                raise ValueError('cursor does not match this query and stored source') from exc
        consumed, remaining, skip = 0, self.max_body_chars, offset
        ranges = []
        for did, start, end in spans:
            if skip >= end-start:
                skip -= end-start
                continue
            start += skip
            skip = 0
            stop = min(end, start+remaining)
            body = self.documents[did]['body']
            if stop < end:
                boundary = body.rfind('\n', start, stop)
                if boundary >= start:
                    stop = boundary+1
            if stop <= start:
                break
            text = body[start:stop]
            eid = f'{did}@{start}:{stop}:{digest(text)[:12]}'
            ranges.append(dict(document_id=did, start_char=start, end_char=stop, evidence_id=eid))
            if text.strip():
                result['evidence'].append(dict(id=eid, document_id=did, title=self.documents[did].get('title', did),
                    text=text, source=self.documents[did]['source_url'], start_char=start, end_char=stop,
                    chunk_ids=[cid for cid in self._doc_chunks[did] if self.chunks[cid].get('start_char', 0) < stop
                               and self.chunks[cid].get('end_char', 0) > start]))
            size = stop-start
            consumed += size
            remaining -= size
            if stop < end or remaining == 0:
                break
        next_offset = offset+consumed
        if next_offset < total:
            result['next_cursor'] = base64.urlsafe_b64encode(json.dumps(dict(query=identity, offset=next_offset)).encode()).decode()
        for relation in result['relations']:
            for key, ranges_key in [('evidence_ids', 'source_ranges'), ('connected_evidence_ids', 'connected_ranges')]:
                relation[key] = [r['evidence_id'] for r in ranges if any(r['document_id'] == s['document_id'] and
                    r['start_char'] < s['end_char'] and r['end_char'] > s['start_char'] for s in relation[ranges_key])]
        result['metadata'].update(total_body_chars=total, returned_body_chars=consumed, offset=offset,
            remaining_body_chars=total-next_offset, returned_ranges=ranges, truncated=next_offset < total,
            missing_source_targets=sorted(set(missing)))
        if not total and not result.get('article_catalog') and result['status'] == 'ok':
            result.update(status='no_results', reason='stored_body_missing')
        return result

    def read(self, arguments):
        try:
            return self._read(arguments)
        except (ValueError, KeyError, TypeError) as exc:
            return dict(tool='graph', status='error', evidence=[], relations=[], next_cursor=None,
                        error=dict(code='invalid_graph_request_or_source', message=str(exc)))
