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

    # Retry only failed judge calls (null or api_error) in existing eval files
    python run_evaluation.py --input evaluation-output/ --retry-judge

Arguments:
    --input       Path to a single .jsonl file or a folder of .jsonl files
    --data_root   Root directory containing subset folders (default: current directory)
    --output_dir  Output directory (default: evaluation-output/)
    --run         Which evaluators to run: validity structural semantic judge
                  validity always runs first as a prerequisite (default: all)
    --only        Skip validity and run only one evaluator: structural semantic judge
                  Cannot be used together with --run.
    --retry-judge Find records in existing eval files where llm_judge_verdict is null
                  or 'api_error', and rerun judge for those records only. Updates the
                  verdict in-place without touching structural/semantic results.
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
        "semantic": { ... },     # null if not run or validity failed
        "llm_judge_verdict": "equivalent" | "not_equivalent" | null
    }
"""

import argparse
import json
import os
import sys
import time

from evaluators import (
    validate_llm_shacl,
    evaluate_syntactic_equivalence,
    evaluate_semantic_graph_equivalence,
    JudgeLLM,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OUTPUT_DIR    = "evaluation-output"
ALL_EVALUATORS = {"validity", "structural", "semantic", "judge"}

JUDGE_FAILED_VALUES = {None, "api_error"}

# Seconds to sleep between judge calls
JUDGE_SLEEP_SECONDS = 5

# Retry settings for 429 errors
JUDGE_MAX_RETRIES = 5
JUDGE_RETRY_BASE_WAIT = 5  # seconds; doubles each attempt: 5, 10, 20, 40, 80


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


def load_descriptions(data_root: str, subset: str) -> dict:
    """Load id -> description mapping from descriptions.jsonl for a subset."""
    desc_path = os.path.join(data_root, subset, "descriptions.jsonl")
    descriptions = {}
    if not os.path.isfile(desc_path):
        print(f"[WARN] descriptions.jsonl not found for subset '{subset}': {desc_path}", file=sys.stderr)
        return descriptions
    with open(desc_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if "id" in obj and "description" in obj:
                    descriptions[obj["id"]] = obj["description"]
            except json.JSONDecodeError:
                continue
    return descriptions


# ---------------------------------------------------------------------------
# Judge with retry + sleep
# ---------------------------------------------------------------------------

def call_judge_with_retry(judge: "JudgeLLM", reference_shacl: str, output_shacl: str, nl_description: str) -> str:
    """
    Call judge.get_semantic_equivalence_verdict with:
      - exponential backoff retry on 429 errors (up to JUDGE_MAX_RETRIES attempts)
      - JUDGE_SLEEP_SECONDS sleep after every successful call

    Returns the verdict string, or "api_error" if all retries fail.
    """
    last_error = None
    for attempt in range(JUDGE_MAX_RETRIES):
        try:
            verdict = judge.get_semantic_equivalence_verdict(
                reference_shacl, output_shacl, nl_description
            )
            # Sleep after every successful judge call to avoid hitting TPM limit
            time.sleep(JUDGE_SLEEP_SECONDS)
            return verdict
        except Exception as e:
            error_str = str(e)
            last_error = error_str
            if "429" in error_str or "rate_limit" in error_str.lower():
                wait = JUDGE_RETRY_BASE_WAIT * (2 ** attempt)
                print(
                    f"  [Judge] Rate limit hit (attempt {attempt + 1}/{JUDGE_MAX_RETRIES}), "
                    f"waiting {wait}s before retry...",
                    file=sys.stderr,
                )
                time.sleep(wait)
            else:
                # Non-429 error, no point retrying
                print(f"  [Judge] Non-retryable error: {e}", file=sys.stderr)
                return "api_error"

    print(f"  [Judge] All {JUDGE_MAX_RETRIES} retries exhausted. Last error: {last_error}", file=sys.stderr)
    return "api_error"


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
    descriptions: dict,
    judge: "JudgeLLM | None",
    scale: int,
    batch_size: int,
    output_dir: str,       # <-- NEW
    subset: str,           # <-- NEW
    model: str,            # <-- NEW
    normalize: bool = False,
) -> dict:
    """Run selected evaluators on a single record. Returns result dict."""
    record_id       = record["id"]
    output_shacl    = record.get("output_shacl")
    reference_shacl = record.get("reference_shacl")

    result = {
        "id":                record_id,
        "status":            None,
        "validity":          None,
        "structural":        None,
        "semantic":          None,
        "llm_judge_verdict": None,
        "reference_shacl":   reference_shacl,
        "output_shacl":      output_shacl,
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

    # --- LLM Judge ---
    if "judge" in run and judge is not None:
        nl_description = descriptions.get(record_id, "")
        if not nl_description:
            print(f"  [WARN] No description found for '{record_id}', judge skipped.", file=sys.stderr)
            result["llm_judge_verdict"] = None
        else:
            result["llm_judge_verdict"] = call_judge_with_retry(
                judge, reference_shacl, output_shacl, nl_description
            )

    return result


# ---------------------------------------------------------------------------
# Retry judge: in-place update of existing eval files
# ---------------------------------------------------------------------------

def retry_judge_in_file(
    eval_path: str,
    processed_input_dir: str,
    data_root: str,
    judge: "JudgeLLM",
) -> None:
    """
    Read an existing eval JSONL file, find records where llm_judge_verdict is
    null or 'api_error', rerun judge for those records only, and write the
    updated file back in-place (all other fields untouched).

    processed_input_dir: folder containing the original _processed.jsonl files,
    used to retrieve output_shacl and reference_shacl for the failed records.
    """
    filename = os.path.basename(eval_path)
    subset, model = parse_filename(filename)

    print(f"\n{'='*60}")
    print(f"Retry judge: {eval_path}")
    print(f"Subset: {subset} | Model: {model}")

    # Load all records from the eval file
    records = load_jsonl(eval_path)
    if not records:
        print("  No records found, skipping.")
        return

    # Find which IDs need re-judging
    need_retry = [r for r in records if r.get("llm_judge_verdict") in JUDGE_FAILED_VALUES]
    if not need_retry:
        print("  No failed judge results found, nothing to do.")
        return

    retry_ids = {r["id"] for r in need_retry}
    print(f"  Found {len(retry_ids)} record(s) needing retry: {sorted(retry_ids)}")

    # Load descriptions for this subset
    descriptions = load_descriptions(data_root, subset)

    # Find the matching processed input file to get shacl fields
    stem = filename.replace("_eval.jsonl", "")
    processed_path = os.path.join(processed_input_dir, f"{stem}_processed.jsonl")
    if not os.path.isfile(processed_path):
        print(f"  [ERROR] Processed input not found: {processed_path}", file=sys.stderr)
        print(f"  Cannot retry without reference_shacl and output_shacl. Skipping.")
        return

    processed_records = {r["id"]: r for r in load_jsonl(processed_path)}

    # Run judge for each failed record and collect updated verdicts
    updated_verdicts: dict[str, str] = {}
    total_retry = len(retry_ids)
    for i, record_id in enumerate(sorted(retry_ids), 1):
        print(f"  [{i}/{total_retry}] Retrying judge for '{record_id}'...")

        processed = processed_records.get(record_id)
        if processed is None:
            print(f"  [WARN] '{record_id}' not found in processed file, skipping.", file=sys.stderr)
            updated_verdicts[record_id] = "api_error"
            continue

        output_shacl    = processed.get("output_shacl")
        reference_shacl = processed.get("reference_shacl")
        nl_description  = descriptions.get(record_id, "")

        if not output_shacl:
            print(f"  [WARN] No output_shacl for '{record_id}', skipping.", file=sys.stderr)
            updated_verdicts[record_id] = None
            continue

        if not nl_description:
            print(f"  [WARN] No description for '{record_id}', skipping.", file=sys.stderr)
            updated_verdicts[record_id] = None
            continue

        verdict = call_judge_with_retry(judge, reference_shacl, output_shacl, nl_description)
        updated_verdicts[record_id] = verdict
        print(f"    -> verdict: {verdict}")

    # Write updated file back in-place
    with open(eval_path, "w", encoding="utf-8") as f:
        for record in records:
            rid = record["id"]
            if rid in updated_verdicts:
                record["llm_judge_verdict"] = updated_verdicts[rid]
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    success_count = sum(
        1 for rid, v in updated_verdicts.items()
        if v not in JUDGE_FAILED_VALUES
    )
    print(f"\n  Done. {success_count}/{len(updated_verdicts)} successfully judged.")
    print(f"  Updated file: {eval_path}")


# ---------------------------------------------------------------------------
# File processing (normal mode)
# ---------------------------------------------------------------------------

def process_file(
    input_path: str,
    data_root: str,
    output_dir: str,
    run: set,
    scale: int,
    batch_size: int,
    judge: "JudgeLLM | None",
    normalize: bool = False,
) -> None:
    """Process a single processed JSONL file."""
    filename        = os.path.basename(input_path)
    subset, model   = parse_filename(filename)
    stem            = os.path.splitext(filename)[0].replace("_processed", "")
    output_path     = os.path.join(output_dir, f"{stem}_eval.jsonl")
    failure_path = os.path.join(output_dir, f"{stem}_semantic_failures.csv")  

    print(f"\n{'='*60}")
    print(f"Input:   {input_path}")
    print(f"Output:  {output_path}")
    print(f"Subset:  {subset}")
    print(f"Model:   {model}")
    print(f"Running: {sorted(run)}")
    print(f"Normalize RDF lists: {normalize}")

    descriptions = {}
    if "judge" in run:
        descriptions = load_descriptions(data_root, subset)
        print(f"Loaded {len(descriptions)} descriptions for judge.")

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
                record, run, descriptions, judge, scale, batch_size,
                output_dir=output_dir,
                subset=subset,
                model=model,
                normalize=normalize,
            )

            result["subset"] = subset
            result["model"]  = model

            ordered = {
                "id":                result["id"],
                "subset":            result["subset"],
                "model":             result["model"],
                "status":            result["status"],
                "validity":          result["validity"],
                "structural":        result["structural"],
                "semantic":          result["semantic"],
                "llm_judge_verdict": result["llm_judge_verdict"],
                "reference_shacl":   result.get("reference_shacl"),
                "output_shacl":      result.get("output_shacl"),
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
        "--data_root", default=".",
        help="Root directory containing subset folders (default: current directory)"
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
            "Choices: validity structural semantic judge. Default: all."
        )
    )
    mode.add_argument(
        "--only", choices=["structural", "semantic", "judge"],
        default=None,
        help="Skip validity and run only this one evaluator. Cannot be used with --run."
    )
    mode.add_argument(
        "--retry-judge", action="store_true", default=False,
        help=(
            "Retry failed judge calls in existing eval files. --input should point to "
            "the eval output folder (or a single eval file). Updates verdicts in-place."
        )
    )

    parser.add_argument(
        "--processed_dir", default=None,
        help=(
            "Folder containing _processed.jsonl files. Required when using --retry-judge. "
            "Defaults to a folder named 'processed-output' next to the eval folder."
        )
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

    # --- Retry judge mode ---
    if args.retry_judge:
        print("Mode: --retry-judge (in-place update of failed judge verdicts)")

        try:
            judge = JudgeLLM()
            print("LLM Judge initialized.")
        except Exception as e:
            print(f"[ERROR] Could not initialize LLM Judge: {e}", file=sys.stderr)
            sys.exit(1)

        # Determine processed input dir
        processed_dir = args.processed_dir
        if processed_dir is None:
            # Guess: sibling folder named after the eval folder but with 'processed-output'
            eval_folder = args.input if os.path.isdir(args.input) else os.path.dirname(args.input)
            processed_dir = os.path.join(os.path.dirname(eval_folder), "processed-output")
            if not os.path.isdir(processed_dir):
                # Try common naming patterns
                for candidate in ["processed-output", "dcat-processed-output", "invoice-processed-output"]:
                    candidate_path = os.path.join(os.path.dirname(eval_folder), candidate)
                    if os.path.isdir(candidate_path):
                        processed_dir = candidate_path
                        break
            print(f"Processed input dir: {processed_dir}")

        # Determine eval files to process
        input_path = args.input
        if os.path.isfile(input_path):
            eval_files = [input_path]
        elif os.path.isdir(input_path):
            eval_files = [
                os.path.join(input_path, f)
                for f in sorted(os.listdir(input_path))
                if f.endswith("_eval.jsonl")
            ]
            if not eval_files:
                print(f"[ERROR] No _eval.jsonl files found in: {input_path}", file=sys.stderr)
                sys.exit(1)
            print(f"Found {len(eval_files)} eval file(s).")
        else:
            print(f"[ERROR] Input path not found: {input_path}", file=sys.stderr)
            sys.exit(1)

        for eval_path in eval_files:
            retry_judge_in_file(
                eval_path=eval_path,
                processed_input_dir=processed_dir,
                data_root=args.data_root,
                judge=judge,
            )

        print("\nAll retry-judge runs complete.")
        return

    # --- Normal evaluation mode ---
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

    judge = None
    if "judge" in run:
        try:
            judge = JudgeLLM()
            print("LLM Judge initialized.")
        except Exception as e:
            print(f"[WARN] Could not initialize LLM Judge: {e}. Judge will be skipped.", file=sys.stderr)
            run = run - {"judge"}

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
            data_root=args.data_root,
            output_dir=args.output_dir,
            run=run,
            scale=args.scale,
            batch_size=args.batch_size,
            judge=judge,
            normalize=args.normalize,
        )

    print("\nAll files processed.")


if __name__ == "__main__":
    main()