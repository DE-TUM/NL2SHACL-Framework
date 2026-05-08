"""
SHACL Quality Check — Part 1
Checks:
  (i)   Syntactic validity   — parse each SHACL string into an RDF graph
  (ii)  Structural completeness — verify required predicates are present
  (iv)  Prefix extraction    — collect all unique prefixes across all entries

Target detection understands three valid SHACL patterns:
  • Explicit target  : sh:targetClass / sh:targetNode / sh:targetSubjectsOf / sh:targetObjectsOf
  • Implicit target  : a named URI is itself typed as sh:NodeShape (e.g. dcat:Catalog a sh:NodeShape)
  • Referenced shape : no target at all — shape is meant to be used via sh:node by other shapes
                       → logged as WARN, not FAIL

Outputs:
  - <stem>_check_log.txt    : per-entry log + summary statistics
  - <stem>_prefixes.json    : all unique prefixes with their namespace URIs

Usage: python shacl_quality_check.py <path/to/data.jsonl>
"""

import json
import sys
import re
from pathlib import Path
from datetime import datetime
from collections import defaultdict

try:
    from rdflib import Graph, URIRef, BNode
    from rdflib.exceptions import ParserError
except ImportError:
    sys.exit("rdflib is required: pip install rdflib")

# ── SHACL / RDF namespaces we care about ───────────────────────────────────
SH  = "http://www.w3.org/ns/shacl#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"

EXPLICIT_TARGET_PREDICATES = {
    f"{SH}targetClass",
    f"{SH}targetNode",
    f"{SH}targetSubjectsOf",
    f"{SH}targetObjectsOf",
}

# ── helpers ────────────────────────────────────────────────────────────────

def parse_prefixes(shacl_text: str) -> dict[str, str]:
    """Extract @prefix declarations from a Turtle string."""
    prefixes = {}
    for m in re.finditer(
        r"@prefix\s+([\w\-]*)\s*:\s*<([^>]+)>", shacl_text, re.IGNORECASE
    ):
        prefixes[m.group(1)] = m.group(2)
    return prefixes


def check_syntactic(shacl_text: str) -> tuple[bool, str, Graph | None]:
    """Try to parse the Turtle string. Returns (ok, message, graph_or_None)."""
    g = Graph()
    try:
        g.parse(data=shacl_text, format="turtle")
        return True, "OK", g
    except Exception as exc:
        return False, str(exc), None


def classify_target(g: Graph) -> str:
    """
    Determine how (or whether) the shape declares its target.

    Returns one of:
      "explicit"   — has sh:targetClass / sh:targetNode / sh:targetSubjectsOf / sh:targetObjectsOf
      "implicit"   — a named URI (not a blank node) is itself typed as sh:NodeShape
      "referenced" — no target at all; intended to be used via sh:node by other shapes
    """
    rdf_type_uri      = f"{RDF}type"
    sh_node_shape_uri = f"{SH}NodeShape"

    all_predicates = {str(p) for _, p, _ in g}

    # 1. Explicit target predicates
    if EXPLICIT_TARGET_PREDICATES & all_predicates:
        return "explicit"

    # 2. Implicit targeting: a named (non-blank) URI is typed as sh:NodeShape
    for s, p, o in g:
        if str(p) == rdf_type_uri and str(o) == sh_node_shape_uri:
            if isinstance(s, URIRef):
                return "implicit"

    # 3. No target found → referenced / unbound shape
    return "referenced"


def check_structural(g: Graph) -> tuple[str, list[str], list[str]]:
    """
    (ii) Structural completeness checks.

    Returns (verdict, issues, warnings) where verdict is "PASS", "WARN", or "FAIL".

    Checks performed:
      (a) Target classification → WARN if referenced (no target), FAIL only for
          missing sh:NodeShape type declaration
      (b) Every inline (BNode) sh:property block must contain sh:path.
          Named URIRef values are references to PropertyShapes defined elsewhere
          and are intentionally skipped.
      (c) At least one resource must be typed as sh:NodeShape
    """
    issues   = []   # → FAIL
    warnings = []   # → WARN

    rdf_type_uri      = f"{RDF}type"
    sh_node_shape_uri = f"{SH}NodeShape"
    sh_property_uri   = f"{SH}property"
    sh_path_uri       = f"{SH}path"

    # (a) target classification
    target_kind = classify_target(g)
    if target_kind == "explicit":
        pass  # all good
    elif target_kind == "implicit":
        pass  # all good — e.g. dcat:Catalog a sh:NodeShape
    else:
        # "referenced" — valid SHACL but worth flagging
        warnings.append(
            "No sh:target* found and no named URI typed as sh:NodeShape — "
            "this shape has no target of its own; it is likely a referenced shape "
            "used via sh:node by other shapes (valid SHACL, but verify intent)"
        )

    # (b) every inline sh:property block must have sh:path.
    # URIRef values are references to named PropertyShapes defined elsewhere —
    # their definition is not present in this snippet so they must not be checked.
    property_nodes = [o for _, p, o in g if str(p) == sh_property_uri]
    for pnode in property_nodes:
        if isinstance(pnode, URIRef):
            continue  # named PropertyShape reference — skip
        props_in_block = {str(p) for _, p, _ in g.triples((pnode, None, None))}
        if sh_path_uri not in props_in_block:
            issues.append(f"sh:property block <{pnode}> is missing sh:path")

    # (c) rdf:type sh:NodeShape must appear somewhere
    types = {str(o) for _, p, o in g if str(p) == rdf_type_uri}
    if sh_node_shape_uri not in types:
        issues.append("No resource typed as sh:NodeShape found")

    if issues:
        verdict = "FAIL"
    elif warnings:
        verdict = "WARN"
    else:
        verdict = "PASS"

    return verdict, issues, warnings


