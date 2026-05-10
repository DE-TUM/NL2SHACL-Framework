import rdflib
from rdflib import SH, RDF, RDFS, BNode, Literal
from rdflib.compare import isomorphic, graph_diff, to_canonical_graph
from typing import Dict, Any, Tuple
from pyshacl import validate
from pyshacl.errors import ShapeLoadError, ValidationFailure, ReportableRuntimeError
from SHACL_VOCAB import VALID_SHACL_PARAMETERS 


print("http://www.w3.org/ns/shacl#zeroOrOnePath" in VALID_SHACL_PARAMETERS)
print(len(VALID_SHACL_PARAMETERS))  

PROPERTIES_TO_IGNORE = { SH.message, SH.name, SH.description, SH.order, SH.group, SH.severity }
LIST_PROPERTIES_TO_NORMALIZE = { SH["in"], SH['and'], SH['or'], SH.xone, SH.ignoredProperties }


def _is_false_positive_meta_violation(report_graph: rdflib.Graph) -> bool:
    """
    Returns True if every violation in the meta-validation report is a known
    false positive caused by sh:targetNode pointing at an external vocabulary
    class (e.g. foaf:Agent, dcat:Catalog).

    The pattern: pyshacl validates the SHACL file itself as data, so any class
    referenced via sh:targetNode has no instances in that graph. This triggers
    a spurious MinCount violation on [ sh:inversePath rdf:type ]. The same
    error appears on the reference (ground-truth) SHACL, so it cannot reflect
    a real problem with the generated shape.
    """
    SH_result          = rdflib.URIRef("http://www.w3.org/ns/shacl#result")
    SH_resultPath      = rdflib.URIRef("http://www.w3.org/ns/shacl#resultPath")
    SH_sourceShape     = rdflib.URIRef("http://www.w3.org/ns/shacl#sourceShape")
    SH_inversePath     = rdflib.URIRef("http://www.w3.org/ns/shacl#inversePath")
    SH_minCount        = rdflib.URIRef("http://www.w3.org/ns/shacl#minCount")

    results = list(report_graph.objects(None, SH_result))
    if not results:
        return False  # no violations at all — should not reach here

    for result in results:
        result_path = report_graph.value(result, SH_resultPath)
        if result_path is None:
            return False  # unknown structure, do not suppress

        # result_path should be a blank node representing [ sh:inversePath rdf:type ]
        if not isinstance(result_path, BNode):
            return False

        inverse_target = report_graph.value(result_path, SH_inversePath)
        if inverse_target != RDF.type:
            return False  # different path, not the known false-positive pattern

        # Additionally confirm the source shape has sh:minCount (not some other constraint)
        source_shape = report_graph.value(result, SH_sourceShape)
        if source_shape is not None:
            min_count = report_graph.value(source_shape, SH_minCount)
            if min_count is None:
                return False

    return True


def validate_llm_shacl(shacl_content: str) -> Tuple[bool, Dict[str, Any] | None]:
    """Validates SHACL code strictly to ensure it runs correctly."""
    g = rdflib.Graph()
    try:
        g.parse(data=shacl_content, format="turtle")
    except Exception as e:
        return False, {"status": "failure", "failure_stage": 1, "failure_type": "SyntaxError", "details": str(e)}

    try:
        conforms, report_graph, results_text = validate(data_graph=g, shacl_shacl_path=True, do_owl_imports=False, debug=False)
        if not conforms:
            if _is_false_positive_meta_violation(report_graph):
                # All violations match the known spurious pattern: sh:targetNode
                # referencing an external vocabulary class (e.g. foaf:Agent,
                # dcat:Catalog) triggers a MinCount violation on
                # [ sh:inversePath rdf:type ] because the SHACL file itself
                # contains no instances of that class. The ground-truth SHACL
                # produces the identical error, so this is not a real defect.
                pass
            else:
                return False, {"status": "failure", "failure_stage": 2, "failure_type": "MetaSyntaxError", "details": results_text}
    except ReportableRuntimeError as e:
        return False, {"status": "failure", "failure_stage": 2, "failure_type": "StructuralError", "details": str(e)}
    except (ShapeLoadError, ValidationFailure) as e:
        return False, {"status": "failure", "failure_stage": 2, "failure_type": "MetaSyntaxError", "details": str(e)}

    sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    query = "SELECT DISTINCT ?p WHERE { ?s ?p ?o . filter(strstarts(str(?p), str(sh:))) }"
    for row in g.query(query, initNs={"sh": sh}):
        predicate = str(row.p)
        if predicate not in VALID_SHACL_PARAMETERS and predicate != str(rdflib.RDF.type):
            details = f"Found non-standard SHACL parameter: <{predicate}>"
            return False, {"status": "failure", "failure_stage": 3, "failure_type": "LinterError", "details": details}

    return True, None


