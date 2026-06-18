"""
convert_shacl.py

Converts SHACL .ttl files into a JSONL file.

All .ttl files in the input directory are merged into a single RDF graph
before processing, so cross-file shape references are resolved correctly.

Handles two file patterns:
  - Pattern A (original): auxiliary shapes referenced via sh:node only.
  - Pattern B (new):      auxiliary shapes referenced via sh:xone / sh:or /
                          sh:and / sh:not; named RDF list heads (rdf:first /
                          rdf:rest) used as sh:ignoredProperties values;
                          sh:PropertyShape siblings shared across NodeShapes.

Key rules:
  1. Top-level shapes = URIRef NodeShapes that are NOT purely auxiliary,
     plus URIRef PropertyShapes that have their own sh:target* declaration.
     BNode NodeShapes are NEVER top-level records.
  2. Auxiliary shape = a named (URIRef) NodeShape or PropertyShape that:
       - is referenced by another shape (via sh:node, sh:xone, sh:or,
         sh:and, sh:not, or sh:property), AND
       - has no sh:target* predicate of its own.
     Auxiliary shapes are inlined into the referencing shape's Turtle.
  3. Named RDF list heads (rdf:first / rdf:rest chains) reachable from a
     shape are fully traversed and included in that shape's snippet.
  4. sh:sparql triples (and their BNode sub-graphs) are stripped from every
     shape before serialization. The rest of the shape is kept intact.
  5. Duplicate IDs get -1, -2, ... suffix.
  6. Only prefixes actually used in a snippet are emitted.

Usage:
  python convert_shacl.py [--input-dir DIR] [--output-file FILE]

Defaults:
  --input-dir   ./shacl
  --output-file output_data.jsonl
"""

import os, json, logging, argparse
from collections import defaultdict
from rdflib import Graph, URIRef, BNode, Namespace, RDF
from rdflib.namespace import SH, RDFS

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# Shape-expecting parameters per W3C SHACL spec.
# Non-list-taking: sh:node, sh:not, sh:qualifiedValueShape
# List-taking:     sh:and, sh:or, sh:xone
# Property-link:   sh:property (NodeShape -> named PropertyShape)
INLINE_PREDS = {
    SH.node,
    SH["not"],
    SH.qualifiedValueShape,
    SH["and"],
    SH["or"],
    SH.xone,
    SH.property,
}

# Predicates that identify a shape as having its own validation target.
SH_TARGET = {SH.targetClass, SH.targetNode, SH.targetSubjectsOf, SH.targetObjectsOf}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def collect_triples(graph: Graph, subject, visited: set) -> list:
    """
    Recursively collect all triples reachable from subject.

    Follows:
      - BNode objects unconditionally (anonymous blank nodes).
      - Named (URIRef) RDF list nodes reached via rdf:first / rdf:rest,
        so that named list heads like :ignoredProperties are fully captured.
    Does NOT recurse into arbitrary URIRef objects (that would pull in the
    whole graph), only into those that are part of an RDF list chain.
    """
    if subject in visited:
        return []
    visited.add(subject)
    triples = []
    for p, o in graph.predicate_objects(subject):
        triples.append((subject, p, o))
        if isinstance(o, BNode):
            triples.extend(collect_triples(graph, o, visited))
        elif isinstance(o, URIRef) and p in (RDF.first, RDF.rest):
            triples.extend(collect_triples(graph, o, visited))
    return triples


def strip_sparql_triples(triples: list) -> tuple[list, int]:
    """
    Remove sh:sparql triples and their entire BNode sub-graphs from a triple list.

    Returns (cleaned_triples, count_removed).
    """
    sparql_bnodes: set = set()
    for s, p, o in triples:
        if p == SH.sparql and isinstance(o, BNode):
            sparql_bnodes.add(o)

    if not sparql_bnodes:
        return triples, 0

    def collect_bnode_subtree(root: BNode, all_triples: list) -> set:
        subtree: set = set()
        stack = [root]
        while stack:
            cur = stack.pop()
            if cur in subtree:
                continue
            subtree.add(cur)
            for s, p, o in all_triples:
                if s == cur and isinstance(o, BNode):
                    stack.append(o)
        return subtree

    bad_subjects: set = set()
    for bn in sparql_bnodes:
        bad_subjects |= collect_bnode_subtree(bn, triples)

    cleaned = [
        (s, p, o) for s, p, o in triples
        if not (p == SH.sparql)
        and s not in bad_subjects
    ]
    removed = len(triples) - len(cleaned)
    return cleaned, removed


