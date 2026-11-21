"""
compute_metrics_by_model.py
---------------------------
Compute model-level evaluation metrics aggregated across all subsets.

Usage:
    python compute_metrics_by_model.py --input evaluation-output/
    python compute_metrics_by_model.py --input evaluation-output/invoice-dataset_my-model_eval.jsonl
    python compute_metrics_by_model.py --input evaluation-output/ --model-aliases model_aliases.json

Model names:
    The model of each file is taken from its name (<subset>-dataset_<model>_eval.jsonl)
    and used as it is. Files of the same model but with different spellings can be
    merged with --model-aliases, a JSON file such as
        {"anthropic/claude-opus-4.7": ["anthropic-claude-opus-4-7", "claude-opus-4-7"]}
    Aliases are compared ignoring case and the separators "-", "_", "." and "/".

Output:
    - Prints metrics table to terminal grouped by model (all subsets combined)
    - Saves metrics_by_model_summary.csv to metrics-output/
    - Saves eval-logs/<model>_model_log.txt for each model (characters that are not
      allowed in file names, such as "/", are replaced by "_")

Metric definitions:
    Validity metrics   : layered denominators (each VR+ER pair sums to 1)
        RDF_VR / RDF_ER    : denominator is N (all records)
        Spec_VR / Spec_ER  : denominator is N_rdf_reached (passed RDF parsing)
        Vocab_VR / Vocab_ER: denominator is N_vocab_reached (passed RDF + Spec)
    Structural metrics : denominator is N_valid (passed all validity checks)
    Semantic metrics   : denominator is N_semantic_valid (semantic status == success)
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict

from filename_utils import parse_filename as _parse_filename, short_subset


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


def _alias_key(name: str) -> str:
    """Comparison key of a model name: lower case, "-", "_", "." and "/" are equivalent."""
    return re.sub(r"[-_./\s]+", "-", name.strip().lower())


def load_aliases(path):
    """
    Load a model alias file: {"canonical name": ["alias", ...], ...}.
    Returns {alias_key: canonical name}. Returns {} when no file is given.
    """
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[ERROR] Cannot read model alias file '{path}': {e}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(data, dict):
        print(f"[ERROR] Model alias file '{path}' must contain a JSON object "
              f'such as {{"canonical name": ["alias", ...]}}.', file=sys.stderr)
        sys.exit(1)

    alias_map = {}
    for canonical, aliases in data.items():
        if isinstance(aliases, str):
            aliases = [aliases]
        alias_map[_alias_key(canonical)] = canonical
        for alias in aliases:
            alias_map[_alias_key(str(alias))] = canonical
    return alias_map


def normalize_model_name(raw: str, alias_map: dict) -> str:
    """Return the canonical name of a model if it is listed in the aliases, else the name itself."""
    return alias_map.get(_alias_key(raw), raw)


def parse_filename(filename: str, alias_map: dict = None):
    """
    Parse (subset, model) from filename. The subset is shown without "-dataset"
    and the model is mapped through the optional aliases.

    e.g. dcat-dataset_my-model_eval.jsonl -> ("dcat", "my-model")
    """
    subset, model = _parse_filename(filename)
    return short_subset(subset), normalize_model_name(model, alias_map or {})


def safe_filename(name: str) -> str:
    """Make a model name usable as a file name on every platform."""
    return re.sub(r"[^\w.\-]+", "_", name).strip("_") or "model"


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

    eq_list = []
    for r in records:
        sem = r.get("semantic")
        if not sem or sem.get("status") != "success":
            continue
        eq_list.append(1 if sem.get("equivalent") is True else 0)

    n_semantic_valid = len(eq_list)

    # Layered denominators so each VR+ER pair sums to 1
    n_rdf_reached   = n
    n_spec_reached  = n - n_parsing_error
    n_vocab_reached = n - n_parsing_error - n_spec_error

    return {
        "N":                               n,
        "N_valid":                         n_valid,
        "N_semantic_valid":                n_semantic_valid,
        "RDF_VR":                          safe_rate(n - n_parsing_error,           n_rdf_reached),
        "RDF_ER":                          safe_rate(n_parsing_error,               n_rdf_reached),
        "Spec_VR":                         safe_rate(n_vocab_valid + n_vocab_error,  n_spec_reached),
        "Spec_ER":                         safe_rate(n_spec_error,                  n_spec_reached),
        "Vocab_VR":                        safe_rate(n_vocab_valid,                 n_vocab_reached),
        "Vocab_ER":                        safe_rate(n_vocab_error,                 n_vocab_reached),
        "EMR":                             safe_mean(iso_scores),
        "PMS":                             safe_mean(f1_scores),
        "SER":                             safe_mean(eq_list),
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
    print(f"  {'N':<12} {m['N']}  (N_valid={m['N_valid']}, N_sem={m['N_semantic_valid']})")
    print(f"  {'':─<60}")
    print(f"  {'RDF-VR':<30} {fmt(m['RDF_VR'])}    RDF-ER:   {fmt(m['RDF_ER'])}")
    print(f"  {'Spec-VR':<30} {fmt(m['Spec_VR'])}    Spec-ER:  {fmt(m['Spec_ER'])}")
    print(f"  {'Vocab-VR':<30} {fmt(m['Vocab_VR'])}    Vocab-ER: {fmt(m['Vocab_ER'])}")
    print(f"  {'':─<60}")
    print(f"  {'EMR':<30} {fmt(m['EMR'])}")
    print(f"  {'PMS':<30} {fmt(m['PMS'])}")
    print(f"  {'':─<60}")
    print(f"  {'SER':<30} {fmt(m['SER'])}")


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
        "--model-aliases", default=None,
        help="Optional JSON file {\"canonical name\": [\"alias\", ...]} to merge different "
             "spellings of the same model name"
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
    alias_map = load_aliases(args.model_aliases)

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
        subset, model = parse_filename(filename, alias_map)
        records = load_jsonl(filepath)
        model_records[model].extend(records)
        model_subsets[model].add(subset)
        print(f"Loaded {len(records):>4} records from {filename}  (subset={subset}, model={model})")

    print()

    all_rows = []
    csv_fieldnames = [
        "model", "subsets",
        "N", "N_valid", "N_semantic_valid",
        "RDF_VR", "RDF_ER",
        "Spec_VR", "Spec_ER",
        "Vocab_VR", "Vocab_ER",
        "EMR", "PMS",
        "SER",
    ]

    for model in sorted(model_records.keys()):
        records = model_records[model]
        subsets = model_subsets[model]

        print(f"{'='*60}")
        print(f"Model: {model}")

        log_lines = compute_log(records, model, subsets)
        log_path  = os.path.join(args.log_dir, f"{safe_filename(model)}_model_log.txt")
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