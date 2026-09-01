#!/usr/bin/env python3
"""
extract_and_compare.py - Mjerenje degradacije koju FreeSWITCH uvodi.

Metodologija:
  1. Iz PCAP-a izdvaja A-leg RTP tok (pjsua_A -> FS, codec_a)
  2. Iz PCAP-a izdvaja B-leg RTP tok (FS -> pjsua_B, codec_b)
  3. Dekodira oba u PCM i vraća praznine iz RTP vremenske linije
  4. Mijenja frekvenciju uzorkovanja oba signala na 16 kHz
  5. Poravnava signale unakrsnom korelacijom
  6. Računa PESQ, segmentalni SNR, STOI

Rezultat: izolovana mjera degradacije FreeSWITCH-a (passthrough ili transcode).

Korištenje:
    python extract_and_compare.py --pcap capture.pcap --codec-a PCMU --codec-b G722 --output result.json
"""

import argparse
import json
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import correlate, resample


CODEC_PT = {
    "PCMU": {"pts": [0], "ffmpeg": ["-f", "mulaw", "-ar", "8000", "-ac", "1"], "rate": 8000},
    "PCMA": {"pts": [8], "ffmpeg": ["-f", "alaw", "-ar", "8000", "-ac", "1"], "rate": 8000},
    "G722": {"pts": [9], "ffmpeg": ["-f", "g722"], "rate": 16000},
    "GSM":  {"pts": [3], "ffmpeg": ["-f", "gsm", "-ar", "8000", "-ac", "1"], "rate": 8000},
    "OPUS": {"pts": [102, 111, 96, 97], "ffmpeg": None, "rate": 48000},
}

CODEC_TIMING = {
    "PCMU": {"rtp_step": 160, "samples_per_frame": 160},
    "PCMA": {"rtp_step": 160, "samples_per_frame": 160},
    "G722": {"rtp_step": 160, "samples_per_frame": 320},
    "GSM": {"rtp_step": 160, "samples_per_frame": 160},
    "OPUS": {"rtp_step": 960, "samples_per_frame": 960},
}

TARGET_SR = 16000


def find_streams(pcap_path):
    """Pronađi RTP streamove u PCAP fajlu."""
    r = subprocess.run(
        ["tshark", "-r", pcap_path, "-q", "-z", "rtp,streams"],
        capture_output=True, text=True, timeout=30,
    )
    streams = []
    for line in r.stdout.split("\n"):
        line = line.strip()
        if not line or "Start" in line or "===" in line:
            continue
        parts = line.split()
        if len(parts) < 9:
            continue
        try:
            streams.append({
                "src_ip": parts[2],
                "src_port": int(parts[3]),
                "dst_ip": parts[4],
                "dst_port": int(parts[5]),
                "ssrc": parts[6],
                "payload": parts[7],
                "pkts": int(parts[8]),
            })
        except (ValueError, IndexError):
            continue
    return streams


def _payload_name_matches(stream_payload, codec):
    """Provjeri da li payload name odgovara codecu."""
    name = stream_payload.lower()
    mapping = {
        "PCMU": ["g711u", "pcmu", "0"],
        "PCMA": ["g711a", "pcma", "8"],
        "G722": ["g722", "9"],
        "GSM": ["gsm", "3"],
        "OPUS": ["opus", "102", "111", "96", "97"],
    }
    return any(m in name for m in mapping.get(codec, []))