def normalize_rdf_lists(graph: rdflib.Graph) -> rdflib.Graph:
    lists_to_rewrite = set()
    for p in LIST_PROPERTIES_TO_NORMALIZE:
        for s, o in graph.subject_objects(predicate=p):
            if isinstance(o, (BNode, rdflib.URIRef)):
                lists_to_rewrite.add(o)
    if not lists_to_rewrite: return graph

    for list_head in lists_to_rewrite:
        if not isinstance(list_head, (BNode, rdflib.URIRef)) or list_head == RDF.nil: continue
        members, old_list_triples = [], set()
        curr, visited = list_head, {list_head}
        while curr and curr != RDF.nil:
            first_val = graph.value(subject=curr, predicate=RDF.first)
            rest_val = graph.value(subject=curr, predicate=RDF.rest)
            if first_val is not None:
                members.append(first_val)
                old_list_triples.add((curr, RDF.first, first_val))
            if rest_val is not None:
                old_list_triples.add((curr, RDF.rest, rest_val))
                curr = rest_val
                if curr in visited: break
                visited.add(curr)
            else:
                break
        for t in old_list_triples: graph.remove(t)
        members.sort(key=lambda x: x.n3())
        if not members:
            for s, p, o in list(graph.triples((None, None, list_head))):
                if p in LIST_PROPERTIES_TO_NORMALIZE:
                    graph.remove((s, p, o))
                    graph.add((s, p, RDF.nil))
            continue
        graph.add((list_head, RDF.first, members[0]))
        current_node = list_head
        for member in members[1:]:
            next_node = BNode()
            graph.add((current_node, RDF.rest, next_node))
            graph.add((next_node, RDF.first, member))
            current_node = next_node
        graph.add((current_node, RDF.rest, RDF.nil))
    return graph

def normalize_literals(graph: rdflib.Graph) -> rdflib.Graph:
    processed_graph = rdflib.Graph()
    regex_canonical_map = {"\\\\d": "[0-9]", "\\\\w": "[a-zA-Z0-9_]", "\\\\s": "[ \\\\t\\\\n\\\\r\\\\f\\\\v]"}
    for s, p, o in graph:
        if p == SH.pattern and isinstance(o, Literal):
            new_pattern = str(o.value)
            for shorthand, canonical in regex_canonical_map.items():
                new_pattern = new_pattern.replace(shorthand, canonical)
            new_literal = Literal(new_pattern, lang=o.language, datatype=o.datatype)
            processed_graph.add((s, p, new_literal))
        elif p == SH['select'] and isinstance(o, Literal):
            query_string = str(o.value)
            canonical_query = " ".join([line.strip() for line in query_string.splitlines() if line.strip()])
            new_literal = Literal(canonical_query)
            processed_graph.add((s, p, new_literal))
        else:
            processed_graph.add((s, p, o))
    return processed_graph

def filter_shacl_metadata(graph: rdflib.Graph) -> rdflib.Graph:
    """Filter metadata properties AND default-value triples that don't affect validation."""
    # First pass: determine which shapes have sh:closed true
    closed_shapes = set()
    for s, p, o in graph.triples((None, SH['closed'], None)):
        if isinstance(o, Literal) and str(o.value).lower() == 'true':
            closed_shapes.add(s)
    
    # Collect BNodes used by sh:ignoredProperties on open shapes (to remove their RDF list triples)
    ignored_list_bnodes = set()
    for s, p, o in graph.triples((None, SH.ignoredProperties, None)):
        if s not in closed_shapes and isinstance(o, BNode):
            # Collect all BNodes in this RDF list
            curr = o
            while curr and curr != RDF.nil and isinstance(curr, BNode):
                ignored_list_bnodes.add(curr)
                curr = graph.value(curr, RDF.rest)
    
    filtered_graph = rdflib.Graph()
    for s, p, o in graph:
        # Skip metadata properties
        if p in PROPERTIES_TO_IGNORE:
            continue
        # Skip sh:closed false (it's the default behavior — omitting it is functionally identical)
        if p == SH['closed'] and isinstance(o, Literal) and str(o.value).lower() == 'false':
            continue
        # Skip sh:ignoredProperties on open shapes (no validation effect)
        if p == SH.ignoredProperties and s not in closed_shapes:
            continue
        # Skip RDF list triples belonging to filtered-out ignoredProperties
        if isinstance(s, BNode) and s in ignored_list_bnodes:
            continue
        filtered_graph.add((s, p, o))
    return filtered_graph

