#!/usr/bin/env python3
"""Spektralna analiza originalnog WAV-a i krajnjeg B-leg signala.

Analiza koristi postojece PCAP zapise. B-leg se dekodira i rekonstruise istim
postupkom kao u end-to-end analizi, a zatim se originalni i krajnji signal
poravnavaju na 16 kHz. Skripta ne mijenja glavne rezultate rada.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import tempfile
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import spectrogram, welch

from analyze_end_to_end import (
    CODECS,
    PROJECT_DIR,
    RAW_DIR,
    UNIFORM_RATE,
    align_signals,
    extract_rtp_packets,
    load_matrix,
    load_wav_float,
    reference_for_codec,
    resample_audio,
    restore_rtp_timeline,
)
from extract_and_compare import decode_to_wav, find_streams, identify_legs


DEFAULT_OUTPUT_DIR = PROJECT_DIR / "results" / "signal_analysis_initial_vs_final_20260901"
END_TO_END_CSV = (
    PROJECT_DIR
    / "results"
    / "end_to_end_original_vs_bleg_20260828"
    / "per_call_results.csv"
)
REPRESENTATIVE_TESTS = ["T001", "T004", "T006", "T009", "T015", "T020", "T023"]
BANDS = {
    "low_80_300": (80.0, 300.0),
    "telephone_300_3400": (300.0, 3400.0),
    "wide_3400_7000": (3400.0, 7000.0),
    "upper_7000_7900": (7000.0, 7900.0),
}
TOTAL_BAND = (80.0, 7900.0)
DISPLAY_CODEC = {
    "PCMU": "PCMU",
    "PCMA": "PCMA",
    "G722": "G.722",
    "GSM": "GSM",
    "OPUS": "Opus",
}


def load_end_to_end_rows() -> list[dict[str, str]]:
    with END_TO_END_CSV.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def representative_iterations(rows: list[dict[str, str]]) -> dict[str, int]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if row.get("status") == "ok":
            grouped[row["test_id"]].append(row)

    selected: dict[str, int] = {}
    for test_id, group in grouped.items():
        values = np.asarray([float(row["pesq_wb_16k"]) for row in group])
        mean = float(np.mean(values))
        chosen = min(group, key=lambda row: abs(float(row["pesq_wb_16k"]) - mean))
        selected[test_id] = int(chosen["iteration"])
    return selected


def load_aligned_pair(
    test_id: str,
    iteration: int,
    codec_a: str,
    codec_b: str,
) -> tuple[np.ndarray, np.ndarray, dict[str, float]]:
    pcap = RAW_DIR / f"{test_id}_iter{iteration}.pcap"
    streams = find_streams(str(pcap))
    _, b_stream = identify_legs(streams, codec_a, codec_b)
    if not b_stream:
        raise RuntimeError(f"B-leg nije pronadjen za {test_id}, iteracija {iteration}")

    packets = extract_rtp_packets(pcap, str(b_stream["ssrc"]))
    payloads = [packet["payload"] for packet in packets]
    with tempfile.TemporaryDirectory(prefix="signal_spectrum_") as temporary:
        native_wav = Path(temporary) / "b_leg_native.wav"
        if not decode_to_wav(payloads, codec_b, str(native_wav)):
            raise RuntimeError(f"Dekodiranje nije uspjelo za {test_id}, iteracija {iteration}")
        native_rate, decoded = load_wav_float(native_wav)

    expected_rate = int(CODECS[codec_b]["pcm_rate"])
    if native_rate != expected_rate:
        raise RuntimeError(
            f"Neocekivana frekvencija B-lega za {test_id}: {native_rate} umjesto {expected_rate}"
        )
    restored, _ = restore_rtp_timeline(decoded, packets, codec_b)

    reference_rate, reference = load_wav_float(reference_for_codec(codec_a))
    reference_16k = resample_audio(reference, reference_rate, UNIFORM_RATE)
    final_16k = resample_audio(restored, native_rate, UNIFORM_RATE)
    return align_signals(reference_16k, final_16k, UNIFORM_RATE)


def spectrum(signal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    frequencies, density = welch(
        signal,
        fs=UNIFORM_RATE,
        window="hann",
        nperseg=2048,
        noverlap=1024,
        detrend="constant",
        scaling="spectrum",
    )
    return frequencies, np.maximum(density, np.finfo(float).tiny)


def integrate_band(frequencies: np.ndarray, density: np.ndarray, low: float, high: float) -> float:
    mask = (frequencies >= low) & (frequencies < high)
    if np.count_nonzero(mask) < 2:
        return 0.0
    return float(np.trapezoid(density[mask], frequencies[mask]))


def spectral_metrics(signal: np.ndarray) -> dict[str, float]:
    frequencies, density = spectrum(signal)
    total = integrate_band(frequencies, density, *TOTAL_BAND)
    if total <= 0:
        raise ValueError("Signal nema mjerljivu energiju u analiziranom opsegu")

    metrics: dict[str, float] = {}
    for name, (low, high) in BANDS.items():
        metrics[f"{name}_share_pct"] = 100.0 * integrate_band(
            frequencies, density, low, high
        ) / total

    total_mask = (frequencies >= TOTAL_BAND[0]) & (frequencies < TOTAL_BAND[1])
    band_frequencies = frequencies[total_mask]
    band_density = density[total_mask]
    metrics["spectral_centroid_hz"] = float(
        np.sum(band_frequencies * band_density) / np.sum(band_density)
    )
    cumulative = np.cumsum(band_density)
    rolloff_index = int(np.searchsorted(cumulative, 0.95 * cumulative[-1]))
    metrics["spectral_rolloff_95_hz"] = float(band_frequencies[rolloff_index])
    metrics["rms_dbfs"] = float(
        20.0 * np.log10(max(float(np.sqrt(np.mean(signal**2))), 1e-12))
    )
    return metrics


def make_comparison_chart(
    output: Path,
    test_id: str,
    codec_a: str,
    codec_b: str,
    iteration: int,
    reference: np.ndarray,
    final: np.ndarray,
    metrics: dict[str, object],
) -> None:
    duration = len(reference) / UNIFORM_RATE
    excerpt_start = min(2.0, max(0.0, duration - 2.5))
    excerpt_end = min(excerpt_start + 2.0, duration)
    start = int(excerpt_start * UNIFORM_RATE)
    end = int(excerpt_end * UNIFORM_RATE)
    time = np.arange(end - start) / UNIFORM_RATE + excerpt_start

    f_ref, t_ref, s_ref = spectrogram(
        reference,
        fs=UNIFORM_RATE,
        window="hann",
        nperseg=512,
        noverlap=384,
        mode="magnitude",
    )
    f_final, t_final, s_final = spectrogram(
        final,
        fs=UNIFORM_RATE,
        window="hann",
        nperseg=512,
        noverlap=384,
        mode="magnitude",
    )
    db_ref = 20.0 * np.log10(np.maximum(s_ref, 1e-8))
    db_final = 20.0 * np.log10(np.maximum(s_final, 1e-8))
    vmax = float(max(np.percentile(db_ref, 99.5), np.percentile(db_final, 99.5)))
    vmin = vmax - 70.0

    freq_ref, psd_ref = spectrum(reference)
    freq_final, psd_final = spectrum(final)
    psd_ref_db = 10.0 * np.log10(psd_ref / np.max(psd_ref))
    psd_final_db = 10.0 * np.log10(psd_final / np.max(psd_final))

    fig = plt.figure(figsize=(14, 10))
    grid = fig.add_gridspec(3, 2, height_ratios=[0.8, 1.2, 1.0])
    waveform_axis = fig.add_subplot(grid[0, :])
    original_axis = fig.add_subplot(grid[1, 0])
    final_axis = fig.add_subplot(grid[1, 1])
    spectrum_axis = fig.add_subplot(grid[2, :])

    waveform_axis.plot(time, reference[start:end], label="Izvorni WAV", linewidth=0.8)
    waveform_axis.plot(time, final[start:end], label="Krajnji B-leg", linewidth=0.8, alpha=0.75)
    waveform_axis.set_title("Vremenski oblik poravnatih signala (isjecak od 2 s)")
    waveform_axis.set_xlabel("Vrijeme (s)")
    waveform_axis.set_ylabel("Normalizirana amplituda")
    waveform_axis.grid(alpha=0.2)
    waveform_axis.legend(loc="upper right")

    original_image = original_axis.pcolormesh(
        t_ref, f_ref / 1000.0, db_ref, shading="auto", cmap="magma", vmin=vmin, vmax=vmax
    )
    original_axis.set_title("Izvorni WAV")
    original_axis.set_xlabel("Vrijeme (s)")
    original_axis.set_ylabel("Frekvencija (kHz)")
    original_axis.set_ylim(0, 8)

    final_axis.pcolormesh(
        t_final, f_final / 1000.0, db_final, shading="auto", cmap="magma", vmin=vmin, vmax=vmax
    )
    final_axis.set_title("Krajnji B-leg")
    final_axis.set_xlabel("Vrijeme (s)")
    final_axis.set_ylabel("Frekvencija (kHz)")
    final_axis.set_ylim(0, 8)
    colorbar_axis = fig.add_axes([0.925, 0.395, 0.012, 0.25])
    fig.colorbar(original_image, cax=colorbar_axis, label="Magnituda (dB)")

    spectrum_axis.plot(freq_ref / 1000.0, psd_ref_db, label="Izvorni WAV", linewidth=1.2)
    spectrum_axis.plot(freq_final / 1000.0, psd_final_db, label="Krajnji B-leg", linewidth=1.2)
    spectrum_axis.axvline(3.4, color="black", linestyle="--", linewidth=0.9, label="3,4 kHz")
    spectrum_axis.set_xlim(0, 8)
    spectrum_axis.set_ylim(-90, 5)
    spectrum_axis.set_xlabel("Frekvencija (kHz)")
    spectrum_axis.set_ylabel("Relativna spektralna snaga (dB)")
    spectrum_axis.set_title("Prosjecna raspodjela spektralne snage")
    spectrum_axis.grid(alpha=0.2)
    spectrum_axis.legend(loc="upper right")

    high_ref = float(metrics["reference_wide_3400_7000_share_pct"])
    high_final = float(metrics["final_wide_3400_7000_share_pct"])
    high_ref_text = f"{high_ref:.2f}".replace(".", ",")
    high_final_text = f"{high_final:.2f}".replace(".", ",")
    fig.suptitle(
        f"{test_id}: {DISPLAY_CODEC[codec_a]} → {DISPLAY_CODEC[codec_b]}, "
        f"reprezentativna iteracija {iteration}\n"
        f"Udio energije 3,4–7 kHz: {high_ref_text}% → {high_final_text}%",
        fontsize=14,
    )
    fig.subplots_adjust(top=0.91, bottom=0.07, left=0.08, right=0.90, hspace=0.40, wspace=0.25)
    fig.savefig(output, dpi=180)
    plt.close(fig)


def summarize(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["test_id"])].append(row)

    summaries: list[dict[str, object]] = []
    numeric_fields = [
        "pesq_wb_16k",
        "waveform_correlation",
        "reference_telephone_300_3400_share_pct",
        "final_telephone_300_3400_share_pct",
        "reference_wide_3400_7000_share_pct",
        "final_wide_3400_7000_share_pct",
        "reference_spectral_centroid_hz",
        "final_spectral_centroid_hz",
        "reference_spectral_rolloff_95_hz",
        "final_spectral_rolloff_95_hz",
        "reference_rms_dbfs",
        "final_rms_dbfs",
    ]
    for test_id in sorted(grouped):
        group = grouped[test_id]
        summary: dict[str, object] = {
            "test_id": test_id,
            "codec_a": group[0]["codec_a"],
            "codec_b": group[0]["codec_b"],
            "scenario_type": group[0]["scenario_type"],
            "n": len(group),
        }
        for field in numeric_fields:
            values = np.asarray([float(row[field]) for row in group])
            summary[f"{field}_mean"] = round(float(np.mean(values)), 4)
            summary[f"{field}_std"] = round(float(np.std(values, ddof=1)), 4)
        summary["wide_band_share_change_pp"] = round(
            float(summary["final_wide_3400_7000_share_pct_mean"])
            - float(summary["reference_wide_3400_7000_share_pct_mean"]),
            4,
        )
        summary["rolloff_95_shift_hz"] = round(
            float(summary["final_spectral_rolloff_95_hz_mean"])
            - float(summary["reference_spectral_rolloff_95_hz_mean"]),
            2,
        )
        summaries.append(summary)
    return summaries


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, data: object) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def make_heatmap(output: Path, summaries: list[dict[str, object]]) -> None:
    codecs = ["PCMU", "PCMA", "G722", "GSM", "OPUS"]
    matrix = np.full((5, 5), np.nan)
    for row in summaries:
        i = codecs.index(str(row["codec_a"]))
        j = codecs.index(str(row["codec_b"]))
        matrix[i, j] = float(row["wide_band_share_change_pp"])

    limit = max(abs(float(np.nanmin(matrix))), abs(float(np.nanmax(matrix))), 0.1)
    fig, axis = plt.subplots(figsize=(8, 6.5))
    image = axis.imshow(matrix, cmap="RdBu_r", vmin=-limit, vmax=limit)
    axis.set_xticks(range(5), [DISPLAY_CODEC[codec] for codec in codecs])
    axis.set_yticks(range(5), [DISPLAY_CODEC[codec] for codec in codecs])
    axis.set_xlabel("Odredišni kodek B")
    axis.set_ylabel("Izvorni kodek A")
    axis.set_title("Promjena udjela energije u opsegu 3,4–7 kHz")
    for i in range(5):
        for j in range(5):
            axis.text(j, i, f"{matrix[i, j]:+.2f}", ha="center", va="center", fontsize=9)
    fig.colorbar(image, ax=axis, label="Promjena (postotni poeni)")
    fig.tight_layout()
    fig.savefig(output, dpi=180)
    plt.close(fig)


def write_readme(output_dir: Path, summaries: list[dict[str, object]]) -> None:
    by_test = {str(row["test_id"]): row for row in summaries}
    strongest_loss = min(summaries, key=lambda row: float(row["wide_band_share_change_pp"]))
    wide_codecs = {"G722", "OPUS"}
    narrow_codecs = {"PCMU", "PCMA", "GSM"}
    wide_to_narrow = [
        row for row in summaries
        if str(row["codec_a"]) in wide_codecs and str(row["codec_b"]) in narrow_codecs
    ]
    wide_to_wide = [
        row for row in summaries
        if str(row["codec_a"]) in wide_codecs and str(row["codec_b"]) in wide_codecs
    ]

    def mean_change(rows: list[dict[str, object]]) -> float:
        return float(np.mean([float(row["wide_band_share_change_pp"]) for row in rows]))

    def decimal(value: object, digits: int = 2) -> str:
        return f"{float(value):.{digits}f}".replace(".", ",")

    text = f"""# Eksplorativna analiza izvornog i krajnjeg signala

