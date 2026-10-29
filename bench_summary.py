#!/usr/bin/env python3
"""Display vllm bench serve JSON results as a console table (standard library only)."""

import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import re
import sys


def number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def fmt(value, decimals=2):
    value = number(value)
    return "—" if value is None else f"{value:.{decimals}f}"


def length(data, path, kind):
    """Prefer the configured length; otherwise show a measured average with ~."""
    match = re.search(r"-in(\d+)-out(\d+)-c\d+-n\d+", path.stem)
    value = match.group(1 if kind == "input" else 2) if match else None
    if value is None:
        value = data.get(f"random_{kind}_len", data.get(f"{kind}_len"))
    configured = number(value)
    if configured is not None:
        return configured, fmt(configured, 0), False
    completed = number(data.get("completed"))
    total = number(data.get(f"total_{kind}_tokens"))
    average = total / completed if total is not None and completed else None
    return average, "~" + fmt(average, 1) if average is not None else "—", True


def profile(input_len, estimated):
    if input_len is None:
        return "Unknown"
    for label, targets in (("Short", (100, 128)), ("Medium", (500,)), ("Long", (1500,))):
        if any(abs(input_len - target) <= (64 if estimated else 0) for target in targets):
            return label + ("~" if estimated else "")
    return "Custom"


def run_time(data, path, modified):
    # Our shell script puts UTC in the filename; vLLM's own date has no timezone.
    match = re.match(r"(\d{8})T(\d{6})\d*Z-", path.name)
    if match:
        value, pattern, suffix = "".join(match.groups()), "%Y%m%d%H%M%S", "Z"
    else:
        value, pattern, suffix = str(data.get("date", "")), "%Y%m%d-%H%M%S", ""
    try:
        return datetime.strptime(value, pattern).strftime("%Y-%m-%d %H:%M:%S") + suffix
    except ValueError:
        return datetime.fromtimestamp(modified).strftime("%Y-%m-%d %H:%M:%S")


def make_row(data, path, modified):
    if not isinstance(data, dict) or not any(key in data for key in (
        "median_ttft_ms", "p50_ttft_ms", "median_tpot_ms", "p50_tpot_ms", "output_throughput",
    )):
        return None
    input_len, input_text, estimated = length(data, path, "input")
    _, output_text, _ = length(data, path, "output")
    completed = number(data.get("completed"))
    total = number(data.get("num_prompts"))
    if total is None and completed is not None:
        failed = number(data.get("failed"))
        if failed is not None:
            total = completed + failed
    concurrency = data.get("max_concurrency")
    if concurrency is None:
        match = re.search(r"(?:-c|-concurrency)(\d+)(?:-|$)", path.stem)
        concurrency = match.group(1) if match else None
    # vLLM may emit zero latency statistics when no requests succeeded.
    ttft = data.get("median_ttft_ms", data.get("p50_ttft_ms")) if completed != 0 else None
    tpot = data.get("median_tpot_ms", data.get("p50_tpot_ms")) if completed != 0 else None
    model = data.get("model_id", data.get("model", "—"))
    model = model if isinstance(model, str) else "—"
    return [
        run_time(data, path, modified),
        profile(input_len, estimated),
        fmt(concurrency, 0),
        f"{input_text}/{output_text}",
        model,
        f"{fmt(completed, 0)}/{fmt(total, 0)}",
        fmt(ttft),
        fmt(tpot),
        fmt(data.get("output_throughput")),
    ]


def print_table(headers, rows):
    # Keep file/model names from introducing control characters into the table.
    rows = [[" ".join("".join(c if c.isprintable() else " " for c in str(cell)).split())
             for cell in row] for row in rows]
    widths = [max(len(header), *(len(row[i]) for row in rows)) for i, header in enumerate(headers)]

    def line(row):
        return " | ".join(cell.ljust(width) for cell, width in zip(row, widths))

    print(line(headers))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(line(row))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path,
                        default=Path(__file__).resolve().parent / "bench-results")
    parser.add_argument("--last", type=int, metavar="N", help="show only N most recent valid result files")
    parser.add_argument("--files", action="store_true", help="include source filenames")
    args = parser.parse_args(argv)
    if args.last is not None and args.last < 1:
        parser.error("--last must be positive")
    if not args.directory.is_dir():
        print(f"Result directory not found: {args.directory}", file=sys.stderr)
        return 1
    try:
        paths = sorted(
            ((p, p.stat().st_mtime) for p in args.directory.glob("*.json")
             if p.is_file() and not p.name.endswith(".pytorch.json")),
            key=lambda item: (item[1], item[0].name), reverse=True,
        )
    except OSError:
        print("Cannot list result files.", file=sys.stderr)
        return 1
    rows = []
    for path, modified in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print(f"Skipping unreadable/invalid JSON: {path.name}", file=sys.stderr)
            continue
        row = make_row(data, path, modified)
        if row is None:
            continue
        rows.append(row + ([path.name] if args.files else []))
        if args.last is not None and len(rows) >= args.last:
            break
    if not rows:
        print(f"No vLLM benchmark results found in {args.directory}")
        return 0
    headers = ["Run time", "Profile", "C", "In/Out tok", "Model", "OK/Total",
               "TTFT P50 ms", "TPOT P50 ms", "Output tok/s"]
    print_table(headers + (["File"] if args.files else []), rows)
    print(f"\nRuns: {len(rows)}; newest files first. ~ = estimated from measured averages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
