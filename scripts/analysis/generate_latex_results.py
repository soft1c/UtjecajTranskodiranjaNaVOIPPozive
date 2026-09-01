#!/usr/bin/env python3
"""Generiše LaTeX makroe i tabele iz konačnog CSV sažetka."""

import csv
import itertools
from pathlib import Path

import numpy as np
from scipy import stats


PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
SUMMARY_FILE = PROJECT_DIR / "results" / "summary" / "results_summary.csv"
RAD_DIR = PROJECT_DIR / "rad"
CODECS = ["PCMU", "PCMA", "G722", "OPUS", "GSM"]
EXPECTED_REPETITIONS = 10


def decimal(value, digits):
    """Formatiraj decimalni broj prema bosanskom pravopisu."""
    return f"{float(value):.{digits}f}".replace(".", ",")


def math_decimal(value, digits):
    """Decimalni broj siguran i u LaTeX matematičkom režimu."""
    return decimal(value, digits).replace(",", "{,}")


def english_decimal(value, digits):
    """Formatiraj decimalni broj za engleski sažetak."""
    return f"{float(value):.{digits}f}"


def codec_name(value):
    return value.replace("G722", "G.722").replace("OPUS", "Opus")


def pair_name(row):
    return f"{codec_name(row['codec_a'])} $\\rightarrow$ {codec_name(row['codec_b'])}"


def load_results():
    with SUMMARY_FILE.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    expected_pairs = set(itertools.product(CODECS, repeat=2))
    actual_pairs = {(row["codec_a"], row["codec_b"]) for row in rows}
    if actual_pairs != expected_pairs:
        missing = sorted(expected_pairs - actual_pairs)
        extra = sorted(actual_pairs - expected_pairs)
        raise ValueError(f"Matrica nije potpuna; nedostaje={missing}, višak={extra}")
    if any(int(row["n_successful"]) != EXPECTED_REPETITIONS for row in rows):
        raise ValueError(
            f"Svaka konfiguracija mora imati {EXPECTED_REPETITIONS} uspješnih ponavljanja"
        )
    return rows


def write_macros(rows):
    passthrough_rows = [r for r in rows if r["scenario_type"] == "passthrough"]
    trans_rows = [r for r in rows if r["scenario_type"] == "transcode"]
    p = np.asarray([float(r["pesq_mean"]) for r in passthrough_rows])
    t = np.asarray([float(r["pesq_mean"]) for r in trans_rows])
    pass_cpu = np.asarray([float(r["cpu_mean"]) for r in passthrough_rows])
    trans_cpu = np.asarray([float(r["cpu_mean"]) for r in trans_rows])
    baseline = {r["codec_a"]: float(r["pesq_mean"]) for r in passthrough_rows}
    degradations = np.asarray([
        baseline[r["codec_a"]] - float(r["pesq_mean"]) for r in trans_rows
    ])

    difference = degradations.mean()
    relative = difference / p.mean() * 100
    test = stats.ttest_1samp(degradations, popmean=0.0)
    degrees_freedom = len(degradations) - 1
    standard_error = degradations.std(ddof=1) / np.sqrt(len(degradations))
    ci_low, ci_high = stats.t.interval(
        0.95, degrees_freedom, loc=difference, scale=standard_error
    )
    correction = 1 - 3 / (4 * len(degradations) - 5)
    hedges_g = correction * difference / degradations.std(ddof=1)
    by_destination = {
        codec: np.mean([
            float(r["pesq_mean"]) for r in trans_rows if r["codec_b"] == codec
        ])
        for codec in CODECS
    }

    content = f"""% Automatski generisano; ne uređivati ručno.
% Izvor: results/summary/results_summary.csv
\\newcommand{{\\BrojKonfiguracija}}{{{len(rows)}}}
\\newcommand{{\\BrojPoziva}}{{{sum(int(r['n_successful']) for r in rows)}}}
\\newcommand{{\\PassthroughPESQ}}{{{math_decimal(np.mean(p), 3)}}}
\\newcommand{{\\TranscodePESQ}}{{{math_decimal(np.mean(t), 3)}}}
\\newcommand{{\\RazlikaPESQ}}{{{math_decimal(difference, 3)}}}
\\newcommand{{\\PassthroughPESQEnglish}}{{{english_decimal(np.mean(p), 3)}}}
\\newcommand{{\\TranscodePESQEnglish}}{{{english_decimal(np.mean(t), 3)}}}
\\newcommand{{\\RazlikaPESQEnglish}}{{{english_decimal(difference, 3)}}}
\\newcommand{{\\RelativnaRazlikaPESQ}}{{{math_decimal(relative, 1)}\\%}}
\\newcommand{{\\PassthroughCPU}}{{{math_decimal(np.mean(pass_cpu), 1)}\\%}}
\\newcommand{{\\TranscodeCPU}}{{{math_decimal(np.mean(trans_cpu), 1)}\\%}}
\\newcommand{{\\OmjerCPU}}{{{math_decimal(np.mean(trans_cpu) / np.mean(pass_cpu), 1)}}}
\\newcommand{{\\DeltaT}}{{{math_decimal(test.statistic, 2)}}}
\\newcommand{{\\DeltaDF}}{{{degrees_freedom}}}
\\newcommand{{\\DeltaP}}{{{math_decimal(test.pvalue, 6)}}}
\\newcommand{{\\RazlikaCIDonja}}{{{math_decimal(ci_low, 3)}}}
\\newcommand{{\\RazlikaCIGornja}}{{{math_decimal(ci_high, 3)}}}
\\newcommand{{\\HedgesG}}{{{math_decimal(hedges_g, 2)}}}
\\newcommand{{\\ProsjekPremaPCMU}}{{{math_decimal(by_destination['PCMU'], 3)}}}
\\newcommand{{\\ProsjekPremaPCMA}}{{{math_decimal(by_destination['PCMA'], 3)}}}
\\newcommand{{\\ProsjekPremaGSevenTwoTwo}}{{{math_decimal(by_destination['G722'], 3)}}}
\\newcommand{{\\ProsjekPremaOpusu}}{{{math_decimal(by_destination['OPUS'], 3)}}}
\\newcommand{{\\ProsjekPremaGSMu}}{{{math_decimal(by_destination['GSM'], 3)}}}
"""
    (RAD_DIR / "generisani_rezultati.tex").write_text(content, encoding="utf-8")