def identify_legs(streams, codec_a, codec_b):
    """
    Identificiraj A-leg (pjsua_A → FS) i B-leg (FS → pjsua_B) streamove.

    Logika: FS je B2BUA sa dva nezavisna RTP porta (jedan za leg A, jedan za leg B).
    Svaki leg ima dva smjera. Mi trebamo:
      - A-leg inbound: pjsua_A šalje PREMA FS-u (ulaz u FS, codec_a)
      - B-leg outbound: FS šalje PREMA pjsua_B (izlaz iz FS, codec_b)

    Identificiramo ih tako što:
    1. Nađemo sve "dugačke" streamove (>100 pkts) sa odgovarajućim codec-om
    2. Grupišemo po FS portu (port >= 16384) - svaki FS port pripada jednom legu
    3. Za svaki FS port imamo par: (pjsua→FS) i (FS→pjsua)
    4. A-leg je onaj koji nosi signal (pjsua_A pušta audio), B-leg prima taj signal
    """

    long_streams = [s for s in streams if s["pkts"] > 100]


    fs_ports = {}
    for s in long_streams:
        if s["src_port"] >= 16384:
            fs_port = s["src_port"]
        elif s["dst_port"] >= 16384:
            fs_port = s["dst_port"]
        else:
            continue
        if fs_port not in fs_ports:
            fs_ports[fs_port] = []
        fs_ports[fs_port].append(s)





    a_leg = None
    b_leg = None

    for fs_port, port_streams in fs_ports.items():
        for s in port_streams:

            if s["src_port"] < 16384 and s["dst_port"] >= 16384:
                if _payload_name_matches(s["payload"], codec_a):
                    if a_leg is None or s["pkts"] > a_leg["pkts"]:
                        a_leg = s

            if s["src_port"] >= 16384 and s["dst_port"] < 16384:
                if _payload_name_matches(s["payload"], codec_b):
                    if b_leg is None or s["pkts"] > b_leg["pkts"]:
                        b_leg = s



    if a_leg and b_leg:
        a_fs_port = a_leg["dst_port"]
        b_fs_port = b_leg["src_port"]
        if a_fs_port == b_fs_port:



            other_b = None
            for fs_port, port_streams in fs_ports.items():
                if fs_port == a_fs_port:
                    continue
                for s in port_streams:
                    if s["src_port"] >= 16384 and s["dst_port"] < 16384:
                        if _payload_name_matches(s["payload"], codec_b):
                            if other_b is None or s["pkts"] > other_b["pkts"]:
                                other_b = s
            if other_b:
                b_leg = other_b

    return a_leg, b_leg


def extract_rtp_packets(pcap_path, ssrc):
    """Izdvoji redni broj, vremensku oznaku i sadržaj RTP paketa."""
    r = subprocess.run(
        ["tshark", "-r", pcap_path,
         "-Y", f"rtp.ssrc == {ssrc} && rtp.payload",
         "-T", "fields",
         "-e", "rtp.seq", "-e", "rtp.timestamp", "-e", "rtp.payload"],
        capture_output=True, text=True, timeout=60,
    )
    packets = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        try:
            packets.append({
                "sequence": int(parts[0]),
                "timestamp": int(parts[1]),
                "payload": bytes.fromhex(parts[2].replace(":", "")),
            })
        except (ValueError, IndexError):
            continue
    return packets


def restore_rtp_timeline(decoded, packets, codec):
    """Vrati vremenske praznine koje nestanu prostim spajanjem RTP sadržaja."""
    timing = CODEC_TIMING[codec]
    samples_per_frame = timing["samples_per_frame"]
    rtp_step = timing["rtp_step"]

    remainder = len(decoded) % samples_per_frame
    if remainder:
        raise ValueError(
            f"Dekodirani {codec} signal nema cijeli broj okvira od "
            f"{samples_per_frame} uzoraka"
        )

    decoded_frames = len(decoded) // samples_per_frame
    skipped_decoder_frames = len(packets) - decoded_frames
    if skipped_decoder_frames < 0:
        raise ValueError("Dekoder je proizveo više okvira nego što postoji RTP paketa")
    if codec != "OPUS" and skipped_decoder_frames != 0:
        raise ValueError(
            f"Neočekivano preskočenih okvira za {codec}: {skipped_decoder_frames}"
        )
    if codec == "OPUS" and skipped_decoder_frames not in range(0, 7):
        raise ValueError(
            f"Neočekivani Opus pre-skip: {skipped_decoder_frames} okvira"
        )

    active_packets = packets[skipped_decoder_frames:]
    pieces = []
    inserted_frames = 0
    sequence_gap_packets = 0
    non_integral_timestamp_jumps = 0

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
            sequence_gap_packets += max(0, sequence_delta - 1)

        ratio = timestamp_delta / rtp_step
        missing = max(0, int(round(ratio)) - 1)
        if abs(ratio - round(ratio)) > 1e-6:
            non_integral_timestamp_jumps += 1
        if missing > 50:
            raise ValueError(
                f"RTP vremenski skok predstavlja {missing} okvira; obrada je zaustavljena"
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
        "sequence_gap_packets": sequence_gap_packets,
        "non_integral_timestamp_jumps": non_integral_timestamp_jumps,
    }
    return restored, diagnostics


