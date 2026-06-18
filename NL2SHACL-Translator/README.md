# NL2SHACL Translator

This module implements the translation step of the NL2SHACL-Bench pipeline. It takes natural language descriptions and ontology snippets from the NL2SHACL-Dataset as input and produces translated SHACL shapes ready for evaluation.

The module supports two usage paths:

- **Custom translator**: Integrate your own translation system (rule-based, local model, or any other approach) using the provided I/O specification and helper scripts.
- **LLM translator**: Use the built-in prompting pipeline to run evaluations with LLMs via the OpenRouter API.

---

## Path A: Using a Custom Translator

To evaluate your own NL2SHACL system on the dataset, follow the three-step workflow defined in [`TRANSLATOR_SPEC.md`](TRANSLATOR_SPEC.md):

1. Run your system on the dataset input files to produce translated SHACL shapes.
2. Attach reference shapes using the provided helper script.
3. Pass the output to the Shapes Evaluation module.

See [`TRANSLATOR_SPEC.md`](TRANSLATOR_SPEC.md) for the full input/output specification and example commands.

### Reference Implementation

A minimal rule-based translator is provided in `translator_example_rule_based.py`. It requires no API access and handles a small subset of simple constraint types (datatype, minCount, maxCount). It is intended as a runnable reference for the I/O contract, not as a competitive baseline.

```bash
python NL2SHACL-Translator/translator_example_rule_based.py \
    --subset dbpedia-dataset \
    --dataset-dir dataset
```

### Helper Scripts

**`attach_reference.py`** attaches reference SHACL shapes from the dataset to your translator outputs, producing a file ready for the Shapes Evaluation module.

```bash
python NL2SHACL-Translator/attach_reference.py \
    --input your_outputs.jsonl \
    --dataset-dir dataset \
    --output processed-output/<subset>_<system>_processed.jsonl
```

---

## Path B: Using the LLM Translator (OpenRouter)

This path uses a prompting-based pipeline to call LLMs via the OpenRouter API. It consists of three scripts run in sequence.

### Requirements

```bash
pip install requests
```

Provide your OpenRouter API key in a `config.json` file in the working directory:

```json
{
  "openrouter_api_key": "your_api_key_here"
}
```

### Expected Input Structure

Before running, ensure the dataset is available under `dataset/` (see the top-level README for setup instructions). Each subset folder must also contain an `example.txt` file with the few-shot example for that subset.

### Pipeline Overview

```
dataset/<subset>/
  <subset>-descriptions.jsonl
  <subset>-ontology_snippets.jsonl
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

### Step 1: generate_prompts.py

Reads the dataset files for a given subset and generates a prompt file. Each prompt includes a fixed system prompt, a one-shot example from `example.txt`, the natural language description, relevant prefix declarations, and ontology term definitions.

The last record in the descriptions file is used as the few-shot example and is excluded from the generated prompts.

```bash
python NL2SHACL-Translator/generate_prompts.py --subset invoice-dataset
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--subset` | (required) | Subset folder name (e.g. `invoice-dataset`, `dcat-dataset`) |
| `--data_root` | `.` | Root directory containing subset folders |
| `--output` | `<subset>/prompts.jsonl` | Output path for the prompt file |

### Step 2: run_openrouter.py

Sends the prompts to the OpenRouter API and saves raw model outputs. Runs all configured models sequentially, producing one output file per model.

```bash
python NL2SHACL-Translator/run_openrouter.py --subset invoice-dataset
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--subset` | (required) | Subset folder name |
| `--data_root` | `.` | Root directory containing subset folders |
| `--config` | `config.json` | Path to the config file containing the API key |

Models run by default: `anthropic/claude-opus-4.7`, `z-ai/glm-5.1`, `qwen/qwen3.5-397b-a17b`. To change the models, edit the `MODELS` list at the top of `run_openrouter.py`.

If a run is interrupted, re-running will skip already-processed records and resume from where it left off.

### Step 3: postprocess.py

Cleans up raw LLM outputs (removes markdown code fences and formatting artifacts) and attaches the corresponding reference SHACL shape to each record.

```bash
# Process a single file
python NL2SHACL-Translator/postprocess.py \
    --input llm-output/invoice-dataset_anthropic-claude-opus-4-7.jsonl

# Process all files in a folder
python NL2SHACL-Translator/postprocess.py --input llm-output/
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--input` | (required) | Path to a single `.jsonl` file or a folder of `.jsonl` files |
| `--data_root` | `.` | Root directory containing subset folders |
| `--output_dir` | `processed-output/` | Output directory |

The processed output files are the direct input to the [Shapes Evaluation](../Shapes-Evaluation/) module.

---

## Notes

- Temperature is set to `0.0` for all LLM calls to ensure deterministic outputs.
- The `example.txt` file for each subset should contain a complete formatted example including the natural language description, prefixes, ontology terms, and expected SHACL output.