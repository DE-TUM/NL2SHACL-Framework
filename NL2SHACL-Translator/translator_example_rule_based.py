"""
translator_example_rule_based.py

A minimal rule-based NL2SHACL translator provided as a reference implementation.

This translator requires no API access. It uses simple pattern matching to handle
a small subset of constraint types:
  - datatype constraints (e.g. "must be an integer", "must be a string")
  - minCount constraints (e.g. "at least one", "at least 2")
  - maxCount constraints (e.g. "at most one", "at most 3")

It is intended as a runnable reference for the translator I/O contract defined in
TRANSLATOR_SPEC.md, not as a competitive baseline.

Usage:
    python NL2SHACL-Translator/translator_example_rule_based.py \\
        --subset dbpedia-dataset \\
        --dataset-dir dataset

Output:
    your_outputs.jsonl  (id + output_shacl, ready for attach_reference.py)
"""

import argparse
import json
import os
import re
import sys


# ---------------------------------------------------------------------------
# Datatype keyword mapping
# ---------------------------------------------------------------------------

DATATYPE_PATTERNS = [
    (r"\binteger\b",        "xsd:integer"),
    (r"\bint\b",            "xsd:integer"),
    (r"\bfloat\b",          "xsd:float"),
    (r"\bdouble\b",         "xsd:double"),
    (r"\bboolean\b",        "xsd:boolean"),
    (r"\bstring\b",         "xsd:string"),
    (r"\bdate\b",           "xsd:date"),
    (r"\bdatetime\b",       "xsd:dateTime"),
    (r"\burl\b",            "xsd:anyURI"),
    (r"\buri\b",            "xsd:anyURI"),
]

MINCOUNT_PATTERNS = [
    (r"at least (\d+)",     None),   # group 1 = count
    (r"minimum (\d+)",      None),
    (r"must have (\d+)",    None),
    (r"at least one",       "1"),
    (r"required",           "1"),
    (r"must have a\b",      "1"),
    (r"must have an\b",     "1"),
]

MAXCOUNT_PATTERNS = [
    (r"at most (\d+)",      None),   # group 1 = count
    (r"maximum (\d+)",      None),
    (r"exactly one",        "1"),
    (r"at most one",        "1"),
]


# ---------------------------------------------------------------------------
# Pattern matching helpers
# ---------------------------------------------------------------------------

def detect_datatype(description):
    text = description.lower()
    for pattern, xsd_type in DATATYPE_PATTERNS:
        if re.search(pattern, text):
            return xsd_type
    return None


def detect_mincount(description):
    text = description.lower()
    for pattern, fixed_value in MINCOUNT_PATTERNS:
        m = re.search(pattern, text)
        if m:
            if fixed_value is not None:
                return fixed_value
            return m.group(1)
    return None


def detect_maxcount(description):
    text = description.lower()
    for pattern, fixed_value in MAXCOUNT_PATTERNS:
        m = re.search(pattern, text)
        if m:
            if fixed_value is not None:
                return fixed_value
            return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Ontology snippet helpers
# ---------------------------------------------------------------------------

def extract_prefixes(ontology_snippet):
    """Collect namespace prefixes from ontology term URIs."""
    namespaces = {}
    known = {
        "http://www.w3.org/ns/shacl#":                      "sh",
        "http://www.w3.org/2001/XMLSchema#":                 "xsd",
        "http://www.w3.org/2000/01/rdf-schema#":             "rdfs",
        "http://www.w3.org/1999/02/22-rdf-syntax-ns#":       "rdf",
        "http://dbpedia.org/ontology/":                      "dbo",
        "http://xmlns.com/foaf/0.1/":                        "foaf",
        "http://www.w3.org/2002/07/owl#":                    "owl",
    }
    # Always include sh and xsd
    namespaces["sh"] = "http://www.w3.org/ns/shacl#"
    namespaces["xsd"] = "http://www.w3.org/2001/XMLSchema#"
    namespaces["ex"] = "http://example.org/"

    for uri in ontology_snippet:
        for ns_uri, prefix in known.items():
            if uri.startswith(ns_uri) and prefix not in namespaces:
                namespaces[prefix] = ns_uri
    return namespaces


def uri_to_prefixed(uri, namespaces):
    """Convert a full URI to prefixed form using known namespaces."""
    for prefix, ns_uri in namespaces.items():
        if uri.startswith(ns_uri):
            return f"{prefix}:{uri[len(ns_uri):]}"
    return f"<{uri}>"


def find_target_class(ontology_snippet, namespaces):
    """Find the first Class or rdf:type in the ontology snippet to use as targetClass."""
    class_types = {
        "http://www.w3.org/2000/01/rdf-schema#Class",
        "http://www.w3.org/2002/07/owl#Class",
    }
    for uri, info in ontology_snippet.items():
        types = info.get("types", [])
        if any(t in class_types for t in types):
            return uri_to_prefixed(uri, namespaces)
    return None