def get_similarity_metrics(gt_graph: rdflib.Graph, llm_graph: rdflib.Graph) -> Dict[str, Any]:
    # Canonicalize blank nodes before diffing to ensure correct matching
    gt_canon = to_canonical_graph(gt_graph)
    llm_canon = to_canonical_graph(llm_graph)
    in_both, in_gt, in_llm = graph_diff(gt_canon, llm_canon)
    format_triple = lambda s, p, o: f"{s.n3()} {p.n3()} {o.n3()} ."
    metrics = {
        "summary": {"common_triples": len(in_both), "gt_only_triples": len(in_gt), "llm_only_triples": len(in_llm)},
        "details": {"missed_triples": sorted([format_triple(s, p, o) for s, p, o in in_gt]),
                    "added_triples": sorted([format_triple(s, p, o) for s, p, o in in_llm])}
    }
    gt_total, llm_total = len(in_both) + len(in_gt), len(in_both) + len(in_llm)
    precision = len(in_both) / llm_total if llm_total > 0 else 0.0
    recall = len(in_both) / gt_total if gt_total > 0 else 0.0
    f1_score = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
    metrics["scores"] = {"precision": precision, "recall": recall, "f1_score": f1_score}
    return metrics

def unify_shape_uris(gt_graph: rdflib.Graph, llm_graph: rdflib.Graph) -> Tuple[rdflib.Graph, rdflib.Graph]:
    gt_shapes = list(gt_graph.subjects(RDF.type, SH.NodeShape))
    llm_shapes = list(llm_graph.subjects(RDF.type, SH.NodeShape))
    
    if len(gt_shapes) == 1 and len(llm_shapes) == 1:
        gt_shape = gt_shapes[0]
        llm_shape = llm_shapes[0]
        
        if gt_shape != llm_shape:
            new_llm_graph = rdflib.Graph()
            for s, p, o in llm_graph:
                new_s = gt_shape if s == llm_shape else s
                new_o = gt_shape if o == llm_shape else o
                new_llm_graph.add((new_s, p, new_o))
            return gt_graph, new_llm_graph
            
    return gt_graph, llm_graph

def evaluate_syntactic_equivalence(gt_shacl: str, llm_shacl: str, normalize: bool = False) -> Dict[str, Any]:
    """Evaluates syntactic equivalence by parsing, normalizing, and comparing triples directly in Python."""
    standard_prefixes = "@prefix sh: <http://www.w3.org/ns/shacl#> .\n@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n@prefix ex: <http://example.org/ns#> .\n@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n@prefix chemrof: <https://w3id.org/chemrof/> .\n@prefix owl: <http://www.w3.org/2002/07/owl#> .\n@prefix CHEMINF: <http://semanticscience.org/resource/CHEMINF_> .\n@prefix gc: <http://purl.org/gc/> .\n@prefix bo: <http://www.blueobelisk.org/dict/terminology> .\n@prefix CHEBI: <http://purl.obolibrary.org/obo/CHEBI_> .\n@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
    full_gt = f"{standard_prefixes}\n{gt_shacl}"
    full_llm = f"{standard_prefixes}\n{llm_shacl}"

    results = {"options": {"list_normalization": normalize}}

    try:
        gt_graph = rdflib.Graph().parse(data=full_gt, format="turtle")
        llm_graph = rdflib.Graph().parse(data=full_llm, format="turtle")
    except Exception as e:
        return {"status": "failure", "failure_stage": 4, "failure_type": "ParseErrorDuringEval", "details": str(e)}

    gt_graph, llm_graph = unify_shape_uris(gt_graph, llm_graph)

    # Filter metadata
    gt_processed = filter_shacl_metadata(gt_graph)
    llm_processed = filter_shacl_metadata(llm_graph)

    # Normalization
    if normalize:
        gt_processed = normalize_rdf_lists(gt_processed)
        llm_processed = normalize_rdf_lists(llm_processed)

    gt_processed = normalize_literals(gt_processed)
    llm_processed = normalize_literals(llm_processed)

    results["isomorphic"] = isomorphic(gt_processed, llm_processed)
    results["similarity"] = get_similarity_metrics(gt_processed, llm_processed)
    results["status"] = "success"

    return results


