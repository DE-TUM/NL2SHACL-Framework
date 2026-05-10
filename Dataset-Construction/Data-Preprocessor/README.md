# Data Preprocessor

This module implements the first step of the Dataset Construction pipeline in NL2SHACL-Framework. It takes raw SHACL shape files and ontologies as input and produces a structured JSONL dataset ready for description generation.

The pipeline consists of four scripts that are run in sequence.

---

## Requirements

```bash
pip install rdflib requests
```

---

## Pipeline Overview

```
Raw .ttl files
      │
      ▼
1. convert_shacl.py        →  output_data.jsonl
      │
      ▼
2. shacl_quality_check.py  →  output_data_check_log.txt
                              output_data_prefixes.json
      │
      ▼
3. ontology_augment.py     →  output_data_augmented.jsonl
      │
      ▼
4. convert_dataset.py      →  <subset>-dataset/
                                  descriptions.jsonl
                                  ontology_snippets.jsonl
                                  shacl/
```

---

## Scripts

### Step 1: convert_shacl.py

Reads all `.ttl` files from a directory and extracts top-level SHACL node shapes into a JSONL file. Each record contains the shape's ID and its self-contained Turtle serialization.

Auxiliary shapes referenced via `sh:node`, `sh:or`, `sh:and`, `sh:not`, or `sh:xone` are automatically inlined into the referencing shape's record. SPARQL-based constraints (`sh:sparql`) are stripped.

```bash
python convert_shacl.py --input-dir ./shacl --output-file output_data.jsonl
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--input-dir` | `./shacl` | Directory containing `.ttl` files |
| `--output-file` | `output_data.jsonl` | Output JSONL file |

**Output format** (one record per line):

```json
{"id": ":MyShape", "shacl": "@prefix ...\n:MyShape a sh:NodeShape ...", "nl": ""}
```

---

### Step 2: shacl_quality_check.py

Checks each record in the JSONL file for syntactic validity and structural completeness, and extracts all prefix declarations.

```bash
python shacl_quality_check.py output_data.jsonl
```

**Outputs:**

- `output_data_check_log.txt` — per-entry results and summary statistics
- `output_data_prefixes.json` — all unique prefix aliases and their namespace URIs

Shapes are classified as `PASS`, `WARN` (no target declared, likely a referenced shape), or `FAIL`. Review the log before proceeding to the next step.

---

### Step 3: ontology_augment.py

Looks up ontology metadata for each URI referenced in the SHACL shapes and adds an `ontology_snippet` field to each record. Lookup is first attempted against a local ontology directory, with HTTP fallback per namespace.

```bash
python ontology_augment.py output_data.jsonl \
    --prefixes output_data_prefixes.json \
    --ontology-dir ./ontology \
    --skip-namespaces http://example.org/shapes#
```

| Argument | Default | Description |
|----------|---------|-------------|
| `output_data.jsonl` | (required) | Input JSONL from Step 2 |
| `--prefixes` | `<stem>_prefixes.json` | Prefix file from Step 2 |
| `--ontology-dir` | `./ontology` | Directory of ontology files (`.ttl`, `.rdf`, `.xml`, `.owl`, `.jsonld`) |
| `--skip-namespaces` | none | One or more namespace URI prefixes to exclude (e.g. shape namespaces) |

**Outputs:**

- `output_data_augmented.jsonl` — original records with `ontology_snippet` added
- `output_data_lookup_log.txt` — per-entry lookup results and global summary

---

### Step 4: convert_dataset.py

Converts the augmented JSONL into the final structured dataset format, with sequential IDs and separate files for descriptions, ontology snippets, and SHACL shapes.

```bash
python convert_dataset.py output_data_augmented.jsonl \
    --out-dir epo-dataset \
    --prefix epo
```

| Argument | Default | Description |
|----------|---------|-------------|
| `output_data_augmented.jsonl` | (required) | Input JSONL from Step 3 |
| `--out-dir` | `<stem>_dataset/` | Output directory |
| `--prefix` | `invoice` | ID prefix for records (e.g. `epo`, `snik`) |

**Output structure:**

```
<out-dir>/
├── descriptions.jsonl       — id, label, description
├── ontology_snippets.jsonl  — id, label, ontology_snippet
└── shacl/
    ├── <prefix>-1.ttl
    ├── <prefix>-2.ttl
    └── ...
```

---

## Notes

- Records that fail syntactic validation in Step 2 should be inspected and either fixed or removed before running Step 3.
- The `--skip-namespaces` argument in Step 3 should be used to exclude shape-specific namespaces that are not part of the domain ontology.
- The output of Step 4 feeds directly into the [Description Generator](../Description-Generator/) module.