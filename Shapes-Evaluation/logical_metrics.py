"""
logical_metrics.py
------------------
Dataset-level metrics of the logical evaluator (see logical_equivalence.py),
shared by compute_metrics.py and compute_metrics_by_model.py.

Definitions
    N_logical                records for which the logical check was attempted
                             (status "success" or "undetermined")
    N_logical_determined     attempted records with a definite outcome,
                             equivalent or non-equivalent
    N_logical_equivalent     determined records that are equivalent
    N_logical_non_equivalent determined records that are not equivalent
    N_logical_undetermined   attempted records without a definite outcome
                             (timeout, construct not supported by SHACL2FOL, ...)
    LER                      Logical Equivalence Rate = N_logical_equivalent / N_logical_determined
    LER_coverage             N_logical_determined / N_logical

    N_output_stricter, N_output_more_permissive, N_incomparable
        Non-equivalent records whose two containment checks are both decided,
        split by the direction of the mismatch.

Records with status "unavailable" (tools missing) were not attempted and are
reported separately as N_logical_unavailable; they do not enter any rate.
All values are None when the logical evaluator was not run.
"""

LOGICAL_FIELDS = [
    "N_logical", "N_logical_equivalent", "N_logical_non_equivalent", "N_logical_undetermined",
    "LER", "LER_coverage",
    "N_output_stricter", "N_output_more_permissive", "N_incomparable",
]


def _rate(count, total):
    return None if not total else count / total


def logical_metrics(records: list) -> dict:
    """Return the logical metrics of a group of evaluation records."""
    results = [r["logical"] for r in records if r.get("logical")]
    attempted = [x for x in results if x.get("status") in ("success", "undetermined")]
    determined = [x for x in attempted if x.get("equivalent") is not None]
    equivalent = [x for x in determined if x["equivalent"] is True]

    if not attempted:
        return {name: None for name in LOGICAL_FIELDS}

    def direction_count(direction):
        return sum(1 for x in determined if x.get("direction") == direction)

    return {
        "N_logical":                len(attempted),
        "N_logical_equivalent":     len(equivalent),
        "N_logical_non_equivalent": len(determined) - len(equivalent),
        "N_logical_undetermined":   len(attempted) - len(determined),
        "LER":                      _rate(len(equivalent), len(determined)),
        "LER_coverage":             _rate(len(determined), len(attempted)),
        "N_output_stricter":        direction_count("output_stricter"),
        "N_output_more_permissive": direction_count("output_more_permissive"),
        "N_incomparable":           direction_count("incomparable"),
    }


def logical_log_lines(records: list) -> list:
    """Diagnostic lines for the evaluation logs."""
    results = [r["logical"] for r in records if r.get("logical")]
    if not results:
        return []
    m = logical_metrics(records)
    unavailable = sum(1 for x in results if x.get("status") == "unavailable")
    reasons = {}
    for x in results:
        if x.get("status") == "undetermined":
            key = (x.get("reason") or "unknown").split(":")[0] + (
                ":" + x["reason"].split(":", 1)[1] if x.get("reason") and x["reason"].startswith("unsupported") else "")
            reasons[key] = reasons.get(key, 0) + 1
    lines = [
        "",
        "--- Logical ---",
        f"  Attempted (N_logical):             {m['N_logical']}",
        f"  Equivalent:                        {m['N_logical_equivalent']}",
        f"  Non-equivalent:                    {m['N_logical_non_equivalent']}",
        f"    output stricter:                 {m['N_output_stricter']}",
        f"    output more permissive:          {m['N_output_more_permissive']}",
        f"    incomparable:                    {m['N_incomparable']}",
        f"  Undetermined:                      {m['N_logical_undetermined']}",
    ]
    for reason, count in sorted(reasons.items()):
        lines.append(f"    {reason}: {count}")
    if unavailable:
        lines.append(f"  Not attempted (tools unavailable): {unavailable}")
    return lines


def format_value(value) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def print_logical(m: dict, indent: str = "  ") -> None:
    """Print the logical metrics block of a metrics table."""
    print(f"{indent}{'':─<60}")
    if m["N_logical"] is None:
        print(f"{indent}{'LER':<30} N/A (logical evaluator not run)")
        return
    print(f"{indent}{'LER':<30} {format_value(m['LER'])}")
    print(f"{indent}{'LER coverage':<30} {format_value(m['LER_coverage'])}    "
          f"(equivalent={m['N_logical_equivalent']}, non-equivalent={m['N_logical_non_equivalent']}, "
          f"undetermined={m['N_logical_undetermined']})")
    print(f"{indent}{'  output stricter':<30} {m['N_output_stricter']}    "
          f"more permissive: {m['N_output_more_permissive']}    incomparable: {m['N_incomparable']}")
