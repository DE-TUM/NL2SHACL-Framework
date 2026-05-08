"""
compute_metrics_by_model.py
---------------------------
Compute model-level evaluation metrics aggregated across all subsets.

Usage:
    python compute_metrics_by_model.py --input evaluation-output/
    python compute_metrics_by_model.py --input evaluation-output/invoice-dataset_gemini-3-1-pro-preview_eval.jsonl

Output:
    - Prints metrics table to terminal grouped by model (all subsets combined)
    - Saves metrics_by_model_summary.csv to metrics-output/
    - Saves eval-logs/<model>_model_log.txt for each model

Metric definitions:
    Validity metrics   : layered denominators (each VR+ER pair sums to 1)
        RDF_VR / RDF_ER    : denominator is N (all records)
        Spec_VR / Spec_ER  : denominator is N_rdf_reached (passed RDF parsing)
        Vocab_VR / Vocab_ER: denominator is N_vocab_reached (passed RDF + Spec)
    Structural metrics : denominator is N_valid (passed all validity checks)
    Semantic metrics   : denominator is N_semantic_valid (semantic status == success)
    LLM Judge          : denominator is N_judge_valid (verdict not null/error)
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict


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
                print(f"[WARN] {path}:{lineno} - {e}", file=sys.stderr)
    return records


def normalize_model_name(raw: str) -> str:
    """
    Normalize the model fragment extracted from a filename to one of the four
    canonical model identifiers, regardless of separator or punctuation variants.

    Canonical names:
        "anthropic/claude-opus-4.7"
        "z-ai/glm-5.1"
        "qwen/qwen3.5-397b-a17b"
        "google/gemini-3.1-pro-preview"

    Examples of variants handled:
        anthropic-claude-opus-4-7   -> anthropic/claude-opus-4.7
        anthropic_claude_opus_4.7   -> anthropic/claude-opus-4.7
        z_ai_glm-5-1                -> z-ai/glm-5.1
        z-ai-glm-5.1                -> z-ai/glm-5.1
        qwen-qwen3-5-397b-a17b      -> qwen/qwen3.5-397b-a17b
        qwen_qwen3.5_397b_a17b      -> qwen/qwen3.5-397b-a17b
        gemini-3-1-pro-preview      -> google/gemini-3.1-pro-preview
        gemini_3.1_pro_preview      -> google/gemini-3.1-pro-preview
    """
    # Normalize to lowercase and replace underscores with hyphens for matching
    s = raw.lower().replace("_", "-")

    if "claude" in s and ("opus" in s or "claude-opus" in s):
        return "anthropic/claude-opus-4.7"

    if "glm" in s:
        return "z-ai/glm-5.1"

    if "qwen" in s:
        return "qwen/qwen3.5-397b-a17b"

    if "gemini" in s:
        return "google/gemini-3.1-pro-preview"

    # Fall back to the raw string if nothing matches
    print(f"[WARN] Could not normalize model name: '{raw}', keeping as-is.", file=sys.stderr)
    return raw


def parse_filename(filename: str):
    """
    Parse (subset, model) from filename and normalize the model name.

    e.g. dcat-dataset_qwen-qwen3-5-397b-a17b.jsonl
      -> ("dcat-dataset", "qwen/qwen3.5-397b-a17b")

    e.g. dbpedia-dataset_anthropic-claude-opus-4-7.jsonl
      -> ("dbpedia-dataset", "anthropic/claude-opus-4.7")
    """
    stem  = os.path.splitext(filename)[0]
    stem  = stem.replace("_eval", "").replace("_processed", "")
    parts = stem.split("_", 1)
    if len(parts) == 2:
        subset, raw_model = parts[0], parts[1]
    else:
        subset, raw_model = stem, "unknown"
    return subset, normalize_model_name(raw_model)


def safe_mean(values: list):
    if not values:
        return None
    return sum(values) / len(values)


def safe_rate(count: int, total: int):
    if total == 0:
        return None
    return count / total


# ---------------------------------------------------------------------------
# Per-model log
# ---------------------------------------------------------------------------

def write_log(log_lines: list, log_path: str) -> None:
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines) + "\n")
    print(f"  Log saved to: {log_path}")


def compute_log(records: list, model: str, subsets: list) -> list:
    """Compute diagnostic counts for a model across all subsets. Returns log lines."""
    n = len(records)

    n_null_output   = sum(1 for r in records if r.get("status") == "skipped")

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

    n_structural_failure = sum(
        1 for r in records
        if r.get("structural") and r["structural"].get("status") == "failure"
    )

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
    n_semantic_equivalent = sum(
        1 for r in records
        if r.get("semantic") and r["semantic"].get("status") == "success"
        and r["semantic"].get("equivalent") is True
    )

    n_judge_equivalent     = sum(1 for r in records if r.get("llm_judge_verdict") == "equivalent")
    n_judge_not_equivalent = sum(1 for r in records if r.get("llm_judge_verdict") == "not_equivalent")
    n_judge_error          = sum(1 for r in records if r.get("llm_judge_verdict") in ("api_error", "error"))
    n_judge_null           = sum(1 for r in records if r.get("llm_judge_verdict") is None and r.get("status") != "skipped")
    n_judge_valid          = n_judge_equivalent + n_judge_not_equivalent

    lines = [
        f"=== Model Log: {model} ===",
        f"Subsets included: {', '.join(sorted(subsets))}",
        "",
        f"Total records (N):                  {n}",
        f"Skipped (null LLM output):          {n_null_output}",
        "",
        "--- Validity ---",
        f"  Parsing error  (RDF-ER):          {n_parsing_error}",
        f"  Spec error     (Spec-ER):          {n_spec_error}",
        f"  Vocab error    (Vocab-ER):         {n_vocab_error}",
        f"  Fully valid    (Vocab-VR):         {n_vocab_valid}",
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

    iso_scores = []
    f1_scores  = []
    for r in records:
        struct = r.get("structural")
        if struct and struct.get("status") == "success":
            iso_scores.append(1 if struct.get("isomorphic") else 0)
            f1 = struct.get("similarity", {}).get("scores", {}).get("f1_score")
            if f1 is not None:
                f1_scores.append(f1)

    over_nr_list    = []
    under_nr_list   = []
    over_rate_list  = []
    under_rate_list = []
    eq_list         = []

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

    judge_scores = []
    for r in records:
        verdict = r.get("llm_judge_verdict")
        if verdict == "equivalent":
            judge_scores.append(1)
        elif verdict == "not_equivalent":
            judge_scores.append(0)

    n_judge_valid = len(judge_scores)

    # Layered denominators so each VR+ER pair sums to 1
    n_rdf_reached   = n
    n_spec_reached  = n - n_parsing_error
    n_vocab_reached = n - n_parsing_error - n_spec_error

    return {
        "N":                               n,
        "N_valid":                         n_valid,
        "N_semantic_valid":                n_semantic_valid,
        "N_judge_valid":                   n_judge_valid,
        "RDF_VR":                          safe_rate(n - n_parsing_error,           n_rdf_reached),
        "RDF_ER":                          safe_rate(n_parsing_error,               n_rdf_reached),
        "Spec_VR":                         safe_rate(n_vocab_valid + n_vocab_error,  n_spec_reached),
        "Spec_ER":                         safe_rate(n_spec_error,                  n_spec_reached),
        "Vocab_VR":                        safe_rate(n_vocab_valid,                 n_vocab_reached),
        "Vocab_ER":                        safe_rate(n_vocab_error,                 n_vocab_reached),
        "EMR":                             safe_mean(iso_scores),
        "PMS":                             safe_mean(f1_scores),
        "SER":                             safe_mean(eq_list),
        "Mean_Over_Restriction_Node_Nr":   safe_mean(over_nr_list),
        "Mean_Under_Restriction_Node_Nr":  safe_mean(under_nr_list),
        "Mean_Over_Restriction_Rate":      safe_mean(over_rate_list),
        "Mean_Under_Restriction_Rate":     safe_mean(under_rate_list),
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


def print_metrics(model: str, subsets: list, m: dict) -> None:
    print(f"\n  {'Model':<12} {model}")
    print(f"  {'Subsets':<12} {', '.join(sorted(subsets))}")
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
    parser = argparse.ArgumentParser(description="Compute model-level metrics aggregated across all subsets.")
    parser.add_argument(
        "--input", required=True,
        help="Path to a single eval .jsonl file or a folder of eval .jsonl files"
    )
    parser.add_argument(
        "--output_dir", default=METRICS_OUTPUT_DIR,
        help=f"Directory to save metrics_by_model_summary.csv (default: {METRICS_OUTPUT_DIR})"
    )
    parser.add_argument(
        "--log_dir", default=LOG_OUTPUT_DIR,
        help=f"Directory to save per-model logs (default: {LOG_OUTPUT_DIR})"
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

    # Group records by model across all subsets
    model_records  = defaultdict(list)   # model -> [records]
    model_subsets  = defaultdict(set)    # model -> {subset, ...}

    for filepath in input_files:
        filename = os.path.basename(filepath)
        subset, model = parse_filename(filename)
        records = load_jsonl(filepath)
        model_records[model].extend(records)
        model_subsets[model].add(subset)
        print(f"Loaded {len(records):>4} records from {filename}  (subset={subset}, model={model})")

    print()

    all_rows = []
    csv_fieldnames = [
        "model", "subsets",
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

    for model in sorted(model_records.keys()):
        records = model_records[model]
        subsets = model_subsets[model]

        print(f"{'='*60}")
        print(f"Model: {model}")

        log_lines = compute_log(records, model, subsets)
        log_path  = os.path.join(args.log_dir, f"{model}_model_log.txt")
        write_log(log_lines, log_path)

        m = compute_metrics(records)
        print_metrics(model, list(subsets), m)

        all_rows.append({
            "model":   model,
            "subsets": "|".join(sorted(subsets)),
            **m,
        })

    csv_path = os.path.join(args.output_dir, "metrics_by_model_summary.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\n{'='*60}")
    print(f"Model-level metrics summary saved to: {csv_path}")
    print("Done.")


if __name__ == "__main__":
    main()