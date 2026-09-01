#!/usr/bin/env python3
"""End-to-end analiza: originalni WAV prema izlaznom B-leg RTP signalu.

Skripta ne pokrece nove pozive i ne mijenja postojece JSON rezultate. Svaki
postojeci PCAP se ponovo cita, B-leg se dekodira, a vremenski razmaci vidljivi
iz RTP timestampova vracaju se kao nulti PCM intervali. Dobijeni signal poredi
se sa WAV datotekom koja je zaista reproducirana na A krajnjem uredjaju.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.io import wavfile
from scipy.signal import correlate, resample_poly


PROJECT_DIR = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_DIR / "results" / "raw"
MATRIX_CSV = PROJECT_DIR / "scripts" / "test" / "codec_matrix.csv"
CURRENT_SUMMARY_CSV = PROJECT_DIR / "results" / "summary" / "results_summary.csv"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "results" / "end_to_end_original_vs_bleg_20260828"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_and_compare import decode_to_wav, find_streams, identify_legs


CODECS = {
    "PCMU": {"pcm_rate": 8000, "rtp_step": 160, "samples_per_frame": 160},
    "PCMA": {"pcm_rate": 8000, "rtp_step": 160, "samples_per_frame": 160},
    "G722": {"pcm_rate": 16000, "rtp_step": 160, "samples_per_frame": 320},
    "GSM": {"pcm_rate": 8000, "rtp_step": 160, "samples_per_frame": 160},
    "OPUS": {"pcm_rate": 48000, "rtp_step": 960, "samples_per_frame": 960},
}

UNIFORM_RATE = 16000
MIN_OVERLAP_SECONDS = 8.0
EDGE_TRIM_SECONDS = 0.5
MAX_ALIGNMENT_SECONDS = 0.5


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_matrix() -> dict[str, dict[str, str]]:
    with MATRIX_CSV.open(newline="", encoding="utf-8") as handle:
        return {row["test_id"]: row for row in csv.DictReader(handle)}


def load_current_summary() -> dict[str, dict[str, str]]:
    with CURRENT_SUMMARY_CSV.open(newline="", encoding="utf-8") as handle:
        return {row["test_id"]: row for row in csv.DictReader(handle)}


def load_wav_float(path: Path) -> tuple[int, np.ndarray]:
    sample_rate, data = wavfile.read(path)
    original_dtype = data.dtype
    if data.ndim > 1:
        data = data[:, 0]
    data = data.astype(np.float64)
    if np.issubdtype(original_dtype, np.integer):
        info = np.iinfo(original_dtype)
        data /= max(abs(info.min), info.max)
    elif not np.issubdtype(original_dtype, np.floating):
        raise TypeError(f"Nepodrzan WAV tip: {original_dtype}")
    return int(sample_rate), data


def resample_audio(data: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        return data.copy()
    divisor = math.gcd(source_rate, target_rate)
    return resample_poly(data, target_rate // divisor, source_rate // divisor)


def extract_rtp_packets(pcap: Path, ssrc: str) -> list[dict[str, object]]:
    command = [
        "tshark", "-r", str(pcap),
        "-Y", f"rtp.ssrc == {ssrc} && rtp.payload",
        "-T", "fields",
        "-e", "rtp.seq",
        "-e", "rtp.timestamp",
        "-e", "rtp.payload",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "tshark nije uspio")

    packets = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        try:
            payload = bytes.fromhex(parts[2].replace(":", ""))
            packets.append({
                "sequence": int(parts[0]),
                "timestamp": int(parts[1]),
                "payload": payload,
            })
        except (ValueError, IndexError):
            continue
    return packets


def restore_rtp_timeline(
    decoded: np.ndarray,
    packets: list[dict[str, object]],
    codec: str,
) -> tuple[np.ndarray, dict[str, object]]:
    """Vrati vremenske praznine koje obicno nestanu spajanjem payloada."""
    config = CODECS[codec]
    samples_per_frame = int(config["samples_per_frame"])
    rtp_step = int(config["rtp_step"])

    remainder = len(decoded) % samples_per_frame
    if remainder:
        raise ValueError(
            f"Dekodirani signal ima {len(decoded)} uzoraka, sto nije cijeli broj "
            f"okvira od {samples_per_frame} uzoraka"
        )

    decoded_frames = len(decoded) // samples_per_frame
    skipped_decoder_frames = len(packets) - decoded_frames
    if skipped_decoder_frames < 0:
        raise ValueError("Dekoder je proizveo vise okvira nego sto postoji RTP paketa")
    if codec != "OPUS" and skipped_decoder_frames != 0:
        raise ValueError(
            f"Neocekivano preskocenih okvira za {codec}: {skipped_decoder_frames}"
        )
    if codec == "OPUS" and skipped_decoder_frames not in range(0, 7):
        raise ValueError(
            f"Neocekivani Opus pre-skip: {skipped_decoder_frames} okvira"
        )



    active_packets = packets[skipped_decoder_frames:]
    if len(active_packets) != decoded_frames:
        raise ValueError("Broj aktivnih RTP i dekodiranih PCM okvira se ne podudara")

    pieces: list[np.ndarray] = []
    inserted_frames = 0
    non_integral_jumps = 0
    sequence_gaps = 0

    for index, packet in enumerate(active_packets):
        start = index * samples_per_frame
        pieces.append(decoded[start:start + samples_per_frame])
        if index + 1 == len(active_packets):
            continue

        next_packet = active_packets[index + 1]
        timestamp_delta = (
            int(next_packet["timestamp"]) - int(packet["timestamp"])
        ) & 0xFFFFFFFF
        sequence_delta = (
            int(next_packet["sequence"]) - int(packet["sequence"])
        ) & 0xFFFF
        if sequence_delta != 1:
            sequence_gaps += max(0, sequence_delta - 1)

        ratio = timestamp_delta / rtp_step
        missing = max(0, int(round(ratio)) - 1)
        if abs(ratio - round(ratio)) > 1e-6:
            non_integral_jumps += 1
        if missing > 50:
            raise ValueError(
                f"RTP timestamp skok predstavlja {missing} okvira; analiza zaustavljena"
            )
        if missing:
            pieces.append(np.zeros(missing * samples_per_frame, dtype=decoded.dtype))
            inserted_frames += missing

    restored = np.concatenate(pieces) if pieces else np.array([], dtype=np.float64)
    diagnostics = {
        "rtp_packets": len(packets),
        "decoded_frames": decoded_frames,
        "decoder_preskip_frames": skipped_decoder_frames,
        "inserted_timeline_frames": inserted_frames,
        "inserted_timeline_ms": inserted_frames * 20,
        "sequence_gap_packets": sequence_gaps,
        "non_integral_timestamp_jumps": non_integral_jumps,
    }
    return restored, diagnostics


def align_signals(
    reference: np.ndarray,
    degraded: np.ndarray,
    sample_rate: int,
) -> tuple[np.ndarray, np.ndarray, dict[str, float]]:
    signal_length = min(len(reference), len(degraded))
    if signal_length < int(MIN_OVERLAP_SECONDS * sample_rate):
        raise ValueError("Premalo zajednickog audio-signala za pouzdanu analizu")

    start = int(signal_length * 0.1)
    end = int(signal_length * 0.9)
    ref_segment = reference[start:end]
    deg_segment = degraded[start:end]

    correlation = correlate(ref_segment, deg_segment, mode="full", method="fft")
    center = len(ref_segment) - 1
    max_delay = int(MAX_ALIGNMENT_SECONDS * sample_rate)
    search_start = max(0, center - max_delay)
    search_end = min(len(correlation), center + max_delay + 1)
    peak = search_start + int(np.argmax(correlation[search_start:search_end]))
    delay = peak - center

    ref_start = max(0, delay)
    deg_start = max(0, -delay)
    common = min(len(reference) - ref_start, len(degraded) - deg_start)
    trim = int(EDGE_TRIM_SECONDS * sample_rate)
    if common <= 2 * trim:
        raise ValueError("Signal je prekratak nakon poravnanja i rubnog skracivanja")

    ref_start += trim
    deg_start += trim
    common -= 2 * trim
    aligned_ref = reference[ref_start:ref_start + common]
    aligned_deg = degraded[deg_start:deg_start + common]

    if np.std(aligned_ref) < 1e-8 or np.std(aligned_deg) < 1e-8:
        pearson = float("nan")
    else:
        pearson = float(np.corrcoef(aligned_ref, aligned_deg)[0, 1])
    return aligned_ref, aligned_deg, {
        "delay_ms": delay * 1000.0 / sample_rate,
        "aligned_duration_s": common / sample_rate,
        "waveform_correlation": pearson,
    }


def pesq_score(reference: np.ndarray, degraded: np.ndarray, sample_rate: int) -> float:
    from pesq import pesq

    mode = "nb" if sample_rate == 8000 else "wb"
    ref_pcm = (np.clip(reference, -1.0, 1.0) * 32767).astype(np.int16)
    deg_pcm = (np.clip(degraded, -1.0, 1.0) * 32767).astype(np.int16)
    return float(pesq(sample_rate, ref_pcm, deg_pcm, mode))


def stoi_score(reference: np.ndarray, degraded: np.ndarray, sample_rate: int) -> float:
    from pystoi import stoi

    return float(stoi(reference, degraded, sample_rate, extended=False))


def reference_for_codec(codec: str) -> Path:
    rate = CODECS[codec]["pcm_rate"]
    return PROJECT_DIR / "audio" / "reference" / f"reference_{rate // 1000}k.wav"


def analyze_call(
    json_path: Path,
    matrix_row: dict[str, str],
    decoded_dir: Path | None = None,
) -> dict[str, object]:
    with json_path.open(encoding="utf-8") as handle:
        source_record = json.load(handle)

    test_id = matrix_row["test_id"]
    iteration_match = re.search(r"_iter(\d+)$", json_path.stem)
    if not iteration_match:
        raise ValueError(f"Ne mogu procitati iteraciju iz {json_path.name}")
    iteration = int(iteration_match.group(1))
    codec_a = matrix_row["codec_a"].upper()
    codec_b = matrix_row["codec_b"].upper()
    pcap = RAW_DIR / f"{test_id}_iter{iteration}.pcap"
    reference_path = reference_for_codec(codec_a)

    if not source_record.get("success"):
        raise ValueError("Izvorni poziv nije oznacen kao uspjesan")
    if not pcap.exists() or not reference_path.exists():
        raise FileNotFoundError(f"Nedostaje PCAP ili referenca za {test_id} iteraciju {iteration}")

    streams = find_streams(str(pcap))
    _, b_stream = identify_legs(streams, codec_a, codec_b)
    if not b_stream:
        raise ValueError(f"B-leg {codec_b} tok nije pronadjen")
    packets = extract_rtp_packets(pcap, str(b_stream["ssrc"]))
    if len(packets) < 50:
        raise ValueError(f"B-leg ima samo {len(packets)} RTP paketa")

    payloads = [packet["payload"] for packet in packets]
    if decoded_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="e2e_bleg_")
        native_wav = Path(temporary.name) / "b_leg_native.wav"
    else:
        temporary = None
        decoded_dir.mkdir(parents=True, exist_ok=True)
        native_wav = decoded_dir / f"{test_id}_iter{iteration}_b_leg_native.wav"

    try:
        if not decode_to_wav(payloads, codec_b, str(native_wav)):
            raise RuntimeError("Dekodiranje B-leg RTP payloada nije uspjelo")
        native_rate, decoded = load_wav_float(native_wav)
        expected_rate = int(CODECS[codec_b]["pcm_rate"])
        if native_rate != expected_rate:
            raise ValueError(
                f"Dekodirani {codec_b} ima {native_rate} Hz umjesto {expected_rate} Hz"
            )
        restored, rtp_diagnostics = restore_rtp_timeline(decoded, packets, codec_b)
    finally:
        if temporary is not None:
            temporary.cleanup()

    reference_rate, reference = load_wav_float(reference_path)
    restored_16k = resample_audio(restored, native_rate, UNIFORM_RATE)
    reference_16k = resample_audio(reference, reference_rate, UNIFORM_RATE)
    ref_wb, deg_wb, alignment = align_signals(reference_16k, restored_16k, UNIFORM_RATE)

    pesq_wb = pesq_score(ref_wb, deg_wb, UNIFORM_RATE)
    stoi_value = stoi_score(ref_wb, deg_wb, UNIFORM_RATE)

    native_pesq_rate = 8000 if reference_rate == 8000 else 16000
    if native_pesq_rate == UNIFORM_RATE:
        pesq_native = pesq_wb
        native_alignment = alignment
    else:
        native_ref = resample_audio(reference, reference_rate, native_pesq_rate)
        native_deg = resample_audio(restored, native_rate, native_pesq_rate)
        ref_native, deg_native, native_alignment = align_signals(
            native_ref, native_deg, native_pesq_rate
        )
        pesq_native = pesq_score(ref_native, deg_native, native_pesq_rate)

    result: dict[str, object] = {
        "test_id": test_id,
        "iteration": iteration,
        "codec_a": codec_a,
        "codec_b": codec_b,
        "scenario_type": matrix_row["scenario_type"],
        "source_timestamp": source_record.get("timestamp"),
        "reference_file": str(reference_path.relative_to(PROJECT_DIR)),
        "reference_sha256": sha256(reference_path),
        "pcap_file": str(pcap.relative_to(PROJECT_DIR)),
        "pcap_sha256": sha256(pcap),
        "pesq_wb_16k": pesq_wb,
        "pesq_native_mode": pesq_native,
        "pesq_native_rate_hz": native_pesq_rate,
        "stoi_16k": stoi_value,
        "alignment_delay_ms": alignment["delay_ms"],
        "aligned_duration_s": alignment["aligned_duration_s"],
        "waveform_correlation": alignment["waveform_correlation"],
        "native_alignment_delay_ms": native_alignment["delay_ms"],
        "b_leg_native_duration_s": len(decoded) / native_rate,
        "b_leg_restored_duration_s": len(restored) / native_rate,
        **rtp_diagnostics,
        "status": "ok",
    }
    return result


def round_or_none(value: object, digits: int) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return round(number, digits)


def summarize(
    per_call: list[dict[str, object]],
    matrix: dict[str, dict[str, str]],
    current_summary: dict[str, dict[str, str]],
) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in per_call:
        if row.get("status") == "ok":
            grouped[str(row["test_id"])].append(row)

    summaries: list[dict[str, object]] = []
    metrics = [
        ("pesq_wb_16k", 3),
        ("pesq_native_mode", 3),
        ("stoi_16k", 4),
        ("waveform_correlation", 4),
        ("inserted_timeline_frames", 2),
        ("alignment_delay_ms", 2),
    ]
    for test_id in sorted(matrix):
        rows = grouped.get(test_id, [])
        spec = matrix[test_id]
        summary: dict[str, object] = {
            "test_id": test_id,
            "codec_a": spec["codec_a"],
            "codec_b": spec["codec_b"],
            "scenario_type": spec["scenario_type"],
            "n_successful": len(rows),
            "native_pesq_mode": "nb" if int(spec["sample_rate_a"]) == 8000 else "wb",
            "a_leg_to_b_leg_pesq_mean": round_or_none(
                current_summary.get(test_id, {}).get("pesq_mean"), 3
            ),
        }
        for metric, digits in metrics:
            values = np.asarray(
                [float(row[metric]) for row in rows if row.get(metric) is not None],
                dtype=float,
            )
            summary[f"{metric}_mean"] = round_or_none(np.mean(values), digits) if len(values) else None
            summary[f"{metric}_std"] = (
                round_or_none(np.std(values, ddof=1), digits) if len(values) > 1 else 0.0
            )
            summary[f"{metric}_min"] = round_or_none(np.min(values), digits) if len(values) else None
            summary[f"{metric}_max"] = round_or_none(np.max(values), digits) if len(values) else None
        summaries.append(summary)

    control_by_source = {
        row["codec_a"]: row
        for row in summaries
        if row["scenario_type"] == "passthrough"
    }
    for row in summaries:
        control = control_by_source.get(str(row["codec_a"]))
        if (
            control
            and row["scenario_type"] == "transcode"
            and control.get("pesq_wb_16k_mean") is not None
            and row.get("pesq_wb_16k_mean") is not None
            and control.get("pesq_native_mode_mean") is not None
            and row.get("pesq_native_mode_mean") is not None
        ):
            row["additional_degradation_vs_source_control_wb"] = round_or_none(
                float(control["pesq_wb_16k_mean"]) - float(row["pesq_wb_16k_mean"]), 3
            )
            row["additional_degradation_vs_source_control_native"] = round_or_none(
                float(control["pesq_native_mode_mean"])
                - float(row["pesq_native_mode_mean"]),
                3,
            )
        else:
            row["additional_degradation_vs_source_control_wb"] = 0.0
            row["additional_degradation_vs_source_control_native"] = 0.0
    return summaries


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, data: object) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def generate_charts(output_dir: Path, rows: list[dict[str, object]]) -> None:
    charts = output_dir / "charts"
    charts.mkdir(parents=True, exist_ok=True)
    valid = [row for row in rows if int(row["n_successful"]) > 0]
    labels = [str(row["test_id"]) for row in valid]
    x = np.arange(len(valid))

    for metric, std_metric, title, ylabel, filename in [
        (
            "pesq_wb_16k_mean", "pesq_wb_16k_std",
            "End-to-end PESQ WB: originalni WAV prema B-legu",
            "PESQ MOS-LQO", "pesq_wb_16k_bars.png",
        ),
        (
            "pesq_native_mode_mean", "pesq_native_mode_std",
            "End-to-end PESQ u rezimu izvornog opsega",
            "PESQ MOS-LQO", "pesq_native_mode_bars.png",
        ),
        (
            "stoi_16k_mean", "stoi_16k_std",
            "End-to-end STOI: originalni WAV prema B-legu",
            "STOI", "stoi_16k_bars.png",
        ),
    ]:
        values = [float(row[metric]) for row in valid]
        errors = [float(row[std_metric]) for row in valid]
        colors = ["#4C78A8" if row["scenario_type"] == "passthrough" else "#F58518" for row in valid]
        fig, axis = plt.subplots(figsize=(13, 6))
        axis.bar(x, values, yerr=errors, capsize=2, color=colors, edgecolor="black", linewidth=0.4)
        axis.set_xticks(x)
        axis.set_xticklabels(labels, rotation=60, ha="right")
        axis.set_ylabel(ylabel)
        axis.set_title(title)
        axis.grid(axis="y", alpha=0.25)
        fig.tight_layout()
        fig.savefig(charts / filename, dpi=180)
        plt.close(fig)

    codecs = ["PCMU", "PCMA", "G722", "GSM", "OPUS"]
    matrix = np.full((len(codecs), len(codecs)), np.nan)
    for row in valid:
        matrix[codecs.index(str(row["codec_a"]))][codecs.index(str(row["codec_b"]))] = float(
            row["pesq_wb_16k_mean"]
        )
    fig, axis = plt.subplots(figsize=(8, 6.5))
    image = axis.imshow(matrix, cmap="viridis", vmin=np.nanmin(matrix), vmax=np.nanmax(matrix))
    axis.set_xticks(range(len(codecs)), codecs)
    axis.set_yticks(range(len(codecs)), codecs)
    axis.set_xlabel("Odredisni kodek B")
    axis.set_ylabel("Izvorni kodek A")
    axis.set_title("End-to-end PESQ WB matrica")
    for i in range(len(codecs)):
        for j in range(len(codecs)):
            axis.text(j, i, f"{matrix[i, j]:.2f}", ha="center", va="center", color="white")
    fig.colorbar(image, ax=axis, label="PESQ MOS-LQO")
    fig.tight_layout()
    fig.savefig(charts / "pesq_wb_16k_heatmap.png", dpi=180)
    plt.close(fig)


def write_readme(
    output_dir: Path,
    per_call: list[dict[str, object]],
    summaries: list[dict[str, object]],
) -> None:
    successful = [row for row in per_call if row.get("status") == "ok"]
    failed = [row for row in per_call if row.get("status") != "ok"]
    controls = [row for row in summaries if row["scenario_type"] == "passthrough"]
    transcodes = [row for row in summaries if row["scenario_type"] == "transcode"]

    def group_mean(rows: list[dict[str, object]], metric: str) -> float:
        values = [float(row[metric]) for row in rows if row.get(metric) is not None]
        return float(np.mean(values)) if values else float("nan")

    text = f"""# End-to-end analiza: originalni WAV -> B-leg

