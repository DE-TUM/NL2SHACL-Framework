"""
generate_nl_prompts.py

Generate NL description prompt files for a dataset subset.

Reads from the augmented JSONL file produced by ontology_augment.py.
Each record's 'shacl' field is used as input to the prompt.

Two modes:
  --subset   : Use a built-in system prompt for a known subset.
  --role     : Provide a custom role description (first sentence of the system prompt).
               The rest of the system prompt is assembled automatically.

Usage:
    python generate_nl_prompts.py --input <augmented.jsonl> --subset <name>
    python generate_nl_prompts.py --input <augmented.jsonl> --role "You are a Senior Logistics Data Curator..."

Examples:
    python generate_nl_prompts.py --input output_data_augmented.jsonl --subset snik
    python generate_nl_prompts.py --input output_data_augmented.jsonl --role "You are a Senior Railway Data Curator specializing in infrastructure management."

Output:
    <input_stem>_nl_prompts.jsonl
    or specified via --output
"""

import argparse
import json
import os
import sys


# ---------------------------------------------------------------------------
# Shared translation rules (appended after the role sentence)
# ---------------------------------------------------------------------------

SHARED_RULES = """\
Goal: Translate SHACL and OWL ontology constraints into precise, natural-sounding plain English. \
The output will train a machine learning model to understand how domain experts naturally formulate strict data rules. \
The translation must be logically flawless and accurate, but read like a human-written curation guideline, \
not a robotic 1-to-1 code translation.
Translation Rules:
    Establish the Core Concept: Identify the sh:targetClass. Start by introducing the entity naturally.
    Leverage Existing Descriptions: If a property has an sh:description, sh:message, or sh:name, use that context \
to inform your natural language explanation, but weave it in smoothly. Do not just copy-paste the description verbatim.
    Translate Ontology Mechanics to Plain English:
        Eliminate prefixes.
        sh:nodeKind sh:IRI: Explain this as "a specific identifier" or "a direct link to".
        sh:nodeKind sh:BlankNode: Explain this as "an internal nested record" or "an unnamed structure".
        sh:closed true: Explicitly state that the data model is strict and no outside properties are allowed \
(except those explicitly ignored, like system types).
        owl:subClassOf or metaclasses: Frame these as hierarchical relationships or categorizations.
    Express Constraints Fluidly:
        sh:minCount 1 / sh:maxCount 1: Frame as "exactly one", "a single required", or "must specify one".
        sh:pattern / sh:maxLength / sh:minLength: Translate logic constraints into human explanation.
        sh:in: Explain lists naturally (e.g., "must be exactly one of the following:...").
        sh:or / sh:not: Translate logical gates organically.
    Formatting: Output ONLY the final plain-English paragraph. Write it as a flowing, continuous data curation rule. \
No markdown, no introductory filler, no bulleted lists."""


# ---------------------------------------------------------------------------
# Built-in role sentences per subset
# ---------------------------------------------------------------------------

SUBSET_ROLES = {
    "invoice":  "You are a Senior Business Data Curator specializing in electronic invoicing and EDI standards.",
    "chemrof":  "You are a Senior Scientific Data Curator specializing in chemical knowledge representation and molecular data standards.",
    "dcat":     "You are a Senior Open Data Curator specializing in metadata standards and European data portal governance.",
    "epo":      "You are a Senior Public Procurement Data Curator specializing in EU procurement regulations and the eProcurement Ontology.",
    "snik":     "You are a Senior Healthcare Information Management Curator specializing in hospital information systems and clinical data governance.",
    "dbpedia":  "You are a Senior Knowledge Graph Curator specializing in cross-domain linked data and general-purpose ontology constraints.",
}


# ---------------------------------------------------------------------------
# User prompt template
# ---------------------------------------------------------------------------

USER_PROMPT_TEMPLATE = (
    "Translate the following SHACL shape into a plain-English data curation rule.\n"
    "Output ONLY the final paragraph. No markdown, no introductory filler, no bulleted lists.\n\n"
    "## SHACL Shape\n"
    "{shacl}"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_system_prompt(role_sentence: str) -> str:
    """Assemble a full system prompt from a role sentence and the shared rules."""
    return f"{role_sentence}\n{SHARED_RULES}"


def load_augmented_jsonl(path: str) -> list:
    """Load records from an augmented JSONL file."""
    records = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"[WARN] Line {lineno}: JSON parse error: {e}", file=sys.stderr)
    return records


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate NL description prompts from an augmented JSONL file."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to the augmented JSONL file produced by ontology_augment.py.",
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--subset",
        choices=list(SUBSET_ROLES.keys()),
        help="Use the built-in system prompt for a known subset.",
    )
    mode.add_argument(
        "--role",
        help="Custom role sentence (first line of the system prompt). "
             "The translation rules are appended automatically. "
             "Example: \"You are a Senior Railway Data Curator specializing in infrastructure management.\"",
    )

    parser.add_argument(
        "--output",
        default=None,
        help="Output JSONL path. Defaults to <input_stem>_nl_prompts.jsonl.",
    )

    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    # Resolve system prompt and output path
    if args.subset:
        role_sentence = SUBSET_ROLES[args.subset]
    else:
        role_sentence = args.role.strip()

    system_prompt = build_system_prompt(role_sentence)

    if args.output:
        output_path = args.output
    else:
        stem = os.path.splitext(args.input)[0]
        output_path = f"{stem}_nl_prompts.jsonl"

    # Load records
    records = load_augmented_jsonl(args.input)
    if not records:
        print(f"Error: no records found in {args.input}", file=sys.stderr)
        sys.exit(1)

    # Generate prompts
    written = 0
    skipped = 0
    with open(output_path, "w", encoding="utf-8") as out_f:
        for record in records:
            record_id = record.get("id", "")
            shacl_content = record.get("shacl", "").strip()

            if not shacl_content:
                print(f"[WARN] Record '{record_id}' has no shacl field, skipping.", file=sys.stderr)
                skipped += 1
                continue

            user_prompt = USER_PROMPT_TEMPLATE.format(shacl=shacl_content)
            out_record = {
                "id": record_id,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            }
            out_f.write(json.dumps(out_record, ensure_ascii=False) + "\n")
            written += 1

    print(f"Done. Written {written} records to {output_path}")
    if skipped:
        print(f"Skipped {skipped} records with no shacl field.")


if __name__ == "__main__":
    main()