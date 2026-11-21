"""
filename_utils.py
-----------------
Shared parsing of evaluation file names, used by run_evaluation.py,
compute_metrics.py and compute_metrics_by_model.py so that all three agree on
which subset and model a file belongs to.

File name convention
    <subset>-dataset_<model>[_processed][_eval].jsonl

    invoice-dataset_my-model_processed.jsonl    -> subset "invoice-dataset", model "my-model"
    snik-dataset_rule_based_eval.jsonl          -> subset "snik-dataset",    model "rule_based"
    my_data-dataset_gpt-4o_eval.jsonl           -> subset "my_data-dataset", model "gpt-4o"

The subset is the part up to and including the first "-dataset" that is followed
by "_"; everything after that "_" is the model. Hyphens and underscores are
allowed in both parts. A trailing "_processed" / "_eval" is ignored.

If the file name does not contain "-dataset_", it is split at the first "_"
into <subset>_<model>. If it contains no "_" at all, the model is "unknown".
Parsing never raises an error.
"""

import os
import re

_SUFFIXES = ("_processed", "_eval")


def strip_suffixes(stem: str, suffixes=_SUFFIXES) -> str:
    """Remove trailing suffixes (and only trailing ones) from a file stem."""
    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            if stem.endswith(suffix) and len(stem) > len(suffix):
                stem = stem[: -len(suffix)]
                changed = True
    return stem


def parse_filename(filename: str):
    """
    Return (subset, model) parsed from an evaluation file name.

    The subset keeps its "-dataset" suffix (it is the name of the dataset
    folder); use short_subset() for a shorter display name.
    """
    stem = strip_suffixes(os.path.splitext(os.path.basename(filename))[0])

    match = re.match(r"^(.+?[-_]dataset)_(.+)$", stem)
    if match:
        return match.group(1), match.group(2)

    parts = stem.split("_", 1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0], parts[1]
    return stem or "unknown", "unknown"


def short_subset(subset: str) -> str:
    """Display name of a subset: "invoice-dataset" -> "invoice"."""
    return re.sub(r"[-_]dataset$", "", subset) or subset