Analiza je izvedena nad svih 250 postojećih PCAP zapisa. Novi pozivi nisu
pokretani, a glavni rezultati rada nisu mijenjani.

## Postupak

- Izvor je WAV koji je stvarno reproduciran za kodek A.
- Krajnji signal je B-leg dekodiran iz PCAP-a i rekonstruisan prema RTP
  vremenskim oznakama.
- Signali su poravnati i svedeni na 16 kHz, kao u postojećoj end-to-end analizi.
- Spektralni udjeli računati su Welchovom procjenom snage u opsezima 80–300 Hz,
  300–3400 Hz, 3400–7000 Hz i 7000–7900 Hz.
- Za ilustracije je iz svake odabrane konfiguracije uzeta iteracija čiji je
  PESQ najbliži prosjeku te konfiguracije, da se izbjegne biranje ekstrema.

## Glavni nalazi

Najveći prosječni pad udjela energije u opsegu 3,4–7 kHz zabilježen je
za {DISPLAY_CODEC[str(strongest_loss['codec_a'])]} → {DISPLAY_CODEC[str(strongest_loss['codec_b'])]}
({decimal(strongest_loss['wide_band_share_change_pp'])} postotnih poena).

Za Opus → GSM udio iznosi {decimal(by_test['T020']['reference_wide_3400_7000_share_pct_mean'])}%
u izvornom i {decimal(by_test['T020']['final_wide_3400_7000_share_pct_mean'])}% u krajnjem
signalu. Frekvencija ispod koje se nalazi 95% spektralne energije pritom se
pomjera sa {decimal(by_test['T020']['reference_spectral_rolloff_95_hz_mean'], 0)} Hz na
{decimal(by_test['T020']['final_spectral_rolloff_95_hz_mean'], 0)} Hz.

