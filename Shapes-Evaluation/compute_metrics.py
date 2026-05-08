"""
compute_metrics.py
------------------
Compute dataset-level evaluation metrics from evaluation output files.

Usage:
    python compute_metrics.py --input evaluation-output/
    python compute_metrics.py --input evaluation-output/invoice-dataset_gemini-3-1-pro-preview_eval.jsonl

Output:
    - Prints metrics table to terminal grouped by (subset, model)
    - Saves metrics_summary.csv to metrics-output/
    - Saves eval-logs/<filename>_log.txt for each input file

Metric definitions:
    Validity metrics   : denominator is N (all records)
    Structural metrics : denominator is N_valid (passed all validity checks)
    Semantic metrics   : denominator is N_semantic_valid (semantic status == success)
    LLM Judge          : denominator is N_judge_valid (verdict not null/error)

Semantic metric details:
    SER
        Semantic Equivalence Rate.
        Fraction of records where gt and llm violation focus node sets are identical.

    Mean_Over_Restriction_Node_Nr
        Mean raw count of focus nodes that appear in LLM violations but not GT
        (LLM over-restricts: catches violations the GT shape does not).
        Range: 0 to scale.

    Mean_Under_Restriction_Node_Nr
        Mean raw count of focus nodes that appear in GT violations but not LLM
        (LLM under-restricts: misses violations the GT shape catches).
        Range: 0 to scale.

    Mean_Over_Restriction_Rate
        Mean of (over_restriction_node_nr / gt_node_count) per record.
        Normalized version of Mean_Over_Restriction_Node_Nr. Range: 0 to 1.

    Mean_Under_Restriction_Rate
        Mean of (under_restriction_node_nr / gt_node_count) per record.
        Normalized version of Mean_Under_Restriction_Node_Nr. Range: 0 to 1.
"""

import argparse
import csv
import json
import os
import sys


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

METRICS_OUTPUT_DIR = "metrics-output"
LOG_OUTPUT_DIR     = "eval-logs"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_jsonl(path: str) -> list:
    records = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"[WARN] {path}:{lineno} — {e}", file=sys.stderr)
    return records


def parse_filename(filename: str):
    """
    Parse (subset, model) from filename.
    e.g. invoice-dataset_gemini-3-1-pro-preview_eval.jsonl
      -> ("invoice", "gemini-3-1-pro-preview")
    """
    stem = os.path.splitext(filename)[0]
    stem = stem.replace("_eval", "").replace("_processed", "")

    # Normalize: replace first occurrence of -dataset or _dataset with a
    # canonical underscore separator, so both invoice-dataset_model and
    # invoice_dataset_model are treated the same way
    import re
    stem = re.sub(r'[-_]dataset', '_dataset', stem, count=1)

    parts = stem.split("_", 1)
    if len(parts) == 2:
        subset_raw, model = parts
    else:
        subset_raw, model = stem, "unknown"

    # Strip trailing -dataset or _dataset from subset name
    subset = re.sub(r'[-_]dataset$', '', subset_raw)

    return subset, model


def safe_mean(values: list) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def safe_rate(count: int, total: int) -> float | None:
    if total == 0:
        return None
    return count / total


# ---------------------------------------------------------------------------
# Per-file log
# ---------------------------------------------------------------------------

def write_log(log_lines: list, log_path: str) -> None:
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines) + "\n")
    print(f"  Log saved to: {log_path}")


