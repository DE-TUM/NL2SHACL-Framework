"""
generate_prompts.py
-------------------
Generate a prompts JSONL file for NL2SHACL evaluation.

Usage:
    python generate_prompts.py --subset <folder_name> [--data_root <path>] [--output <path>]
    e.g. python generate_prompts.py --subset invoice-dataset

Examples:
    python generate_prompts.py --subset dcat-dataset
    python generate_prompts.py --subset invoice-dataset --data_root ./my_data --output ./out/invoice_prompts.jsonl

Expected folder structure:
    <data_root>/
      <subset>/
        <subset_prefix>-descriptions.jsonl       # fields: id, label, description
        <subset_prefix>-ontology_snippets.jsonl  # fields: id, label, ontology_snippet
        llm/
          prefixes.json                          # prefix -> namespace URI mapping
          example.txt                            # few-shot example for this subset
"""

import argparse
import json
import os
import sys


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FIXED_PREFIXES = {"", "sh", "rdf", "xsd", "owl", "rdfs"}

KEYS_TO_EXCLUDE = {"source"}


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an expert in Semantic Web technologies, RDF, and SHACL (Shapes Constraint Language).

Your task is to translate a natural language description of a data constraint into a valid SHACL shape, written in Turtle (TTL) format.
**IMPORTANT: Do NOT use RDF/XML format.**

Follow these rules strictly:

1. Output only valid Turtle. Do not include any explanation, commentary, markdown code fences, or extra text — output the raw Turtle document and nothing else.
2. Include all necessary prefix declarations. Every prefix used in the shape must be declared with @prefix at the top of the output. The output must be a self-contained, parseable RDF graph.
3. Use the provided ontology terms. You will be given a set of ontology terms (URIs with their labels, types, and descriptions). Use these URIs exactly as given when constructing sh:path, sh:targetClass, sh:class, sh:hasValue, sh:in, and similar predicates. Do not invent URIs or substitute alternative terms.
4. Produce exactly one sh:NodeShape. Unless the description explicitly involves multiple shapes, emit a single named sh:NodeShape as the top-level resource.
5. Use correct SHACL predicates such as sh:targetClass, sh:targetNode, sh:property, sh:path, sh:minCount, sh:maxCount, sh:datatype, sh:nodeKind, sh:class, sh:in, sh:hasValue, sh:or, sh:and, sh:not, as appropriate for the described constraint.
6. Derive the shape's local name from the target class or the constraint's subject (e.g., ex:CatalogShape, :AgentShape).\
"""

USER_PROMPT_TEMPLATE = """\
Translate the following natural language constraint description into a SHACL shape in Turtle format.
Output only the Turtle document. No explanation, no markdown fences.

# Example
{example}

# Your Task
## Natural Language Description
{description}

## Prefixes
{prefix_block}

## Ontology Terms
{ontology_block}

## Output:
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_jsonl(path: str) -> dict:
    """Load a JSONL file, return a dict keyed by 'id'."""
    records = {}
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"[WARN] {path}:{lineno} — JSON parse error: {e}", file=sys.stderr)
                continue
            record_id = obj.get("id")
            if record_id is None:
                print(f"[WARN] {path}:{lineno} — missing 'id', skipping", file=sys.stderr)
                continue
            records[record_id] = obj
    return records


def load_json(path: str) -> dict:
    """Load a JSON file and return as dict."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_text(path: str) -> str:
    """Load a plain text file. Returns empty string if file does not exist."""
    if not os.path.isfile(path):
        print(f"[WARN] File not found: {path} — example will be omitted.", file=sys.stderr)
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def strip_excluded_keys(snippet: dict) -> dict:
    """Remove unwanted keys (e.g. 'source') from each URI's metadata dict."""
    return {
        uri: {k: v for k, v in meta.items() if k not in KEYS_TO_EXCLUDE}
        for uri, meta in snippet.items()
    }


def filter_prefixes(all_prefixes: dict, ontology_snippet: dict) -> dict:
    """
    Return a filtered prefix dict containing:
    - All prefixes in FIXED_PREFIXES (by key)
    - Any prefix whose namespace URI is a prefix of a URI in ontology_snippet
    """
    filtered = {}

    for prefix, namespace in all_prefixes.items():
        # Always include fixed prefixes
        if prefix in FIXED_PREFIXES:
            filtered[prefix] = namespace
            continue

        # Include if namespace matches any ontology URI
        for uri in ontology_snippet:
            if uri.startswith(namespace):
                filtered[prefix] = namespace
                break

    return filtered


def format_prefixes(prefixes: dict) -> str:
    """Render prefix dict as Turtle-style @prefix declarations."""
    if not prefixes:
        return "(no prefixes)"

    lines = []
    for prefix, namespace in sorted(prefixes.items(), key=lambda x: x[0]):
        if prefix == "":
            lines.append(f"@prefix : <{namespace}> .")
        else:
            lines.append(f"@prefix {prefix}: <{namespace}> .")

    return "\n".join(lines)