# ── main ───────────────────────────────────────────────────────────────────

def main(jsonl_path: str) -> None:
    src = Path(jsonl_path)
    if not src.exists():
        sys.exit(f"File not found: {jsonl_path}")

    log_path    = src.with_name(src.stem + "_check_log.txt")
    prefix_path = src.with_name(src.stem + "_prefixes.json")

    # counters
    total = 0
    syntax_ok_count   = 0
    syntax_fail_count = 0
    struct_pass_count = 0
    struct_warn_count = 0
    struct_fail_count = 0

    # prefix accumulator:  alias -> set of namespace URIs (catches alias clashes)
    global_prefixes: dict[str, set[str]] = defaultdict(set)

    log_lines: list[str] = []

    def log(msg: str = "") -> None:
        log_lines.append(msg)

    log("SHACL Quality Check — Part 1")
    log(f"Source : {src}")
    log(f"Run at : {datetime.now().isoformat(timespec='seconds')}")
    log("=" * 70)

    with src.open(encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            total += 1

            try:
                record = json.loads(raw)
            except json.JSONDecodeError as e:
                log(f"\n[Line {lineno}] JSON parse error: {e}")
                syntax_fail_count += 1
                struct_fail_count += 1
                continue

            entry_id  = record.get("id", f"<no-id at line {lineno}>")
            shacl_txt = record.get("shacl", "")

            log(f"\n{'─'*70}")
            log(f"Entry : {entry_id}  (line {lineno})")

            # ── (i) syntactic validity ─────────────────────────────────────
            syn_ok, syn_msg, graph = check_syntactic(shacl_txt)
            if syn_ok:
                syntax_ok_count += 1
                log(f"  [i]  Syntax        : PASS  ({len(graph)} triples)")
            else:
                syntax_fail_count += 1
                log(f"  [i]  Syntax        : FAIL")
                log(f"       Error: {syn_msg}")

            # ── (ii) structural completeness ───────────────────────────────
            if graph is not None:
                verdict, str_issues, str_warnings = check_structural(graph)

                if verdict == "PASS":
                    struct_pass_count += 1
                    log(f"  [ii] Structure     : PASS")
                elif verdict == "WARN":
                    struct_warn_count += 1
                    log(f"  [ii] Structure     : WARN  ({len(str_warnings)} warning(s))")
                    for w in str_warnings:
                        log(f"       ⚠ {w}")
                else:  # FAIL
                    struct_fail_count += 1
                    log(f"  [ii] Structure     : FAIL  ({len(str_issues)} issue(s))")
                    for iss in str_issues:
                        log(f"       • {iss}")
                    if str_warnings:
                        for w in str_warnings:
                            log(f"       ⚠ {w}")
            else:
                struct_fail_count += 1
                log(f"  [ii] Structure     : SKIP  (syntax error prevents check)")

            # ── (iv) prefix extraction ─────────────────────────────────────
            local_prefixes = parse_prefixes(shacl_txt)
            for alias, ns in local_prefixes.items():
                global_prefixes[alias].add(ns)
            log(f"  [iv] Prefixes found: {sorted(local_prefixes.keys())}")

    # ── summary ────────────────────────────────────────────────────────────
    log(f"\n{'='*70}")
    log("SUMMARY")
    log(f"  Total entries processed      : {total}")
    log(f"  (i)  Syntax   PASS / FAIL    : {syntax_ok_count} / {syntax_fail_count}")
    log(f"  (ii) Structure PASS / WARN / FAIL : "
        f"{struct_pass_count} / {struct_warn_count} / {struct_fail_count}")
    log(f"  (iv) Unique prefix aliases   : {len(global_prefixes)}")

    # Warn about alias collisions (same prefix alias, different namespaces)
    collisions = {a: ns for a, ns in global_prefixes.items() if len(ns) > 1}
    if collisions:
        log(f"\n  ⚠ Prefix alias collisions ({len(collisions)}):")
        for alias, ns_set in sorted(collisions.items()):
            log(f"    {alias!r:20s} → {ns_set}")
    else:
        log("  ✓ No prefix alias collisions detected")

    # write log
    log_path.write_text("\n".join(log_lines), encoding="utf-8")
    print(f"Log written  → {log_path}")

    # ── (iv) write prefix file ─────────────────────────────────────────────
    prefix_output = {
        alias: sorted(ns_set)[0] if len(ns_set) == 1 else sorted(ns_set)
        for alias, ns_set in sorted(global_prefixes.items())
    }
    prefix_path.write_text(
        json.dumps(prefix_output, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Prefixes     → {prefix_path}")
    print(f"\nDone. {total} entries — syntax {syntax_ok_count}✓/{syntax_fail_count}✗ "
          f"| structure {struct_pass_count}✓/{struct_warn_count}⚠/{struct_fail_count}✗")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python shacl_quality_check.py <path/to/data.jsonl>")
    main(sys.argv[1])