#!/usr/bin/env python3
"""
generate_charts.py - Grafovi za diplomski rad.

Čita results_summary.json i generiše:
  1. PESQ bar chart (passthrough vs transcode)
  2. CPU bar chart
  3. Codec heatmap matrica (A×B, PESQ boja)
  4. Raspodjela kontrolnih i transkodiranih konfiguracija
  5. Dodatna degradacija u odnosu na kontrolu izvornog kodeka

Korištenje:
    python generate_charts.py
"""

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import FuncFormatter

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
SUMMARY_DIR = PROJECT_DIR / "results" / "summary"
CHARTS_DIR = SUMMARY_DIR / "charts"
RAD_DIR = PROJECT_DIR / "rad"

plt.rcParams.update({
    "font.size": 11,
    "font.family": "serif",
    "axes.titlesize": 13,
    "axes.labelsize": 11,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "legend.fontsize": 9,
    "figure.dpi": 150,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
})

C_PASS = "#2ecc71"
C_TRANS = "#e74c3c"


def use_decimal_comma(ax):
    """Prikaži decimalni zarez na numeričkoj y-osi."""
    ax.yaxis.set_major_formatter(
        FuncFormatter(lambda value, _: f"{value:g}".replace(".", ","))
    )


def load_results():
    path = SUMMARY_DIR / "results_summary.json"
    if not path.exists():
        print(f"ERROR: {path} not found")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def save(fig, name):
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(CHARTS_DIR / f"{name}.png")
    fig.savefig(CHARTS_DIR / f"{name}.pdf")

    fig.savefig(RAD_DIR / f"{name}.png")
    plt.close(fig)
    print(f"  Saved: {name}")


def chart_pesq_bars(results):
    """PESQ MOS bar chart - sorted, passthrough vs transcode."""
    valid = [r for r in results if r.get("pesq_mean") is not None]
    valid.sort(key=lambda x: x["pesq_mean"], reverse=True)

    fig, ax = plt.subplots(figsize=(14, 6))
    labels = [f"{r['codec_a']}→{r['codec_b']}" for r in valid]
    values = [r["pesq_mean"] for r in valid]
    errors = [r.get("pesq_std", 0) for r in valid]
    colors = [C_PASS if r["scenario_type"] == "passthrough" else C_TRANS for r in valid]

    x = np.arange(len(labels))
    ax.bar(x, values, color=colors, yerr=errors, capsize=3, edgecolor="black", linewidth=0.5)
    ax.set_ylabel("PESQ MOS-LQO (krak A prema kraku B)")
    ax.set_title("Kvaliteta govora nakon prolaska kroz FreeSWITCH")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.set_ylim(0, 5)
    ax.axhline(4.0, color="gray", linestyle="--", linewidth=0.5, alpha=0.7)
    ax.axhline(3.0, color="orange", linestyle="--", linewidth=0.5, alpha=0.7)

    from matplotlib.patches import Patch
    ax.legend(handles=[
        Patch(facecolor=C_PASS, label="Bez transkodiranja (isti kodek)"),
        Patch(facecolor=C_TRANS, label="S transkodiranjem (različiti kodeci)"),
    ], loc="upper right")
    ax.grid(axis="y", alpha=0.3)
    use_decimal_comma(ax)
    save(fig, "pesq_mos_bars")


def chart_cpu_bars(results):
    """CPU usage bar chart."""
    valid = [r for r in results if r.get("cpu_mean") is not None]
    valid.sort(key=lambda x: x["cpu_mean"], reverse=True)

    fig, ax = plt.subplots(figsize=(14, 6))
    labels = [f"{r['codec_a']}→{r['codec_b']}" for r in valid]
    values = [r["cpu_mean"] for r in valid]
    colors = [C_PASS if r["scenario_type"] == "passthrough" else C_TRANS for r in valid]

    x = np.arange(len(labels))
    ax.bar(x, values, color=colors, edgecolor="black", linewidth=0.5)
    ax.set_ylabel("Iskorištenje procesora (%)")
    ax.set_title("Procesorsko opterećenje FreeSWITCH-a po kombinaciji kodeka")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right")

    from matplotlib.patches import Patch
    ax.legend(handles=[
        Patch(facecolor=C_PASS, label="Bez transkodiranja"),
        Patch(facecolor=C_TRANS, label="S transkodiranjem"),
    ])
    ax.grid(axis="y", alpha=0.3)
    use_decimal_comma(ax)
    save(fig, "cpu_usage_bars")


def chart_heatmap(results):
    """Codec A×B heatmap matrica."""
    codecs = sorted(set(r["codec_a"] for r in results) | set(r["codec_b"] for r in results))
    matrix = np.full((len(codecs), len(codecs)), np.nan)

    for r in results:
        if r.get("pesq_mean") is None:
            continue
        i = codecs.index(r["codec_a"])
        j = codecs.index(r["codec_b"])
        matrix[i, j] = r["pesq_mean"]

    fig, ax = plt.subplots(figsize=(9, 7))
    im = ax.imshow(matrix, cmap="RdYlGn", vmin=1.0, vmax=4.5, aspect="auto")

    ax.set_xticks(np.arange(len(codecs)))
    ax.set_yticks(np.arange(len(codecs)))
    ax.set_xticklabels(codecs, rotation=45, ha="right")
    ax.set_yticklabels(codecs)
    ax.set_xlabel("Kodek B (prijemni)")
    ax.set_ylabel("Kodek A (predajni)")
    ax.set_title("PESQ MOS-LQO matrica (krak A prema kraku B)")

    for i in range(len(codecs)):
        for j in range(len(codecs)):
            if not np.isnan(matrix[i, j]):
                color = "white" if matrix[i, j] < 2.5 else "black"
                value = f"{matrix[i, j]:.2f}".replace(".", ",")
                ax.text(j, i, value, ha="center", va="center",
                        color=color, fontsize=10, fontweight="bold")

    colorbar = fig.colorbar(im, ax=ax, label="PESQ MOS-LQO", shrink=0.8)
    colorbar.ax.yaxis.set_major_formatter(
        FuncFormatter(lambda value, _: f"{value:g}".replace(".", ","))
    )
    save(fig, "codec_heatmap")


