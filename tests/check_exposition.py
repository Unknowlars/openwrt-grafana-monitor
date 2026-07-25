#!/usr/bin/env python3
"""Validate Prometheus exposition output for duplicate series.

Prometheus does not fail a scrape that contains the same series twice: it keeps
the first sample and silently drops the later one (counting it as a duplicate).
That makes this class of bug invisible from the dashboard side, which is exactly
how the multiqueue tc collector reported 0 bytes sent while the router had
actually forwarded 703 MB.

Usage:
    check_exposition.py FILE...          validate textfile-collector output
    check_exposition.py --url URL        validate a live /metrics endpoint
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from collections import defaultdict

SAMPLE = re.compile(r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?P<labels>\{.*\})?\s+(?P<value>.+)$")


def parse(text: str) -> list[tuple[str, str, str]]:
    """Return (name, normalised-labels, value) for every sample line."""
    samples = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = SAMPLE.match(line)
        if not match:
            continue
        labels = match.group("labels") or "{}"
        # Normalise so {} and an absent label set compare equal, and so label
        # order does not mask a genuine duplicate.
        inner = labels[1:-1].strip()
        pairs = sorted(p.strip() for p in re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*="(?:[^"\\]|\\.)*"', inner))
        samples.append((match.group("name"), "{" + ",".join(pairs) + "}", match.group("value")))
    return samples


def find_duplicates(samples):
    seen = defaultdict(list)
    for name, labels, value in samples:
        seen[(name, labels)].append(value)
    return {key: values for key, values in seen.items() if len(values) > 1}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*")
    parser.add_argument("--url")
    args = parser.parse_args()

    blobs = []
    if args.url:
        with urllib.request.urlopen(args.url, timeout=15) as response:
            blobs.append((args.url, response.read().decode("utf-8", "replace")))
    for path in args.files:
        with open(path, encoding="utf-8") as handle:
            blobs.append((path, handle.read()))

    if not blobs:
        parser.error("provide at least one file or --url")

    # A textfile collector exposes every file in its directory as one document,
    # so duplicates across files matter just as much as duplicates within one.
    combined = []
    for _, text in blobs:
        combined.extend(parse(text))

    duplicates = find_duplicates(combined)
    if duplicates:
        print(f"FAIL: {len(duplicates)} duplicated series across {len(blobs)} source(s)")
        for (name, labels), values in sorted(duplicates.items())[:40]:
            distinct = sorted(set(values))
            flag = "  <-- DIVERGENT VALUES, data silently dropped" if len(distinct) > 1 else ""
            print(f"  {name}{labels} x{len(values)} values={distinct[:4]}{flag}")
        return 1

    print(f"OK: {len(combined)} samples, no duplicate series")
    return 0


if __name__ == "__main__":
    sys.exit(main())