def build_ogg_opus(payloads, sample_rate=48000, channels=1, pre_skip=3840):
    """Kreiraj OGG Opus container iz RTP Opus payloada."""
    ogg_crc_table = []
    for i in range(256):
        r = i << 24
        for _ in range(8):
            if r & 0x80000000:
                r = ((r << 1) ^ 0x04c11db7) & 0xFFFFFFFF
            else:
                r = (r << 1) & 0xFFFFFFFF
        ogg_crc_table.append(r)

    def ogg_crc32(data):
        crc = 0
        for byte in data:
            crc = ((crc << 8) ^ ogg_crc_table[((crc >> 24) & 0xFF) ^ byte]) & 0xFFFFFFFF
        return crc

    def ogg_page(serial, page_seq, granule, bos=False, eos=False, segments_data=None):
        if segments_data is None:
            segments_data = [b""]
        header_type = 0
        if bos:
            header_type |= 0x02
        if eos:
            header_type |= 0x04
        seg_table = b""
        for seg in segments_data:
            size = len(seg)
            while size >= 255:
                seg_table += bytes([255])
                size -= 255
            seg_table += bytes([size])
        body = b"".join(segments_data)
        header = struct.pack(
            "<4sBBqIIIB", b"OggS", 0, header_type, granule,
            serial, page_seq, 0, len(seg_table),
        )
        header += seg_table
        page_data = header + body
        crc = ogg_crc32(page_data)
        page_data = page_data[:22] + struct.pack("<I", crc) + page_data[26:]
        return page_data

    serial = 0x12345678
    output = b""

    opus_head = struct.pack(
        "<8sBBHIhB", b"OpusHead", 1, channels, pre_skip, sample_rate, 0, 0,
    )
    output += ogg_page(serial, 0, 0, bos=True, segments_data=[opus_head])

    vendor = b"python"
    opus_tags = struct.pack("<8sI", b"OpusTags", len(vendor)) + vendor + struct.pack("<I", 0)
    output += ogg_page(serial, 1, 0, segments_data=[opus_tags])

    granule = pre_skip
    page_seq = 2
    for i, payload in enumerate(payloads):
        granule += 960
        is_last = (i == len(payloads) - 1)
        output += ogg_page(serial, page_seq, granule, eos=is_last, segments_data=[payload])
        page_seq += 1

    return output


def decode_to_wav(payloads, codec, output_wav):
    """Dekodiraj RTP payloade u WAV fajl."""
    config = CODEC_PT[codec]

    with tempfile.TemporaryDirectory() as tmpdir:
        if codec == "OPUS":
            raw_path = os.path.join(tmpdir, "input.ogg")
            with open(raw_path, "wb") as f:
                f.write(build_ogg_opus(payloads))
            cmd = ["ffmpeg", "-y", "-i", raw_path,
                   "-ar", str(config["rate"]), "-ac", "1", "-f", "wav", output_wav]
        else:
            raw_path = os.path.join(tmpdir, "input.raw")
            with open(raw_path, "wb") as f:
                for p in payloads:
                    f.write(p)
            cmd = ["ffmpeg", "-y"] + config["ffmpeg"] + ["-i", raw_path, "-f", "wav", output_wav]

        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            print(f"  ffmpeg error: {r.stderr[:200]}")
            return False

    return os.path.exists(output_wav) and os.path.getsize(output_wav) > 100


def load_wav_native(filepath):
    """Učitaj WAV kao monofoni float64 signal u izvornoj frekvenciji."""
    sr, data = wavfile.read(filepath)
    if data.dtype == np.int16:
        data = data.astype(np.float64) / 32768.0
    elif data.dtype == np.int32:
        data = data.astype(np.float64) / 2147483648.0
    elif np.issubdtype(data.dtype, np.floating):
        data = data.astype(np.float64)
    else:
        raise ValueError(f"Nepodržan WAV format uzorka: {data.dtype}")
    if len(data.shape) > 1:
        data = data[:, 0]
    return sr, data


def load_wav_16k(filepath):
    """Učitaj WAV i promijeni frekvenciju uzorkovanja na 16 kHz."""
    sr, data = load_wav_native(filepath)
    if sr != TARGET_SR:
        new_len = int(len(data) * TARGET_SR / sr)
        data = resample(data, new_len)
    return data


