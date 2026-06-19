"""
run_evaluation.py
-----------------
Run evaluation metrics on post-processed NL2SHACL outputs.

Usage:
    # Evaluate a single file with all evaluators
    python run_evaluation.py --input processed-output/invoice-dataset_gemini-2-5-pro_processed.jsonl

    # Run validity + structural (validity always runs first when using --run)
    python run_evaluation.py --input processed-output/ --run structural

    # Skip validity, run only structural
    python run_evaluation.py --input processed-output/ --only structural

    # Run semantic evaluator with custom scale
    python run_evaluation.py --input processed-output/ --run semantic --scale 10

Arguments:
    --input       Path to a single .jsonl file or a folder of .jsonl files
    --output_dir  Output directory (default: evaluation-output/)
    --run         Which evaluators to run: validity structural semantic
                  validity always runs first as a prerequisite (default: all)
    --only        Skip validity and run only one evaluator: structural semantic
                  Cannot be used together with --run.
    --scale       Scale factor for rdf-graph-gen (default: 10)
    --batch_size  Batch size for rdf-graph-gen (default: 100)

Output format per record:
    {
        "id": "invoice-6",
        "subset": "invoice-dataset",
        "model": "gemini-2-5-pro",
        "status": "evaluated" | "skipped",
        "validity": {
            "parsing_valid": true,
            "spec_valid": true,
            "vocab_valid": true,
            "validation_error": null
        },
        "structural": { ... },   # null if not run or validity failed
        "semantic": { ... }      # null if not run or validity failed
    }
"""

import argparse
import json
import os
import sys