Kod GSM → Opus udio u istom opsegu ostaje vrlo nizak:
{decimal(by_test['T023']['reference_wide_3400_7000_share_pct_mean'])}% u izvornom i
{decimal(by_test['T023']['final_wide_3400_7000_share_pct_mean'])}% u krajnjem signalu.
To je u skladu s činjenicom da kodiranje u širokopojasni format ne može vratiti
frekvencijski sadržaj izgubljen u prethodnom uskopojasnom koraku.

Prosječna promjena za širokopojasni izvor i uskopojasno odredište iznosi
{decimal(mean_change(wide_to_narrow))} postotnih poena, a kada su oba kodeka širokopojasna
{decimal(mean_change(wide_to_wide))} postotnih poena.

Ovi pokazatelji su deskriptivni. Ne zamjenjuju PESQ ili STOI i ne treba ih
tumačiti kao zasebnu mjeru subjektivnog kvaliteta. Zaključci su ograničeni na
korišteni sintetizirani govorni uzorak i eksperimentalno okruženje.
"""
    (output_dir / "README.md").write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--reuse-metrics",
        action="store_true",
        help="Ponovo generisi grafikone bez obrade svih 250 PCAP zapisa",
    )
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    charts_dir = output_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    matrix = load_matrix()
    e2e_rows = load_end_to_end_rows()
    e2e_lookup = {
        (row["test_id"], int(row["iteration"])): row
        for row in e2e_rows
        if row.get("status") == "ok"
    }
    chosen_iterations = representative_iterations(e2e_rows)

    if args.reuse_metrics:
        per_call_path = output_dir / "per_call_spectral_metrics.csv"
        summary_path = output_dir / "summary_by_configuration.csv"
        if not per_call_path.exists() or not summary_path.exists():
            raise FileNotFoundError("Za --reuse-metrics nedostaju prethodno generisani CSV rezultati")
        with per_call_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        with summary_path.open(newline="", encoding="utf-8") as handle:
            summaries = list(csv.DictReader(handle))
        row_lookup = {
            (str(row["test_id"]), int(row["iteration"])): row
            for row in rows
        }
        for test_id in REPRESENTATIVE_TESTS:
            spec = matrix[test_id]
            codec_a = spec["codec_a"].upper()
            codec_b = spec["codec_b"].upper()
            iteration = chosen_iterations[test_id]
            reference, final, _ = load_aligned_pair(test_id, iteration, codec_a, codec_b)
            filename = f"{test_id}_{codec_a}_to_{codec_b}_comparison.png"
            make_comparison_chart(
                charts_dir / filename,
                test_id,
                codec_a,
                codec_b,
                iteration,
                reference,
                final,
                row_lookup[(test_id, iteration)],
            )
        make_heatmap(charts_dir / "wide_band_energy_change_heatmap.png", summaries)
        write_readme(output_dir, summaries)
        print(f"Grafikoni ponovo generisani u {output_dir}")
        return 0

    rows: list[dict[str, object]] = []
    total = len(matrix) * 10
    completed = 0
    for test_id in sorted(matrix):
        spec = matrix[test_id]
        codec_a = spec["codec_a"].upper()
        codec_b = spec["codec_b"].upper()
        for iteration in range(1, 11):
            reference, final, alignment = load_aligned_pair(
                test_id, iteration, codec_a, codec_b
            )
            ref_metrics = spectral_metrics(reference)
            final_metrics = spectral_metrics(final)
            e2e = e2e_lookup[(test_id, iteration)]
            row: dict[str, object] = {
                "test_id": test_id,
                "iteration": iteration,
                "codec_a": codec_a,
                "codec_b": codec_b,
                "scenario_type": spec["scenario_type"],
                "pesq_wb_16k": float(e2e["pesq_wb_16k"]),
                "alignment_delay_ms": alignment["delay_ms"],
                "aligned_duration_s": alignment["aligned_duration_s"],
                "waveform_correlation": alignment["waveform_correlation"],
            }
            row.update({f"reference_{key}": value for key, value in ref_metrics.items()})
            row.update({f"final_{key}": value for key, value in final_metrics.items()})
            rows.append(row)

            if (
                test_id in REPRESENTATIVE_TESTS
                and iteration == chosen_iterations[test_id]
            ):
                filename = f"{test_id}_{codec_a}_to_{codec_b}_comparison.png"
                make_comparison_chart(
                    charts_dir / filename,
                    test_id,
                    codec_a,
                    codec_b,
                    iteration,
                    reference,
                    final,
                    row,
                )

            completed += 1
            print(f"[{completed:03d}/{total}] {test_id} iteracija {iteration}", flush=True)

    summaries = summarize(rows)
    write_csv(output_dir / "per_call_spectral_metrics.csv", rows)
    write_json(output_dir / "per_call_spectral_metrics.json", rows)
    write_csv(output_dir / "summary_by_configuration.csv", summaries)
    write_json(output_dir / "summary_by_configuration.json", summaries)
    make_heatmap(charts_dir / "wide_band_energy_change_heatmap.png", summaries)
    write_readme(output_dir, summaries)
    print(f"Rezultati zapisani u {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