def find_property_paths(ontology_snippet, namespaces):
    """Find all Property URIs in the ontology snippet to use as sh:path values."""
    property_types = {
        "http://www.w3.org/1999/02/22-rdf-syntax-ns#Property",
        "http://www.w3.org/2002/07/owl#ObjectProperty",
        "http://www.w3.org/2002/07/owl#DatatypeProperty",
    }
    paths = []
    for uri, info in ontology_snippet.items():
        types = info.get("types", [])
        if any(t in property_types for t in types):
            paths.append(uri_to_prefixed(uri, namespaces))
    return paths


# ---------------------------------------------------------------------------
# Shape generation
# ---------------------------------------------------------------------------

def generate_shape(record_id, label, description, ontology_snippet):
    """
    Generate a SHACL NodeShape in Turtle from a description and ontology snippet.
    Returns a Turtle string, or None if not enough information is available.
    """
    namespaces = extract_prefixes(ontology_snippet)
    target_class = find_target_class(ontology_snippet, namespaces)
    property_paths = find_property_paths(ontology_snippet, namespaces)

    if target_class is None:
        return None  # Cannot generate a shape without a target class

    datatype = detect_datatype(description)
    mincount = detect_mincount(description)
    maxcount = detect_maxcount(description)

    # Build prefix declarations
    prefix_lines = "\n".join(
        f"@prefix {p}: <{ns}> ." for p, ns in sorted(namespaces.items())
    )

    # Shape name: use label if available, otherwise derive from record id
    shape_name = f"ex:{label}" if label else f"ex:{record_id.replace('-', '_')}Shape"

    lines = [
        prefix_lines,
        "",
        f"{shape_name}",
        "    a sh:NodeShape ;",
        f"    sh:targetClass {target_class} ;",
    ]

    if property_paths:
        # Generate one property shape per detected property path
        for i, path in enumerate(property_paths):
            is_last = (i == len(property_paths) - 1)
            prop_lines = [f"    sh:property ["]
            prop_lines.append(f"        sh:path {path} ;")
            if datatype:
                prop_lines.append(f"        sh:datatype {datatype} ;")
            if mincount:
                prop_lines.append(f"        sh:minCount {mincount} ;")
            if maxcount:
                prop_lines.append(f"        sh:maxCount {maxcount} ;")
            prop_lines.append("    ]" + ("" if not is_last else " ."))
            if is_last:
                prop_lines[-1] = "    ] ."
            else:
                prop_lines[-1] = "    ] ;"
            lines.extend(prop_lines)
    else:
        # No property paths found; close the shape with just the target
        lines[-1] = lines[-1].replace(" ;", " .")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_jsonl(path):
    records = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                r = json.loads(line)
                records[r["id"]] = r
    return records


def main():
    parser = argparse.ArgumentParser(
        description="Minimal rule-based NL2SHACL translator (reference implementation)."
    )
    parser.add_argument(
        "--subset",
        required=True,
        help="Subset folder name (e.g. 'dbpedia-dataset', 'invoice-dataset').",
    )
    parser.add_argument(
        "--dataset-dir",
        default="dataset",
        help="Path to the NL2SHACL-Dataset directory (default: dataset/).",
    )
    parser.add_argument(
        "--output",
        default="your_outputs.jsonl",
        help="Output JSONL file (default: your_outputs.jsonl).",
    )
    args = parser.parse_args()

    subset_dir = os.path.join(args.dataset_dir, args.subset)
    if not os.path.exists(subset_dir):
        print(f"Error: subset directory not found: {subset_dir}")
        sys.exit(1)

    # Infer file prefix from subset name (e.g. 'dbpedia-dataset' -> 'dbpedia')
    prefix = args.subset.replace("-dataset", "")

    desc_path = os.path.join(subset_dir, f"{prefix}-descriptions.jsonl")
    onto_path = os.path.join(subset_dir, f"{prefix}-ontology_snippets.jsonl")

    if not os.path.exists(desc_path):
        print(f"Error: descriptions file not found: {desc_path}")
        sys.exit(1)
    if not os.path.exists(onto_path):
        print(f"Error: ontology snippets file not found: {onto_path}")
        sys.exit(1)

    descriptions = load_jsonl(desc_path)
    ontologies = load_jsonl(onto_path)

    results = []
    skipped = 0

    for record_id, desc_record in descriptions.items():
        onto_record = ontologies.get(record_id, {})
        ontology_snippet = onto_record.get("ontology_snippet", {})
        label = desc_record.get("label", "")
        description = desc_record.get("description", "")

        output_shacl = generate_shape(record_id, label, description, ontology_snippet)

        if output_shacl is None:
            skipped += 1

        results.append({
            "id": record_id,
            "output_shacl": output_shacl,
        })

    with open(args.output, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"Written {len(results)} records to {args.output}")
    if skipped:
        print(f"Skipped (no target class found): {skipped} record(s) — output_shacl set to null")
    print(f"\nNext step: attach reference SHACL and run evaluation:")
    print(f"  python NL2SHACL-Translator/attach_reference.py \\")
    print(f"      --input {args.output} \\")
    print(f"      --dataset-dir {args.dataset_dir} \\")
    print(f"      --output processed-output/{args.subset}_rule_based_processed.jsonl")


if __name__ == "__main__":
    main()