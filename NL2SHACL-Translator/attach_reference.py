"""
attach_reference.py

Attaches reference SHACL shapes to translator output records.

After your translation system produces output_shacl for each record,
this script reads the corresponding reference shape from the dataset
and writes a combined output file ready for the Shapes Evaluation module.

Usage:
    python attach_reference.py \
        --input your_outputs.jsonl \
        --dataset-dir dataset \
        --output processed-output/invoice-dataset_mysystem_processed.jsonl
"""

import argparse
import json
import os
import sys


def get_subset_from_id(record_id):
    """Infer subset name from record ID (e.g. 'invoice-1' -> 'invoice-dataset')."""
    prefix = record_id.rsplit("-", 1)[0]
    return f"{prefix}-dataset"


def load_reference_shacl(record_id, dataset_dir, subset=None):
    """Load the reference SHACL Turtle file for a given record ID."""
    if subset is None:
        subset = get_subset_from_id(record_id)
    shacl_path = os.path.join(dataset_dir, subset, "shacl", f"{record_id}.ttl")
    if not os.path.exists(shacl_path):
        return None, shacl_path
    with open(shacl_path, encoding="utf-8") as f:
        return f.read(), shacl_path


def main():
    parser = argparse.ArgumentParser(
        description="Attach reference SHACL shapes to translator output records."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to your translator output JSONL file. "
             "Each record must have an 'id' and 'output_shacl' field.",
    )
    parser.add_argument(
        "--dataset-dir",
        default="dataset",
        help="Path to the NL2SHACL-Dataset directory (default: dataset/).",
    )
    parser.add_argument(
        "--subset",
        default=None,
        help="Subset name (e.g. 'invoice-dataset'). "
             "If not provided, inferred from the record ID.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path for the output JSONL file, ready for the Shapes Evaluation module.",
    )
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: input file not found: {args.input}")
        sys.exit(1)

    if not os.path.exists(args.dataset_dir):
        print(f"Error: dataset directory not found: {args.dataset_dir}")
        print("Clone the dataset first: git clone https://github.com/DE-TUM/NL2SHACL-Dataset dataset")
        sys.exit(1)

    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    records_in = []
    with open(args.input, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records_in.append(json.loads(line))

    missing = []
    records_out = []

    for record in records_in:
        record_id = record["id"]
        reference_shacl, shacl_path = load_reference_shacl(
            record_id, args.dataset_dir, subset=args.subset
        )
        if reference_shacl is None:
            missing.append(shacl_path)
            reference_shacl = None

        records_out.append({
            "id": record_id,
            "output_shacl": record.get("output_shacl"),
            "reference_shacl": reference_shacl,
        })

    with open(args.output, "w", encoding="utf-8") as f:
        for record in records_out:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"Written {len(records_out)} records to {args.output}")

    if missing:
        print(f"\nWarning: reference SHACL not found for {len(missing)} record(s):")
        for path in missing:
            print(f"  {path}")
        print("These records will have reference_shacl set to null.")


if __name__ == "__main__":
    main()