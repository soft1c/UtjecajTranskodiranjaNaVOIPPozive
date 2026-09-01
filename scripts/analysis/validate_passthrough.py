#!/usr/bin/env python3
"""Provjerava sljedivost RTP korisnih sadržaja u kontrolnim pozivima."""

import csv
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts" / "analysis"))

from extract_and_compare import extract_payloads, find_streams, identify_legs


def is_ordered_subsequence(source, candidate):
    position = 0
    for frame in candidate:
        while position < len(source) and source[position] != frame:
            position += 1
        if position == len(source):
            return False
        position += 1
    return True


def main():
    matrix_path = PROJECT_DIR / "scripts" / "test" / "codec_matrix.csv"
    with matrix_path.open(newline="", encoding="utf-8") as handle:
        controls = [
            row for row in csv.DictReader(handle)
            if row["scenario_type"] == "passthrough"
        ]

    checked = 0
    failures = []
    for test in controls:
        for iteration in range(1, 11):
            pcap = PROJECT_DIR / "results" / "raw" / f"{test['test_id']}_iter{iteration}.pcap"
            streams = find_streams(str(pcap))
            a_stream, b_stream = identify_legs(
                streams, test["codec_a"], test["codec_b"]
            )
            if not a_stream or not b_stream:
                failures.append(f"{pcap.name}: tok nije pronađen")
                continue
            incoming = extract_payloads(str(pcap), a_stream["ssrc"])
            outgoing = extract_payloads(str(pcap), b_stream["ssrc"])
            if not is_ordered_subsequence(incoming, outgoing):
                failures.append(f"{pcap.name}: izlaz nije podniz ulaza")
            checked += 1

    print(f"Provjereno kontrolnih poziva: {checked}")
    print(f"Neuspjelih provjera: {len(failures)}")
    for failure in failures:
        print(f"  {failure}")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