from evaluators import (
    validate_llm_shacl,
    evaluate_syntactic_equivalence,
    evaluate_semantic_graph_equivalence,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OUTPUT_DIR     = "evaluation-output"
ALL_EVALUATORS = {"validity", "structural", "semantic"}


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
                print(f"[WARN] {path}:{lineno} - JSON parse error: {e}", file=sys.stderr)
    return records


def load_existing_ids(output_path: str) -> set:
    """Load already-processed IDs from output file to support resuming."""
    processed = set()
    if not os.path.isfile(output_path):
        return processed
    with open(output_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if "id" in obj:
                    processed.add(obj["id"])
            except json.JSONDecodeError:
                continue
    return processed


def parse_filename(filename: str) -> tuple[str, str]:
    """
    Parse subset and model from filename.
    e.g. invoice-dataset_gemini-2-5-pro_processed.jsonl
      -> ("invoice-dataset", "gemini-2-5-pro")
    """
    stem = os.path.splitext(filename)[0]
    stem = stem.replace("_processed", "").replace("_eval", "")
    parts = stem.split("_", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return stem, "unknown"


# ---------------------------------------------------------------------------
# Evaluation logic
# ---------------------------------------------------------------------------

def run_validity(output_shacl: str) -> dict:
    """Run three-stage validity check. Returns validity result dict."""
    is_valid, error = validate_llm_shacl(output_shacl)

    if is_valid:
        return {
            "parsing_valid":    True,
            "spec_valid":       True,
            "vocab_valid":      True,
            "validation_error": None,
        }

    stage = error.get("failure_stage")
    return {
        "parsing_valid":    stage > 1,
        "spec_valid":       stage > 2 if stage is not None else False,
        "vocab_valid":      False,
        "validation_error": error,
    }


def all_valid(validity: dict) -> bool:
    """Return True if all three validity checks passed."""
    return (
        validity["parsing_valid"] and
        validity["spec_valid"] and
        validity["vocab_valid"]
    )


def evaluate_record(
    record: dict,
    run: set,
    scale: int,
    batch_size: int,
    output_dir: str,
    subset: str,
    model: str,
    normalize: bool = False,
) -> dict:
    """Run selected evaluators on a single record. Returns result dict."""
    record_id       = record["id"]
    output_shacl    = record.get("output_shacl")
    reference_shacl = record.get("reference_shacl")

    result = {
        "id":             record_id,
        "status":         None,
        "validity":       None,
        "structural":     None,
        "semantic":       None,
        "reference_shacl": reference_shacl,
        "output_shacl":   output_shacl,
    }

    if output_shacl is None:
        result["status"] = "skipped"
        return result

    result["status"] = "evaluated"

    # --- Validity ---
    if "validity" in run:
        validity = run_validity(output_shacl)
        result["validity"] = validity
        if not all_valid(validity):
            return result
    else:
        result["validity"] = None

    # --- Structural ---
    if "structural" in run:
        try:
            result["structural"] = evaluate_syntactic_equivalence(
                reference_shacl, output_shacl, normalize=normalize
            )
        except Exception as e:
            print(f"  [WARN] Structural eval failed for '{record_id}': {e}", file=sys.stderr)
            result["structural"] = {"status": "failure", "details": str(e)}

    # --- Semantic ---
    if "semantic" in run:
        try:
            result["semantic"] = evaluate_semantic_graph_equivalence(
                reference_shacl, output_shacl,
                record_id=record_id,
                subset=subset,
                model=model,
                output_dir=output_dir,
                scale=scale,
                batch_size=batch_size,
            )
        except Exception as e:
            print(f"  [WARN] Semantic eval failed for '{record_id}': {e}", file=sys.stderr)
            result["semantic"] = {"status": "failure", "details": str(e)}

    return result


# ---------------------------------------------------------------------------
# File processing
# ---------------------------------------------------------------------------

def process_file(
    input_path: str,
    output_dir: str,
    run: set,
    scale: int,
    batch_size: int,
    normalize: bool = False,
) -> None:
    """Process a single processed JSONL file."""
    filename      = os.path.basename(input_path)
    subset, model = parse_filename(filename)
    stem          = os.path.splitext(filename)[0].replace("_processed", "")
    output_path   = os.path.join(output_dir, f"{stem}_eval.jsonl")
    failure_path  = os.path.join(output_dir, f"{stem}_semantic_failures.csv")

    print(f"\n{'='*60}")
    print(f"Input:   {input_path}")
    print(f"Output:  {output_path}")
    print(f"Subset:  {subset}")
    print(f"Model:   {model}")
    print(f"Running: {sorted(run)}")
    print(f"Normalize RDF lists: {normalize}")

    records       = load_jsonl(input_path)
    total         = len(records)
    processed_ids = load_existing_ids(output_path)

    if processed_ids:
        print(f"Resuming: {len(processed_ids)} already processed, skipping.")

    print(f"Total records: {total}\n")

    skipped_count   = 0
    evaluated_count = 0
    error_count     = 0

    with open(output_path, "a", encoding="utf-8") as out_f:
        for idx, record in enumerate(records, 1):
            record_id = record.get("id", f"unknown_{idx}")

            if record_id in processed_ids:
                continue

            if idx == 1 or idx % 5 == 0:
                print(f"[{idx}/{total}] Processing '{record_id}'...")

            result = evaluate_record(
                record, run, scale, batch_size,
                output_dir=output_dir,
                subset=subset,
                model=model,
                normalize=normalize,
            )

            result["subset"] = subset
            result["model"]  = model

            ordered = {
                "id":              result["id"],
                "subset":          result["subset"],
                "model":           result["model"],
                "status":          result["status"],
                "validity":        result["validity"],
                "structural":      result["structural"],
                "semantic":        result["semantic"],
                "reference_shacl": result.get("reference_shacl"),
                "output_shacl":    result.get("output_shacl"),
            }

            out_f.write(json.dumps(ordered, ensure_ascii=False) + "\n")
            out_f.flush()

            sem = result.get("semantic")
            if sem and sem.get("status") == "failure":
                write_header = not os.path.exists(failure_path)
                with open(failure_path, "a", encoding="utf-8") as fail_f:
                    if write_header:
                        fail_f.write("id,details\n")
                    fail_f.write(f"{record_id},{sem.get('details', '')}\n")

            if result["status"] == "skipped":
                skipped_count += 1
            else:
                evaluated_count += 1
                validity = result.get("validity") or {}
                if validity and not all_valid(validity):
                    error_count += 1

    print(f"\nDone.")
    print(f"  Evaluated: {evaluated_count}")
    print(f"  Skipped:   {skipped_count}")
    if "validity" in run:
        print(f"  Validity failed: {error_count}")
    print(f"  Output: {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Run evaluation on NL2SHACL post-processed outputs.")
    parser.add_argument(
        "--input", required=True,
        help="Path to a single .jsonl file or a folder of .jsonl files"
    )
    parser.add_argument(
        "--output_dir", default=OUTPUT_DIR,
        help=f"Output directory (default: {OUTPUT_DIR})"
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--run", nargs="+", choices=list(ALL_EVALUATORS),
        default=None,
        help=(
            "Which evaluators to run. validity always runs first as a prerequisite. "
            "Choices: validity structural semantic. Default: all."
        )
    )
    mode.add_argument(
        "--only", choices=["structural", "semantic"],
        default=None,
        help="Skip validity and run only this one evaluator. Cannot be used with --run."
    )

    parser.add_argument(
        "--scale", type=int, default=10,
        help="Scale factor for rdf-graph-gen semantic evaluator (default: 10)"
    )
    parser.add_argument(
        "--batch_size", type=int, default=100,
        help="Batch size for rdf-graph-gen semantic evaluator (default: 100)"
    )
    parser.add_argument(
        "--normalize", action="store_true", default=False,
        help="Normalize RDF list ordering in structural evaluation (default: False)"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.only:
        run = {args.only}
        print(f"Mode: --only {args.only} (validity skipped)")
    elif args.run:
        run = set(args.run)
        run.add("validity")
        print(f"Mode: --run {sorted(run)} (validity always included)")
    else:
        run = ALL_EVALUATORS
        print("Mode: all evaluators")

    os.makedirs(args.output_dir, exist_ok=True)

    input_path = args.input
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

    for f in input_files:
        process_file(
            input_path=f,
            output_dir=args.output_dir,
            run=run,
            scale=args.scale,
            batch_size=args.batch_size,
            normalize=args.normalize,
        )

    print("\nAll files processed.")


if __name__ == "__main__":
    main()