def write_table(rows):
    lines = [
        "% Automatski generisano; ne uređivati ručno.",
        "% Izvor: results/summary/results_summary.csv",
        r"\begin{table}[htbp]", r"\centering",
        rf"\caption{{Rezultati za sve ispitane kombinacije (prosjek $\pm$ standardna devijacija, {EXPECTED_REPETITIONS} ponavljanja)}}",
        r"\label{tab:complete-results}", r"\scriptsize",
        r"\begin{tabular}{|l|l|l|c|c|c|}", r"\hline",
        r"\textbf{Test} & \textbf{Kodek A} & \textbf{Kodek B} & \textbf{PESQ MOS-LQO} & \textbf{STOI} & \textbf{CPU (\%)} \\",
        r"\hline",
    ]
    for index, row in enumerate(rows):
        if index == 5:
            lines.append(r"\hline")
        lines.append(
            f"{row['test_id']} & {codec_name(row['codec_a'])} & {codec_name(row['codec_b'])} & "
            f"{decimal(row['pesq_mean'], 3)} $\\pm$ {decimal(row['pesq_std'], 3)} & "
            f"{decimal(row['stoi_mean'], 3)} & {decimal(row['cpu_mean'], 2)} \\\\"
        )
    lines.extend([r"\hline", r"\end{tabular}", r"\end{table}", ""])
    (RAD_DIR / "generisana_tabela_rezultata.tex").write_text("\n".join(lines), encoding="utf-8")


