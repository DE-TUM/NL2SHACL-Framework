"""
SHACL Quality Check — Part 2
Extracts ontology URIs from each SHACL entry, looks up their metadata,
and writes an augmented JSONL with an "ontology_snippet" field.

URI classification
──────────────────
domain terms      : URIs whose namespace is NOT in skip_prefixes
                    + values of sh:path and sh:targetClass
                    → looked up; snippet entry written only if metadata found
                    → not-found is logged and counted

shacl value terms : values of sh:hasValue, sh:class, sh:in
                    → looked up the same way
                    → snippet entry ALWAYS written
                      (with full metadata if found, {"uri_only": true} if not)
                    → no-metadata logged and counted SEPARATELY

Shape URI filtering
───────────────────
URIs whose local name ends with "shape" (case-insensitive) are excluded from
both domain terms and value terms. These are SHACL shape identifiers, not
ontology domain terms.

Local lookup
────────────
Single local TTL file (--ontology-file). Every subject URI is indexed at
startup, so lookup is O(1). HTTP fallback is tried per namespace (cached).

Inputs
──────
  <data.jsonl>        original JSONL
  --prefixes          prefixes.json from Part 1  (default: <stem>_prefixes.json)
  --ontology-file     single local .ttl file  (default: ./ontology/ontology.ttl)

Outputs
───────
  <stem>_augmented.jsonl    original records + "ontology_snippet"
  <stem>_lookup_log.txt     per-entry detail + global summary

Useage:
python ontology_augment.py epo_data.jsonl --skip-namespaces http://data.europa.eu/a4g/data-shape# http://example.org/other-shape# --prefixes epo_data_prefixes.json --ontology-dir epo-dataset/ontology
"""

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

try:
    import requests
    from rdflib import Graph, URIRef, BNode, Literal
    from rdflib.namespace import RDF, RDFS, OWL, DCTERMS, SKOS
    from rdflib.collection import Collection
except ImportError:
    sys.exit("Required: pip install rdflib requests")

# ── constants ──────────────────────────────────────────────────────────────

SKIP_PREFIXES = {"sh", "xsd", "rdf", "owl", ""}

SH = "http://www.w3.org/ns/shacl#"

# sh predicates whose object is a plain domain ontology term
SH_DOMAIN_PREDS = {
    URIRef(SH + "path"),
    URIRef(SH + "targetClass"),
    URIRef(SH + "targetNode"),
    URIRef(SH + "targetSubjectsOf"),
    URIRef(SH + "targetObjectsOf"),
    URIRef(SH + "node"),
    URIRef(SH + "shape"),
}

# sh predicates whose object is a "value term" (always written to snippet)
SH_VALUE_PREDS = {
    URIRef(SH + "hasValue"),
    URIRef(SH + "class"),
    URIRef(SH + "in"),
}

LABEL_PREDICATES = [
    RDFS.label,
    SKOS.prefLabel,
    URIRef("http://schema.org/name"),
]
DESCRIPTION_PREDICATES = [
    RDFS.comment,
    DCTERMS.description,
    SKOS.definition,
    URIRef("http://schema.org/description"),
]

HTTP_HEADERS = {
    "Accept": "text/turtle, application/rdf+xml;q=0.9, application/ld+json;q=0.8"
}
HTTP_TIMEOUT = 10
HTTP_RETRY_DELAY = 1.5


# ── shape URI filter ───────────────────────────────────────────────────────

def is_shape_uri(uri: str) -> bool:
    """
    Return True if the URI's local name ends with 'shape' (case-insensitive).
    These are SHACL shape identifiers, not ontology domain terms.

    Examples filtered out:
      http://example.org/meta/RoleShape
      http://example.org/meta/labelShape
      http://example.org/meta/MYSHAPE
    """
    # local name is the part after the last # or /
    local = uri.rsplit("#", 1)[-1] if "#" in uri else uri.rsplit("/", 1)[-1]
    return local.lower().endswith("shape")


# ── ontology index ─────────────────────────────────────────────────────────