import sys
from pathlib import Path
import tempfile
from typing import Set, Tuple
from textwrap import dedent

from rdflib import Graph
from rdflib.namespace import SH
from pyshacl import validate

try:
    from rdf_graph_gen.rdf_graph_generator import generate_rdf
except ImportError:
    print("ERROR: The 'rdf-graph-gen' library is not installed or accessible.", file=sys.stderr)
    sys.exit(1)


def generate_graph_from_library(shape_file: str, output_graph_file: str,  scale: int, batch_size: int) -> bool:
    try:
        generate_rdf(shape_file, output_graph_file, scale, batch_size)
        return True
    except Exception as e:
        print(f"    - SEMANTIC WARNING: Graph generation failed for '{Path(shape_file).name}'. Details: {e}", file=sys.stderr)
        return False

def extract_violations(validation_report_graph: Graph) -> Set[Tuple]:
    violations = set()
    for report in validation_report_graph.subjects(predicate=SH.focusNode):
        focus_node = validation_report_graph.value(report, SH.focusNode)
        result_path = validation_report_graph.value(report, SH.resultPath)
        constraint = validation_report_graph.value(report, SH.sourceConstraintComponent)
        if focus_node and result_path and constraint:
            violations.add((str(focus_node), str(result_path), str(constraint)))
    return violations



from textwrap import dedent
import tempfile
import sys
from pathlib import Path
from rdflib import Graph, RDF, URIRef
from pyshacl import validate

 
 
