"""
Dataset Converter
Converts invoice_data_augmented.jsonl into a structured dataset:

  <out_dir>/
    index.jsonl              — id + label (original id without ":")
    descriptions.jsonl       — id + label + description (from nl)
    shacl/
      invoice-1.ttl          — one TTL per entry, first line is # label: ...
      invoice-2.ttl
      ...
    ontology_snippets.jsonl  — id + label + ontology_snippet

Usage:
  python convert_dataset.py <augmented.jsonl> [--out-dir <dir>] [--prefix invoice]
"""

import argparse
import json
import re
from pathlib import Path


def clean_label(raw_id: str) -> str:
    """Remove leading ':' from the original id."""
    return raw_id.lstrip(":")


def main():
    ap = argparse.ArgumentParser(description="Convert augmented JSONL to dataset")
    ap.add_argument("jsonl", help="Input augmented JSONL file")
    ap.add_argument("--out-dir", default=None,
                    help="Output directory (default: <jsonl_stem>_dataset/)")
    ap.add_argument("--prefix", default="invoice",
                    help="New ID prefix (default: invoice)")
    args = ap.parse_args()

    src = Path(args.jsonl)
    if not src.exists():
        raise SystemExit(f"Not found: {src}")

    out_dir = Path(args.out_dir) if args.out_dir \
              else src.with_name(src.stem + "_dataset")
    shacl_dir = out_dir / "shacl"
    shacl_dir.mkdir(parents=True, exist_ok=True)

    index_path     = out_dir / "index.jsonl"
    desc_path      = out_dir / "descriptions.jsonl"
    snippet_path   = out_dir / "ontology_snippets.jsonl"

    index_lines   = []
    desc_lines    = []
    snippet_lines = []

    counter = 0

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
            new_id = f"{args.prefix}-{counter}"
            label  = clean_label(record.get("id", f"entry-{counter}"))
            nl     = record.get("nl", "")
            shacl  = record.get("shacl", "")
            snippet = record.get("ontology_snippet", {})

            # ── index.jsonl ───────────────────────────────────────────────
            index_lines.append(json.dumps(
                {"id": new_id, "label": label},
                ensure_ascii=False
            ))

            # ── descriptions.jsonl ────────────────────────────────────────
            desc_lines.append(json.dumps(
                {"id": new_id, "label": label, "description": nl},
                ensure_ascii=False
            ))

            # ── shacl/<new_id>.ttl ────────────────────────────────────────
            ttl_path = shacl_dir / f"{new_id}.ttl"
            ttl_content = f"# label: {label}\n{shacl}"
            ttl_path.write_text(ttl_content, encoding="utf-8")

            # ── ontology_snippets.jsonl ───────────────────────────────────
            snippet_lines.append(json.dumps(
                {"id": new_id, "label": label, "ontology_snippet": snippet},
                ensure_ascii=False
            ))

    # write the three JSONL files
    index_path.write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    desc_path.write_text("\n".join(desc_lines) + "\n", encoding="utf-8")
    snippet_path.write_text("\n".join(snippet_lines) + "\n", encoding="utf-8")

    print(f"Converted {counter} entries → {out_dir}/")
    print(f"  index.jsonl              ({counter} lines)")
    print(f"  descriptions.jsonl       ({counter} lines)")
    print(f"  shacl/                   ({counter} .ttl files)")
    print(f"  ontology_snippets.jsonl  ({counter} lines)")


if __name__ == "__main__":
    main()