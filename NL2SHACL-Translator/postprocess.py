"""
postprocess.py
--------------
Post-process LLM output files: clean up raw outputs and attach reference SHACL.

Usage:
    # Process a single file
    python postprocess.py --input llm-output/invoice-dataset_gemini-2-5-pro.jsonl

    # Process all files in a folder
    python postprocess.py --input llm-output/

Optional:
    --data_root   Root directory containing subset folders (default: current directory)
    --output_dir  Output directory (default: processed-output/)

Output:
    processed-output/<input_filename>_processed.jsonl

Each output record contains:
    id                : original record ID
    output_shacl      : cleaned LLM output (null if original output was null)
    reference_shacl   : content of the corresponding reference .ttl file (null if not found)
"""

import argparse
import json
import os
import re
import sys


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OUTPUT_DIR = "processed-output"

# Map from ID prefix to subset folder name
# e.g. "invoice-1" -> "invoice-dataset", "dcat-1" -> "dcat-dataset"
ID_PREFIX_TO_SUBSET = {
    "invoice": "invoice-dataset",
    "dcat":    "dcat-dataset",
    "dbpedia": "dbpedia-dataset",
    "chemrof": "chemrof-dataset",
    "snik":    "snik-dataset",
    "epo":     "epo-dataset",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_jsonl(path: str) -> list:
    """Load a JSONL file, return a list of dicts in order."""
    records = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"[WARN] {path}:{lineno} — JSON parse error: {e}", file=sys.stderr)
    return records


def cleanup_output(raw: str) -> str:
    """Remove markdown fences and other formatting artifacts from LLM output."""
    if raw is None:
        return None

    output = raw.strip()

    # Remove markdown code fences
    if output.startswith("```ttl"):
        output = output[6:]
    if output.startswith("```turtle"):
        output = output[9:]
    if output.startswith("```"):
        output = output[3:]
    if output.endswith("```"):
        output = output[:-3]

    # Remove leading "turtle" word if present
    if output.lower().startswith("turtle"):
        output = output[6:].lstrip()

    # Remove markdown links [label](uri) -> uri
    output = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\2', output)

    return output.strip()


def find_reference_shacl(record_id: str, data_root: str) -> str | None:
    """
    Find and read the reference SHACL file for a given record ID.
    Expects files at: <data_root>/<subset>/shacl/<id>.ttl
    e.g. invoice-1 -> invoice-dataset/shacl/invoice-1.ttl
    """
    # Determine subset from ID prefix
    prefix = record_id.split("-")[0]
    subset = ID_PREFIX_TO_SUBSET.get(prefix)

    if subset is None:
        print(f"[WARN] Unknown ID prefix '{prefix}' for ID '{record_id}', cannot find reference.", file=sys.stderr)
        return None

    ttl_path = os.path.join(data_root, subset, "shacl", f"{record_id}.ttl")

    if not os.path.isfile(ttl_path):
        print(f"[WARN] Reference SHACL not found: {ttl_path}", file=sys.stderr)
        return None

    with open(ttl_path, encoding="utf-8") as f:
        return f.read().strip()


def process_file(input_path: str, data_root: str, output_dir: str) -> None:
    """Process a single LLM output JSONL file."""
    filename     = os.path.basename(input_path)
    stem         = os.path.splitext(filename)[0]
    output_path  = os.path.join(output_dir, f"{stem}_processed.jsonl")

    print(f"\nProcessing: {input_path}")
    print(f"Output:     {output_path}")

    records = load_jsonl(input_path)
    total   = len(records)
    print(f"Records:    {total}")

    null_output_count     = 0
    missing_ref_count     = 0
    written               = 0

    with open(output_path, "w", encoding="utf-8") as out_f:
        for record in records:
            record_id = record.get("id", "unknown")
            raw_output = record.get("output")

            # Clean up LLM output
            output_shacl = cleanup_output(raw_output)
            if raw_output is None:
                null_output_count += 1

            # Find reference SHACL
            reference_shacl = find_reference_shacl(record_id, data_root)
            if reference_shacl is None:
                missing_ref_count += 1

            result = {
                "id":               record_id,
                "output_shacl":     output_shacl,
                "reference_shacl":  reference_shacl,
            }

            out_f.write(json.dumps(result, ensure_ascii=False) + "\n")
            written += 1

    print(f"Done. {written} records written.")
    if null_output_count:
        print(f"  [WARN] {null_output_count} records had null LLM output.")
    if missing_ref_count:
        print(f"  [WARN] {missing_ref_count} records had no reference SHACL file.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Post-process LLM output JSONL files.")
    parser.add_argument(
        "--input", required=True,
        help="Path to a single .jsonl file or a folder of .jsonl files"
    )
    parser.add_argument(
        "--data_root", default=".",
        help="Root directory containing subset folders (default: current directory)"
    )
    parser.add_argument(
        "--output_dir", default=OUTPUT_DIR,
        help=f"Output directory (default: {OUTPUT_DIR})"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    input_path = args.input
    output_dir = args.output_dir

    os.makedirs(output_dir, exist_ok=True)

    # Determine input files
    if os.path.isfile(input_path):
        input_files = [input_path]
    elif os.path.isdir(input_path):
        input_files = [
            os.path.join(input_path, f)
            for f in sorted(os.listdir(input_path))
            if f.endswith(".jsonl")
        ]
        if not input_files:
            print(f"[ERROR] No .jsonl files found in: {input_path}", file=sys.stderr)
            sys.exit(1)
        print(f"Found {len(input_files)} file(s) in '{input_path}'.")
    else:
        print(f"[ERROR] Input path not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    # Process each file
    for f in input_files:
        process_file(f, args.data_root, output_dir)

    print("\nAll done.")


if __name__ == "__main__":
    main()