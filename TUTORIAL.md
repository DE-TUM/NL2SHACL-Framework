# End-to-End Tutorial

This tutorial walks through the complete NL2SHACL-Bench pipeline, from raw SHACL shapes to a benchmark evaluation. It uses the example data in `examples/my-dataset/` and produces a dataset in the same format as `examples/example-nl2shacl-dataset/`.

The pipeline has two main stages:

- **Stage 1: Dataset Construction** — extract shape fragments, generate natural language descriptions, and produce a structured dataset.
- **Stage 2: Translation and Evaluation** — run a translation system on the dataset and evaluate the results.

---

## Prerequisites

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

**Output:** `examples/my-dataset/output_data.jsonl` — one record per shape, with `id`, `shacl`, and `nl` fields.

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

Send the prompts to the Gemini API and save the generated descriptions.

```bash
python Dataset-Construction/Description-Generator/run_gemini.py \
    --input examples/my-dataset/output_data_augmented_nl_prompts.jsonl
```

**Output:** `examples/my-dataset/output_data_augmented_nl_prompts_generated_description.jsonl`

Each record contains an `id` and a `generated_description` field.

### Step 6: Human review

Open the Description Reviewer GUI to review and edit the generated descriptions. Annotators check each description against its corresponding shape for accuracy, completeness, and domain-appropriate language.

```bash
python Dataset-Construction/Description-Reviewer/UI_annotator.py
```

The reviewer saves the final approved descriptions to a reviewed JSONL file. For this tutorial, we assume the reviewed file is:

```
examples/my-dataset/output_data_description_reviewed.jsonl
```

Each record in this file must have `id` and `description` fields.

### Step 7: Convert to dataset format

Combine the augmented JSONL and the reviewed descriptions into the final structured dataset format.

```bash
python Dataset-Construction/convert_dataset.py \
    examples/my-dataset/output_data_augmented.jsonl \
    --descriptions examples/my-dataset/output_data_description_reviewed.jsonl \
    --out-dir examples/example-nl2shacl-dataset \
    --prefix snik
```

**Output:**

```
examples/example-nl2shacl-dataset/
├── snik-descriptions.jsonl
├── snik-ontology_snippets.jsonl
└── shacl/
    ├── snik-1.ttl
    ├── snik-2.ttl
    └── ...
```

This is the final dataset format, ready for use with the Translation and Evaluation module.

---

## Stage 2: Translation and Evaluation

This stage evaluates a translation system on the dataset produced in Stage 1. We use the minimal rule-based translator included in the framework as a reference example. To evaluate your own system, follow the same steps with your system's output.

### Step 1: Run the translator

The rule-based translator reads the dataset and produces translated SHACL shapes.

```bash
python NL2SHACL-Translator/translator_example_rule_based.py \
    --subset snik-dataset \
    --dataset-dir examples/example-nl2shacl-dataset
```

**Output:** `your_outputs.jsonl` — one record per input, with `id` and `output_shacl` fields.

### Step 2: Attach reference SHACL

Attach the reference shapes from the dataset to each translated record.

```bash
python NL2SHACL-Translator/attach_reference.py \
    --input your_outputs.jsonl \
    --dataset-dir examples/example-nl2shacl-dataset \
    --subset snik-dataset \
    --output processed-output/snik-dataset_rule_based_processed.jsonl
```

**Output:** `processed-output/snik-dataset_rule_based_processed.jsonl` — records with `id`, `output_shacl`, and `reference_shacl` fields.

### Step 3: Run evaluation

Evaluate the translated shapes against the reference shapes across validity, structural, and semantic dimensions.

```bash
python Shapes-Evaluation/run_evaluation.py \
    --input processed-output/snik-dataset_rule_based_processed.jsonl
```

**Output:** `evaluation-output/snik-dataset_rule_based_processed_eval.jsonl`

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
| 5 | `run_gemini.py` | `output_data_augmented_nl_prompts.jsonl` | `..._generated_description.jsonl` |
| 6 | `UI_annotator.py` | generated descriptions | `output_data_description_reviewed.jsonl` |
| 7 | `convert_dataset.py` | augmented + reviewed | `snik-descriptions.jsonl`, `snik-ontology_snippets.jsonl`, `shacl/` |
| 8 | `translator_example_rule_based.py` | dataset | `your_outputs.jsonl` |
| 9 | `attach_reference.py` | `your_outputs.jsonl` | `processed-output/xxx_processed.jsonl` |
| 10 | `run_evaluation.py` | `processed-output/` | `evaluation-output/xxx_eval.jsonl` |
| 11 | `compute_metrics.py` | `evaluation-output/` | `metrics-output/metrics_summary.csv` |