def align_signals(ref, deg):
    """
    Alignaj dva signala cross-correlacijom.

    FS typično uvodi 20-200ms delay. Ograničavamo pretragu na ±500ms
    da izbjegnemo lažne korelacione peakove.
    """

    max_delay_samples = int(TARGET_SR * 0.5)


    sig_len = min(len(ref), len(deg))
    start = int(sig_len * 0.1)
    end = int(sig_len * 0.9)
    ref_seg = ref[start:end]
    deg_seg = deg[start:end]



    corr = correlate(ref_seg, deg_seg, mode="full")
    center = len(ref_seg) - 1
    search_start = max(0, center - max_delay_samples)
    search_end = min(len(corr), center + max_delay_samples + 1)

    local_peak = search_start + np.argmax(corr[search_start:search_end])
    delay = local_peak - center

    if delay > 0:
        ref = ref[delay:]
    elif delay < 0:
        deg = deg[-delay:]

    min_len = min(len(ref), len(deg))

    trim = int(TARGET_SR * 0.5)
    if min_len > trim * 4:
        ref = ref[trim:min_len - trim]
        deg = deg[trim:min_len - trim]
    else:
        ref = ref[:min_len]
        deg = deg[:min_len]

    delay_ms = (delay / TARGET_SR) * 1000
    return ref, deg, delay_ms


def compute_pesq(ref, deg):
    """PESQ wideband (16kHz)."""
    try:
        from pesq import pesq
        ref_16 = (ref * 32768).astype(np.int16)
        deg_16 = (deg * 32768).astype(np.int16)
        if len(ref_16) < TARGET_SR or len(deg_16) < TARGET_SR:
            return None
        return float(pesq(TARGET_SR, ref_16, deg_16, "wb"))
    except Exception as e:
        print(f"  PESQ error: {e}")
        return None


def compute_segsnr(ref, deg, frame_len=256, hop=128):
    """Segmentalni SNR u dB."""
    n_frames = (min(len(ref), len(deg)) - frame_len) // hop
    snrs = []
    for i in range(n_frames):
        s = i * hop
        rf = ref[s:s + frame_len]
        nf = rf - deg[s:s + frame_len]
        sp = np.sum(rf ** 2)
        np_ = np.sum(nf ** 2)
        if sp > 1e-10 and np_ > 1e-10:
            snrs.append(np.clip(10 * np.log10(sp / np_), -10, 35))
    return float(np.mean(snrs)) if snrs else 0.0


def compute_stoi(ref, deg):
    """STOI intelligibility score."""
    try:
        from pystoi import stoi
        return float(stoi(ref, deg, TARGET_SR, extended=False))
    except Exception as e:
        print(f"  STOI error: {e}")
        return None


