"""
run_llm_openrouter.py
---------------------
Send prompts from a prompts.jsonl file to the OpenRouter API and save raw outputs.
Runs all three models sequentially, producing one output file per model.

Usage:
    python run_llm_openrouter.py --subset <folder_name> [--data_root <path>] [--config <path>]

Examples:
    python run_llm_openrouter.py --subset invoice-dataset
    python run_llm_openrouter.py --subset dcat-dataset --data_root ./my_data
"""

import argparse
import json
import os
import sys
import time

import requests


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODELS = [
    "anthropic/claude-opus-4.7",
    "z-ai/glm-5.1",
    "qwen/qwen3.5-397b-a17b",
]

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OUTPUT_DIR = "llm-output"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_config(config_path: str) -> dict:
    """Load config.json and return as dict."""
    if not os.path.isfile(config_path):
        print(f"[ERROR] Config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


def load_jsonl(path: str) -> list:
    """Load a JSONL file, return a list of dicts in order."""
    records = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"[WARN] {path}:{lineno} - JSON parse error: {e}", file=sys.stderr)
    return records


def load_existing_outputs(output_path: str) -> set:
    """Load already-processed IDs from output file to support resuming."""
    processed = set()
    if not os.path.isfile(output_path):
        return processed
    with open(output_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if "id" in obj:
                    processed.add(obj["id"])
            except json.JSONDecodeError:
                continue
    return processed


def normalize_model_name(model: str) -> str:
    """Convert 'anthropic/claude-opus-4.7' to 'anthropic-claude-opus-4-7' for filenames."""
    return model.replace("/", "-").replace(".", "-")


def call_openrouter(
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> tuple[str, dict]:
    """
    Call the OpenRouter API and return (output_text, token_stats).
    Raises on HTTP or API errors.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "temperature": 0.0,
    }

    response = requests.post(OPENROUTER_URL, headers=headers, data=json.dumps(payload))
    response.raise_for_status()

    data = response.json()

    # Extract output text
    output_text = data["choices"][0]["message"]["content"].strip()

    # Extract token usage (mirrors gemini field names)
    usage = data.get("usage", {})
    token_stats = {
        "prompt_token_count":     usage.get("prompt_tokens"),
        "candidates_token_count": usage.get("completion_tokens"),
        "total_token_count":      usage.get("total_tokens"),
    }

    return output_text, token_stats


# ---------------------------------------------------------------------------
# Per-model inference run
# ---------------------------------------------------------------------------

def run_model(
    model: str,
    prompts: list,
    api_key: str,
    output_path: str,
):
    total = len(prompts)
    safe_model = normalize_model_name(model)

    print(f"\n{'=' * 60}")
    print(f"Model:       {model}")
    print(f"Output file: {output_path}")
    print(f"Total prompts: {total}")

    # Resume support
    processed_ids = load_existing_outputs(output_path)
    if processed_ids:
        print(f"Resuming: {len(processed_ids)} entries already processed, skipping.")

    print("\nStarting inference...\n")
    success_count = 0
    error_count   = 0

    with open(output_path, "a", encoding="utf-8") as out_f:
        for idx, prompt_record in enumerate(prompts, 1):
            record_id     = prompt_record.get("id", f"unknown_{idx}")
            system_prompt = prompt_record.get("system_prompt", "")
            user_prompt   = prompt_record.get("user_prompt", "")

            # Skip already-processed entries
            if record_id in processed_ids:
                continue

            # Progress print every 5 entries and on first entry
            if idx % 5 == 0 or idx == 1:
                print(f"[{idx}/{total}] Processing '{record_id}'...")

            try:
                start_time = time.monotonic()

                output_text, token_stats = call_openrouter(
                    api_key, model, system_prompt, user_prompt
                )

                elapsed = time.monotonic() - start_time

                result = {
                    "id":          record_id,
                    "output":      output_text,
                    "runtime_sec": round(elapsed, 3),
                    "token_stats": token_stats,
                }
                success_count += 1

            except Exception as e:
                elapsed = time.monotonic() - start_time
                print(f"  [ERROR] ID '{record_id}': {e}")
                result = {
                    "id":          record_id,
                    "output":      None,
                    "runtime_sec": round(elapsed, 3),
                    "token_stats": None,
                    "error":       str(e),
                }
                error_count += 1

            out_f.write(json.dumps(result, ensure_ascii=False) + "\n")
            out_f.flush()

            # Print preview every 5 entries
            if idx % 5 == 0:
                preview = result.get("output") or ""
                print(f"  Output preview: {preview[:120].strip()!r}...")
                print(f"  Runtime: {result['runtime_sec']}s | "
                      f"Tokens: {result['token_stats']}\n")

    print(f"\nModel done: {model}")
    print(f"  Successful: {success_count}  |  Errors: {error_count}")
    print(f"  Output saved to: {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Run OpenRouter inference on NL2SHACL prompts for multiple models."
    )
    parser.add_argument(
        "--subset", required=True,
        help="Subdirectory name under data_root (e.g. 'dcat-dataset', 'invoice-dataset')"
    )
    parser.add_argument(
        "--data_root", default=".",
        help="Root directory containing subset folders (default: current directory)"
    )
    parser.add_argument(
        "--config", default="config.json",
        help="Path to config.json containing API key (default: config.json)"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Paths
    subset_dir   = os.path.join(args.data_root, args.subset)
    prompts_path = os.path.join(subset_dir, "prompts.jsonl")
    output_dir   = os.path.join(args.data_root, OUTPUT_DIR)

    # Validate inputs
    if not os.path.isdir(subset_dir):
        print(f"[ERROR] Subset directory not found: {subset_dir}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(prompts_path):
        print(f"[ERROR] Prompts file not found: {prompts_path}", file=sys.stderr)
        sys.exit(1)

    # Load config and API key
    config  = load_config(args.config)
    api_key = config.get("openrouter_api_key")
    if not api_key:
        print("[ERROR] 'openrouter_api_key' not found in config.json", file=sys.stderr)
        sys.exit(1)

    # Load prompts once, reuse across all models
    prompts = load_jsonl(prompts_path)

    print(f"Subset:        {args.subset}")
    print(f"Prompts file:  {prompts_path}")
    print(f"Total prompts: {len(prompts)}")
    print(f"Models:        {', '.join(MODELS)}")

    # Create output dir if needed
    os.makedirs(output_dir, exist_ok=True)

    # Run each model
    for model in MODELS:
        safe_model  = normalize_model_name(model)
        output_path = os.path.join(output_dir, f"{args.subset}_{safe_model}.jsonl")
        run_model(model, prompts, api_key, output_path)

    print(f"\n{'=' * 60}")
    print("All models complete.")
    print(f"Outputs saved to: {output_dir}/")
    print("=" * 60)


if __name__ == "__main__":
    main()