Generisano: {datetime.now().isoformat(timespec='seconds')}

Ovaj direktorij je odvojen od vazecih rezultata rada. Postojeci pozivi nisu
ponovo pokretani i datoteke u `results/raw` nisu mijenjane.

## Metodologija

- Referenca je WAV koji je zaista reproduciran za izvorni kodek A.
- Iz svakog PCAP-a izdvojen je izlazni B-leg RTP tok i ponovo dekodiran.
- RTP timestamp praznine vracene su kao nulti PCM intervali, jer bi prosto
  spajanje payloada vremenski sabijalo signal.
- `pesq_wb_16k` koristi jedinstveni sirokopojasni postupak na 16 kHz radi
  usporedbe s postojecim radom.
- `pesq_native_mode` koristi NB na 8 kHz za izvore od 8 kHz, a WB na 16 kHz
  za G.722 i Opus izvore. NB i WB vrijednosti ne treba direktno rangirati kao
  jednu homogenu skalu.
- STOI je racunat na 16 kHz.

## Kompletnost

- Uspjesno obradjenih poziva: {len(successful)} / {len(per_call)}
- Neuspjelih analiza: {len(failed)}
- Konfiguracija sa svih deset rezultata: {sum(int(row['n_successful']) == 10 for row in summaries)} / {len(summaries)}

