"""
run_gemini.py

Call the Gemini API with generated prompt files and save results.

Usage:
    python run_gemini.py --input <nl_prompts.jsonl> [--model <model-name>]

Example:
    python run_gemini.py --input examples/my-dataset/output_data_augmented_nl_prompts.jsonl
    python run_gemini.py --input examples/my-dataset/output_data_augmented_nl_prompts.jsonl --model gemini-2.5-pro

Model and API version:
    --model selects the Gemini model (default: gemini-3.1-pro-preview, the model
    used for the released dataset). Preview models are only served under the
    "v1beta" API version, whereas recent google-genai releases default to "v1",
    so the client is created with --api-version (default: v1beta).

Requires:
    - GEMINI_API_KEY environment variable set
    - pip install google-genai

Output (two files, both written next to the input by default):
    <input_stem>_generated_description.jsonl
        One record per prompt: {"id", "generated_description"}.
    <input_stem>_for_review.jsonl
        Ready to open in Description-Reviewer/UI_annotator.py:
        {"id", "shacl", "description"}. It is rebuilt from the generated
        descriptions at the end of every run, so it also covers resumed runs.
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

DEFAULT_MODEL = "gemini-3.1-pro-preview"
DEFAULT_API_VERSION = "v1beta"
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


def call_gemini(client, model, system_prompt, user_prompt):
    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            response = client.models.generate_content(
                model=model,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.2,
                ),
            )
            return response.text.strip()
        except Exception as e:
            if getattr(e, "code", None) == 404:
                # Retrying cannot help: the model name or API version is wrong.
                print(f"\nError: model '{model}' was not found by the Gemini API: {e}\n"
                      f"Check the model name (--model) and, for preview models, "
                      f"the API version (--api-version).", file=sys.stderr)
                sys.exit(1)
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
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Gemini model name (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--api-version",
        default=DEFAULT_API_VERSION,
        help=f"Gemini API version (default: {DEFAULT_API_VERSION}; preview models need v1beta).",
    )
    parser.add_argument(
        "--review-output",
        default=None,
        help="Path of the file to open in the Description Reviewer "
             "({id, shacl, description}). Defaults to <input_stem>_for_review.jsonl.",
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

    if args.review_output:
        review_path = args.review_output
    else:
        stem = os.path.splitext(args.input)[0]
        review_path = f"{stem}_for_review.jsonl"

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(api_version=args.api_version),
    )

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
    print(f"Processing {len(remaining)} records with model {args.model}...")

    with open(output_path, "a", encoding="utf-8") as out_f:
        for i, record in enumerate(remaining, 1):
            record_id = record["id"]
            print(f"  [{i}/{len(remaining)}] {record_id} ...", end=" ", flush=True)

            description = call_gemini(client, args.model, record["system_prompt"], record["user_prompt"])

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

    # Build the reviewer-ready file from every generated description. Rebuilding
    # from scratch keeps it consistent with the output file, including after a
    # resumed run.
    generated = {}
    with open(output_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rec = json.loads(line)
                generated[rec["id"]] = rec["generated_description"]

    review_count = 0
    with open(review_path, "w", encoding="utf-8") as rf:
        for record in records:
            if record["id"] not in generated:
                continue
            if not record.get("shacl"):
                print(f"[WARN] Prompt record '{record['id']}' has no 'shacl' field "
                      f"(prompt file from an older get_nl_prompt.py?). Re-run get_nl_prompt.py.",
                      file=sys.stderr)
            rf.write(json.dumps({
                "id": record["id"],
                "shacl": record.get("shacl", ""),
                "description": generated[record["id"]],
            }, ensure_ascii=False) + "\n")
            review_count += 1
    print(f"Reviewer input ({review_count} records) saved to {review_path}")


if __name__ == "__main__":
    main()