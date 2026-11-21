# Dataset Construction

This folder contains the tools for constructing paired NL-SHACL datasets for the NL2SHACL task. Starting from raw SHACL shape files and ontologies, the pipeline produces human-reviewed records, each consisting of a reference SHACL shapes graph, a natural language description, and an ontology snippet.

The pipeline runs across three modules and a final conversion script, in sequence:

1. **[Data Preprocessor](Data-Preprocessor/)** — extracts shape fragments from raw SHACL files, checks their quality, and augments them with ontology metadata. The result is an augmented JSONL file.
2. **[Description Generator](Description-Generator/)** — generates natural language descriptions for each shape fragment using the Gemini API.
3. **[Description Reviewer](Description-Reviewer/)** — provides a graphical interface for human annotators to review, edit, and approve the generated descriptions.
4. **[`convert_dataset.py`](convert_dataset.py)** — combines the augmented JSONL with the reviewed descriptions into the final structured dataset.

```
Raw .ttl files + ontology
        │
        ▼
Data-Preprocessor           →  output_data_augmented.jsonl
        │
        ▼
Description-Generator       →  ..._generated_description.jsonl
                               ..._for_review.jsonl
        │
        ▼
Description-Reviewer        →  corrected_dcat_dataset_<name>.jsonl
                               (reviewed descriptions)
        │
        ▼
convert_dataset.py          →  <prefix>-dataset/
                                   <prefix>-descriptions.jsonl
                                   <prefix>-ontology_snippets.jsonl
                                   shacl/
```

---

## Final step: convert_dataset.py

Converts the augmented JSONL and the reviewed descriptions into the final structured dataset format, with sequential IDs and separate files for descriptions, ontology snippets, and SHACL shapes. Run it once the reviewed descriptions are ready.

```bash
python Dataset-Construction/convert_dataset.py output_data_augmented.jsonl \
    --descriptions output_data_description_reviewed.jsonl \
    --out-dir snik-dataset \
    --prefix snik
```

| Argument | Default | Description |
|----------|---------|-------------|
| `output_data_augmented.jsonl` | (required) | Augmented JSONL from the Data Preprocessor. Provides the `shacl` and `ontology_snippet` fields. |
| `--descriptions` | (required) | Reviewed descriptions JSONL, i.e. the file saved by the Description Reviewer. Each record must have `id` and `description` fields (other fields are ignored); descriptions are matched to augmented records by `id`. |
| `--out-dir` | `<augmented_stem>_dataset/` | Output directory. Name it `<prefix>-dataset` so that the Translator and Evaluation modules can locate it as `<dataset-dir>/<subset>/`. |
| `--prefix` | `snik` | ID prefix for output records (e.g. `epo`, `snik`). |

Only records that have both a description and an ontology snippet are written; records missing either are reported as skipped.

**Output structure:**

```
<out-dir>/
├── <prefix>-descriptions.jsonl       — id, label, description
├── <prefix>-ontology_snippets.jsonl  — id, label, ontology_snippet
└── shacl/
    ├── <prefix>-1.ttl
    ├── <prefix>-2.ttl
    └── ...
```