def used_prefixes(triples: list, prefix_map: dict) -> dict:
    """Return only the prefix -> ns entries whose namespace appears in the triples."""
    all_uris = {str(t) for triple in triples for t in triple if isinstance(t, URIRef)}
    used = {}
    for uri in all_uris:
        best_prefix, best_ns = None, ""
        for prefix, ns in prefix_map.items():
            if uri.startswith(ns) and len(ns) > len(best_ns):
                best_ns, best_prefix = ns, prefix
        if best_prefix is not None:
            used[best_prefix] = prefix_map[best_prefix]
    return used


def shape_to_turtle(main_shape: URIRef, aux_shapes: set,
                    graph: Graph, prefix_map: dict) -> tuple[str, int]:
    """Serialize main_shape + aux_shapes into a minimal self-contained Turtle string.

    Returns (turtle_string, sparql_triples_removed).
    """
    triples = collect_triples(graph, main_shape, set())
    for aux in aux_shapes:
        triples.extend(collect_triples(graph, aux, set()))

    triples, n_removed = strip_sparql_triples(triples)

    mini = Graph()
    up = used_prefixes(triples, prefix_map)
    for p, ns in up.items():
        mini.bind(p, Namespace(ns))
    for t in triples:
        mini.add(t)
    return mini.serialize(format="turtle").strip(), n_removed


def uri_to_short_id(uri: str, prefix_map: dict) -> str:
    """Convert a URIRef string to a prefixed name like :Foo or dcat:Catalog."""
    best_prefix, best_ns = None, ""
    for prefix, ns in prefix_map.items():
        if uri.startswith(ns) and len(ns) > len(best_ns):
            best_ns, best_prefix = ns, prefix
    if best_prefix is None:
        return f"<{uri}>"
    local = uri[len(best_ns):]
    return f":{local}" if best_prefix == "" else f"{best_prefix}:{local}"


def has_target(graph: Graph, shape) -> bool:
    """Return True if shape has any sh:target* declaration."""
    return any((shape, tp, None) in graph for tp in SH_TARGET)


def unwrap_list_members(graph: Graph, node) -> list:
    """
    Walk an RDF list (BNode or URIRef) and return all rdf:first values,
    recursing into nested lists if needed.
    """
    members = []
    visited = set()
    cur = node
    while cur and cur != RDF.nil and cur not in visited:
        visited.add(cur)
        for first in graph.objects(cur, RDF.first):
            members.append(first)
        rest = list(graph.objects(cur, RDF.rest))
        cur = rest[0] if rest else None
    return members


# ---------------------------------------------------------------------------
# Core graph processing
# ---------------------------------------------------------------------------

