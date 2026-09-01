#!/usr/bin/env python3
"""
advanced_metrics.py - Napredne audio metrike za VoIP transcoding analizu.

Metrike:
  - LSD (Log-Spectral Distance) - razlika u spektralnoj energiji po frekvencijama
  - MCD (Mel Cepstral Distortion) - razlika u mel-cepstralnim koeficijentima
  - Spectral Convergence - koliko se spektri podudaraju (0=identično)
  - fwSNR (Frequency-Weighted SNR) - SNR ponderisan prema govornim frekvencijama
  - Waveform RMSE - RMS razlika između signala
  - Correlation - Pearsonova korelacija valnih oblika
"""

import json
import sys
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import resample, correlate, stft
from scipy.fft import fft, fftfreq


def load_and_align(ref_path, deg_path):
    """Učitaj, resampleaj i poravnaj dva audio fajla."""
    ref_sr, ref = wavfile.read(ref_path)
    deg_sr, deg = wavfile.read(deg_path)


    if ref.dtype == np.int16:
        ref = ref.astype(np.float64) / 32768.0
    if deg.dtype == np.int16:
        deg = deg.astype(np.float64) / 32768.0
    if len(ref.shape) > 1:
        ref = ref[:, 0]
    if len(deg.shape) > 1:
        deg = deg[:, 0]


    target_sr = min(ref_sr, deg_sr)
    if ref_sr != target_sr:
        ref = resample(ref, int(len(ref) * target_sr / ref_sr))
    if deg_sr != target_sr:
        deg = resample(deg, int(len(deg) * target_sr / deg_sr))


    max_search = min(len(ref), len(deg), target_sr)
    corr = correlate(ref[:max_search], deg[:max_search], mode="full")
    delay = np.argmax(np.abs(corr)) - max_search + 1

    if delay > 0:
        ref = ref[delay:]
    elif delay < 0:
        deg = deg[-delay:]

    min_len = min(len(ref), len(deg))
    return ref[:min_len], deg[:min_len], target_sr


