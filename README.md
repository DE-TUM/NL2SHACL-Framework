# NL2SHACL Framework

A pipeline for constructing a parallel dataset of **natural language descriptions ↔ SHACL shapes**, intended for training and evaluating models that generate SHACL constraints from natural language (NL2SHACL).

---

## Project Overview

The goal is to build a dataset where each record pairs:
- A self-contained **SHACL shape** (as a valid Turtle snippet)
- A **natural language description** of the constraint the shape encodes
- An **ontology snippet** providing metadata about the domain terms the shape references

This dataset can be used to train or benchmark language models on the task of translating natural language constraint descriptions into formal SHACL shapes.

---

## Pipeline

### Step 1 — Extract SHACL Shapes (`convert_shacl.py`)

Input: one or more raw SHACL `.ttl` files downloaded from a public repository (e.g. DCAT-AP).

Each named `sh:NodeShape` becomes one record. The script handles several non-trivial cases:

- **Auxiliary shapes**: shapes that have no `sh:target*` predicate of their own and are only referenced via `sh:node` by other shapes are treated as helpers. They are not emitted as separate records; instead their content is inlined into the referencing shape's Turtle snippet.
- **Cross-file references**: if a `sh:node` points to a shape defined in a different file, the reference is dropped and logged.
- **Prefix filtering**: only the prefixes actually used in a given snippet are included in its `@prefix` declarations, keeping each record self-contained.
- **Duplicate IDs**: if two shapes share the same local name, later occurrences receive a `-1`, `-2`, … suffix.
- **Anonymous BNode shapes** (e.g. inline `sh:or` wrappers) are never emitted as top-level records; they appear embedded inside their parent shape.

Output: `dcat_data.jsonl` — one JSON object per line with fields `id`, `shacl`, `nl` (empty at this stage).

**`dcat_data_prefixes.json`** is also produced: a mapping of all prefix abbreviations to their namespace URIs, used by downstream steps. The default (`""`) prefix may map to a list if multiple source files use different base namespaces.

---

### Step 2 — Augment with Ontology Metadata (`check_shacl_part2.py`)

Input: the JSONL from Step 1 + a directory of local ontology files + the prefixes JSON.

For each shape, the script parses the Turtle with rdflib and classifies all URIs it finds into two categories:

| Category | SHACL predicates | Behaviour |
|---|---|---|
| **Domain terms** | `sh:path`, `sh:targetClass`, `sh:targetNode`, `sh:targetSubjectsOf`, `sh:targetObjectsOf`, `sh:node`, `sh:shape` | Looked up in the ontology index; written to snippet only if metadata is found |
| **Value terms** | `sh:hasValue`, `sh:class`, `sh:in` | Always written to snippet (with full metadata if found, `{"uri_only": true}` if not) |

Several edge cases are handled:

- **`sh:hasValue` as string literal**: some shapes write `sh:hasValue "http://..."` (a Literal, not a URIRef). The script detects these and treats them as URIs.
- **`sh:sparql` / `sh:select`**: URIs inside SPARQL query strings are invisible to the RDF parser. The script extracts `sh:declare` prefix declarations from the constraint node and uses them to expand prefixed names found in the SPARQL text via regex.
- **Turtle parse errors**: if a shape's Turtle cannot be parsed (e.g. due to an undefined prefix typo), this is logged explicitly rather than silently swallowed.
- **schema.org http/https mismatch**: older shapes use `http://schema.org/` while current ontology files use `https://schema.org/`. The lookup tries both variants.

The ontology index is built at startup by loading all files from a directory (`.ttl`, `.rdf`, `.xml`, `.owl`, `.jsonld`) and merging them into a single rdflib graph. An HTTP fallback is available for namespaces not covered locally.

Output: `dcat_data_augmented.jsonl` + `dcat_data_lookup_log.txt`.

---

### Step 3 — Manual Review (`shacl_reviewer.py`)

A local desktop GUI (Python + tkinter, no external dependencies) for reviewing records one by one before or after writing natural language descriptions.

Features:
- Open any JSONL file via file picker
- Left panel: SHACL Turtle (monospace, scrollable, copyable)
- Right panel: `ontology_snippet` as formatted JSON with per-URI separators and a count header (scrollable, copyable, searchable via Ctrl+F)
- Keyboard navigation (←/→ or ↑/↓ arrow keys)
- Progress bar