def evaluate_semantic_graph_equivalence(
    ground_truth_shacl: str,
    llm_generated_shacl: str,
    record_id: str = "unknown",
    subset: str = "unknown",
    model: str = "unknown",
    output_dir: str = "evaluation-output",
    scale: int = 10,
    batch_size: int = 100,
) -> dict:
    """
    Evaluate semantic equivalence by:
      1. Generating ONE data graph from the ground-truth SHACL only.
         Calls MultiprocessGenerator directly so any exception is caught
         and reported clearly (the library is completely silent on failure).
      2. Validating that data graph against BOTH the GT and LLM SHACL shapes.
      3. Comparing the sets of violation focus nodes.
         equivalent = True  iff  the two sets are identical.
      4. Saving a detailed debug file under:
         {output_dir}/semantic-validation-process/{subset}/{model}/{record_id}.txt
    """
 
    standard_prefixes = dedent("""
        @prefix sh: <http://www.w3.org/ns/shacl#> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
        @prefix ex: <http://example.org/ns#> .
        @prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
    """).strip()
 
    # ------------------------------------------------------------------
    # Prepare debug output directory
    # ------------------------------------------------------------------
    debug_dir = Path(output_dir) / "semantic-validation-process" / subset / model
    debug_dir.mkdir(parents=True, exist_ok=True)
    debug_file = debug_dir / f"{record_id}.txt"
 
    debug_lines = []
 
    def log(text: str = ""):
        debug_lines.append(text)
 
    def flush_debug():
        try:
            debug_file.write_text("\n".join(debug_lines), encoding="utf-8")
        except Exception as e:
            print(f"  - WARNING: Could not write debug file {debug_file}: {e}", file=sys.stderr)
 
    log(f"RECORD: {record_id}")
    log(f"Subset: {subset}  |  Model: {model}")
    log("=" * 70)
 
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        gt_shacl_file  = temp / "gt_shacl.ttl"
        llm_shacl_file = temp / "llm_shacl.ttl"
        gt_graph_file  = temp / "gt_graph.ttl"
 
        full_gt_shacl  = f"{standard_prefixes}\n\n{ground_truth_shacl}"
        full_llm_shacl = f"{standard_prefixes}\n\n{llm_generated_shacl}"
 
        gt_shacl_file.write_text(full_gt_shacl,  encoding="utf-8")
        llm_shacl_file.write_text(full_llm_shacl, encoding="utf-8")
 
        log("\n--- GROUND TRUTH SHACL ---")
        log(full_gt_shacl)
        log("\n--- LLM-GENERATED SHACL ---")
        log(full_llm_shacl)
 
        # ------------------------------------------------------------------
        # Step 1: Generate data graph from GT SHACL
        #
        # The rdf-graph-gen library throws exceptions completely silently
        # (zero stdout/stderr output). We call MultiprocessGenerator directly
        # so we can catch and surface the error message ourselves.
        # ------------------------------------------------------------------
        gt_graph_generated = False
        graph_gen_error = None
 
        try:
            generate_rdf(str(gt_shacl_file), str(gt_graph_file), scale, batch_size)
            gt_graph_generated = gt_graph_file.exists() and gt_graph_file.stat().st_size > 0
            if not gt_graph_generated:
                graph_gen_error = "generate_rdf ran but produced no output file."
        except Exception as e:
            graph_gen_error = f"{type(e).__name__}: {e}"
 
        if not gt_graph_generated:
            msg = f"Data graph generation from GT SHACL failed: {graph_gen_error}"
            print(f"  - SEMANTIC: [{record_id}] {msg}", file=sys.stderr)
            log(f"\n[ERROR] {msg}")
            flush_debug()
            return {
                "status": "failure",
                "details": msg,
            }
 
        # ------------------------------------------------------------------
        # Read the generated data graph
        # ------------------------------------------------------------------
        try:
            data_graph = Graph().parse(str(gt_graph_file), format="turtle")
        except Exception as e:
            msg = f"Could not parse generated data graph: {type(e).__name__}: {e}"
            print(f"  - SEMANTIC: [{record_id}] {msg}", file=sys.stderr)
            log(f"\n[ERROR] {msg}")
            flush_debug()
            return {"status": "failure", "details": msg}
 
        gt_node_count   = len(set(data_graph.subjects(RDF.type, None)))
        gt_data_triples = len(data_graph)
 
        log(f"\n--- GENERATED DATA GRAPH (from GT SHACL) ---")
        log(f"Triples: {gt_data_triples}  |  Typed nodes: {gt_node_count}")
        log(data_graph.serialize(format="turtle"))
 
        # ------------------------------------------------------------------
        # Step 2a: Validate data graph against GT SHACL
        # ------------------------------------------------------------------
        gt_focus_nodes = set()
        gt_report_text = ""
        try:
            _, report_graph_gt, gt_report_text = validate(
                data_graph, shacl_graph=str(gt_shacl_file)
            )
            gt_focus_nodes, _ = _extract_focus_nodes(report_graph_gt)
        except Exception as e:
            msg = f"GT validation failed: {type(e).__name__}: {e}"
            print(f"  - SEMANTIC WARNING: [{record_id}] {msg}", file=sys.stderr)
            log(f"\n[ERROR] {msg}")
 
        # ------------------------------------------------------------------
        # Step 2b: Validate data graph against LLM SHACL
        # ------------------------------------------------------------------
        llm_focus_nodes = set()
        llm_report_text = ""
        try:
            _, report_graph_llm, llm_report_text = validate(
                data_graph, shacl_graph=str(llm_shacl_file)
            )
            llm_focus_nodes, _ = _extract_focus_nodes(report_graph_llm)
        except Exception as e:
            msg = f"LLM validation failed: {type(e).__name__}: {e}"
            print(f"  - SEMANTIC WARNING: [{record_id}] {msg}", file=sys.stderr)
            log(f"\n[ERROR] {msg}")
 
        # ------------------------------------------------------------------
        # Step 3: Compare focus node sets
        # ------------------------------------------------------------------
        equivalent  = gt_focus_nodes == llm_focus_nodes
        only_in_gt  = sorted(gt_focus_nodes  - llm_focus_nodes)
        only_in_llm = sorted(llm_focus_nodes - gt_focus_nodes)
        in_both     = sorted(gt_focus_nodes  & llm_focus_nodes)
 
        # ------------------------------------------------------------------
        # Step 4: Write debug file
        # ------------------------------------------------------------------
        log("\n" + "=" * 70)
        log("VALIDATION RESULTS")
        log("=" * 70)
        log(f"\nData graph: {gt_data_triples} triples, {gt_node_count} typed nodes")
        log(f"Equivalent (focus nodes match): {equivalent}")
 
        log(f"\n--- Violation focus nodes against GT SHACL ({len(gt_focus_nodes)}) ---")
        for node in sorted(gt_focus_nodes):
            log(f"  {node}")
 
        log(f"\n--- Violation focus nodes against LLM SHACL ({len(llm_focus_nodes)}) ---")
        for node in sorted(llm_focus_nodes):
            log(f"  {node}")
 
        log(f"\n--- Focus nodes in BOTH ({len(in_both)}) ---")
        for node in in_both:
            log(f"  {node}")
 
        log(f"\n--- Focus nodes ONLY in GT ({len(only_in_gt)}) ---")
        for node in only_in_gt:
            log(f"  {node}")
 
        log(f"\n--- Focus nodes ONLY in LLM ({len(only_in_llm)}) ---")
        for node in only_in_llm:
            log(f"  {node}")
 
        log("\n--- Full GT SHACL validation report ---")
        log(gt_report_text or "(no report)")
 
        log("\n--- Full LLM SHACL validation report ---")
        log(llm_report_text or "(no report)")
 
        flush_debug()
 
        # ------------------------------------------------------------------
        # Step 5: Return result dict
        # ------------------------------------------------------------------
        return {
            "status": "success",
            "equivalent": equivalent,
            "gt_violation_focus_node_count":  len(gt_focus_nodes),
            "llm_violation_focus_node_count": len(llm_focus_nodes),
            "focus_nodes_only_in_gt":  only_in_gt,
            "focus_nodes_only_in_llm": only_in_llm,
            "gt_data_triples": gt_data_triples,
            "gt_node_count":   gt_node_count,
            "debug_file": str(debug_file),
        }
 
 
# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
 
def _extract_focus_nodes(report_graph: Graph):
    """
    Extract violation focus nodes from a pyshacl report graph.
 
    Returns:
        focus_nodes (set of str)
        details     (list of dicts with keys: focus_node, result_message, source_shape)
    """
    SH_result      = URIRef("http://www.w3.org/ns/shacl#result")
    SH_focusNode   = URIRef("http://www.w3.org/ns/shacl#focusNode")
    SH_resultMsg   = URIRef("http://www.w3.org/ns/shacl#resultMessage")
    SH_sourceShape = URIRef("http://www.w3.org/ns/shacl#sourceShape")
 
    focus_nodes = set()
    details = []
 
    for result in report_graph.objects(None, SH_result):
        focus = report_graph.value(result, SH_focusNode)
        msg   = report_graph.value(result, SH_resultMsg)
        shape = report_graph.value(result, SH_sourceShape)
 
        if focus is not None:
            focus_str = str(focus)
            focus_nodes.add(focus_str)
            details.append({
                "focus_node":     focus_str,
                "result_message": str(msg)   if msg   else "",
                "source_shape":   str(shape) if shape else "",
            })
 
    return focus_nodes, details


import os
import json
from openai import OpenAI, APIError


class JudgeLLM:
    def __init__(self, api_key: str = None, judge_model: str = "gpt-4o", config_path: str = "config.json"):
        """Initializes the OpenAI client for judging."""
        if api_key:
            self.api_key = api_key
        else:
            try:
                with open(config_path, encoding="utf-8") as f:
                    config = json.load(f)
                self.api_key = config.get("openAI_api_key")
            except FileNotFoundError:
                self.api_key = None

        if not self.api_key:
            raise ValueError(f"OpenAI API key not found. Set 'openAI_api_key' in '{config_path}'.")

        self.judge_model = judge_model
        self.client = OpenAI(
            api_key=self.api_key,
        )

    def get_semantic_equivalence_verdict(self, shape1: str, shape2: str, nl_description: str) -> str:
        prompt = f"""
You are an expert in SHACL (Shapes Constraint Language). Your task is to determine if two SHACL shapes are **semantically equivalent**.
Semantic equivalence means that both shapes will validate the exact same set of data graphs. They must enforce the same constraints, even if their syntax is slightly different (e.g., different prefix names, ordering of properties).
**Natural Language Requirement:** "{nl_description}"

**SHACL Shape 1 (Ground Truth):**
```turtle
{shape1}
```

**SHACL Shape 2 (LLM Generated):**
```turtle
{shape2}
```

**Analysis Steps:**
1.  Parse both shapes to understand their constraints.
2.  Identify any differences in constraints (e.g., `sh:minCount`, `sh:maxInclusive`, `sh:pattern`).
3.  Evaluate if these differences change validation behavior. A descriptive property like `sh:name` is acceptable, but a new constraint like `sh:minInclusive 0` makes the shapes non-equivalent.

**Respond with only a single word:** `equivalent` or `not_equivalent`. Do not provide any explanation.
"""

        try:
            response = self.client.chat.completions.create(
                model=self.judge_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0
            )
            verdict = response.choices[0].message.content.strip().lower()

            if verdict in ["equivalent", "not_equivalent"]:
                return verdict
            else:
                print(f"    - [Judge] Warning: LLM returned unexpected verdict: '{verdict}'. Defaulting to 'not_equivalent'.")
                return "not_equivalent"

        except APIError as e:
            print(f"    - [Judge] API ERROR: {e.message}")
            return "api_error"
        except Exception as e:
            print(f"    - [Judge] ERROR: {e}")
            return "error"