def chart_passthrough_vs_transcode(results):
    """Box plot: passthrough vs transcode PESQ distribution."""
    pt_pesq = [r["pesq_mean"] for r in results
               if r["scenario_type"] == "passthrough" and r.get("pesq_mean")]
    tc_pesq = [r["pesq_mean"] for r in results
               if r["scenario_type"] == "transcode" and r.get("pesq_mean")]

    if not pt_pesq or not tc_pesq:
        return

    fig, ax = plt.subplots(figsize=(8, 6))
    bp = ax.boxplot([pt_pesq, tc_pesq], tick_labels=["Bez transkodiranja", "S transkodiranjem"],
                    patch_artist=True, widths=0.5)
    bp["boxes"][0].set_facecolor(C_PASS)
    bp["boxes"][1].set_facecolor(C_TRANS)

    ax.set_ylabel("PESQ MOS")
    ax.set_title("Raspodjela kvalitete prema načinu prijenosa")
    ax.grid(axis="y", alpha=0.3)
    use_decimal_comma(ax)

    pt_mean = np.mean(pt_pesq)
    tc_mean = np.mean(tc_pesq)
    ax.axhline(pt_mean, color=C_PASS, linestyle="--", alpha=0.5)
    ax.axhline(tc_mean, color=C_TRANS, linestyle="--", alpha=0.5)

    degrad = pt_mean - tc_mean
    ax.text(1.5, (pt_mean + tc_mean) / 2,
            (f"Razlika prosjeka:\n{degrad:.3f} MOS\n"
             f"({degrad/pt_mean*100:.1f}%)").replace(".", ","),
            ha="center", va="center", fontsize=11,
            bbox=dict(boxstyle="round", facecolor="lightyellow", alpha=0.8))

    save(fig, "passthrough_vs_transcode")


def chart_degradation(results):
    """Dodatna PESQ degradacija prema kontroli istog izvornog kodeka."""
    baseline = {
        r["codec_a"]: r["pesq_mean"]
        for r in results if r["scenario_type"] == "passthrough"
    }
    values = []
    for row in results:
        if row["scenario_type"] != "transcode" or row.get("pesq_mean") is None:
            continue
        values.append((
            f"{row['codec_a']}→{row['codec_b']}",
            baseline[row["codec_a"]] - row["pesq_mean"],
        ))
    values.sort(key=lambda item: item[1])

    fig, ax = plt.subplots(figsize=(14, 6))
    x = np.arange(len(values))
    ax.bar(x, [value for _, value in values], color="#4c78a8",
           edgecolor="black", linewidth=0.5)
    ax.set_xticks(x)
    ax.set_xticklabels([label for label, _ in values], rotation=45, ha="right")
    ax.set_ylabel(r"Dodatna degradacija $\Delta$PESQ")
    ax.set_title("Degradacija u odnosu na kontrolu izvornog kodeka")
    ax.axhline(0, color="black", linewidth=0.8)
    ax.grid(axis="y", alpha=0.3)
    use_decimal_comma(ax)
    save(fig, "pesq_degradation")


def chart_combined(results):
    """3-panel: PESQ, CPU, SegSNR."""
    valid = sorted([r for r in results if r.get("pesq_mean")],
                   key=lambda x: x["pesq_mean"], reverse=True)
    if not valid:
        return

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(14, 14))

    labels = [f"{r['codec_a']}→{r['codec_b']}" for r in valid]
    x = np.arange(len(labels))
    colors = [C_PASS if r["scenario_type"] == "passthrough" else C_TRANS for r in valid]


    ax1.bar(x, [r["pesq_mean"] for r in valid], color=colors, edgecolor="black", linewidth=0.5)
    ax1.set_ylabel("PESQ MOS")
    ax1.set_title("Kvaliteta govora (A-leg vs B-leg)")
    ax1.set_ylim(0, 5)
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax1.grid(axis="y", alpha=0.3)


    cpu_vals = [r.get("cpu_mean", 0) or 0 for r in valid]
    ax2.bar(x, cpu_vals, color=colors, edgecolor="black", linewidth=0.5)
    ax2.set_ylabel("CPU (%)")
    ax2.set_title("CPU korištenje FreeSWITCH-a")
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax2.grid(axis="y", alpha=0.3)


    snr_vals = [r.get("seg_snr_mean", 0) or 0 for r in valid]
    ax3.bar(x, snr_vals, color=colors, edgecolor="black", linewidth=0.5)
    ax3.set_ylabel("Segmentalni SNR (dB)")
    ax3.set_title("Signal-to-Noise Ratio (A-leg vs B-leg)")
    ax3.set_xticks(x)
    ax3.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax3.grid(axis="y", alpha=0.3)

    fig.tight_layout()
    save(fig, "combined_metrics")


def main():
    results = load_results()
    print(f"Loaded {len(results)} results")

    chart_pesq_bars(results)
    chart_cpu_bars(results)
    chart_heatmap(results)
    chart_passthrough_vs_transcode(results)
    chart_degradation(results)

    print(f"\nAll charts saved to: {CHARTS_DIR}/")


if __name__ == "__main__":
    main()