---

## Output Format

Each record in the final JSONL dataset has four fields:

```json
{
  "id": ":MandatoryAgent",
  "shacl": "@prefix : <http://data.europa.eu/r5r/mandatory-classes#> .\n@prefix foaf: <http://xmlns.com/foaf/0.1/> .\n...\n\n:MandatoryAgent a sh:NodeShape ;\n    sh:targetNode foaf:Agent ;\n    ...",
  "nl": "Every instance of foaf:Agent must exist in the dataset — at least one resource must be typed as an Agent.",
  "ontology_snippet": {
    "http://xmlns.com/foaf/0.1/Agent": {
      "source": "local",
      "types": ["http://www.w3.org/2002/07/owl#Class"],
      "label": "Agent",
      "description": "An agent (eg. person, group, software or physical artifact)."
    }
  }
}
```

| Field | Type | Description |
|---|---|---|
| `id` | string | Short identifier, usually the SHACL shape's prefixed name (e.g. `:MandatoryAgent`, `dcat:Catalog`) |
| `shacl` | string | A complete, self-contained Turtle document containing only the relevant shape(s) and their used prefixes |
| `nl` | string | Natural language description of the constraint (written manually or generated) |
| `ontology_snippet` | object | Metadata for each domain/value URI referenced by the shape, keyed by full URI |

### `ontology_snippet` entry structure

```json
"https://purl.org/p2p-o/organization#formalName": {
  "source": "local",
  "types": ["http://www.w3.org/2002/07/owl#DatatypeProperty"],
  "label": "formalName",
  "description": "Name of a party involved in the invoice"
}
```

- `source`: `"local"` (from ontology files) or `"http"` (fetched at runtime) or `"not-found"`
- `uri_only`: `true` if the URI was identified as a value term but no ontology metadata was found
- `types`, `label`, `description`: standard ontology metadata when available

---

## Known Data Quality Issues

When building from real-world SHACL files, the following issues can arise and are logged by the pipeline:

- **Prefix typos** in source SHACL (e.g. `etifact-o:` instead of `edifact-o:`) cause the entire shape to fail Turtle parsing
- **Case mismatches** between SHACL and ontology (e.g. `ItemNumberSupplier` vs `itemNumberSupplier`)
- **Namespace mismatches** where the same concept is referenced under different namespace URIs in different files
- **Missing ontology terms** where a property is used in SHACL but not defined in any available ontology file

All of the above are reported in `*_lookup_log.txt`.

---

## Source Data

The pipeline has been applied to:

- **DCAT-AP** shape files (`dcat-ap_shapes.ttl`, `dcat-ap-mandatory-classes_shapes.ttl`, `dcat-ap-mdr-vocabularies_shapes.ttl`)
- **Invoice / EDI** shapes based on EDIFACT and P2P-O ontologies

Ontologies used locally: DCAT 3, Dublin Core Terms, SKOS, schema.org, VCard, ADMS, FOAF, SPDX v2, and domain-specific ontologies (EDIFACT-O, P2P-O).


