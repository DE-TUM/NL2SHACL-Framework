# NL2SHACL Translator

This module implements the translation step of the NL2SHACL-Bench pipeline. It takes natural language descriptions and ontology snippets from the dataset, generates prompts, calls LLMs via the OpenRouter API, and post-processes the raw outputs into a format ready for evaluation.

The module consists of three scripts run in sequence.

---

## Requirements

```bash
pip install requests
```

The OpenRouter API key must be provided in a `config.json` file in the working directory:

```json
{
  "openrouter_api_key": "your_api_key_here"
}
```

---

## Expected Folder Structure

Before running, each subset folder must contain the following files:

```
<subset>/
├── descriptions.jsonl        — id, label, description
├── ontology_snippets.jsonl   — id, label, ontology_snippet
├── prefixes.json             — prefix -> namespace URI mapping
└── example.txt               — few-shot example for this subset
```

---

## Pipeline Overview

```
descriptions.jsonl
ontology_snippets.jsonl
prefixes.json
example.txt
      │
      ▼
1. generate_prompts.py    →  <subset>/prompts.jsonl
      │
      ▼
2. run_openrouter.py      →  llm-output/<subset>_<model>.jsonl
      │
      ▼
3. postprocess.py         →  processed-output/<subset>_<model>_processed.jsonl
```

---

## Scripts

### Step 1: generate_prompts.py

Reads the dataset files for a given subset and generates a prompt file. Each prompt includes a fixed system prompt, a one-shot example from `example.txt`, the natural language description, relevant prefix declarations, and ontology term definitions.

The last record in `descriptions.jsonl` is used as the few-shot example and is excluded from the generated prompts.

```bash
python generate_prompts.py --subset invoice-dataset
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--subset` | (required) | Subset folder name (e.g. `invoice-dataset`, `dcat-dataset`) |
| `--data_root` | `.` | Root directory containing subset folders |
| `--output` | `<subset>/prompts.jsonl` | Output path for the prompt file |

**Output format** (`prompts.jsonl`, one record per line):

```json
{
  "id": "invoice-1",
  "system_prompt": "You are an expert in Semantic Web technologies...",
  "user_prompt": "Translate the following natural language constraint..."
}
```

---

### Step 2: run_openrouter.py

Sends the prompts to the OpenRouter API and saves raw model outputs. Runs all configured models sequentially, producing one output file per model.

```bash
python run_openrouter.py --subset invoice-dataset
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--subset` | (required) | Subset folder name |
| `--data_root` | `.` | Root directory containing subset folders |
| `--config` | `config.json` | Path to the config file containing the API key |

**Models run by default:**

- `anthropic/claude-opus-4.7`
- `z-ai/glm-5.1`
- `qwen/qwen3.5-397b-a17b`

**Output** (one file per model in `llm-output/`):

```
llm-output/
└── invoice-dataset_anthropic-claude-opus-4-7.jsonl
└── invoice-dataset_z-ai-glm-5-1.jsonl
└── invoice-dataset_qwen-qwen3-5-397b-a17b.jsonl
```

Each output record contains:

| Field | Description |
|-------|-------------|
| `id` | Record identifier |
| `output` | Raw model output text (`null` on error) |
| `runtime_sec` | Inference time in seconds |
| `token_stats` | Prompt, completion, and total token counts |
| `error` | Error message, if the call failed |

If a run is interrupted, re-running the script will skip already-processed records and resume from where it left off.

---

### Step 3: postprocess.py

Cleans up raw LLM outputs (removes markdown code fences and formatting artifacts) and attaches the corresponding reference SHACL shape to each record.

```bash
# Process a single file
python postprocess.py --input llm-output/invoice-dataset_anthropic-claude-opus-4-7.jsonl

# Process all files in a folder
python postprocess.py --input llm-output/
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--input` | (required) | Path to a single `.jsonl` file or a folder of `.jsonl` files |
| `--data_root` | `.` | Root directory containing subset folders (for locating reference SHACL files) |
| `--output_dir` | `processed-output/` | Output directory |

**Output** (one file per input file in `processed-output/`):

```json
{
  "id": "invoice-1",
  "output_shacl": "@prefix ...\n:InvoiceShape a sh:NodeShape ...",
  "reference_shacl": "@prefix ...\n:InvoiceShape a sh:NodeShape ..."
}
```

The processed output files are the direct input to the [Shapes Evaluation](../Shapes-Evaluation/) module.

---

## Notes

- Temperature is set to `0.0` for all model calls to ensure deterministic outputs.
- The `example.txt` file for each subset should contain a complete formatted example in the same structure as the user prompt, including the natural language description, prefixes, ontology terms, and expected SHACL output.
- To add or change the models used, edit the `MODELS` list at the top of `run_openrouter.py`.