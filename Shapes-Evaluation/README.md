# Shapes Evaluation

This module evaluates the quality of translated SHACL shapes against reference shapes. It takes post-processed LLM outputs and computes metrics across three dimensions: validity, structural similarity, and semantic equivalence.

The module consists of three scripts, which share `filename_utils.py` for reading the subset and model from file names.

---

## Requirements

Python 3.10 or newer.

```bash
pip install rdflib pyshacl rdf-graph-gen requests
```

---

## File names

The scripts read the **subset** and the **model** of an evaluation file from its name:

```
<subset>-dataset_<model>_processed.jsonl     (input of run_evaluation.py)
<subset>-dataset_<model>_eval.jsonl          (output of run_evaluation.py)
```

For example, `snik-dataset_my-system_processed.jsonl` is evaluated as subset `snik-dataset` and model `my-system`.

- Everything up to and including the first `-dataset` that is followed by `_` is the subset; the rest is the model. Hyphens and underscores are allowed in both parts, e.g. `my_data-dataset_rule_based_processed.jsonl`.
- A trailing `_processed` or `_eval` is ignored.
- If the name does not contain `-dataset_`, it is split at the first `_` into `<subset>_<model>`; if it contains no `_`, the model is `unknown`. A file name never causes an error.
- The summary tables show the subset without the `-dataset` suffix (`snik`); the evaluation records keep the full `snik-dataset`.

Names are used as they are: the model `gemini-2-5-pro` and the model `gemini-3-1-pro-preview` are two different models. To merge several spellings of one model, see `--model-aliases` of [`compute_metrics_by_model.py`](#compute_metrics_by_modelpy).

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

Aggregates metrics across subsets, reporting results per model. Useful for comparing overall model performance. It reports the same metrics as `compute_metrics.py`, but pools the records of all subsets for each model.

```bash
# Aggregate all eval files in a folder
python compute_metrics_by_model.py --input evaluation-output/

# Aggregate a single file
python compute_metrics_by_model.py --input evaluation-output/invoice-dataset_claude-opus-4-7_eval.jsonl

# Merge different spellings of the same model name
python compute_metrics_by_model.py --input evaluation-output/ --model-aliases model_aliases.json
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--input` | (required) | Path to a single eval `.jsonl` file or a folder |
| `--model-aliases` | none | JSON file that merges different spellings of one model name (see below) |
| `--output_dir` | `metrics-output/` | Directory to save the CSV summary |
| `--log_dir` | `eval-logs/` | Directory to save per-model diagnostic logs |

**Outputs:**

- `metrics-output/metrics_by_model_summary.csv` — one row per model, with the list of pooled subsets
- `eval-logs/<model>_model_log.txt` — per-model diagnostic counts across all subsets (characters that are not allowed in file names, such as the `/` in an alias name, are replaced by `_`, e.g. `anthropic_claude-opus-4.7_model_log.txt`)

**Model names.** The model is read from the file name (see [File names](#file-names)) and used as it is, so files with the same model name are pooled and files with different names are reported separately.

If the same model appears under several spellings (for example because `/` and `.` in `anthropic/claude-opus-4.7` were replaced when the file was written), list them in an alias file:

```json
{
  "anthropic/claude-opus-4.7": ["anthropic-claude-opus-4-7", "claude-opus-4-7"]
}
```

The keys are the names to report; the values are the spellings to merge into them. Names are compared ignoring case and the separators `-`, `_`, `.` and `/`, so `anthropic_claude_opus_4.7` is also matched. Models that are not listed keep their own name.

---

## Notes

- Structural metrics are computed only for records that pass all three validity checks.
- Semantic evaluation uses RDFGraphGen to generate a synthetic RDF data graph from the reference shape. Records where graph generation fails (e.g. due to complex logical constraints such as `sh:not` or `sh:xone`) are marked as `failure` in the semantic status field and excluded from SER computation.
- The `evaluators.py` file implements all metric logic and is imported by `run_evaluation.py`. It does not need to be run directly.