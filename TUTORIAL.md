# End-to-End Tutorial

This tutorial walks through the complete NL2SHACL-Bench pipeline, from raw SHACL shapes to a benchmark evaluation. It uses the example data in `examples/my-dataset/` and produces a dataset in the same format as `examples/example-nl2shacl-dataset/`.

The pipeline has two main stages:

- **Stage 1: Dataset Construction** — extract shape fragments, generate natural language descriptions, and produce a structured dataset.
- **Stage 2: Translation and Evaluation** — run a translation system on the dataset and evaluate the results.

---

## Prerequisites

Python 3.10 or newer.

```bash
pip install rdflib pyshacl requests rdf-graph-gen
pip install google-genai  # for Stage 1 description generation
```

Set your Gemini API key as an environment variable:

```bash
# Windows
set GEMINI_API_KEY=your_api_key_here

# macOS / Linux
export GEMINI_API_KEY=your_api_key_here
```

Clone the framework and the dataset:

```bash
git clone https://github.com/DE-TUM/NL2SHACL-Framework
cd NL2SHACL-Framework
git clone https://github.com/DE-TUM/NL2SHACL-Dataset dataset
```

---

## Stage 1: Dataset Construction

The example input data is in `examples/my-dataset/`:

```
examples/my-dataset/
├── shacl/
│   └── shacl.ttl        # raw SHACL shapes file
└── ontology/
    └── ontology.ttl     # domain ontology
```

The example input data uses the SNIK ontology as a demonstration. To build a dataset from your own SHACL shapes, replace the contents of `examples/my-dataset/shacl/` and `examples/my-dataset/ontology/` with your own files before running Step 1:

- `shacl/` — one or more `.ttl` files containing your SHACL shapes
- `ontology/` — the domain ontology file(s) referenced by your shapes

All other steps remain the same.

### Step 1: Extract shape fragments

Extract top-level node shapes from the raw SHACL file and serialize each as a self-contained fragment.

```bash
python Dataset-Construction/Data-Preprocessor/convert_shacl.py \
    --input-dir examples/my-dataset/shacl \
    --output-file examples/my-dataset/output_data.jsonl
```

**Output:** `examples/my-dataset/output_data.jsonl` — one record per shape, with `id`, `shacl`, and an empty `description` field (filled in later by the description generation steps).

### Step 2: Quality check and prefix extraction

Check structural completeness of extracted fragments and extract all prefix declarations.

```bash
python Dataset-Construction/Data-Preprocessor/shacl_quality_check.py \
    examples/my-dataset/output_data.jsonl
```

**Output:**
- `examples/my-dataset/output_data_check_log.txt` — per-entry results; review this before proceeding
- `examples/my-dataset/output_data_prefixes.json` — prefix declarations used in subsequent steps

Review the log and remove any `FAIL` entries from `output_data.jsonl` before continuing.

### Step 3: Augment with ontology metadata

Look up ontology term metadata for each URI referenced in the shapes and add an `ontology_snippet` field to each record.

```bash
python Dataset-Construction/Data-Preprocessor/ontology_augment.py \
    examples/my-dataset/output_data.jsonl \
    --prefixes examples/my-dataset/output_data_prefixes.json \
    --ontology-dir examples/my-dataset/ontology
```

**Output:** `examples/my-dataset/output_data_augmented.jsonl` — original records with `ontology_snippet` added.

### Step 4: Generate description prompts

Generate LLM prompts for each shape fragment. Use `--subset` for a built-in domain role, or `--role` to provide a custom role sentence.

```bash
python Dataset-Construction/Description-Generator/get_nl_prompt.py \
    --input examples/my-dataset/output_data_augmented.jsonl \
    --subset snik
```

For a custom domain:

```bash
python Dataset-Construction/Description-Generator/get_nl_prompt.py \
    --input examples/my-dataset/output_data_augmented.jsonl \
    --role "You are a Senior Railway Data Curator specializing in infrastructure management."
```

**Output:** `examples/my-dataset/output_data_augmented_nl_prompts.jsonl`

### Step 5: Call Gemini API to generate descriptions

Send the prompts to the Gemini API and save the generated descriptions. This step requires the `GEMINI_API_KEY` environment variable (see Prerequisites). If you do not have an API key, skip it: Step 6 and Step 7 can use the pre-reviewed example file instead.

```bash
python Dataset-Construction/Description-Generator/run_gemini.py \
    --input examples/my-dataset/output_data_augmented_nl_prompts.jsonl
```

**Output:** two files next to the input:

- `examples/my-dataset/output_data_augmented_nl_prompts_generated_description.jsonl` — one record per shape, with `id` and `generated_description`.
- `examples/my-dataset/output_data_augmented_nl_prompts_for_review.jsonl` — the same descriptions together with the shapes, as `id`, `shacl`, and `description`. This is the file you open in the Description Reviewer in Step 6.

### Step 6: Human review

Open the Description Reviewer GUI to review and edit the generated descriptions. Annotators check each description against its corresponding shape for accuracy, completeness, and domain-appropriate language.

```bash
python Dataset-Construction/Description-Reviewer/UI_annotator.py
```

When the tool starts, select the `..._for_review.jsonl` file produced in Step 5 (a file picker opens if `dcat_nl.jsonl` is not found in the current directory) and enter your name. Progress is saved to `corrected_dcat_dataset_<name>.jsonl` in the current directory. Each record in that file has `id`, `shacl`, the reviewed `description`, and the review fields `comment`, `flagged` and `checked`. Pass this file as `--descriptions` in Step 7; only `id` and `description` are used.

