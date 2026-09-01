#!/usr/bin/env python3
"""
run_full_matrix.py - Pokretanje kompletne matrice VoIP transcoding testova.

Testovi se zadano izvršavaju sekvencijalno kako bi PCAP i CPU mjerenja
pripadala samo jednom pozivu. Paralelni režim je dostupan isključivo za
funkcionalne probe; nije prikladan za prikupljanje naučnih rezultata.

25 usmjerenih parova kodeka × 10 iteracija = 250 testova
Sa 5 paralelnih slotova: ~5 rundi × 10 iter × 18s = ~15min

Korištenje:
    python run_full_matrix.py --iterations 10
    python run_full_matrix.py --iterations 3 --only T006,T007
    python run_full_matrix.py --iterations 3 --parallel  # samo funkcionalna proba
    python run_full_matrix.py --skip-tests  # samo agregacija
"""

import argparse
import csv
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import numpy as np

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = PROJECT_DIR / "scripts"
RESULTS_DIR = PROJECT_DIR / "results"
RAW_DIR = RESULTS_DIR / "raw"
SUMMARY_DIR = RESULTS_DIR / "summary"

sys.path.insert(0, str(SCRIPTS_DIR / "test"))
from run_single_test import run_test

SLOTS = [
    {"a_user": "1001", "b_user": "2001", "a_port": 5062, "b_port": 5072},
    {"a_user": "1002", "b_user": "2002", "a_port": 5082, "b_port": 5092},
    {"a_user": "1003", "b_user": "2003", "a_port": 5102, "b_port": 5112},
    {"a_user": "1004", "b_user": "2004", "a_port": 5122, "b_port": 5132},
    {"a_user": "1005", "b_user": "2005", "a_port": 5142, "b_port": 5152},
]


def load_matrix(csv_path):
    tests = []
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            tests.append(row)
    return tests


def run_test_all_iters(test, iterations, slot):
    """Pokreni sve iteracije jednog testa sekvencijalno u datom slotu."""
    tid = test["test_id"]
    ok = 0
    fail = 0

    for i in range(1, iterations + 1):
        try:
            result = run_test(
                codec_a=test["codec_a"],
                codec_b=test["codec_b"],
                test_id=tid,
                iteration=i,
                a_user=slot["a_user"],
                b_user=slot["b_user"],
                a_port=slot["a_port"],
                b_port=slot["b_port"],
            )
            if result.get("success"):
                ok += 1
            else:
                fail += 1
        except Exception as e:
            print(f"  [ERR] {tid}_iter{i}: {e}")
            fail += 1
        time.sleep(2)

    return tid, ok, fail


def run_all_parallel(tests, iterations, max_workers=5):
    """Pokreni testove paralelno (do max_workers istovremeno)."""
    total_ok = 0
    total_fail = 0


    for batch_idx in range(0, len(tests), max_workers):
        batch = tests[batch_idx:batch_idx + max_workers]
        batch_ids = [t["test_id"] for t in batch]
        print(f"\n{'='*60}")
        print(f"  Batch {batch_idx//max_workers + 1}: {', '.join(batch_ids)}")
        print(f"{'='*60}")

        with ThreadPoolExecutor(max_workers=len(batch)) as executor:
            futures = {}
            for i, test in enumerate(batch):
                slot = SLOTS[i % len(SLOTS)]
                future = executor.submit(run_test_all_iters, test, iterations, slot)
                futures[future] = test["test_id"]

            for future in as_completed(futures):
                tid, ok, fail = future.result()
                total_ok += ok
                total_fail += fail
                print(f"  >> {tid}: {ok}/{iterations} OK, {fail} FAIL")

        time.sleep(3)

    return total_ok, total_fail


