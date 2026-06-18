# NL2SHACL-Framework

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Dataset](https://img.shields.io/badge/Dataset-Zenodo-blue)](https://doi.org/10.5281/zenodo.20082565)
[![Benchmark](https://img.shields.io/badge/Benchmark-NL2SHACL--Bench-green)](https://de-tum.github.io/NL2SHACL-Bench)

An extensible framework for benchmarking natural language to SHACL translation systems, introduced as part of NL2SHACL-Bench. The framework covers the full pipeline from dataset construction to evaluation, and is designed to be modular, extensible, and reproducible.

![Framework Overview](assets/framework.png)

---

## Overview

The framework consists of three modules that together support end-to-end benchmarking of NL2SHACL systems.

**Dataset Construction** is a semi-automated pipeline for creating paired NL-SHACL datasets. Starting from existing SHACL shapes and ontologies, it extracts self-contained shape fragments, generates natural language descriptions using an LLM, and produces human-reviewed records ready for benchmarking. Each output record consists of a reference shapes graph, a natural language description, and an ontology snippet:

![Record Example](assets/record_example.png)

**NL2SHACL Translator** is a model-agnostic module that takes a natural language description and an ontology snippet as input and produces a translated SHACL shapes graph. Users can plug in arbitrary translation systems. The current implementation uses a prompting-based approach via the OpenRouter API.

**Shapes Evaluation** assesses the quality of translated shapes against reference shapes across three complementary dimensions: validity, structural similarity, and semantic equivalence.

---

## Getting Started

Choose the path that matches your goal.

**Path A: Evaluate your own NL2SHACL system on the existing dataset**

This is the most common use case. You have a translation system and want to benchmark it against the NL2SHACL-Dataset.

```bash
# Step 1: Clone the framework and the dataset
git clone https://github.com/DE-TUM/NL2SHACL-Framework
cd NL2SHACL-Framework
git clone https://github.com/DE-TUM/NL2SHACL-Dataset dataset
```

Then follow the translator I/O specification to produce translated shapes, and run the Shapes Evaluation module on your outputs. See [`NL2SHACL-Translator/TRANSLATOR_SPEC.md`](NL2SHACL-Translator/TRANSLATOR_SPEC.md) for the full instructions.

A minimal rule-based translator is provided in [`NL2SHACL-Translator/translator_example_rule_based.py`](NL2SHACL-Translator/translator_example_rule_based.py) as a runnable reference that requires no API access.

**Path B: Contribute a new dataset subset**

You have a set of SHACL shapes and want to build a new NL-SHACL subset and contribute it to the NL2SHACL-Dataset.

1. Use the Dataset Construction pipeline to extract shape fragments, generate descriptions, and produce human-reviewed records. See [`Dataset-Construction/README.md`](Dataset-Construction/README.md) for the full pipeline.
2. Once your subset is ready, open an issue or pull request in the [NL2SHACL-Dataset repository](https://github.com/DE-TUM/NL2SHACL-Dataset) describing your data source and the number of records produced.
3. See the [Contributing section in the dataset repository](https://github.com/DE-TUM/NL2SHACL-Dataset#contributing) for dataset quality requirements and the review process.

**Path C: Run the full end-to-end pipeline**

You want to go from raw SHACL shapes to a complete benchmark evaluation, covering dataset construction, translation, and evaluation in sequence.

- For a step-by-step written walkthrough, see [`TUTORIAL.md`](TUTORIAL.md).
- For an interactive version, open [`tutorial.ipynb`](tutorial.ipynb) in Jupyter.

---

## Repository Structure

```
NL2SHACL-Framework/
├── assets/                        # Images for documentation
├── TUTORIAL.md                    # End-to-end written walkthrough
├── tutorial.ipynb                 # Interactive end-to-end tutorial (Jupyter)
├── dataset/                       # Place the NL2SHACL-Dataset here (see Path A above)
├── examples/
│   ├── my-dataset/                # Example input: raw SHACL and ontology files
│   └── example-nl2shacl-dataset/  # Example output: structured dataset
├── Dataset-Construction/
│   ├── convert_dataset.py         # Converts augmented JSONL to final dataset format
│   ├── Data-Preprocessor/         # Fragment extraction and ontology augmentation
│   ├── Description-Generator/     # LLM-based NL description generation
│   └── Description-Reviewer/      # GUI for human review and annotation
├── NL2SHACL-Translator/
│   ├── TRANSLATOR_SPEC.md         # Input/output specification for custom translators
│   ├── translator_example_rule_based.py  # Minimal rule-based translator (no API required)
│   ├── attach_reference.py        # Attaches reference SHACL to translator outputs
│   └── ...                        # Prompt generation and model inference
└── Shapes-Evaluation/             # Metric computation and evaluation pipeline
```

Each module folder contains its own README with detailed usage instructions.

---

## License

This project is licensed under the [MIT License](LICENSE).