For this tutorial, we skip the interactive review and use a pre-reviewed file that is already included:

```
examples/my-dataset/output_data_description_reviewed.jsonl
```

Each record in this file has `id` and `description` fields.

### Step 7: Convert to dataset format

Combine the augmented JSONL and the reviewed descriptions into the final structured dataset format.

```bash
python Dataset-Construction/convert_dataset.py \
    examples/my-dataset/output_data_augmented.jsonl \
    --descriptions examples/my-dataset/output_data_description_reviewed.jsonl \
    --out-dir examples/example-nl2shacl-dataset \
    --prefix example-nl2shacl
```

**Output:**

```
examples/example-nl2shacl-dataset/
├── example-nl2shacl-descriptions.jsonl
├── example-nl2shacl-ontology_snippets.jsonl
└── shacl/
    ├── example-nl2shacl-1.ttl
    ├── example-nl2shacl-2.ttl
    └── ...
```

This is the final dataset format, ready for use with the Translation and Evaluation module.

> **Note:** The example reviewed file contains descriptions for only 8 of the 27 shapes, so the script reports 19 skipped records (`missing description`). This is expected for the example data.
>
> The output folder must be named `<prefix>-dataset` (here `example-nl2shacl-dataset`), because Stage 2 locates the dataset as `<dataset-dir>/<subset>/` and infers the file prefix by removing `-dataset` from the subset name.

---

## Stage 2: Translation and Evaluation

This stage evaluates a translation system on the dataset produced in Stage 1. We use the minimal rule-based translator included in the framework as a reference example. To evaluate your own system, follow the same steps with your system's output.

### Step 1: Run the translator

The rule-based translator reads the dataset and produces translated SHACL shapes.

```bash
python NL2SHACL-Translator/translator_example_rule_based.py \
    --subset example-nl2shacl-dataset \
    --dataset-dir examples
```

**Output:** `your_outputs.jsonl` — one record per input, with `id` and `output_shacl` fields.

### Step 2: Attach reference SHACL

Attach the reference shapes from the dataset to each translated record.

```bash
python NL2SHACL-Translator/attach_reference.py \
    --input your_outputs.jsonl \
    --dataset-dir examples \
    --subset example-nl2shacl-dataset \
    --output processed-output/example-nl2shacl-dataset_rule_based_processed.jsonl
```

**Output:** `processed-output/example-nl2shacl-dataset_rule_based_processed.jsonl` — records with `id`, `output_shacl`, and `reference_shacl` fields.

### Step 3: Run evaluation

Evaluate the translated shapes against the reference shapes across validity, structural, and semantic dimensions.

```bash
python Shapes-Evaluation/run_evaluation.py \
    --input processed-output/example-nl2shacl-dataset_rule_based_processed.jsonl
```

**Output:** `evaluation-output/example-nl2shacl-dataset_rule_based_eval.jsonl`

**Optional: logical equivalence.** In addition to the sampling-based ASER, the logical evaluator decides equivalence for all data graphs (LER) with SHACL2FOL and the Vampire theorem prover. It needs Java 21+ and two external tools, which `Shapes-Evaluation/tools/setup_logical_tools.sh` installs (see the [Shapes-Evaluation README](Shapes-Evaluation/README.md#logical-equivalence-ler)). It is not part of the default run; request it with `--run logical`:

```bash
# optional, requires Java 21+, SHACL2FOL and Vampire
bash Shapes-Evaluation/tools/setup_logical_tools.sh
python Shapes-Evaluation/run_evaluation.py \
    --input processed-output/example-nl2shacl-dataset_rule_based_processed.jsonl \
    --run logical --output_dir evaluation-output-logical
```

### Step 4: Compute metrics

Aggregate the evaluation results into dataset-level metrics.

```bash
python Shapes-Evaluation/compute_metrics.py \
    --input evaluation-output/
```

**Output:**
- `metrics-output/metrics_summary.csv` — all metrics for each (subset, model) combination
- `eval-logs/` — per-file diagnostic logs

---

## Summary

| Step | Script | Input | Output |
|------|--------|-------|--------|
| 1 | `convert_shacl.py` | `shacl/` | `output_data.jsonl` |
| 2 | `shacl_quality_check.py` | `output_data.jsonl` | `output_data_check_log.txt`, `output_data_prefixes.json` |
| 3 | `ontology_augment.py` | `output_data.jsonl` | `output_data_augmented.jsonl` |
| 4 | `get_nl_prompt.py` | `output_data_augmented.jsonl` | `output_data_augmented_nl_prompts.jsonl` |
| 5 | `run_gemini.py` | `output_data_augmented_nl_prompts.jsonl` | `..._generated_description.jsonl`, `..._for_review.jsonl` |
| 6 | `UI_annotator.py` | `..._for_review.jsonl` | `corrected_dcat_dataset_<name>.jsonl` (reviewed descriptions) |
| 7 | `convert_dataset.py` | augmented + reviewed | `example-nl2shacl-descriptions.jsonl`, `example-nl2shacl-ontology_snippets.jsonl`, `shacl/` |
| 8 | `translator_example_rule_based.py` | dataset | `your_outputs.jsonl` |
| 9 | `attach_reference.py` | `your_outputs.jsonl` | `processed-output/xxx_processed.jsonl` |
| 10 | `run_evaluation.py` | `processed-output/` | `evaluation-output/xxx_eval.jsonl` |
| 11 | `compute_metrics.py` | `evaluation-output/` | `metrics-output/metrics_summary.csv` |