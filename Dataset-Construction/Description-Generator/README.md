# Description Generator

This module generates natural language descriptions for SHACL shape fragments using the Gemini API. It is part of the Dataset Construction pipeline in NL2SHACL-Framework.

The module consists of two scripts that are run in sequence: first generating prompt files from SHACL shapes, then calling the Gemini API to produce descriptions.

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

```bash
python get_nl_prompt.py <subset>
```

Reads all `.ttl` files from `<subset>-dataset/shacl/` and produces a prompt file:

```
<subset>-dataset/<subset>-get_nl_prompt.jsonl
```

Each line in the output file is a JSON record with the following fields:

| Field | Description |
|-------|-------------|
| `id` | Record identifier, matching the TTL filename (e.g. `epo-1`) |
| `system_prompt` | Subset-specific role and translation instructions |
| `user_prompt` | The SHACL shape content to be translated |

### Step 2: Call Gemini

```bash
python run_gemini.py <subset>
```

Reads the prompt file generated in Step 1 and calls the Gemini API for each record. Results are saved to:

```
<subset>-dataset/<subset>-generated_description.jsonl
```

Each line in the output file contains:

| Field | Description |
|-------|-------------|
| `id` | Record identifier |
| `generated_description` | The generated natural language description |

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

- The model used is `gemini-3.1-pro-preview` with temperature 0.2.
- Each record is retried up to 3 times on failure before being skipped.
- The generated descriptions are drafts intended for human review. See the [Description Reviewer](../Description-Reviewer/) module for the next step.