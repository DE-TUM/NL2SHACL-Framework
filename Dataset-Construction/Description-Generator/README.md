# Description Generator

This module generates natural language descriptions for SHACL shape fragments using the Gemini API. It is part of the Dataset Construction pipeline in NL2SHACL-Framework.

The module consists of two scripts that are run in sequence: first generating prompt files from the augmented JSONL produced by the [Data Preprocessor](../Data-Preprocessor/), then calling the Gemini API to produce descriptions.

---

## Requirements

```bash
pip install google-genai
```

Set your Gemini API key as an environment variable:

```bash
export GEMINI_API_KEY=your_api_key_here
```

---

## Usage

### Step 1: Generate prompts

Use `--subset` for a built-in domain role:

```bash
python get_nl_prompt.py --input output_data_augmented.jsonl --subset snik
```

or `--role` to provide a custom role sentence for your own domain (the translation rules are appended automatically):

```bash
python get_nl_prompt.py --input output_data_augmented.jsonl \
    --role "You are a Senior Railway Data Curator specializing in infrastructure management."
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--input` | (required) | Augmented JSONL from `ontology_augment.py`. The `shacl` field of each record is used as the prompt input. |
| `--subset` | | Built-in system prompt for a known subset (see [Supported Subsets](#supported-subsets)). Exactly one of `--subset` and `--role` is required. |
| `--role` | | Custom role sentence, used as the first line of the system prompt. |
| `--output` | `<input_stem>_nl_prompts.jsonl` | Output JSONL path. |

Each line in the output file is a JSON record with the following fields:

| Field | Description |
|-------|-------------|
| `id` | Record identifier, copied from the input record (e.g. `meta:EntityTypeShape`) |
| `system_prompt` | Subset-specific role and translation instructions |
| `user_prompt` | The SHACL shape content to be translated |
| `shacl` | The SHACL shape itself, carried along so that Step 2 can write the reviewer input file |

### Step 2: Call Gemini

```bash
python run_gemini.py --input output_data_augmented_nl_prompts.jsonl
python run_gemini.py --input output_data_augmented_nl_prompts.jsonl --model <other-model-name>
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--input` | (required) | Prompt JSONL from Step 1. |
| `--model` | `gemini-3.1-pro-preview` | Gemini model name. If the model is not found, the script stops immediately with an error instead of retrying every record. |
| `--api-version` | `v1beta` | Gemini API version. Preview models such as `gemini-3.1-pro-preview` are only served under `v1beta`, while recent `google-genai` releases default to `v1`. |
| `--output` | `<input_stem>_generated_description.jsonl` | Path of the generated descriptions. |
| `--review-output` | `<input_stem>_for_review.jsonl` | Path of the file to open in the Description Reviewer. |

Reads the prompt file generated in Step 1 and calls the Gemini API for each record. Two files are written next to the input:

| File | Content |
|------|---------|
| `<input_stem>_generated_description.jsonl` (or the path given by `--output`) | `id`, `generated_description` |
| `<input_stem>_for_review.jsonl` (or the path given by `--review-output`) | `id`, `shacl`, `description` — open this file in the [Description Reviewer](../Description-Reviewer/) |

For example, with the tutorial input:

```
output_data_augmented_nl_prompts_generated_description.jsonl
output_data_augmented_nl_prompts_for_review.jsonl
```

The reviewer file is rebuilt from the generated descriptions at the end of every run, so it is also complete after a resumed run.

Each line in the `_generated_description.jsonl` file contains:

| Field | Description |
|-------|-------------|
| `id` | Record identifier |
| `generated_description` | The generated natural language description |

Each line in the `_for_review.jsonl` file contains `id`, `shacl` and `description` (the generated text, to be reviewed and edited by the annotator).

If the run is interrupted, re-running the script will skip already-processed records and resume from where it left off.

---

## Supported Subsets

| Subset | Domain |
|--------|--------|
| `chemrof` | Chemistry |
| `dcat` | Open Data Metadata |
| `epo` | Public Procurement |
| `invoice` | Electronic Invoicing |
| `snik` | Healthcare Information Management |
| `dbpedia` | General (Cross-domain) |

---

## Notes

- The default model is `gemini-3.1-pro-preview` with temperature 0.2 (the temperature is fixed). Use `--model` to select another model. Some models are only available under a specific `--api-version`.
- Each record is retried up to 3 times on failure before being skipped.
- The generated descriptions are drafts intended for human review. See the [Description Reviewer](../Description-Reviewer/) module for the next step.