def run_all_sequential(tests, iterations):
    """Pokreni sve testove sekvencijalno."""
    total_ok = 0
    total_fail = 0
    total = len(tests) * iterations
    done = 0

    for test in tests:
        tid = test["test_id"]
        print(f"\n{'#'*60}")
        print(f"# {tid} | {test['codec_a']} → {test['codec_b']}")
        print(f"{'#'*60}")

        for i in range(1, iterations + 1):
            done += 1
            try:
                result = run_test(
                    codec_a=test["codec_a"],
                    codec_b=test["codec_b"],
                    test_id=tid, iteration=i,
                    a_user=SLOTS[0]["a_user"], b_user=SLOTS[0]["b_user"],
                    a_port=SLOTS[0]["a_port"], b_port=SLOTS[0]["b_port"],
                )
                if result.get("success"):
                    total_ok += 1
                else:
                    total_fail += 1
            except Exception as e:
                print(f"  [ERR] {tid}_iter{i}: {e}")
                total_fail += 1
            time.sleep(2)

    return total_ok, total_fail


def aggregate_results(tests, iterations):
    summary = []
    for test in tests:
        tid = test["test_id"]
        pesq, snr, stoi_v, cpu, delay = [], [], [], [], []

        for i in range(1, iterations + 1):
            rf = RAW_DIR / f"{tid}_iter{i}.json"
            if not rf.exists():
                continue
            with open(rf) as f:
                d = json.load(f)
            if not d.get("success"):
                continue

            qm = d.get("quality_metrics")
            if qm and "error" not in qm:
                if qm.get("pesq_mos") is not None:
                    pesq.append(qm["pesq_mos"])
                if qm.get("segmental_snr_db") is not None:
                    snr.append(qm["segmental_snr_db"])
                if qm.get("stoi") is not None:
                    stoi_v.append(qm["stoi"])
                if qm.get("delay_ms") is not None:
                    delay.append(qm["delay_ms"])

            cm = d.get("cpu_metrics")
            if cm and cm.get("avg_percent") is not None:
                cpu.append(cm["avg_percent"])

        if not pesq:
            print(f"  WARN: {tid} - no successful PESQ data")
            continue

        summary.append({
            "test_id": tid,
            "codec_a": test["codec_a"],
            "codec_b": test["codec_b"],
            "scenario_type": test["scenario_type"],
            "n_successful": len(pesq),
            "pesq_mean": round(float(np.mean(pesq)), 3),
            "pesq_std": round(float(np.std(pesq, ddof=1)), 3) if len(pesq) > 1 else 0.0,
            "pesq_min": round(float(np.min(pesq)), 3),
            "pesq_max": round(float(np.max(pesq)), 3),
            "seg_snr_mean": round(float(np.mean(snr)), 2) if snr else None,
            "stoi_mean": round(float(np.mean(stoi_v)), 4) if stoi_v else None,
            "cpu_mean": round(float(np.mean(cpu)), 2) if cpu else None,
            "cpu_std": round(float(np.std(cpu, ddof=1)), 2) if len(cpu) > 1 else 0.0,
            "delay_mean_ms": round(float(np.mean(delay)), 1) if delay else None,
        })

    return summary


