# Shapes Evaluation

This module evaluates the quality of translated SHACL shapes against reference shapes. It takes post-processed LLM outputs and computes metrics across three dimensions: validity, structural similarity, and semantic equivalence.

The module consists of three scripts.

---

## Requirements

```bash
pip install rdflib pyshacl rdf-graph-gen requests
```

---

## Pipeline Overview

```
processed-output/<subset>_<model>_processed.jsonl
      │
      ▼
1. run_evaluation.py     →  evaluation-output/<subset>_<model>_eval.jsonl
      │
      ▼
2. compute_metrics.py    →  metrics-output/metrics_summary.csv
                            eval-logs/<subset>_<model>_eval_log.txt

   compute_metrics_by_model.py  →  metrics aggregated across subsets per model
```

---

## Metrics

### Validity

Validity is assessed as a three-stage layered pipeline. Each stage is only reached if the previous one passes.

| Metric | Description |
|--------|-------------|
| **RDF-VR** | Proportion of outputs that parse as valid RDF graphs |
| **Spec-VR** | Proportion of RDF-valid outputs that conform to the SHACL specification |
| **Vocab-VR** | Proportion of spec-valid outputs that use only standard SHACL constraint components |

### Structural

Structural metrics compare the generated and reference shapes at the triple level after graph canonicalization.

| Metric | Description |
|--------|-------------|
| **EMR** | Exact Matching Rate — proportion of records where the generated and reference shapes are structurally identical |
| **PMS** | Partial Matching Score — macro-averaged F1 score over triple-level precision and recall |

### Semantic

The semantic metric assesses whether the generated and reference shapes enforce the same constraints by comparing their validation behavior on a synthetic RDF data graph generated with RDFGraphGen.

| Metric | Description |
|--------|-------------|
| **SER** | Semantic Equivalence Rate — proportion of records where the sets of violating focus nodes are identical for both shapes |

---

## Scripts

### Step 1: run_evaluation.py

Runs evaluation metrics on post-processed output files. Accepts a single file or a folder of files.

```bash
# Run all evaluators on a single file
python run_evaluation.py --input processed-output/invoice-dataset_claude-opus-4-7_processed.jsonl

# Run all evaluators on all files in a folder
python run_evaluation.py --input processed-output/

# Run only validity and structural
python run_evaluation.py --input processed-output/ --run validity structural

# Run only semantic (skip validity)
python run_evaluation.py --input processed-output/ --only semantic
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--input` | (required) | Path to a single `.jsonl` file or a folder |
| `--data_root` | `.` | Root directory containing subset folders |
| `--output_dir` | `evaluation-output/` | Output directory |
| `--run` | all | Evaluators to run: `validity`, `structural`, `semantic` |
| `--only` | none | Skip validity and run only one evaluator |
| `--scale` | `10` | Scale factor for RDFGraphGen synthetic data generation |

**Output** (`evaluation-output/<subset>_<model>_eval.jsonl`, one record per line):

```json
{
  "id": "invoice-6",
  "subset": "invoice-dataset",
  "model": "claude-opus-4-7",
  "status": "evaluated",
  "validity": {
    "parsing_valid": true,
    "spec_valid": true,
    "vocab_valid": true,
    "validation_error": null
  },
  "structural": { ... },
  "semantic": { ... }
}
```

If a run is interrupted, re-running will skip already-processed records and resume from where it left off.

---

### Step 2: compute_metrics.py

Aggregates evaluation results into dataset-level metrics, grouped by subset and model.

```bash
# Compute metrics for all eval files in a folder
python compute_metrics.py --input evaluation-output/

# Compute metrics for a single file
python compute_metrics.py --input evaluation-output/invoice-dataset_claude-opus-4-7_eval.jsonl
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--input` | (required) | Path to a single eval `.jsonl` file or a folder |
| `--output_dir` | `metrics-output/` | Directory to save the CSV summary |
| `--log_dir` | `eval-logs/` | Directory to save per-file diagnostic logs |

**Outputs:**

- `metrics-output/metrics_summary.csv` — all metrics for all (subset, model) combinations
- `eval-logs/<filename>_log.txt` — per-file diagnostic counts and validity breakdown

---

### compute_metrics_by_model.py

Aggregates metrics across subsets, reporting results per model. Useful for comparing overall model performance.

```bash
python compute_metrics_by_model.py --input evaluation-output/
```

---

## Notes

- Structural metrics are computed only for records that pass all three validity checks.
- Semantic evaluation uses RDFGraphGen to generate a synthetic RDF data graph from the reference shape. Records where graph generation fails (e.g. due to complex logical constraints such as `sh:not` or `sh:xone`) are marked as `failure` in the semantic status field and excluded from SER computation.
- The `evaluators.py` file implements all metric logic and is imported by `run_evaluation.py`. It does not need to be run directly.