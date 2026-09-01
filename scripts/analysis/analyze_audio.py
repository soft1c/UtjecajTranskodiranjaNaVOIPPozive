#!/usr/bin/env python3
"""
analyze_audio.py - Analiza kvalitete audio signala nakon VoIP transkodiranja.

Uspoređuje referentni (originalni) audio sa degradiranim (primljenim) audiom
koristeći objektivne metrike:
  - PESQ (ITU-T P.862) - Perceptual Evaluation of Speech Quality
  - SNR - Signal-to-Noise Ratio
  - STOI - Short-Time Objective Intelligibility

Korištenje:
    python analyze_audio.py --reference ref.wav --degraded deg.wav --output result.json
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import correlate, resample


def load_wav(filepath):
    """Učitaj WAV fajl i vrati (sample_rate, data kao float64 normalizirano)."""
    sr, data = wavfile.read(filepath)

    if data.dtype == np.int16:
        data = data.astype(np.float64) / 32768.0
    elif data.dtype == np.int32:
        data = data.astype(np.float64) / 2147483648.0
    elif data.dtype == np.float32:
        data = data.astype(np.float64)

    if len(data.shape) > 1:
        data = data[:, 0]
    return sr, data


def resample_to_rate(data, orig_sr, target_sr):
    """Resample audio signal na ciljani sample rate."""
    if orig_sr == target_sr:
        return data
    ratio = target_sr / orig_sr
    new_length = int(len(data) * ratio)
    return resample(data, new_length)


def align_signals(ref, deg):
    """
    Poravnaj degradirani signal sa referentnim koristeći cross-correlation.
    Vraća (poravnani_ref, poravnani_deg, delay_u_uzorcima).
    """

    max_search = min(len(ref), len(deg), 48000)

    ref_segment = ref[:max_search]
    deg_segment = deg[:max_search]


    correlation = correlate(ref_segment, deg_segment, mode="full")
    max_idx = np.argmax(np.abs(correlation))
    delay = max_idx - len(ref_segment) + 1


    if delay > 0:

        aligned_ref = ref[delay:]
        aligned_deg = deg
    elif delay < 0:

        aligned_ref = ref
        aligned_deg = deg[-delay:]
    else:
        aligned_ref = ref
        aligned_deg = deg


    min_len = min(len(aligned_ref), len(aligned_deg))
    aligned_ref = aligned_ref[:min_len]
    aligned_deg = aligned_deg[:min_len]

    return aligned_ref, aligned_deg, delay


def calculate_snr(reference, degraded):
    """Izračunaj Signal-to-Noise Ratio u dB."""
    noise = reference - degraded
    signal_power = np.sum(reference ** 2)
    noise_power = np.sum(noise ** 2)

    if noise_power == 0:
        return float("inf")
    if signal_power == 0:
        return float("-inf")

    return 10 * np.log10(signal_power / noise_power)


def calculate_segmental_snr(reference, degraded, frame_length=256, hop_length=128):
    """
    Izračunaj segmentalni SNR - prosjek SNR-a po frameovima.
    Robusniji od globalnog SNR-a za govorni signal.
    """
    num_frames = (len(reference) - frame_length) // hop_length + 1
    snr_values = []

    for i in range(num_frames):
        start = i * hop_length
        end = start + frame_length

        ref_frame = reference[start:end]
        deg_frame = degraded[start:end]
        noise_frame = ref_frame - deg_frame

        signal_power = np.sum(ref_frame ** 2)
        noise_power = np.sum(noise_frame ** 2)

        if signal_power > 1e-10 and noise_power > 1e-10:
            frame_snr = 10 * np.log10(signal_power / noise_power)

            frame_snr = np.clip(frame_snr, -10, 35)
            snr_values.append(frame_snr)

    return float(np.mean(snr_values)) if snr_values else 0.0


def calculate_pesq(reference, degraded, sample_rate):
    """
    Izračunaj PESQ skor (MOS-LQO).
    Zahtijeva pesq Python paket.
    """
    try:
        from pesq import pesq


        if sample_rate == 8000:
            mode = "nb"
        elif sample_rate == 16000:
            mode = "wb"
        else:

            reference = resample_to_rate(reference, sample_rate, 16000)
            degraded = resample_to_rate(degraded, sample_rate, 16000)
            sample_rate = 16000
            mode = "wb"


        ref_int16 = (reference * 32768).astype(np.int16)
        deg_int16 = (degraded * 32768).astype(np.int16)


        min_samples = sample_rate
        if len(ref_int16) < min_samples or len(deg_int16) < min_samples:
            print("  UPOZORENJE: Audio prekratak za PESQ analizu")
            return None

        score = pesq(sample_rate, ref_int16, deg_int16, mode)
        return float(score)

    except ImportError:
        print("  UPOZORENJE: pesq paket nije instaliran. Instalirajte: pip install pesq")
        return None
    except Exception as e:
        print(f"  UPOZORENJE: PESQ izračun neuspješan: {e}")
        return None


def calculate_stoi(reference, degraded, sample_rate):
    """
    Izračunaj STOI (Short-Time Objective Intelligibility).
    Zahtijeva pystoi Python paket.
    """
    try:
        from pystoi import stoi

        score = stoi(reference, degraded, sample_rate, extended=False)
        return float(score)

    except ImportError:
        print("  UPOZORENJE: pystoi paket nije instaliran. Instalirajte: pip install pystoi")
        return None
    except Exception as e:
        print(f"  UPOZORENJE: STOI izračun neuspješan: {e}")
        return None


def analyze(reference_path, degraded_path, target_sr=None):
    """
    Kompletna audio analiza: učitaj, poravnaj, izračunaj metrike.
    """
    print(f"  Referentni: {reference_path}")
    print(f"  Degradirani: {degraded_path}")


    ref_sr, ref_data = load_wav(reference_path)
    deg_sr, deg_data = load_wav(degraded_path)

    print(f"  Ref SR: {ref_sr} Hz, dužina: {len(ref_data)} uzoraka ({len(ref_data)/ref_sr:.2f}s)")
    print(f"  Deg SR: {deg_sr} Hz, dužina: {len(deg_data)} uzoraka ({len(deg_data)/deg_sr:.2f}s)")


    if target_sr is None:
        target_sr = min(ref_sr, deg_sr)


    ref_data = resample_to_rate(ref_data, ref_sr, target_sr)
    deg_data = resample_to_rate(deg_data, deg_sr, target_sr)

    print(f"  Ciljani SR: {target_sr} Hz")
    print(f"  Ref dužina nakon resample: {len(ref_data)}")
    print(f"  Deg dužina nakon resample: {len(deg_data)}")


    aligned_ref, aligned_deg, delay_samples = align_signals(ref_data, deg_data)
    delay_ms = (delay_samples / target_sr) * 1000

    print(f"  Delay: {delay_samples} uzoraka ({delay_ms:.1f} ms)")
    print(f"  Poravnana dužina: {len(aligned_ref)} uzoraka")


    results = {
        "sample_rate": target_sr,
        "delay_samples": int(delay_samples),
        "delay_ms": round(delay_ms, 2),
        "ref_duration_s": round(len(ref_data) / target_sr, 2),
        "deg_duration_s": round(len(deg_data) / target_sr, 2),
        "aligned_duration_s": round(len(aligned_ref) / target_sr, 2),
    }


    print("  Izračunavam SNR...")
    results["snr_db"] = round(calculate_snr(aligned_ref, aligned_deg), 2)
    results["segmental_snr_db"] = round(
        calculate_segmental_snr(aligned_ref, aligned_deg), 2
    )
    print(f"    SNR: {results['snr_db']} dB")
    print(f"    Segmentalni SNR: {results['segmental_snr_db']} dB")


    print("  Izračunavam PESQ...")
    results["pesq_mos"] = calculate_pesq(aligned_ref, aligned_deg, target_sr)
    if results["pesq_mos"]:
        print(f"    PESQ MOS: {results['pesq_mos']:.3f}")


    print("  Izračunavam STOI...")
    results["stoi"] = calculate_stoi(aligned_ref, aligned_deg, target_sr)
    if results["stoi"]:
        print(f"    STOI: {results['stoi']:.4f}")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Analiza kvalitete audio signala nakon VoIP transkodiranja"
    )
    parser.add_argument("--reference", required=True, help="Putanja do referentnog WAV fajla")
    parser.add_argument("--degraded", required=True, help="Putanja do degradiranog WAV fajla")
    parser.add_argument("--output", help="Putanja do JSON fajla za ažuriranje rezultata")
    parser.add_argument("--sample-rate", type=int, default=None, help="Ciljani sample rate za analizu")
    args = parser.parse_args()


    if not Path(args.reference).exists():
        print(f"GREŠKA: Referentni fajl ne postoji: {args.reference}")
        sys.exit(1)
    if not Path(args.degraded).exists():
        print(f"GREŠKA: Degradirani fajl ne postoji: {args.degraded}")
        sys.exit(1)


    results = analyze(args.reference, args.degraded, args.sample_rate)


    if args.output and Path(args.output).exists():
        with open(args.output, "r") as f:
            data = json.load(f)
        data["audio_quality"] = results
        with open(args.output, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"\n  Rezultati ažurirani u: {args.output}")
    else:

        print(f"\n  Rezultati:")
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