class OntologyIndex:
    """
    Loads all ontology files from a directory at startup and indexes every
    subject URI.  Supports .ttl, .rdf, .xml, .owl, .jsonld/.json-ld.
    HTTP fallback is attempted per namespace URI (cached per namespace).
    """

    _FMT_MAP = {
        ".ttl":     "turtle",
        ".rdf":     "xml",
        ".xml":     "xml",
        ".owl":     "xml",
        ".jsonld":  "json-ld",
        ".json-ld": "json-ld",
    }

    def __init__(self, ontology_dir: Path | None):
        self._local_graph: Graph = Graph()
        self._local_subjects: set[str] = set()
        self._http_graphs: dict[str, Graph | None] = {}
        self._http_source: dict[str, str] = {}

        if ontology_dir and ontology_dir.is_dir():
            files = sorted(ontology_dir.iterdir())
            loaded, skipped = 0, 0
            for f in files:
                fmt = self._FMT_MAP.get(f.suffix.lower())
                if fmt is None:
                    continue
                try:
                    before = len(self._local_graph)
                    self._local_graph.parse(f, format=fmt)
                    added = len(self._local_graph) - before
                    print(f"  OK   {f.name:45s} +{added} triples")
                    loaded += 1
                except Exception as e:
                    print(f"  WARN {f.name:45s} could not parse: {e}")
                    skipped += 1
            self._local_subjects = {
                str(s) for s in self._local_graph.subjects()
                if str(s).startswith("http")
            }
            print(f"Loaded {loaded} files ({skipped} skipped) — "
                  f"{len(self._local_graph)} triples total, "
                  f"{len(self._local_subjects)} subject URIs indexed")
        else:
            print("Warning: no ontology directory found — HTTP only")

    def _namespace_of(self, uri: str) -> str:
        if "#" in uri:
            return uri.rsplit("#", 1)[0] + "#"
        return uri.rsplit("/", 1)[0] + "/"

    def _load_http(self, ns: str) -> Graph | None:
        if ns in self._http_graphs:
            return self._http_graphs[ns]
        for attempt in range(2):
            try:
                r = requests.get(ns, headers=HTTP_HEADERS,
                                 timeout=HTTP_TIMEOUT, allow_redirects=True)
                if r.status_code == 200:
                    ct = r.headers.get("Content-Type", "")
                    fmt = ("xml"     if "rdf+xml" in ct else
                           "json-ld" if ("ld+json" in ct or "json" in ct) else
                           "turtle")
                    g = Graph()
                    g.parse(data=r.text, format=fmt)
                    self._http_graphs[ns] = g
                    self._http_source[ns] = f"http"
                    return g
            except Exception:
                if attempt == 0:
                    time.sleep(HTTP_RETRY_DELAY)
        self._http_graphs[ns] = None
        self._http_source[ns] = "http:failed"
        return None

    def _uri_variants(self, uri: str) -> list[str]:
        variants = [uri]
        if uri.startswith("http://schema.org/"):
            variants.append("https://schema.org/" + uri[len("http://schema.org/"):])
        elif uri.startswith("https://schema.org/"):
            variants.append("http://schema.org/" + uri[len("https://schema.org/"):])
        return variants

    def lookup(self, full_uri: str) -> tuple[dict | None, str]:
        """
        Returns (metadata_or_None, source_label).
        Tries local index first (with URI variant fallback), then HTTP by namespace.
        """
        for uri in self._uri_variants(full_uri):
            if uri in self._local_subjects:
                meta = self._get_meta(self._local_graph, URIRef(uri))
                if meta:
                    return meta, "local"

        ns = self._namespace_of(full_uri)
        g = self._load_http(ns)
        if g is not None:
            meta = self._get_meta(g, URIRef(full_uri))
            return meta, self._http_source.get(ns, "http")

        return None, "not-found"

    @staticmethod
    def _lang_sort(lit) -> int:
        lang = getattr(lit, "language", None) or ""
        return 0 if lang.startswith("en") else (1 if lang == "" else 2)

    def _pick(self, g: Graph, uri: URIRef, preds: list) -> str | None:
        for pred in preds:
            vals = list(g.objects(uri, pred))
            if vals:
                vals.sort(key=self._lang_sort)
                return str(vals[0])
        return None

    def _get_meta(self, g: Graph, uri: URIRef) -> dict | None:
        types = [str(o) for o in g.objects(uri, RDF.type)]
        label = self._pick(g, uri, LABEL_PREDICATES)
        desc  = self._pick(g, uri, DESCRIPTION_PREDICATES)
        if not types and label is None and desc is None:
            return None
        meta: dict = {}
        if types: meta["types"]       = types
        if label: meta["label"]       = label
        if desc:  meta["description"] = desc
        return meta


# ── URI extraction from SHACL via rdflib ──────────────────────────────────

