# Translator I/O Specification

This document describes how to evaluate any NL2SHACL translation system on the NL2SHACL-Dataset using this framework. The specification covers the input format, output contract, and how to connect your system to the Shapes Evaluation module.

---

## Setup

Clone the framework and the dataset into the expected directory structure:

```bash
git clone https://github.com/DE-TUM/NL2SHACL-Framework
cd NL2SHACL-Framework
git clone https://github.com/DE-TUM/NL2SHACL-Dataset dataset
```

After cloning, the `dataset/` directory will contain one folder per subset (e.g. `dataset/invoice-dataset/`), each with the description and ontology snippet files and a `shacl/` folder of reference shapes.

---

## Overview

```
dataset/<subset>/
  <subset>-descriptions.jsonl
  <subset>-ontology_snippets.jsonl
            │
            ▼
    [Your translation system]
            │
            ▼
  your_outputs.jsonl                        (id + output_shacl)
            │
            ▼
    attach_reference.py
            │
            ▼
  processed-output/<subset>_<system>_processed.jsonl    (id + output_shacl + reference_shacl)
            │
            ▼
    Shapes Evaluation module
```

---

## Input Format

Your system reads from two JSONL files in `dataset/<subset>/`. Both files share the same record IDs and are joined on the `id` field.

### `<subset>-descriptions.jsonl`

One record per line:

```json
{
  "id": "invoice-1",
  "label": "InvoiceShape",
  "description": "An invoice must have exactly one buyer and one seller, both of which must be organizations."
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique record identifier, format `<subset>-<number>` |
| `label` | string | Shape name as it appears in the reference SHACL |
| `description` | string | Natural language description of the constraints |

### `<subset>-ontology_snippets.jsonl`

One record per line:

```json
{
  "id": "invoice-1",
  "label": "InvoiceShape",
  "ontology_snippet": {
    "https://example.org/invoice#Invoice": {
      "source": "local",
      "types": ["http://www.w3.org/2002/07/owl#Class"],
      "label": "Invoice",
      "description": "A commercial document issued by a seller to a buyer."
    }
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Matches the corresponding record in the descriptions file |
| `label` | string | Shape name |
| `ontology_snippet` | object | Dictionary keyed by ontology term URIs, each with `source`, `types`, `label`, and optionally `description` |

---

## Step 1: Run Your Translation System

Your system reads the input files above and produces a JSONL file with one record per input record. Each record must contain two fields:

```json
{
  "id": "invoice-1",
  "output_shacl": "@prefix inv: <https://example.org/invoice#> .\n@prefix sh: <http://www.w3.org/ns/shacl#> .\n\ninv:InvoiceShape a sh:NodeShape ;\n    sh:targetClass inv:Invoice ."
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Must match the input record ID exactly |
| `output_shacl` | string | Translated SHACL shapes graph serialized as Turtle. Set to `null` if translation failed for this record. |

**Requirements for `output_shacl`:**

- Serialization format must be Turtle. Other RDF serializations are not accepted by the Evaluation module.
- All prefixes used must be declared in the Turtle header.
- Do not include Markdown code fences or any non-RDF text. The Evaluation module does not apply post-processing to externally produced outputs.
- If translation fails for a record, set `output_shacl` to `null`. The Evaluation module will record this as a parsing error.

Save this file anywhere convenient, e.g. `your_outputs.jsonl`.

---

## Step 2: Attach Reference SHACL

The Evaluation module requires a `reference_shacl` field in each record. Run the provided helper script to attach the reference shapes from the dataset:

```bash
python NL2SHACL-Translator/attach_reference.py \
    --input your_outputs.jsonl \
    --dataset-dir dataset \
    --output processed-output/<subset>_<system>_processed.jsonl
```

This reads each record's `id`, locates the corresponding `.ttl` file in `dataset/<subset>/shacl/`, and writes the combined output file to `processed-output/`.

---

## Step 3: Run Evaluation

Pass the processed output file to the Shapes Evaluation module:

```bash
python Shapes-Evaluation/run_evaluation.py \
    --input processed-output/<subset>_<system>_processed.jsonl
```

See [`Shapes-Evaluation/README.md`](../Shapes-Evaluation/README.md) for full evaluation options and metric descriptions.

---

## Reference Implementation

A minimal rule-based translator is provided in `translator_example_rule_based.py`. It requires no API access and handles a small subset of simple cardinality constraints. It is intended as a runnable reference for the input/output contract, not as a competitive baseline.

```bash
python NL2SHACL-Translator/translator_example_rule_based.py \
    --subset invoice-dataset \
    --dataset-dir dataset
```

This produces `your_outputs.jsonl` conforming to Step 1 above. You can then run Steps 2 and 3 as normal.