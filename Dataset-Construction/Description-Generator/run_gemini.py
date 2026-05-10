"""
run_gemini.py

Call the Gemini API with generated prompt files and save results.

Usage:
    python run_gemini.py <subset_name>

Example:
    python run_gemini.py epo

Requires:
    - GEMINI_API_KEY environment variable set
    - pip install google-genai

Output:
    <subset_name>-dataset/<subset_name>-generated_description.jsonl
"""

import argparse
import json
import os
import sys
import time

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("Error: google-genai package not found. Run: pip install google-genai", file=sys.stderr)
    sys.exit(1)

MODEL = "gemini-3.1-pro-preview"
RETRY_LIMIT = 3
RETRY_DELAY = 5  # seconds between retries


def load_prompts(prompt_path):
    records = []
    with open(prompt_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def call_gemini(client, system_prompt, user_prompt):
    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.2,
                ),
            )
            return response.text.strip()
        except Exception as e:
            print(f"  Attempt {attempt} failed: {e}", file=sys.stderr)
            if attempt < RETRY_LIMIT:
                time.sleep(RETRY_DELAY)
    return None


def main():
    parser = argparse.ArgumentParser(description="Run Gemini API on NL prompt files for a dataset subset.")
    parser.add_argument("subset", help="Subset name, e.g. epo, invoice, snik")
    args = parser.parse_args()

    subset = args.subset.lower()
    subset_dir = f"{subset}-dataset"

    if not os.path.isdir(subset_dir):
        print(f"Error: Subset directory not found: {subset_dir}", file=sys.stderr)
        sys.exit(1)

    prompt_path = os.path.join(subset_dir, f"{subset}-get_nl_prompt.jsonl")
    if not os.path.isfile(prompt_path):
        print(f"Error: Prompt file not found: {prompt_path}", file=sys.stderr)
        print(f"Run generate_prompts.py {subset} first.", file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    records = load_prompts(prompt_path)
    output_path = os.path.join(subset_dir, f"{subset}-generated_description.jsonl")

    # Load already-processed IDs to allow resuming interrupted runs
    processed_ids = set()
    if os.path.isfile(output_path):
        with open(output_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    existing = json.loads(line)
                    processed_ids.add(existing["id"])
        print(f"Resuming: {len(processed_ids)} records already processed.")

    remaining = [r for r in records if r["id"] not in processed_ids]
    print(f"Processing {len(remaining)} records for subset '{subset}'...")

    with open(output_path, "a", encoding="utf-8") as out_f:
        for i, record in enumerate(remaining, 1):
            record_id = record["id"]
            print(f"  [{i}/{len(remaining)}] {record_id} ...", end=" ", flush=True)

            description = call_gemini(client, record["system_prompt"], record["user_prompt"])

            if description is None:
                print("FAILED (skipped)")
                continue

            result = {
                "id": record_id,
                "generated_description": description,
            }
            out_f.write(json.dumps(result, ensure_ascii=False) + "\n")
            out_f.flush()
            print("OK")

    print(f"\nDone. Results saved to {output_path}")


if __name__ == "__main__":
    main()