def extract_uris(
    shacl_text: str,
    prefix_map: dict[str, str],
    skip_prefixes: set[str],
    skip_namespaces: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """
    Parse the SHACL Turtle with rdflib so all prefixed names are expanded to
    full URIs before classification — no manual string concatenation needed.

    URIs whose local name ends with 'shape' (case-insensitive) are excluded
    from both lists — they are SHACL shape identifiers, not ontology terms.

    URIs starting with any namespace in skip_namespaces are also excluded
    from both lists (e.g. the shape graph namespace itself).

    Returns:
      domain_uris : URIs from non-skip-prefix namespaces
                    + sh:path / sh:targetClass values
      value_uris  : URIs from sh:hasValue / sh:class / sh:in values
    Both lists are deduplicated and sorted.
    """
    try:
        g = Graph()
        g.parse(data=shacl_text, format="turtle")
    except Exception as _parse_err:
        return [], [f"__PARSE_ERROR__:{_parse_err}"]

    # namespace URI strings to skip (built from prefix_map)
    skip_ns: set[str] = set()
    for k, v in prefix_map.items():
        if k in skip_prefixes:
            if isinstance(v, list):
                skip_ns.update(v)
            else:
                skip_ns.add(v)

    def is_http_uri(node) -> bool:
        return isinstance(node, URIRef) and str(node).startswith("http")

    def in_skip_ns(uri: str) -> bool:
        return any(uri.startswith(ns) for ns in skip_ns)

    def in_extra_ns(uri: str) -> bool:
        return bool(skip_namespaces) and any(uri.startswith(ns) for ns in skip_namespaces)

    def should_exclude(uri: str) -> bool:
        """True if this URI should be excluded from both domain and value sets."""
        return in_skip_ns(uri) or is_shape_uri(uri) or in_extra_ns(uri)

    domain: set[str] = set()
    value:  set[str] = set()

    for subj, pred, obj in g:
        pred_str = str(pred)

        # sh:path / sh:targetClass → domain
        if pred in SH_DOMAIN_PREDS:
            if is_http_uri(obj) and not should_exclude(str(obj)):
                domain.add(str(obj))

        # sh:hasValue / sh:class → value
        elif pred in (URIRef(SH + "hasValue"), URIRef(SH + "class")):
            obj_str = str(obj)
            if is_http_uri(obj) and not should_exclude(obj_str):
                value.add(obj_str)
            elif isinstance(obj, Literal) and obj_str.startswith("http") and not should_exclude(obj_str):
                value.add(obj_str)

        # sh:in → expand RDF list, each item → value
        elif pred == URIRef(SH + "in"):
            if is_http_uri(obj) or isinstance(obj, BNode):
                try:
                    for item in Collection(g, obj):
                        item_str = str(item)
                        if is_http_uri(item) and not should_exclude(item_str):
                            value.add(item_str)
                        elif isinstance(item, Literal) and item_str.startswith("http") and not should_exclude(item_str):
                            value.add(item_str)
                except Exception:
                    pass

        # all other non-sh predicates: objects that are URIs → domain
        elif not pred_str.startswith(SH):
            if is_http_uri(obj) and not should_exclude(str(obj)):
                domain.add(str(obj))

    # subjects that belong to non-skip namespaces → domain
    for subj in g.subjects():
        if is_http_uri(subj):
            s = str(subj)
            if not should_exclude(s) and s not in domain and s not in value:
                domain.add(s)

    # ── SPARQL constraint strings (sh:select / sh:ask) ──────────────────────
    SH_SELECT = URIRef(SH + "select")
    SH_ASK    = URIRef(SH + "ask")
    SH_PREFIXES   = URIRef(SH + "prefixes")
    SH_DECLARE    = URIRef(SH + "declare")
    SH_PREFIX_LIT = URIRef(SH + "prefix")
    SH_NAMESPACE  = URIRef(SH + "namespace")

    def _collect_sparql_prefixes(constraint_node) -> dict[str, str]:
        sparql_pfx: dict[str, str] = {}
        for pfx_col in g.objects(constraint_node, SH_PREFIXES):
            for decl in g.objects(pfx_col, SH_DECLARE):
                pfx = str(g.value(decl, SH_PREFIX_LIT) or "")
                ns  = str(g.value(decl, SH_NAMESPACE) or "")
                if pfx and ns:
                    sparql_pfx[pfx] = ns
        return sparql_pfx

    def _extract_sparql_uris(sparql_str: str, sparql_pfx: dict[str, str]) -> list[str]:
        import re
        found = []
        for m in re.finditer(r"\b([A-Za-z][\w-]*):([A-Za-z_][\w.-]*)", sparql_str):
            prefix, local = m.group(1), m.group(2)
            if prefix in ("SELECT","WHERE","FILTER","GROUP","HAVING",
                          "AS","BY","FROM","OPTIONAL","UNION","BIND",
                          "VALUES","MINUS","NOT","EXISTS","IN","ASC","DESC"):
                continue
            ns = sparql_pfx.get(prefix)
            if ns:
                uri = ns + local
                if uri.startswith("http") and not should_exclude(uri):
                    found.append(uri)
        return found

    for constraint in g.subjects(RDF.type, URIRef(SH + "SPARQLConstraint")):
        sparql_pfx = _collect_sparql_prefixes(constraint)
        for pred_s in (SH_SELECT, SH_ASK):
            for sparql_lit in g.objects(constraint, pred_s):
                uris = _extract_sparql_uris(str(sparql_lit), sparql_pfx)
                for uri in uris:
                    if uri not in value:
                        domain.add(uri)

    # remove any value URIs that ended up in domain too (value takes priority)
    domain -= value

    return sorted(domain), sorted(value)


# ── main ───────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="SHACL Part 2 — ontology augmentation")
    ap.add_argument("jsonl", help="Input JSONL file")
    ap.add_argument("--prefixes", default=None,
                    help="prefixes.json from Part 1 (default: <stem>_prefixes.json)")
    ap.add_argument("--ontology-dir", default=None,
                    help="Directory of ontology files (.ttl/.rdf/.xml/.owl/.jsonld) "
                         "(default: ./ontology/)")
    ap.add_argument("--skip-namespaces", nargs="+", default=[],
                    metavar="NS",
                    help="One or more namespace URI prefixes to skip entirely "
                         "(e.g. http://data.europa.eu/a4g/data-shape#). "
                         "URIs starting with any of these are excluded from "
                         "both domain and value terms.")
    args = ap.parse_args()

    src = Path(args.jsonl)
    if not src.exists():
        sys.exit(f"Not found: {src}")

    prefix_file = (Path(args.prefixes) if args.prefixes
                   else src.with_name(src.stem + "_prefixes.json"))
    if not prefix_file.exists():
        sys.exit(f"Prefixes file not found: {prefix_file}")

    ont_dir = Path(args.ontology_dir) if args.ontology_dir else Path("ontology")

    out_jsonl = src.with_name(src.stem + "_augmented.jsonl")
    out_log   = src.with_name(src.stem + "_lookup_log.txt")

    prefix_map: dict[str, str] = json.loads(prefix_file.read_text(encoding="utf-8"))
    index = OntologyIndex(ont_dir if ont_dir.is_dir() else None)
    skip_namespaces: list[str] = args.skip_namespaces
    if skip_namespaces:
        print(f"Skipping namespaces: {skip_namespaces}")

    # ── global accumulators ────────────────────────────────────────────────
    total_entries = 0

    d_found_uris:     set[str] = set()
    d_not_found_uris: set[str] = set()
    d_all_found   = 0
    d_some_missing = 0
    d_none_found   = 0
    d_missing_by_entry: list[str] = []

    v_found_uris:   set[str] = set()
    v_no_meta_uris: set[str] = set()
    v_no_meta_by_entry: list[str] = []

    log_lines:   list[str] = []
    out_records: list[str] = []

    def log(msg: str = "") -> None:
        log_lines.append(msg)

    log("SHACL Quality Check — Part 2: Ontology Metadata Lookup")
    log(f"Source        : {src}")
    log(f"Prefixes      : {prefix_file}")
    log(f"Ontology dir  : {ont_dir}")
    log(f"Run at        : {datetime.now().isoformat(timespec='seconds')}")
    log("=" * 70)

    with src.open(encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            total_entries += 1

            try:
                record = json.loads(raw)
            except json.JSONDecodeError as e:
                log(f"\n[Line {lineno}] JSON parse error: {e}")
                out_records.append(raw)
                continue

            entry_id  = record.get("id", f"<line {lineno}>")
            shacl_txt = record.get("shacl", "")

            log(f"\n{'─'*70}")
            log(f"Entry : {entry_id}  (line {lineno})")

            domain_uris, value_uris = extract_uris(shacl_txt, prefix_map, SKIP_PREFIXES, skip_namespaces)

            # check for parse error sentinel
            parse_error = None
            if value_uris and value_uris[0].startswith("__PARSE_ERROR__:"):
                parse_error = value_uris[0][len("__PARSE_ERROR__:"):]
                domain_uris, value_uris = [], []
                log(f"  [PARSE ERROR] Could not parse SHACL Turtle — skipping URI extraction.")
                log(f"    Reason: {parse_error}")

            snippet:     dict[str, dict] = {}
            d_found:     list[str] = []
            d_not_found: list[str] = []
            v_found:     list[str] = []
            v_no_meta:   list[str] = []

            # ── domain terms ───────────────────────────────────────────────
            if domain_uris:
                log(f"  [domain terms] ({len(domain_uris)} URIs)")
                for uri in domain_uris:
                    meta, source = index.lookup(uri)
                    if meta:
                        snippet[uri] = {"source": source, **meta}
                        d_found.append(uri)
                        d_found_uris.add(uri)
                        log(f"    FOUND      [{source:30s}]  {uri}")
                    else:
                        d_not_found.append(uri)
                        d_not_found_uris.add(uri)
                        log(f"    NOT FOUND  [{source:30s}]  {uri}")
            else:
                log("  [domain terms] none")

            # ── shacl value terms ──────────────────────────────────────────
            if value_uris:
                log(f"  [shacl value terms] ({len(value_uris)} URIs)")
                for uri in value_uris:
                    meta, source = index.lookup(uri)
                    if meta:
                        snippet[uri] = {"source": source, **meta}
                        v_found.append(uri)
                        v_found_uris.add(uri)
                        log(f"    FOUND      [{source:30s}]  {uri}")
                    else:
                        snippet[uri] = {"uri_only": True}
                        v_no_meta.append(uri)
                        v_no_meta_uris.add(uri)
                        log(f"    NO METADATA[{'uri_only':30s}]  {uri}")
            else:
                log("  [shacl value terms] none")

            log(f"  Summary — domain: {len(d_found)} found, {len(d_not_found)} not found"
                f"  |  value: {len(v_found)} found, {len(v_no_meta)} no metadata")

            if d_not_found:
                d_missing_by_entry.append(f"    Entry : {entry_id}  (line {lineno})")
                for u in d_not_found:
                    d_missing_by_entry.append(f"      • {u}")

            if v_no_meta:
                v_no_meta_by_entry.append(f"    Entry : {entry_id}  (line {lineno})")
                for u in v_no_meta:
                    v_no_meta_by_entry.append(f"      • {u}")

            if not d_not_found:
                d_all_found += 1
            elif not d_found:
                d_none_found += 1
            else:
                d_some_missing += 1

            record["ontology_snippet"] = snippet
            out_records.append(json.dumps(record, ensure_ascii=False))

    # ── global summary ─────────────────────────────────────────────────────
    log(f"\n{'='*70}")
    log("GLOBAL SUMMARY")
    log(f"  Total entries processed : {total_entries}")

    log(f"\n  [domain terms]")
    log(f"    Unique URIs found        : {len(d_found_uris)}")
    log(f"    Unique URIs not found    : {len(d_not_found_uris)}")
    log(f"    Entries — all found      : {d_all_found}")
    log(f"    Entries — some missing   : {d_some_missing}")
    log(f"    Entries — none found     : {d_none_found}")

    if d_missing_by_entry:
        log(f"\n    Entries with missing domain URIs:")
        for line in d_missing_by_entry:
            log(line)

    if d_not_found_uris:
        log(f"\n    All missing domain URIs ({len(d_not_found_uris)}):")
        for u in sorted(d_not_found_uris):
            log(f"      • {u}")

    log(f"\n  [shacl value terms]")
    log(f"    Unique URIs found            : {len(v_found_uris)}")
    log(f"    Unique URIs without metadata : {len(v_no_meta_uris)}")

    if v_no_meta_by_entry:
        log(f"\n    Entries with value URIs lacking metadata:")
        for line in v_no_meta_by_entry:
            log(line)

    if v_no_meta_uris:
        log(f"\n    All value URIs without metadata ({len(v_no_meta_uris)}):")
        for u in sorted(v_no_meta_uris):
            log(f"      • {u}")

    # ── write outputs ──────────────────────────────────────────────────────
    out_log.write_text("\n".join(log_lines), encoding="utf-8")
    out_jsonl.write_text("\n".join(out_records) + "\n", encoding="utf-8")

    print(f"Augmented JSONL → {out_jsonl}")
    print(f"Lookup log      → {out_log}")
    print(f"\nDone. {total_entries} entries | "
          f"domain: {len(d_found_uris)} found / {len(d_not_found_uris)} missing | "
          f"value: {len(v_found_uris)} found / {len(v_no_meta_uris)} no metadata")


if __name__ == "__main__":
    main()