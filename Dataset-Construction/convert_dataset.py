"""
convert_dataset.py

Converts an augmented JSONL file into a structured dataset:

  <out_dir>/
    <prefix>-descriptions.jsonl      — id + label + description
    <prefix>-ontology_snippets.jsonl — id + label + ontology_snippet
    shacl/
      <prefix>-1.ttl                 — one TTL per entry, first line is # label: ...
      <prefix>-2.ttl
      ...

Only records where both description and ontology_snippet are non-empty are written.
Records missing either field are warned and skipped.

Usage:
    python convert_dataset.py <augmented.jsonl> \
        --descriptions <reviewed.jsonl> \
        --out-dir <dir> \
        --prefix <prefix>

Example:
    python convert_dataset.py output_data_augmented.jsonl \
        --descriptions output_data_description_reviewed.jsonl \
        --out-dir examples/my-dataset/snik-dataset \
        --prefix snik

Arguments:
    augmented.jsonl         Augmented JSONL from ontology_augment.py.
                            Provides shacl and ontology_snippet fields.
    --descriptions          Reviewed descriptions JSONL from the Description Reviewer.
                            Each record must have an id and a description field.
                            Descriptions are matched to augmented records by id.
    --out-dir               Output directory (default: <augmented_stem>_dataset/).
    --prefix                ID prefix for output records (e.g. snik, invoice).
                            Default: snik.
"""

import argparse
import json
from pathlib import Path


def clean_label(raw_id: str) -> str:
    """Remove leading ':' or prefix from the original shape id."""
    return raw_id.lstrip(":")


def load_descriptions(path: Path) -> dict:
    """Load reviewed descriptions keyed by original id."""
    descriptions = {}
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"[WARN] descriptions file line {lineno}: JSON parse error: {e}")
                continue
            record_id = record.get("id")
            description = record.get("description", "").strip()
            if record_id and description:
                descriptions[record_id] = description
    return descriptions


def main():
    ap = argparse.ArgumentParser(
        description="Convert augmented JSONL and reviewed descriptions into a structured dataset."
    )
    ap.add_argument(
        "jsonl",
        help="Input augmented JSONL file from ontology_augment.py.",
    )
    ap.add_argument(
        "--descriptions",
        required=True,
        help="Reviewed descriptions JSONL file. Each record must have 'id' and 'description' fields.",
    )
    ap.add_argument(
        "--out-dir",
        default=None,
        help="Output directory (default: <augmented_stem>_dataset/).",
    )
    ap.add_argument(
        "--prefix",
        default="snik",
        help="ID prefix for output records (e.g. snik, invoice). Default: snik.",
    )
    args = ap.parse_args()

    src = Path(args.jsonl)
    if not src.exists():
        raise SystemExit(f"Not found: {src}")

    desc_src = Path(args.descriptions)
    if not desc_src.exists():
        raise SystemExit(f"Descriptions file not found: {desc_src}")

    out_dir = Path(args.out_dir) if args.out_dir \
              else src.with_name(src.stem + "_dataset")
    shacl_dir = out_dir / "shacl"
    shacl_dir.mkdir(parents=True, exist_ok=True)

    desc_path    = out_dir / f"{args.prefix}-descriptions.jsonl"
    snippet_path = out_dir / f"{args.prefix}-ontology_snippets.jsonl"

    descriptions = load_descriptions(desc_src)
    print(f"Loaded {len(descriptions)} reviewed descriptions from {desc_src}")

    desc_lines    = []
    snippet_lines = []
    counter       = 0   # counts all input records (for sequential id assignment)
    written       = 0   # counts records actually written
    skipped       = []  # (orig_id, reason)

    with src.open(encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError as e:
                print(f"Skipping bad JSON line: {e}")
                continue

            counter += 1
            orig_id = record.get("id", f"entry-{counter}")
            label   = clean_label(orig_id)
            shacl   = record.get("shacl", "")
            snippet = record.get("ontology_snippet")

            description = descriptions.get(orig_id, "")

            # Check both fields before writing
            missing = []
            if not description:
                missing.append("description")
            if not snippet:
                missing.append("ontology_snippet")

            if missing:
                skipped.append((orig_id, ", ".join(missing)))
                continue

            written += 1
            new_id   = f"{args.prefix}-{written}"

            # descriptions.jsonl
            desc_lines.append(json.dumps(
                {"id": new_id, "label": label, "description": description},
                ensure_ascii=False
            ))

            # shacl/<new_id>.ttl
            ttl_path = shacl_dir / f"{new_id}.ttl"
            ttl_content = f"# label: {label}\n{shacl}"
            ttl_path.write_text(ttl_content, encoding="utf-8")

            # ontology_snippets.jsonl
            snippet_lines.append(json.dumps(
                {"id": new_id, "label": label, "ontology_snippet": snippet},
                ensure_ascii=False
            ))

    desc_path.write_text("\n".join(desc_lines) + "\n", encoding="utf-8")
    snippet_path.write_text("\n".join(snippet_lines) + "\n", encoding="utf-8")

    print(f"Converted {written} of {counter} entries to {out_dir}/")
    print(f"  {args.prefix}-descriptions.jsonl      ({written} lines)")
    print(f"  {args.prefix}-ontology_snippets.jsonl ({written} lines)")
    print(f"  shacl/                                ({written} .ttl files)")

    if skipped:
        print(f"\n[WARN] {len(skipped)} records skipped (missing fields):")
        for orig_id, reason in skipped:
            print(f"  {orig_id}: missing {reason}")

if __name__ == "__main__":
    main()