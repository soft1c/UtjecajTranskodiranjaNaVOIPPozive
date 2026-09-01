#!/usr/bin/env python3
"""
measure_cpu.py - Parsiranje CPU metrika za FreeSWITCH proces.

Podržava dva formata:
  1. docker stats output (timestamp, cpu_percent, mem_usage, pids)
  2. pidstat output (klasični format)

Korištenje:
    python measure_cpu.py --input cpu_log.txt --output result.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np


def parse_docker_stats(filepath):
    """Parsira docker stats izlaz."""
    measurements = []

    ansi_escape = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\[[ -~]')
    with open(filepath, "r") as f:
        for line in f:

            line = ansi_escape.sub("", line).strip()
            if not line or line.startswith("timestamp") or line.startswith("CPU"):
                continue


            parts = line.split("\t")
            if not parts:
                continue
            cpu_str = parts[0].strip().replace("%", "")
            try:
                cpu = float(cpu_str)
                measurements.append({"total_cpu": cpu})
            except ValueError:
                continue
    return measurements


def parse_pidstat_output(filepath):
    """Parsira pidstat izlaz."""
    measurements = []
    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("Linux"):
                continue
            if "Average" in line:
                continue
            parts = line.split()
            if len(parts) < 9:
                continue
            try:
                float(parts[3])
                usr_cpu = float(parts[3])
                sys_cpu = float(parts[4])
                total_cpu = float(parts[7])
                measurements.append({
                    "total_cpu": total_cpu,
                    "usr_cpu": usr_cpu,
                    "sys_cpu": sys_cpu,
                })
            except (ValueError, IndexError):
                continue
    return measurements


def parse_cpu_log(filepath):
    """Auto-detect format i parsiraj."""
    with open(filepath, "r") as f:
        first_lines = f.read(500)

    if "cpu_percent" in first_lines or "%" in first_lines.split("\n")[1] if len(first_lines.split("\n")) > 1 else False:
        return parse_docker_stats(filepath)
    else:
        return parse_pidstat_output(filepath)


def analyze_cpu(measurements):
    """Izračunaj CPU metrike iz mjerenja."""
    if not measurements:
        return {
            "avg_cpu_percent": None,
            "peak_cpu_percent": None,
            "std_cpu_percent": None,
            "min_cpu_percent": None,
            "num_samples": 0,
        }

    total_cpus = [m["total_cpu"] for m in measurements]

    return {
        "avg_cpu_percent": round(float(np.mean(total_cpus)), 2),
        "peak_cpu_percent": round(float(np.max(total_cpus)), 2),
        "min_cpu_percent": round(float(np.min(total_cpus)), 2),
        "std_cpu_percent": round(float(np.std(total_cpus)), 2),
        "num_samples": len(measurements),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Parsiranje CPU metrika za FreeSWITCH"
    )
    parser.add_argument("--input", required=True, help="Putanja do CPU log fajla")
    parser.add_argument("--output", help="Putanja do JSON fajla za ažuriranje")
    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"  UPOZORENJE: CPU log ne postoji: {args.input}")
        return

    measurements = parse_cpu_log(args.input)
    metrics = analyze_cpu(measurements)

    print(f"  CPU metrike ({metrics['num_samples']} uzoraka):")
    print(f"    Prosječan: {metrics['avg_cpu_percent']}%")
    print(f"    Peak:      {metrics['peak_cpu_percent']}%")
    print(f"    Min:       {metrics['min_cpu_percent']}%")
    print(f"    Std dev:   {metrics['std_cpu_percent']}%")

    if args.output and Path(args.output).exists():
        with open(args.output, "r") as f:
            data = json.load(f)
        data["cpu_metrics"] = metrics
        with open(args.output, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"  CPU metrike ažurirane u: {args.output}")
    else:
        print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