def process_graph(g: Graph, stats: dict) -> list[dict]:
    """Extract top-level shape records from a merged RDF graph."""

    prefix_map = {p: str(ns) for p, ns in g.namespaces()}

    # --- Shape discovery ---

    # Explicit rdf:type declarations
    named_node_shapes: set = {
        s for s in g.subjects(RDF.type, SH.NodeShape)
        if isinstance(s, URIRef)
    }
    named_property_shapes: set = {
        s for s in g.subjects(RDF.type, SH.PropertyShape)
        if isinstance(s, URIRef)
    }

    # W3C condition 2: shapes identified by sh:target* without explicit type declaration
    for tp in SH_TARGET:
        for s in g.subjects(tp, None):
            if not isinstance(s, URIRef):
                continue
            if s not in named_node_shapes and s not in named_property_shapes:
                log.info("  Implicit shape discovered via %s: %s",
                         tp.split("#")[-1], uri_to_short_id(str(s), prefix_map))
                named_node_shapes.add(s)

    all_named_shapes = named_node_shapes | named_property_shapes

    # --- Build reference map ---

    aux_to_parents: dict = defaultdict(set)

    def scan_for_refs(parent: URIRef, root):
        """DFS from root; record named-shape references into aux_to_parents."""
        visited = set()
        stack = [root]
        while stack:
            cur = stack.pop()
            if cur in visited:
                continue
            visited.add(cur)
            for p, o in g.predicate_objects(cur):
                if p in INLINE_PREDS:
                    if isinstance(o, URIRef):
                        if o in all_named_shapes and o != parent:
                            aux_to_parents[o].add(parent)
                    elif isinstance(o, BNode):
                        for member in unwrap_list_members(g, o):
                            if isinstance(member, URIRef):
                                if member in all_named_shapes and member != parent:
                                    aux_to_parents[member].add(parent)
                            elif isinstance(member, BNode):
                                stack.append(member)
                        stack.append(o)
                if isinstance(o, BNode):
                    stack.append(o)

    for shape in named_node_shapes:
        scan_for_refs(shape, shape)

    # A shape is auxiliary if it is referenced by others AND has no target of its own
    auxiliary_shapes = {
        shape for shape, parents in aux_to_parents.items()
        if not has_target(g, shape)
    }

    # Top-level NodeShapes: not auxiliary
    top_level_node_shapes = named_node_shapes - auxiliary_shapes

    # Top-level PropertyShapes: have their own sh:target* declaration
    # These are standalone PropertyShapes that trigger validation independently.
    top_level_property_shapes = {
        s for s in named_property_shapes
        if has_target(g, s) and s not in auxiliary_shapes
    }

    top_level_shapes = top_level_node_shapes | top_level_property_shapes

    log.info("  NodeShapes: %d | PropertyShapes: %d | "
             "Auxiliary (inlined): %d | Top-level: %d "
             "(NodeShapes: %d, PropertyShapes with target: %d)",
             len(named_node_shapes), len(named_property_shapes),
             len(auxiliary_shapes), len(top_level_shapes),
             len(top_level_node_shapes), len(top_level_property_shapes))

    stats["total_shapes"]       += len(top_level_shapes)
    stats["inlined_aux_shapes"] += len(auxiliary_shapes)

    # --- Build records ---

    records = []
    for shape in sorted(top_level_shapes, key=str):
        # Collect all auxiliary shapes transitively needed by this shape
        needed_aux = set()
        vis = set()
        stk = [shape]
        while stk:
            cur = stk.pop()
            if cur in vis:
                continue
            vis.add(cur)
            for p, o in g.predicate_objects(cur):
                if p in INLINE_PREDS:
                    if isinstance(o, URIRef) and o in auxiliary_shapes:
                        needed_aux.add(o)
                        stk.append(o)
                    elif isinstance(o, BNode):
                        for member in unwrap_list_members(g, o):
                            if isinstance(member, URIRef) and member in auxiliary_shapes:
                                needed_aux.add(member)
                                stk.append(member)
                        stk.append(o)
                if isinstance(o, BNode):
                    stk.append(o)

        short_id = uri_to_short_id(str(shape), prefix_map)
        ttl, n_sparql = shape_to_turtle(shape, needed_aux, g, prefix_map)
        if n_sparql:
            log.info("  sh:sparql stripped (%d triple(s)) from %s", n_sparql, short_id)
            stats["sparql_triples_stripped"] += n_sparql
        records.append({"_raw_id": short_id, "shacl": ttl, "nl": ""})

    return records


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Convert SHACL .ttl files into a JSONL file."
    )
    parser.add_argument(
        "--input-dir", "-i",
        default="./shacl",
        help="Directory containing .ttl files (default: ./shacl)"
    )
    parser.add_argument(
        "--output-file", "-o",
        default="output_data.jsonl",
        help="Output JSONL file path (default: output_data.jsonl)"
    )
    args = parser.parse_args()

    input_dir   = args.input_dir
    output_file = args.output_file

    if not os.path.isdir(input_dir):
        log.error("Input directory not found: %s", input_dir)
        return

    ttl_files = sorted(f for f in os.listdir(input_dir) if f.endswith(".ttl"))
    if not ttl_files:
        log.error("No .ttl files found in %s", input_dir)
        return

    log.info("Found %d TTL file(s): %s", len(ttl_files), ttl_files)

    # Merge all .ttl files into a single graph before processing.
    # This resolves cross-file shape references correctly.
    g = Graph()
    for fname in ttl_files:
        fpath = os.path.join(input_dir, fname)
        log.info("Parsing: %s", fname)
        g.parse(fpath, format="turtle")

    log.info("Merged graph: %d triples total", len(g))

    stats = {
        "files_processed":         len(ttl_files),
        "total_shapes":            0,
        "inlined_aux_shapes":      0,
        "sparql_triples_stripped": 0,
    }

    all_records = process_graph(g, stats)

    # Deduplicate IDs
    id_counter: dict[str, int] = {}
    final_records = []
    for rec in all_records:
        raw = rec.pop("_raw_id")
        if raw not in id_counter:
            id_counter[raw] = 0
            unique_id = raw
        else:
            id_counter[raw] += 1
            unique_id = f"{raw}-{id_counter[raw]}"
        final_records.append({"id": unique_id, "shacl": rec["shacl"], "nl": rec["nl"]})

    with open(output_file, "w", encoding="utf-8") as f:
        for rec in final_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    log.info("=" * 60)
    log.info("SUMMARY")
    log.info("  Files processed           : %d", stats["files_processed"])
    log.info("  Top-level shape records   : %d", stats["total_shapes"])
    log.info("  Auxiliary shapes inlined  : %d", stats["inlined_aux_shapes"])
    log.info("  sh:sparql triples stripped: %d", stats["sparql_triples_stripped"])
    log.info("  Output JSONL records      : %d", len(final_records))
    log.info("=" * 60)
    log.info("Written to %s", output_file)


if __name__ == "__main__":
    main()