def format_ontology_snippet(ontology_snippet: dict) -> str:
    """Render ontology_snippet dict as a readable plain-text block."""
    if not ontology_snippet:
        return "(no ontology terms provided)"

    lines = []
    for uri, meta in ontology_snippet.items():
        lines.append(f"URI: {uri}")

        types = meta.get("types") or meta.get("type")
        if types:
            if isinstance(types, list):
                type_labels = [t.split("#")[-1].split("/")[-1] for t in types]
                lines.append(f"  Type: {', '.join(type_labels)}")
            else:
                lines.append(f"  Type: {str(types).split('#')[-1].split('/')[-1]}")

        label = meta.get("label")
        if label:
            lines.append(f"  Label: {label}")

        description = meta.get("description")
        if description:
            lines.append(f"  Description: {description}")

        lines.append("")  # blank line between entries

    return "\n".join(lines).rstrip()


def build_user_prompt(
    example_text: str,
    description_text: str,
    all_prefixes: dict,
    clean_snippet: dict,
) -> str:
    """Construct the user prompt for a single data point."""
    ontology_block    = format_ontology_snippet(clean_snippet)
    filtered_prefixes = filter_prefixes(all_prefixes, clean_snippet)
    prefix_block      = format_prefixes(filtered_prefixes)

    return USER_PROMPT_TEMPLATE.format(
        example=example_text,
        description=description_text,
        prefix_block=prefix_block,
        ontology_block=ontology_block,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Generate NL2SHACL prompt JSONL.")
    parser.add_argument(
        "--subset", required=True,
        help="Subdirectory name under data_root (e.g. 'dcat-dataset', 'invoice-dataset')"
    )
    parser.add_argument(
        "--data_root", default=".",
        help="Root directory containing subset folders (default: current directory)"
    )
    parser.add_argument(
        "--output", default=None,
        help="Output JSONL path. Defaults to <data_root>/<subset>/prompts.jsonl"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    subset_dir = os.path.join(args.data_root, args.subset)
    if not os.path.isdir(subset_dir):
        print(f"[ERROR] Subset directory not found: {subset_dir}", file=sys.stderr)
        sys.exit(1)

    # Infer file prefix from subset name (e.g. 'invoice-dataset' -> 'invoice')
    subset_prefix = args.subset.replace("-dataset", "")
    llm_dir       = os.path.join(subset_dir, "llm")

    desc_path     = os.path.join(subset_dir, f"{subset_prefix}-descriptions.jsonl")
    onto_path     = os.path.join(subset_dir, f"{subset_prefix}-ontology_snippets.jsonl")
    prefixes_path = os.path.join(llm_dir, "prefixes.json")
    example_path  = os.path.join(llm_dir, "example.txt")

    for path in (desc_path, onto_path, prefixes_path):
        if not os.path.isfile(path):
            print(f"[ERROR] Required file not found: {path}", file=sys.stderr)
            sys.exit(1)

    output_path = args.output or os.path.join(subset_dir, "prompts.jsonl")

    print(f"Loading descriptions from:      {desc_path}")
    print(f"Loading ontology snippets from: {onto_path}")
    print(f"Loading prefixes from:          {prefixes_path}")
    print(f"Loading example from:           {example_path}")

    descriptions = load_jsonl(desc_path)
    ontologies   = load_jsonl(onto_path)
    all_prefixes = load_json(prefixes_path)
    example_text = load_text(example_path)

    ids = list(descriptions.keys())
    example_id = ids[-1]  # last entry is used as the few-shot example
    ids = ids[:-1]        # skip it during generation
    print(f"Skipping last entry '{example_id}' (used as few-shot example).")

    missing_onto = [i for i in ids if i not in ontologies]
    if missing_onto:
        print(
            f"[WARN] {len(missing_onto)} IDs have no ontology entry: "
            f"{missing_onto[:5]}{'...' if len(missing_onto) > 5 else ''}",
            file=sys.stderr
        )

    written  = 0
    previews = []  # store first two (id, user_prompt) for inspection

    with open(output_path, "w", encoding="utf-8") as out_f:
        for record_id in ids:
            desc_record = descriptions[record_id]
            onto_record = ontologies.get(record_id, {})

            description_text = desc_record.get("description", "").strip()
            if not description_text:
                print(f"[WARN] ID '{record_id}' has empty description, skipping", file=sys.stderr)
                continue

            raw_snippet   = onto_record.get("ontology_snippet", {})
            clean_snippet = strip_excluded_keys(raw_snippet)

            user_prompt = build_user_prompt(
                example_text,
                description_text,
                all_prefixes,
                clean_snippet,
            )

            # Collect preview before incrementing written
            if written < 2:
                previews.append((record_id, user_prompt))

            record = {
                "id": record_id,
                "system_prompt": SYSTEM_PROMPT,
                "user_prompt": user_prompt,
            }

            out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1

    # Print preview of first two prompts
    print("\n" + "=" * 60)
    print("PREVIEW: First two generated prompts")
    print("=" * 60)
    for preview_id, preview_prompt in previews:
        print(f"\n--- ID: {preview_id} ---")
        print("SYSTEM PROMPT:")
        print(SYSTEM_PROMPT)
        print("\nUSER PROMPT:")
        print(preview_prompt)
        print("=" * 60)

    print(f"\nDone. {written} prompts written to: {output_path}")


if __name__ == "__main__":
    main()