def compute_log(records: list, filename: str) -> list:
    """Compute diagnostic counts for a single file. Returns log lines."""
    n = len(records)

    # Null output
    n_null_output = sum(1 for r in records if r.get("status") == "skipped")

    # Validity buckets
    n_parsing_error = 0
    n_spec_error    = 0
    n_vocab_error   = 0
    n_vocab_valid   = 0

    for r in records:
        val = r.get("validity")
        if val is None:
            continue
        err = val.get("validation_error")
        if err is None:
            n_vocab_valid += 1
        elif err.get("failure_type") == "SyntaxError":
            n_parsing_error += 1
        elif err.get("failure_type") in ("MetaSyntaxError", "StructuralError"):
            n_spec_error += 1
        elif err.get("failure_type") == "LinterError":
            n_vocab_error += 1

    n_valid = n_vocab_valid
    n_rdf  = n - n_parsing_error
    n_spec = n_valid + n_vocab_error

    # Structural
    n_structural_failure = sum(
        1 for r in records
        if r.get("structural") and r["structural"].get("status") == "failure"
    )

    # Semantic
    n_semantic_success = sum(
        1 for r in records
        if r.get("semantic") and r["semantic"].get("status") == "success"
    )
    n_semantic_failure = sum(
        1 for r in records
        if r.get("semantic") and r["semantic"].get("status") == "failure"
    )
    n_semantic_null = sum(
        1 for r in records
        if r.get("semantic") is None and r.get("status") != "skipped"
    )

    # Semantic detail counts (only for successful records)
    n_semantic_equivalent = sum(
        1 for r in records
        if r.get("semantic") and r["semantic"].get("status") == "success"
        and r["semantic"].get("equivalent") is True
    )

    # Judge
    n_judge_equivalent     = sum(1 for r in records if r.get("llm_judge_verdict") == "equivalent")
    n_judge_not_equivalent = sum(1 for r in records if r.get("llm_judge_verdict") == "not_equivalent")
    n_judge_error          = sum(1 for r in records if r.get("llm_judge_verdict") in ("api_error", "error"))
    n_judge_null           = sum(1 for r in records if r.get("llm_judge_verdict") is None and r.get("status") != "skipped")
    n_judge_valid          = n_judge_equivalent + n_judge_not_equivalent

    lines = [
        f"=== Eval Log: {filename} ===",
        "",
        f"Total records (N):                  {n}",
        f"Skipped (null LLM output):          {n_null_output}",
        "",
        "--- Validity ---",
        f"  Parsing error  (RDF-ER):          {n_parsing_error}  / N={n}",
        f"  Spec error     (Spec-ER):          {n_spec_error}  / N_rdf={n_rdf}",
        f"  Vocab error    (Vocab-ER):         {n_vocab_error}  / N_spec={n_spec}",
        f"  Fully valid    (Vocab-VR):         {n_vocab_valid}  / N_spec={n_spec}",
        f"  N_valid:                           {n_valid}",
        "",
        "--- Structural ---",
        f"  Status=failure:                    {n_structural_failure}",
        "",
        "--- Semantic ---",
        f"  Status=success (N_semantic_valid): {n_semantic_success}",
        f"  Status=failure (gen failed):       {n_semantic_failure}",
        f"  Not run (null):                    {n_semantic_null}",
        f"  Equivalent focus nodes:            {n_semantic_equivalent}",
        "",
        "--- LLM Judge ---",
        f"  equivalent:                        {n_judge_equivalent}",
        f"  not_equivalent:                    {n_judge_not_equivalent}",
        f"  api_error / error:                 {n_judge_error}",
        f"  null (not run):                    {n_judge_null}",
        f"  N_judge_valid:                     {n_judge_valid}",
    ]
    return lines


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def compute_metrics(records: list) -> dict:
    """Compute all dataset-level metrics for a group of records."""
    n = len(records)

    # --- Validity ---
    n_parsing_error = 0
    n_spec_error    = 0
    n_vocab_error   = 0
    n_vocab_valid   = 0

    for r in records:
        val = r.get("validity")
        if val is None:
            continue
        err = val.get("validation_error")
        if err is None:
            n_vocab_valid += 1
        elif err.get("failure_type") == "SyntaxError":
            n_parsing_error += 1
        elif err.get("failure_type") in ("MetaSyntaxError", "StructuralError"):
            n_spec_error += 1
        elif err.get("failure_type") == "LinterError":
            n_vocab_error += 1

    n_valid = n_vocab_valid
    # Layered denominators per paper definition:
    # n_rdf  = records that passed RDF parsing (denominator for Spec-VR/ER)
    # n_spec = records that passed RDF + Spec check (denominator for Vocab-VR/ER)
    n_rdf  = n - n_parsing_error        # passed stage 1
    n_spec = n_valid + n_vocab_error    # passed stage 1 and 2

    # --- Structural (denominator = n_valid) ---
    iso_scores = []
    f1_scores  = []
    for r in records:
        struct = r.get("structural")
        if struct and struct.get("status") == "success":
            iso_scores.append(1 if struct.get("isomorphic") else 0)
            f1 = struct.get("similarity", {}).get("scores", {}).get("f1_score")
            if f1 is not None:
                f1_scores.append(f1)

    # --- Semantic (denominator = n_semantic_valid) ---
    # Raw counts
    over_nr_list  = []   # len(focus_nodes_only_in_llm) per record
    under_nr_list = []   # len(focus_nodes_only_in_gt)  per record
    # Normalized rates
    over_rate_list  = []
    under_rate_list = []
    # Equivalence
    eq_list = []

    for r in records:
        sem = r.get("semantic")
        if not sem or sem.get("status") != "success":
            continue

        gt_node_count = sem.get("gt_node_count") or 1

        over_nr  = len(sem.get("focus_nodes_only_in_llm") or [])
        under_nr = len(sem.get("focus_nodes_only_in_gt")  or [])

        over_nr_list.append(over_nr)
        under_nr_list.append(under_nr)
        over_rate_list.append(over_nr  / gt_node_count)
        under_rate_list.append(under_nr / gt_node_count)
        eq_list.append(1 if sem.get("equivalent") is True else 0)

    n_semantic_valid = len(eq_list)

    # --- LLM Judge (denominator = n_judge_valid) ---
    judge_scores = []
    for r in records:
        verdict = r.get("llm_judge_verdict")
        if verdict == "equivalent":
            judge_scores.append(1)
        elif verdict == "not_equivalent":
            judge_scores.append(0)

    n_judge_valid = len(judge_scores)

    return {
        # counts
        "N":                               n,
        "N_valid":                         n_valid,
        "N_semantic_valid":                n_semantic_valid,
        "N_judge_valid":                   n_judge_valid,
        # validity
        "RDF_VR":                          safe_rate(n_rdf,           n),
        "RDF_ER":                          safe_rate(n_parsing_error, n),
        "Spec_VR":                         safe_rate(n_spec,          n_rdf),
        "Spec_ER":                         safe_rate(n_spec_error,    n_rdf),
        "Vocab_VR":                        safe_rate(n_vocab_valid,   n_spec),
        "Vocab_ER":                        safe_rate(n_vocab_error,   n_spec),
        # structural
        "EMR":                             safe_mean(iso_scores),
        "PMS":                             safe_mean(f1_scores),
        # semantic
        "SER":                             safe_mean(eq_list),
        "Mean_Over_Restriction_Node_Nr":   safe_mean(over_nr_list),
        "Mean_Under_Restriction_Node_Nr":  safe_mean(under_nr_list),
        "Mean_Over_Restriction_Rate":      safe_mean(over_rate_list),
        "Mean_Under_Restriction_Rate":     safe_mean(under_rate_list),
        # judge
        "LJER":                            safe_mean(judge_scores),
    }


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def fmt(val) -> str:
    if val is None:
        return "N/A"
    if isinstance(val, float):
        return f"{val:.4f}"
    return str(val)