def log_spectral_distance(ref, deg, sr, frame_len=512, hop=256):
    """Log-Spectral Distance (LSD) u dB - mjeri spektralnu distorziju."""
    n_frames = (min(len(ref), len(deg)) - frame_len) // hop
    lsd_values = []

    for i in range(n_frames):
        start = i * hop
        ref_frame = ref[start:start + frame_len]
        deg_frame = deg[start:start + frame_len]


        ref_spec = np.abs(fft(ref_frame * np.hanning(frame_len))[:frame_len // 2]) ** 2
        deg_spec = np.abs(fft(deg_frame * np.hanning(frame_len))[:frame_len // 2]) ** 2


        ref_spec = np.maximum(ref_spec, 1e-10)
        deg_spec = np.maximum(deg_spec, 1e-10)

        lsd = np.sqrt(np.mean((10 * np.log10(ref_spec) - 10 * np.log10(deg_spec)) ** 2))
        if np.isfinite(lsd):
            lsd_values.append(lsd)

    return float(np.mean(lsd_values)) if lsd_values else 0.0


def mel_cepstral_distortion(ref, deg, sr, n_mfcc=13, frame_len=512, hop=256):
    """Mel Cepstral Distortion (MCD) u dB - standardna mjera za govorni kodek kvalitet."""
    def compute_mfcc(signal, sr, n_mfcc, frame_len, hop):
        n_frames = (len(signal) - frame_len) // hop
        n_fft = frame_len

        n_mels = 40
        low_freq = 0
        high_freq = sr / 2
        mel_low = 2595 * np.log10(1 + low_freq / 700)
        mel_high = 2595 * np.log10(1 + high_freq / 700)
        mel_points = np.linspace(mel_low, mel_high, n_mels + 2)
        hz_points = 700 * (10 ** (mel_points / 2595) - 1)
        bin_points = np.floor((n_fft + 1) * hz_points / sr).astype(int)

        filterbank = np.zeros((n_mels, n_fft // 2))
        for m in range(n_mels):
            for k in range(bin_points[m], bin_points[m + 1]):
                if k < n_fft // 2:
                    filterbank[m, k] = (k - bin_points[m]) / max(bin_points[m + 1] - bin_points[m], 1)
            for k in range(bin_points[m + 1], bin_points[m + 2]):
                if k < n_fft // 2:
                    filterbank[m, k] = (bin_points[m + 2] - k) / max(bin_points[m + 2] - bin_points[m + 1], 1)

        mfccs = []
        for i in range(n_frames):
            start = i * hop
            frame = signal[start:start + frame_len] * np.hanning(frame_len)
            power_spec = np.abs(fft(frame)[:n_fft // 2]) ** 2
            mel_spec = np.dot(filterbank, power_spec)
            mel_spec = np.maximum(mel_spec, 1e-10)
            log_mel = np.log(mel_spec)

            dct_matrix = np.zeros((n_mfcc, n_mels))
            for k in range(n_mfcc):
                for n in range(n_mels):
                    dct_matrix[k, n] = np.cos(np.pi * k * (2 * n + 1) / (2 * n_mels))
            mfcc = np.dot(dct_matrix, log_mel)
            mfccs.append(mfcc)

        return np.array(mfccs)

    ref_mfcc = compute_mfcc(ref, sr, n_mfcc, frame_len, hop)
    deg_mfcc = compute_mfcc(deg, sr, n_mfcc, frame_len, hop)

    n_frames = min(len(ref_mfcc), len(deg_mfcc))


    coeff = 10.0 * np.sqrt(2.0) / np.log(10.0)
    diff = ref_mfcc[:n_frames, 1:] - deg_mfcc[:n_frames, 1:]
    mcd_per_frame = np.sqrt(np.sum(diff ** 2, axis=1))
    mcd = coeff * np.mean(mcd_per_frame)

    return float(mcd)


def spectral_convergence(ref, deg, frame_len=512, hop=256):
    """Spectral Convergence - 0 = identični spektri, veće = lošije."""
    n_frames = (min(len(ref), len(deg)) - frame_len) // hop
    sc_values = []

    for i in range(n_frames):
        start = i * hop
        ref_spec = np.abs(fft(ref[start:start + frame_len] * np.hanning(frame_len))[:frame_len // 2])
        deg_spec = np.abs(fft(deg[start:start + frame_len] * np.hanning(frame_len))[:frame_len // 2])

        norm_ref = np.linalg.norm(ref_spec)
        if norm_ref > 1e-10:
            sc = np.linalg.norm(ref_spec - deg_spec) / norm_ref
            sc_values.append(sc)

    return float(np.mean(sc_values)) if sc_values else 0.0


def frequency_weighted_snr(ref, deg, sr, frame_len=512, hop=256):
    """Frequency-Weighted SNR - ponderisan prema govornim frekvencijama (300-3400 Hz)."""
    n_frames = (min(len(ref), len(deg)) - frame_len) // hop
    freqs = fftfreq(frame_len, 1.0 / sr)[:frame_len // 2]


    weights = np.zeros(frame_len // 2)
    for i, f in enumerate(freqs):
        if 300 <= abs(f) <= 3400:
            weights[i] = 1.0
        elif 100 <= abs(f) < 300 or 3400 < abs(f) <= 4000:
            weights[i] = 0.3
        else:
            weights[i] = 0.05

    fwsnr_values = []
    for i in range(n_frames):
        start = i * hop
        ref_spec = np.abs(fft(ref[start:start + frame_len] * np.hanning(frame_len))[:frame_len // 2]) ** 2
        deg_spec = np.abs(fft(deg[start:start + frame_len] * np.hanning(frame_len))[:frame_len // 2]) ** 2
        noise_spec = np.abs(ref_spec - deg_spec)

        signal_power = np.sum(weights * ref_spec)
        noise_power = np.sum(weights * noise_spec)

        if noise_power > 1e-10 and signal_power > 1e-10:
            snr = 10 * np.log10(signal_power / noise_power)
            snr = np.clip(snr, -10, 50)
            fwsnr_values.append(snr)

    return float(np.mean(fwsnr_values)) if fwsnr_values else 0.0


def waveform_rmse(ref, deg):
    """RMS Error između valnih oblika - direktna mjera distorzije."""
    return float(np.sqrt(np.mean((ref - deg) ** 2)))


def waveform_correlation(ref, deg):
    """Pearsonova korelacija valnih oblika - 1.0 = identični."""
    if np.std(ref) < 1e-10 or np.std(deg) < 1e-10:
        return 0.0
    return float(np.corrcoef(ref, deg)[0, 1])


def compute_all_metrics(ref_path, deg_path):
    """Izračunaj sve napredne metrike."""
    ref, deg, sr = load_and_align(ref_path, deg_path)

    return {
        "lsd_db": round(log_spectral_distance(ref, deg, sr), 3),
        "mcd_db": round(mel_cepstral_distortion(ref, deg, sr), 3),
        "spectral_convergence": round(spectral_convergence(ref, deg), 4),
        "fwsnr_db": round(frequency_weighted_snr(ref, deg, sr), 2),
        "waveform_rmse": round(waveform_rmse(ref, deg), 6),
        "waveform_correlation": round(waveform_correlation(ref, deg), 4),
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", required=True)
    parser.add_argument("--degraded", required=True)
    args = parser.parse_args()

    metrics = compute_all_metrics(args.reference, args.degraded)
    for k, v in metrics.items():
        print(f"  {k}: {v}")