def write_controls(rows):
    controls = [r for r in rows if r["scenario_type"] == "passthrough"]
    lines = [
        "% Automatski generisano; ne uređivati ručno.",
        r"\begin{table}[htbp]", r"\centering",
        r"\caption{Rezultati prijenosa bez transkodiranja}",
        r"\label{tab:passthrough-details}",
        r"\begin{tabular}{|l|c|c|c|c|}", r"\hline",
        r"\textbf{Test} & \textbf{Kodek} & \textbf{PESQ MOS-LQO} & \textbf{STOI} & \textbf{CPU (\%)} \\",
        r"\hline",
    ]
    for row in controls:
        lines.append(
            f"{row['test_id']} & {codec_name(row['codec_a'])} & "
            f"{decimal(row['pesq_mean'], 3)} $\\pm$ {decimal(row['pesq_std'], 3)} & "
            f"{decimal(row['stoi_mean'], 3)} & {decimal(row['cpu_mean'], 2)} \\\\"
        )
    lines.extend([
        r"\hline",
        rf"\textbf{{Prosjek}} & & \textbf{{{decimal(np.mean([float(r['pesq_mean']) for r in controls]), 3)}}} & "
        rf"\textbf{{{decimal(np.mean([float(r['stoi_mean']) for r in controls]), 3)}}} & "
        rf"\textbf{{{decimal(np.mean([float(r['cpu_mean']) for r in controls]), 2)}}} \\",
        r"\hline", r"\end{tabular}", r"\end{table}", "",
    ])
    (RAD_DIR / "generisana_tabela_kontrola.tex").write_text("\n".join(lines), encoding="utf-8")


def write_extremes(rows):
    baseline = {
        r["codec_a"]: float(r["pesq_mean"])
        for r in rows if r["scenario_type"] == "passthrough"
    }
    trans = [dict(r) for r in rows if r["scenario_type"] == "transcode"]
    for row in trans:
        row["degradation"] = baseline[row["codec_a"]] - float(row["pesq_mean"])
    selected = sorted(trans, key=lambda r: r["degradation"])[:4]
    selected += sorted(trans, key=lambda r: r["degradation"], reverse=True)[:4]

    lines = [
        "% Automatski generisano; ne uređivati ručno.",
        r"\begin{table}[htbp]", r"\centering",
        r"\caption{Scenariji s najmanjom i najvećom dodatnom degradacijom}",
        r"\label{tab:transcode-extremes}", r"\small",
        r"\begin{tabular}{|l|c|c|c|}", r"\hline",
        r"\textbf{Kombinacija} & \textbf{PESQ MOS-LQO} & \textbf{$\Delta$PESQ} & \textbf{CPU (\%)} \\",
        r"\hline",
    ]
    for index, row in enumerate(selected):
        if index == 4:
            lines.append(r"\hline")
        lines.append(
            f"{pair_name(row)} & {decimal(row['pesq_mean'], 3)} $\\pm$ "
            f"{decimal(row['pesq_std'], 3)} & {decimal(row['degradation'], 3)} & "
            f"{decimal(row['cpu_mean'], 2)} \\\\"
        )
    lines.extend([r"\hline", r"\end{tabular}", r"\end{table}", ""])
    (RAD_DIR / "generisana_tabela_ekstrema.tex").write_text("\n".join(lines), encoding="utf-8")


def write_asymmetry(rows):
    values = {
        (r["codec_a"], r["codec_b"]): float(r["pesq_mean"])
        for r in rows if r["scenario_type"] == "transcode"
    }
    lines = [
        "% Automatski generisano; ne uređivati ručno.",
        r"\begin{table}[htbp]", r"\centering",
        r"\caption{Razlika rezultata između suprotnih smjerova transkodiranja}",
        r"\label{tab:directional-asymmetry}", r"\small",
        r"\begin{tabular}{|l|c|c|c|}", r"\hline",
        r"\textbf{Par kodeka} & \textbf{prvi$\rightarrow$drugi} & \textbf{drugi$\rightarrow$prvi} & \textbf{Apsolutna razlika} \\",
        r"\hline",
    ]
    for a, b in itertools.combinations(CODECS, 2):
        ab, ba = values[(a, b)], values[(b, a)]
        lines.append(
            f"{codec_name(a)}/{codec_name(b)} & {decimal(ab, 3)} & "
            f"{decimal(ba, 3)} & {decimal(abs(ab - ba), 3)} \\\\"
        )
    lines.extend([r"\hline", r"\end{tabular}", r"\end{table}", ""])
    (RAD_DIR / "generisana_tabela_asimetrije.tex").write_text("\n".join(lines), encoding="utf-8")


def main():
    rows = load_results()
    write_macros(rows)
    write_table(rows)
    write_controls(rows)
    write_extremes(rows)
    write_asymmetry(rows)
    print("Generisani su LaTeX makroi i tabele iz konačnog CSV sažetka")


if __name__ == "__main__":
    main()