def print_metrics(subset: str, model: str, m: dict) -> None:
    print(f"\n  {'Subset':<12} {subset}")
    print(f"  {'Model':<12} {model}")
    print(f"  {'N':<12} {m['N']}  (N_valid={m['N_valid']}, N_sem={m['N_semantic_valid']}, N_judge={m['N_judge_valid']})")
    print(f"  {'':─<60}")
    print(f"  {'RDF-VR':<30} {fmt(m['RDF_VR'])}    RDF-ER:   {fmt(m['RDF_ER'])}")
    print(f"  {'Spec-VR':<30} {fmt(m['Spec_VR'])}    Spec-ER:  {fmt(m['Spec_ER'])}")
    print(f"  {'Vocab-VR':<30} {fmt(m['Vocab_VR'])}    Vocab-ER: {fmt(m['Vocab_ER'])}")
    print(f"  {'':─<60}")
    print(f"  {'EMR':<30} {fmt(m['EMR'])}")
    print(f"  {'PMS':<30} {fmt(m['PMS'])}")
    print(f"  {'':─<60}")
    print(f"  {'SER':<30} {fmt(m['SER'])}")
    print(f"  {'Mean-Over-Restriction-Node-Nr':<30} {fmt(m['Mean_Over_Restriction_Node_Nr'])}")
    print(f"  {'Mean-Under-Restriction-Node-Nr':<30} {fmt(m['Mean_Under_Restriction_Node_Nr'])}")
    print(f"  {'Mean-Over-Restriction-Rate':<30} {fmt(m['Mean_Over_Restriction_Rate'])}")
    print(f"  {'Mean-Under-Restriction-Rate':<30} {fmt(m['Mean_Under_Restriction_Rate'])}")
    print(f"  {'':─<60}")
    print(f"  {'LJER':<30} {fmt(m['LJER'])}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Compute dataset-level metrics from evaluation outputs.")
    parser.add_argument(
        "--input", required=True,
        help="Path to a single eval .jsonl file or a folder of eval .jsonl files"
    )
    parser.add_argument(
        "--output_dir", default=METRICS_OUTPUT_DIR,
        help=f"Directory to save metrics_summary.csv (default: {METRICS_OUTPUT_DIR})"
    )
    parser.add_argument(
        "--log_dir", default=LOG_OUTPUT_DIR,
        help=f"Directory to save per-file logs (default: {LOG_OUTPUT_DIR})"
    )
    return parser.parse_args()


def main():
    args = parse_args()

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
    else:
        print(f"[ERROR] Input path not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)
    os.makedirs(args.log_dir,    exist_ok=True)

    print(f"Found {len(input_files)} file(s).\n")

    all_rows = []
    csv_fieldnames = [
        "subset", "model",
        "N", "N_valid", "N_semantic_valid", "N_judge_valid",
        "RDF_VR", "RDF_ER",
        "Spec_VR", "Spec_ER",
        "Vocab_VR", "Vocab_ER",
        "EMR", "PMS",
        "SER",
        "Mean_Over_Restriction_Node_Nr",
        "Mean_Under_Restriction_Node_Nr",
        "Mean_Over_Restriction_Rate",
        "Mean_Under_Restriction_Rate",
        "LJER",
    ]

    for filepath in input_files:
        filename = os.path.basename(filepath)
        subset, model = parse_filename(filename)

        print(f"{'='*60}")
        print(f"File: {filename}")

        records = load_jsonl(filepath)

        # Write per-file log
        log_lines = compute_log(records, filename)
        log_path  = os.path.join(args.log_dir, filename.replace(".jsonl", "_log.txt"))
        write_log(log_lines, log_path)

        # Compute and print metrics
        m = compute_metrics(records)
        print_metrics(subset, model, m)

        all_rows.append({"subset": subset, "model": model, **m})

    # Save CSV
    csv_path = os.path.join(args.output_dir, "metrics_summary.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\n{'='*60}")
    print(f"Metrics summary saved to: {csv_path}")
    print("Done.")


if __name__ == "__main__":
    main()