## Grupni deskriptivni rezultati

- PESQ WB, kontrole bez transkodiranja: {group_mean(controls, 'pesq_wb_16k_mean'):.3f}
- PESQ WB, konfiguracije s transkodiranjem: {group_mean(transcodes, 'pesq_wb_16k_mean'):.3f}
- STOI, kontrole bez transkodiranja: {group_mean(controls, 'stoi_16k_mean'):.3f}
- STOI, konfiguracije s transkodiranjem: {group_mean(transcodes, 'stoi_16k_mean'):.3f}

## Vazna ogranicenja

Ova analiza ukljucuje pocetno kodiranje kodekom A i izlazno kodiranje kodekom
B, pa nije zamjena za postojecu A-leg -> B-leg mjeru koja izoluje server.
Nulti PCM u RTP prazninama predstavlja konzervativnu rekonstrukciju; stvarni
krajnji uredjaj moze koristiti prikrivanje gubitka paketa. Opus se rekonstruise
u Ogg wrapper i zadrzava ranije dokumentovano ogranicenje te rekonstrukcije.
Rezultati su ukljuceni u poglavlja o rezultatima i zakljucku diplomskog rada.
"""
    (output_dir / "README.md").write_text(text, encoding="utf-8")


def write_validation_report(
    output_dir: Path,
    per_call: list[dict[str, object]],
    summaries: list[dict[str, object]],
) -> None:
    successful = [row for row in per_call if row.get("status") == "ok"]

    def bounds(field: str) -> dict[str, float]:
        values = [float(row[field]) for row in successful]
        return {
            "min": min(values),
            "mean": float(np.mean(values)),
            "max": max(values),
        }

    report = {
        "checks": {
            "all_250_calls_successful": len(successful) == 250,
            "all_25_configurations_have_10_results": (
                len(summaries) == 25
                and all(int(row["n_successful"]) == 10 for row in summaries)
            ),
            "all_pcap_hashes_unique": (
                len({str(row["pcap_sha256"]) for row in successful}) == len(successful)
            ),
            "no_non_integral_rtp_timestamp_jumps": (
                sum(int(row["non_integral_timestamp_jumps"]) for row in successful) == 0
            ),
            "no_rtp_sequence_gaps": (
                sum(int(row["sequence_gap_packets"]) for row in successful) == 0
            ),
            "aligned_duration_at_least_10_seconds": all(
                float(row["aligned_duration_s"]) >= 10.0 for row in successful
            ),
            "alignment_within_search_window": all(
                abs(float(row["alignment_delay_ms"])) < MAX_ALIGNMENT_SECONDS * 1000
                for row in successful
            ),
            "opus_preskip_consistent": all(
                int(row["decoder_preskip_frames"]) == (4 if row["codec_b"] == "OPUS" else 0)
                for row in successful
            ),
        },
        "ranges": {
            field: bounds(field)
            for field in [
                "pesq_wb_16k",
                "pesq_native_mode",
                "stoi_16k",
                "alignment_delay_ms",
                "aligned_duration_s",
                "waveform_correlation",
                "inserted_timeline_frames",
            ]
        },
        "totals": {
            "calls": len(per_call),
            "successful": len(successful),
            "configurations": len(summaries),
            "non_integral_timestamp_jumps": sum(
                int(row["non_integral_timestamp_jumps"]) for row in successful
            ),
            "rtp_sequence_gap_packets": sum(
                int(row["sequence_gap_packets"]) for row in successful
            ),
            "unique_pcap_sha256": len(
                {str(row["pcap_sha256"]) for row in successful}
            ),
        },
        "interpretation_note": (
            "Niske korelacije u pojedinim Opus/GSM lancima nisu automatski kvar "
            "poravnanja; PESQ i STOI su primarne metrike, a Opus Ogg rekonstrukcija "
            "ostaje posebno metodolosko ogranicenje."
        ),
    }
    write_json(output_dir / "validation_report.json", report)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--test-id", action="append", help="Obradi samo navedeni Txxx")
    parser.add_argument("--iteration", type=int, help="Obradi samo navedenu iteraciju")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--keep-decoded",
        action="store_true",
        help="Sacuvaj ponovo dekodirane B-leg WAV datoteke u izlaznom direktoriju",
    )
    parser.add_argument(
        "--reuse-per-call",
        action="store_true",
        help="Ponovo generisi sazetke iz postojecih pojedinacnih rezultata bez dekodiranja PCAP-a",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    matrix = load_matrix()
    current_summary = load_current_summary()

    if args.reuse_per_call:
        per_call_path = output_dir / "per_call_results.json"
        if not per_call_path.exists():
            raise FileNotFoundError(f"Nedostaje {per_call_path}")
        with per_call_path.open(encoding="utf-8") as handle:
            per_call = json.load(handle)
        json_files = []
    else:
        json_files = sorted(
            RAW_DIR.glob("T*_iter*.json"),
            key=lambda path: (
                int(re.search(r"T(\d+)", path.stem).group(1)),
                int(re.search(r"iter(\d+)", path.stem).group(1)),
            ),
        )
    if args.test_id:
        selected = set(args.test_id)
        json_files = [path for path in json_files if path.stem.split("_", 1)[0] in selected]
    if args.iteration is not None:
        json_files = [
            path for path in json_files
            if int(re.search(r"iter(\d+)", path.stem).group(1)) == args.iteration
        ]
    if args.limit is not None:
        json_files = json_files[: args.limit]

    decoded_dir = output_dir / "decoded_b_leg" if args.keep_decoded else None
    if not args.reuse_per_call:
        per_call = []
    total = len(json_files)
    for index, json_path in enumerate(json_files, start=1):
        test_id = json_path.stem.split("_", 1)[0]
        try:
            result = analyze_call(json_path, matrix[test_id], decoded_dir)
            print(
                f"[{index:03d}/{total:03d}] {json_path.stem}: "
                f"PESQ-WB={result['pesq_wb_16k']:.3f}, "
                f"STOI={result['stoi_16k']:.3f}, "
                f"gaps={result['inserted_timeline_frames']}"
            )
        except Exception as error:
            result = {
                "test_id": test_id,
                "iteration": int(re.search(r"iter(\d+)", json_path.stem).group(1)),
                "codec_a": matrix[test_id]["codec_a"],
                "codec_b": matrix[test_id]["codec_b"],
                "scenario_type": matrix[test_id]["scenario_type"],
                "status": "error",
                "error": str(error),
            }
            print(f"[{index:03d}/{total:03d}] {json_path.stem}: ERROR: {error}")
        per_call.append(result)

    summaries = summarize(per_call, matrix, current_summary)
    write_json(output_dir / "per_call_results.json", per_call)
    write_csv(output_dir / "per_call_results.csv", per_call)
    write_json(output_dir / "summary_by_configuration.json", summaries)
    write_csv(output_dir / "summary_by_configuration.csv", summaries)
    generate_charts(output_dir, summaries)
    write_readme(output_dir, per_call, summaries)
    write_validation_report(output_dir, per_call, summaries)

    failures = [row for row in per_call if row.get("status") != "ok"]
    incomplete = [row for row in summaries if int(row["n_successful"]) != 10]
    print(f"\nRezultati: {output_dir}")
    print(f"Uspjesno: {len(per_call) - len(failures)}/{len(per_call)}")
    if (
        not args.test_id
        and args.iteration is None
        and args.limit is None
        and (failures or incomplete)
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