## File Structure
```
(nl2shacl) PS C:\Users\yuche\Desktop\NL2SHACL> tree /f
Folder PATH listing for volume Windows
Volume serial number is 2CE8-62CF
C:.
│   2-datasets.zip
│   annotator_yzh.py
│   convert_dataset.py
│   convert_shacl.py
│   extract.py
│   find_cross_refs.py
│   ontology_augment.py
│   reader.py
│   shacl_ontology_reviewer.py
│   shacl_quality_check.py
│   shapes_of_you.xlsx
│
├───chemrof
│       chemrof_nl_dataset_full.jsonl
│       corrected_dataset_yuchen.jsonl
│
├───dcat-dataset
│   │   descriptions.jsonl
│   │   index.jsonl
│   │   ontology_snippets.jsonl
│   │
│   ├───full-data
│   │       dcat_data.jsonl
│   │       dcat_data_augmented.jsonl
│   │
│   ├───logs
│   │       dcat-cross-ref.log
│   │       dcat_data_check_log.txt
│   │       dcat_data_lookup_log.txt
│   │       dcat_data_prefixes.json
│   │
│   ├───ontology
│   │       dcat3.ttl
│   │       dct.ttl
│   │       dublin_core_terms.ttl
│   │       foaf.rdf
│   │       legacy_adms.ttl
│   │       ns.ttl
│   │       schemaorg-current-https.ttl
│   │       skos.rdf
│   │       spdx-ontology.owl.xml
│   │
│   ├───raw_shacl
│   │       dcat-ap-mandatory-classes.shapes.ttl
│   │       dcat-ap-mdr-vocabularies.shapes.ttl
│   │       dcat-ap.shapes.ttl
│   │
│   └───shacl
│           dcat-1.ttl
│           dcat-10.ttl
│           dcat-11.ttl
│           dcat-12.ttl
│           dcat-13.ttl
│           dcat-14.ttl
│           dcat-15.ttl
│           dcat-16.ttl
│           dcat-17.ttl
│           dcat-18.ttl
│           dcat-19.ttl
│           dcat-2.ttl
│           dcat-20.ttl
│           dcat-21.ttl
│           dcat-3.ttl
│           dcat-4.ttl
│           dcat-5.ttl
│           dcat-6.ttl
│           dcat-7.ttl
│           dcat-8.ttl
│           dcat-9.ttl
│
├───invoice-dataset
│   │   descriptions.jsonl
│   │   index.jsonl
│   │   ontology_snippets.jsonl
│   │
│   ├───full-data
│   │       invoice_data.jsonl
│   │       invoice_data_augmented.jsonl
│   │
│   ├───logs
│   │       invoice_data_check_log.txt
│   │       invoice_data_lookup_log.txt
│   │       invoice_data_prefixes.json
│   │
│   ├───ontology
│   │       edifact-o.ttl
│   │
│   └───shacl
│           invoice-1.ttl
│           invoice-10.ttl
│           invoice-11.ttl
│           invoice-12.ttl
│           invoice-13.ttl
│           invoice-14.ttl
│           invoice-15.ttl
│           invoice-16.ttl
│           invoice-17.ttl
│           invoice-18.ttl
│           invoice-19.ttl
│           invoice-2.ttl
│           invoice-20.ttl
│           invoice-21.ttl
│           invoice-22.ttl
│           invoice-23.ttl
│           invoice-24.ttl
│           invoice-25.ttl
│           invoice-26.ttl
│           invoice-27.ttl
│           invoice-28.ttl
│           invoice-29.ttl
│           invoice-3.ttl
│           invoice-30.ttl
│           invoice-31.ttl
│           invoice-32.ttl
│           invoice-33.ttl
│           invoice-34.ttl
│           invoice-35.ttl
│           invoice-36.ttl
│           invoice-37.ttl
│           invoice-38.ttl
│           invoice-39.ttl
│           invoice-4.ttl
│           invoice-40.ttl
│           invoice-41.ttl
│           invoice-42.ttl
│           invoice-43.ttl
│           invoice-44.ttl
│           invoice-45.ttl
│           invoice-46.ttl
│           invoice-47.ttl
│           invoice-48.ttl
│           invoice-49.ttl
│           invoice-5.ttl
│           invoice-50.ttl
│           invoice-51.ttl
│           invoice-52.ttl
│           invoice-53.ttl
│           invoice-54.ttl
│           invoice-55.ttl
│           invoice-56.ttl
│           invoice-57.ttl
│           invoice-58.ttl
│           invoice-59.ttl
│           invoice-6.ttl
│           invoice-60.ttl
│           invoice-61.ttl
│           invoice-62.ttl
│           invoice-63.ttl
│           invoice-64.ttl
│           invoice-65.ttl
│           invoice-66.ttl
│           invoice-67.ttl
│           invoice-68.ttl
│           invoice-69.ttl
│           invoice-7.ttl
│           invoice-70.ttl
│           invoice-71.ttl
│           invoice-72.ttl
│           invoice-73.ttl
│           invoice-74.ttl
│           invoice-75.ttl
│           invoice-76.ttl
│           invoice-77.ttl
│           invoice-78.ttl
│           invoice-79.ttl
│           invoice-8.ttl
│           invoice-9.ttl
│
└───old data
        chemrof_nl_dataset_small.jsonl
        corrected_dataset_Yuchen.jsonl
        corrected_process_dataset_yuchen.jsonl
        dataset_editor.py
        dataset_editor_v3_py.py
        process_nl_dataset_full.jsonl
``` 