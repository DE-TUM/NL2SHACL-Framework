"""
run_gemini.py

Call the Gemini API with generated prompt files and save results.

Usage:
    python run_gemini.py --input <nl_prompts.jsonl>

Example:
    python run_gemini.py --input examples/my-dataset/output_data_augmented_nl_prompts.jsonl

Requires:
    - GEMINI_API_KEY environment variable set
    - pip install google-genai

Output:
    <input_stem>_generated_description.jsonl (in the same directory as input)
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
    parser = argparse.ArgumentParser(
        description="Run Gemini API on NL prompt files."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to the prompt JSONL file produced by generate_nl_prompts.py.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSONL path. Defaults to <input_stem>_generated_description.jsonl.",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        output_path = args.output
    else:
        stem = os.path.splitext(args.input)[0]
        output_path = f"{stem}_generated_description.jsonl"

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    records = load_prompts(args.input)
    if not records:
        print(f"Error: No records found in {args.input}", file=sys.stderr)
        sys.exit(1)

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
    print(f"Processing {len(remaining)} records...")

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