def save_summary(summary):
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    with open(SUMMARY_DIR / "results_summary.json", "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    if summary:
        with open(SUMMARY_DIR / "results_summary.csv", "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=summary[0].keys())
            writer.writeheader()
            writer.writerows(summary)
    print(f"\n  Summary: {SUMMARY_DIR}/")


def print_table(summary):
    print(f"\n{'='*85}")
    print(f"  {'Test':<6} {'Codec':<14} {'Type':<12} {'PESQ':>6} {'±std':>6} "
          f"{'SegSNR':>7} {'STOI':>6} {'CPU%':>6}")
    print(f"  {'-'*79}")

    for r in sorted(summary, key=lambda x: x.get("pesq_mean", 0), reverse=True):
        codec = f"{r['codec_a']}→{r['codec_b']}"
        snr = f"{r['seg_snr_mean']:.1f}" if r.get('seg_snr_mean') is not None else "N/A"
        stoi = f"{r['stoi_mean']:.3f}" if r.get('stoi_mean') is not None else "N/A"
        cpu = f"{r['cpu_mean']:.1f}" if r.get('cpu_mean') is not None else "N/A"
        print(f"  {r['test_id']:<6} {codec:<14} {r['scenario_type']:<12} "
              f"{r['pesq_mean']:>6.3f} ±{r['pesq_std']:.3f} {snr:>7} {stoi:>6} {cpu:>6}")

    pt = [r for r in summary if r["scenario_type"] == "passthrough"]
    tc = [r for r in summary if r["scenario_type"] == "transcode"]
    if pt and tc:
        pt_m = np.mean([r["pesq_mean"] for r in pt])
        tc_m = np.mean([r["pesq_mean"] for r in tc])
        print(f"\n  Passthrough avg PESQ: {pt_m:.3f}")
        print(f"  Transcode avg PESQ:   {tc_m:.3f}")
        print(f"  Degradation:          {pt_m - tc_m:.3f} MOS ({(pt_m-tc_m)/pt_m*100:.1f}%)")
        if [r for r in pt if r.get("cpu_mean")] and [r for r in tc if r.get("cpu_mean")]:
            pt_c = np.mean([r["cpu_mean"] for r in pt if r.get("cpu_mean")])
            tc_c = np.mean([r["cpu_mean"] for r in tc if r.get("cpu_mean")])
            print(f"  Passthrough avg CPU:  {pt_c:.1f}%")
            print(f"  Transcode avg CPU:    {tc_c:.1f}%")
            if pt_c > 0:
                print(f"  CPU increase:         {tc_c/pt_c:.2f}x")


def main():
    parser = argparse.ArgumentParser(description="Run full codec test matrix")
    parser.add_argument("--iterations", "-n", type=int, default=10)
    parser.add_argument("--matrix", type=str,
                        default=str(SCRIPTS_DIR / "test" / "codec_matrix.csv"))
    parser.add_argument("--only", type=str, default=None)
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument(
        "--parallel", action="store_true",
        help=("Eksperimentalni režim za funkcionalne probe. Ne koristiti za "
              "konačna PCAP i CPU mjerenja."),
    )
    args = parser.parse_args()

    all_tests = load_matrix(args.matrix)
    tests = all_tests
    if args.only:
        only_ids = set(args.only.split(","))
        tests = [t for t in all_tests if t["test_id"] in only_ids]
        unknown_ids = only_ids - {t["test_id"] for t in tests}
        if unknown_ids:
            parser.error(f"Nepoznati test ID: {', '.join(sorted(unknown_ids))}")

    RAW_DIR.mkdir(parents=True, exist_ok=True)

    start = datetime.now()
    total_tests = len(tests) * args.iterations
    print(f"\n{'='*60}")
    print(f"  START: {start.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  {len(tests)} tests × {args.iterations} iter = {total_tests} total")
    print(f"  Mode: {'parallel (EKSPERIMENTALNO)' if args.parallel else 'sequential'}")
    print(f"{'='*60}")

    if not args.skip_tests:
        if args.parallel:
            print("  UPOZORENJE: paralelni režim nije validan za konačna mjerenja CPU-a.")
            ok, fail = run_all_parallel(tests, args.iterations)
        else:
            ok, fail = run_all_sequential(tests, args.iterations)

        duration = datetime.now() - start
        print(f"\n{'='*60}")
        print(f"  DONE in {duration}")
        print(f"  OK: {ok}, FAIL: {fail}, Total: {total_tests}")
        print(f"{'='*60}")




    summary = aggregate_results(all_tests, args.iterations)
    save_summary(summary)
    print_table(summary)

    print("\n  Generating charts...")
    subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "analysis" / "generate_charts.py")],
        timeout=120, check=False,
    )
    subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "analysis" / "generate_latex_results.py")],
        timeout=30, check=True,
    )


if __name__ == "__main__":
    main()