def analyze_pcap(pcap_path, codec_a, codec_b, output_dir=None):
    """Glavni entrypoint: extrahiraj i poredi A-leg vs B-leg."""
    pcap_path = str(pcap_path)

    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="voip_analysis_")
    else:
        os.makedirs(output_dir, exist_ok=True)

    a_wav = os.path.join(output_dir, "a_leg_16k.wav")
    b_wav = os.path.join(output_dir, "b_leg_16k.wav")


    streams = find_streams(pcap_path)
    if not streams:
        return {"error": "No RTP streams found in PCAP"}

    print(f"  Found {len(streams)} RTP streams")
    for s in streams:
        print(f"    {s['src_port']}→{s['dst_port']} ssrc={s['ssrc']} "
              f"payload={s['payload']} pkts={s['pkts']}")


    a_stream, b_stream = identify_legs(streams, codec_a, codec_b)

    if not a_stream:
        return {"error": f"A-leg stream ({codec_a}) not found"}
    if not b_stream:
        return {"error": f"B-leg stream ({codec_b}) not found"}

    print(f"  A-leg: port {a_stream['src_port']}→{a_stream['dst_port']}, "
          f"ssrc={a_stream['ssrc']}, {a_stream['pkts']} pkts")
    print(f"  B-leg: port {b_stream['src_port']}→{b_stream['dst_port']}, "
          f"ssrc={b_stream['ssrc']}, {b_stream['pkts']} pkts")


    a_packets = extract_rtp_packets(pcap_path, a_stream["ssrc"])
    b_packets = extract_rtp_packets(pcap_path, b_stream["ssrc"])
    a_payloads = [packet["payload"] for packet in a_packets]
    b_payloads = [packet["payload"] for packet in b_packets]

    if len(a_payloads) < 50:
        return {"error": f"A-leg too few packets ({len(a_payloads)})"}
    if len(b_payloads) < 50:
        return {"error": f"B-leg too few packets ({len(b_payloads)})"}

    print(f"  A-leg: {len(a_payloads)} payloads, {sum(len(p) for p in a_payloads)} bytes")
    print(f"  B-leg: {len(b_payloads)} payloads, {sum(len(p) for p in b_payloads)} bytes")


    a_raw_wav = os.path.join(output_dir, "a_leg_native.wav")
    b_raw_wav = os.path.join(output_dir, "b_leg_native.wav")

    if not decode_to_wav(a_payloads, codec_a, a_raw_wav):
        return {"error": "Failed to decode A-leg audio"}
    if not decode_to_wav(b_payloads, codec_b, b_raw_wav):
        return {"error": "Failed to decode B-leg audio"}


    a_native_sr, a_native_pcm = load_wav_native(a_raw_wav)
    b_native_sr, b_native_pcm = load_wav_native(b_raw_wav)

    a_restored, a_timeline = restore_rtp_timeline(a_native_pcm, a_packets, codec_a)
    b_restored, b_timeline = restore_rtp_timeline(b_native_pcm, b_packets, codec_b)

    if a_native_sr != CODEC_PT[codec_a]["rate"]:
        return {"error": f"Neočekivana A-leg PCM frekvencija: {a_native_sr} Hz"}
    if b_native_sr != CODEC_PT[codec_b]["rate"]:
        return {"error": f"Neočekivana B-leg PCM frekvencija: {b_native_sr} Hz"}

    a_pcm = a_restored
    b_pcm = b_restored
    if a_native_sr != TARGET_SR:
        a_pcm = resample(a_pcm, int(len(a_pcm) * TARGET_SR / a_native_sr))
    if b_native_sr != TARGET_SR:
        b_pcm = resample(b_pcm, int(len(b_pcm) * TARGET_SR / b_native_sr))

    print(f"  A-leg PCM: {len(a_pcm)} samples ({len(a_pcm)/TARGET_SR:.2f}s)")
    print(f"  B-leg PCM: {len(b_pcm)} samples ({len(b_pcm)/TARGET_SR:.2f}s)")


    a_aligned, b_aligned, delay_ms = align_signals(a_pcm, b_pcm)
    print(f"  Aligned: {len(a_aligned)} samples, delay={delay_ms:.1f}ms")


    pesq_score = compute_pesq(a_aligned, b_aligned)
    seg_snr = compute_segsnr(a_aligned, b_aligned)
    stoi_score = compute_stoi(a_aligned, b_aligned)

    result = {
        "pesq_mos": pesq_score,
        "segmental_snr_db": round(seg_snr, 2),
        "stoi": stoi_score,
        "delay_ms": round(delay_ms, 2),
        "a_leg_duration_s": round(len(a_pcm) / TARGET_SR, 2),
        "b_leg_duration_s": round(len(b_pcm) / TARGET_SR, 2),
        "aligned_duration_s": round(len(a_aligned) / TARGET_SR, 2),
        "a_leg_packets": len(a_payloads),
        "b_leg_packets": len(b_payloads),
        "a_leg_timeline": a_timeline,
        "b_leg_timeline": b_timeline,
    }

    print(f"  Results: PESQ={pesq_score}, SegSNR={seg_snr:.2f}dB, STOI={stoi_score}")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A-leg vs B-leg audio quality measurement")
    parser.add_argument("--pcap", required=True, help="PCAP file path")
    parser.add_argument("--codec-a", required=True, help="A-leg codec (PCMU, PCMA, G722, GSM, OPUS)")
    parser.add_argument("--codec-b", required=True, help="B-leg codec")
    parser.add_argument("--output-dir", default=None, help="Directory for intermediate WAV files")
    parser.add_argument("--result-json", default=None, help="JSON file to update with results")
    args = parser.parse_args()

    result = analyze_pcap(args.pcap, args.codec_a, args.codec_b, args.output_dir)

    if args.result_json and Path(args.result_json).exists():
        with open(args.result_json, "r") as f:
            data = json.load(f)
        data["quality_metrics"] = result
        with open(args.result_json, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"  Updated: {args.result_json}")
    else:
        print(json.dumps(result, indent=2))
