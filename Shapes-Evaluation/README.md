# Shapes Evaluation

This module evaluates the quality of translated SHACL shapes against reference shapes. It takes post-processed LLM outputs and computes metrics across three dimensions: validity, structural similarity, and semantic equivalence.

The module consists of three scripts, which share `filename_utils.py` for reading the subset and model from file names. An optional logical evaluator (`logical_equivalence.py`) decides equivalence with SHACL2FOL and the Vampire theorem prover; see [Logical equivalence](#logical-equivalence-ler).

---

## Requirements

Python 3.10 or newer.

```bash
pip install rdflib pyshacl rdf-graph-gen requests
```

The logical evaluator (`--run logical`) is optional and additionally needs Java 21 or newer, SHACL2FOL and Vampire; see [Logical equivalence](#logical-equivalence-ler).

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

The semantic metrics assess whether the generated and reference shapes enforce the same constraints. They approach this from two complementary angles.

| Metric | Description |
|--------|-------------|
| **ASER** | Approximate Semantic Equivalence Rate — proportion of records where the sets of violating focus nodes are identical for both shapes on a synthetic RDF data graph generated with RDFGraphGen. Only records for which a data graph could be generated are counted. |
| **LER** | Logical Equivalence Rate — proportion of equivalent records among those with a definite logical outcome. Each pair of shapes graphs is translated to first-order logic with SHACL2FOL and both containment directions are decided with Vampire. Opt-in, see [Logical equivalence](#logical-equivalence-ler). |
| **LER coverage** | Proportion of the records for which the logical check was attempted that obtained a definite outcome (equivalent or non-equivalent). The rest is *undetermined* (timeout, or constructs SHACL2FOL cannot handle). |

ASER is a sampling-based approximation; LER is a proof that holds on all data graphs but is only available where SHACL2FOL and the prover succeed. Their limitations are disjoint, so both are worth reporting. Records that could not be evaluated are left out of both the numerator and the denominator of a rate, so each rate describes only the records that were evaluated. How many that are is reported next to it: `N_semantic_valid` for ASER, and `LER coverage` and the undetermined count for LER.

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

# Also decide logical equivalence (opt-in; needs Java 21+, SHACL2FOL and Vampire)
python run_evaluation.py --input processed-output/ --run logical --ler-timeout 120
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--input` | (required) | Path to a single `.jsonl` file or a folder |
| `--data_root` | `.` | Root directory containing subset folders |
| `--output_dir` | `evaluation-output/` | Output directory |
| `--run` | `validity structural semantic` | Evaluators to run: `validity`, `structural`, `semantic`, `logical`. `logical` is only run when requested. |
| `--only` | none | Skip validity and run only one evaluator: `structural`, `semantic` or `logical` |
| `--scale` | `10` | Scale factor for RDFGraphGen synthetic data generation |
| `--ler-timeout` | `60` | Time budget in seconds of each of the two containment checks of the logical evaluator |
| `--shacl2fol-jar`, `--prover`, `--java` | see below | Locations of `SHACL2FOL.jar`, the Vampire executable and a Java 21+ executable |
| `--ler-strip-annotations` | off | Remove labels, descriptions and similar annotations from both shapes graphs before the logical check |
| `--ler-no-component-check` | off | Do not report records as undetermined when the graphs differ in constraints that SHACL2FOL ignores (see below) |

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
  "semantic": { ... },
  "logical": { ... }
}
```

`logical` is `null` unless the logical evaluator was run. If a run is interrupted, re-running will skip already-processed records and resume from where it left off. To add the logical evaluator to files that were already evaluated, write to a new `--output_dir`.

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

## Logical equivalence (LER)

The logical evaluator decides whether a translated shapes graph is equivalent to the reference for **all** data graphs. Equivalence is checked as two containments, `reference ⊑ output` and `output ⊑ reference`, each translated to first-order logic by [SHACL2FOL](https://github.com/paolo7/SHACL2FOL) and decided by the [Vampire](https://vprover.github.io/) theorem prover. Checking both directions also shows what is wrong with a non-equivalent output.

### Setup

1. **Java 21 or newer** (not installed by the script), for example `conda create -n shacl2fol -c conda-forge openjdk=21`, and `export JAVA_BIN=<that java>` (or put it first on `PATH`).
2. **SHACL2FOL.jar and Vampire 4.9**, installed into `tools/bin/` (about 46 MB, Linux x86-64) at a fixed SHACL2FOL commit and checked against SHA-256 checksums:

```bash
bash tools/setup_logical_tools.sh                # download
bash tools/setup_logical_tools.sh --from <dir>   # copy SHACL2FOL.jar and vampire from a folder (no internet needed)
```

The tools are looked up from the command-line arguments, then from the environment variables `SHACL2FOL_JAR`, `VAMPIRE_PATH` and `JAVA_BIN`, then from `tools/bin/` and the usual Java locations (`PATH`, `JAVA_HOME`, `CONDA_PREFIX`). If Vampire needs a newer `libstdc++`, set `VAMPIRE_LD_LIBRARY_PATH` (for example to `$CONDA_PREFIX/lib`). If a requested tool is missing, `run_evaluation.py --run logical` stops with a message that says which one.

### Result of one record

```json
"logical": {
  "status": "success",
  "equivalent": false,
  "direction": "output_stricter",
  "ref_contained_in_out": false,
  "out_contained_in_ref": true,
  "reason": null,
  "unsupported_components": [],
  "time_s": 1.13
}
```

| Outcome | Meaning |
|---------|---------|
| `equivalent` | Both containments hold. |
| non-equivalent, `direction: output_stricter` | `reference ⊑ output` is refuted: some data graph is valid for the reference but not for the output (for example an extra `sh:minCount`). |
| non-equivalent, `direction: output_more_permissive` | `output ⊑ reference` is refuted: the output accepts graphs the reference rejects (for example a dropped constraint). |
| non-equivalent, `direction: incomparable` | Both are refuted (for example a different target class). |
| `undetermined` (`status: "undetermined"`) | No refutation, but a direction could not be decided. `reason` is `timeout`, `prover_gave_up`, `unsupported:<exception>` (SHACL2FOL cannot handle the shapes graph), `unsupported_components_differ:<components>` (see below), or `error:<...>`. |

`status` is `unavailable` if the tools are missing when the function is called directly.

The time budget `--ler-timeout` applies to each of the two containment checks. Within the budget SHACL2FOL searches for a finite counterexample with Vampire, so large cardinalities and some combinations of qualified counts can end as `timeout`.

### Metrics

`compute_metrics.py` and `compute_metrics_by_model.py` add these columns when the logical evaluator was run, and `N/A` otherwise:

| Column | Description |
|--------|-------------|
| `N_logical` | Records for which the logical check was attempted |
| `N_logical_equivalent`, `N_logical_non_equivalent`, `N_logical_undetermined` | Outcome counts |
| `LER` | `N_logical_equivalent / (N_logical_equivalent + N_logical_non_equivalent)` |
| `LER_coverage` | `(N_logical_equivalent + N_logical_non_equivalent) / N_logical` |
| `N_output_stricter`, `N_output_more_permissive`, `N_incomparable` | Non-equivalent records, by direction of the mismatch |

### Things to know

- **Shapes with the same name.** SHACL2FOL puts both shapes graphs into one logical theory, so a shape that has the same IRI in the reference and in the output would be merged with its counterpart and hide differences. The evaluator therefore renames the shapes of the output before the check (classes, properties and other IRIs are untouched).
- **Constraints that SHACL2FOL ignores.** SHACL2FOL does not implement `sh:datatype`, the value ranges (`sh:minInclusive`, `sh:maxInclusive`, `sh:minExclusive`, `sh:maxExclusive`), the string constraints (`sh:minLength`, `sh:maxLength`, `sh:pattern`, `sh:flags`) and `sh:languageIn`. It ignores them silently, so two shapes that differ only in these would be reported as equivalent. To avoid this, a record whose two graphs do not carry the same such constraints on the same paths is reported as `undetermined` with reason `unsupported_components_differ:<components>`. Records that differ in these constraints and also in a supported one are still reported as non-equivalent. Use `--ler-no-component-check` to get the plain result of SHACL2FOL.
- **Unsupported shapes graphs.** For some shapes graphs SHACL2FOL fails with an exception (for example a property shape with several `sh:path` values); these records are `undetermined` with reason `unsupported:<exception>`.
- **Annotations.** `--ler-strip-annotations` removes labels, descriptions, names and similar annotations before the check. They do not change validation, but whether this makes the prover faster has not been established.

### Tests

```bash
python -m unittest discover -s tests -v
```

The tests that call SHACL2FOL and Vampire are skipped when the tools are not installed.

---

## Notes

- Structural metrics are computed only for records that pass all three validity checks.
- Semantic evaluation uses RDFGraphGen to generate a synthetic RDF data graph from the reference shape. Records where graph generation fails (e.g. due to complex logical constraints such as `sh:not` or `sh:xone`) are marked as `failure` in the semantic status field and excluded from ASER computation.
- The `evaluators.py` file implements all metric logic and is imported by `run_evaluation.py`. It does not need to be run directly.