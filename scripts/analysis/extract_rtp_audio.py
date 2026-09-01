#!/usr/bin/env python3
"""
extract_rtp_audio.py - Ekstrakcija audio signala iz PCAP RTP streamova.

Izvlači RTP payload u nativnom codec formatu i dekodira u PCM WAV.
Ovo daje tačan signal koji endpointi primaju - bez gubitaka od
intermedijarnog procesiranja.

Podržani kodeci: PCMU (PT=0), PCMA (PT=8), G.722 (PT=9), GSM (PT=3), Opus (PT=102)

Korištenje:
    python extract_rtp_audio.py --pcap capture.pcap --codec-a PCMU --codec-b G722
"""

import argparse
import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path



CODEC_CONFIG = {
    "PCMU": {
        "payload_types": [0],
        "tshark_name": "g711U",
        "ffmpeg_decode": ["-f", "mulaw", "-ar", "8000", "-ac", "1"],
        "sample_rate": 8000,
    },
    "PCMA": {
        "payload_types": [8],
        "tshark_name": "g711A",
        "ffmpeg_decode": ["-f", "alaw", "-ar", "8000", "-ac", "1"],
        "sample_rate": 8000,
    },
    "G722": {
        "payload_types": [9],
        "tshark_name": "g722",
        "ffmpeg_decode": ["-f", "g722"],
        "sample_rate": 16000,
    },
    "GSM": {
        "payload_types": [3],
        "tshark_name": "gsm",
        "ffmpeg_decode": ["-f", "gsm", "-ar", "8000", "-ac", "1"],
        "sample_rate": 8000,
    },
    "OPUS": {
        "payload_types": [102, 111, 96, 97],
        "tshark_name": "opus",
        "ffmpeg_decode": None,
        "sample_rate": 48000,
    },
}


def find_rtp_streams(pcap_path):
    """Pronađi sve RTP streamove u PCAP fajlu."""
    result = subprocess.run(
        ["tshark", "-r", pcap_path, "-q", "-z", "rtp,streams"],
        capture_output=True, text=True, timeout=30,
    )
    streams = []
    for line in result.stdout.split("\n"):

        line = line.strip()
        if not line or "Start time" in line or "===" in line:
            continue
        parts = line.split()
        if len(parts) < 16:
            continue
        try:
            streams.append({
                "src_ip": parts[2],
                "src_port": int(parts[3]),
                "dst_ip": parts[4],
                "dst_port": int(parts[5]),
                "ssrc": parts[6],
                "payload_name": parts[7],
                "packets": int(parts[8]),
                "lost": int(parts[9]),
            })
        except (ValueError, IndexError):
            continue
    return streams


def _stream_decoded_rms(pcap_path, ssrc, codec):
    """Dekodiraj kompletni stream i vrati RMS."""
    import numpy as np
    import tempfile

    payloads = extract_rtp_payloads(pcap_path, ssrc)
    if len(payloads) < 50:
        return 0.0

    config = CODEC_CONFIG.get(codec)
    if not config:
        return 0.0

    with tempfile.TemporaryDirectory() as tmpdir:
        wav_path = os.path.join(tmpdir, "test.wav")

        if codec == "OPUS":
            ogg_path = os.path.join(tmpdir, "test.ogg")
            ogg_data = build_ogg_opus(payloads)
            with open(ogg_path, "wb") as f:
                f.write(ogg_data)
            cmd = ["ffmpeg", "-y", "-i", ogg_path, "-f", "wav", wav_path]
        elif codec == "G722":
            raw_path = os.path.join(tmpdir, "test.raw")
            with open(raw_path, "wb") as f:
                for p in payloads:
                    f.write(p)
            cmd = ["ffmpeg", "-y", "-f", "g722", "-i", raw_path, "-f", "wav", wav_path]
        else:
            raw_path = os.path.join(tmpdir, "test.raw")
            with open(raw_path, "wb") as f:
                for p in payloads:
                    f.write(p)
            cmd = ["ffmpeg", "-y"] + config["ffmpeg_decode"] + ["-i", raw_path, "-f", "wav", wav_path]

        try:
            subprocess.run(cmd, capture_output=True, timeout=15)
            if os.path.exists(wav_path):
                from scipy.io import wavfile
                sr, data = wavfile.read(wav_path)
                if data.dtype == np.int16:
                    data = data.astype(np.float64) / 32768.0
                return float(np.sqrt(np.mean(data ** 2)))
        except Exception:
            pass

    return 0.0


def _stream_signal_energy(pcap_path, ssrc):
    """Izračunaj energiju signala koristeći varijaciju raw bajtova (brzo)."""
    import numpy as np

    r = subprocess.run(
        ["tshark", "-r", pcap_path,
         "-Y", f"rtp.ssrc == {ssrc} && rtp.payload",
         "-T", "fields", "-e", "rtp.payload"],
        capture_output=True, text=True, timeout=30,
    )
    payloads = r.stdout.strip().split("\n")
    if len(payloads) < 50:
        return 0.0

    mid_start = len(payloads) // 2 - 5
    mid_payloads = payloads[mid_start:mid_start + 10]

    total_variance = 0
    count = 0
    for hex_str in mid_payloads:
        hex_str = hex_str.strip().replace(":", "")
        if not hex_str:
            continue
        try:
            raw_bytes = bytes.fromhex(hex_str)
            total_variance += float(np.var(list(raw_bytes)))
            count += 1
        except ValueError:
            continue

    return total_variance / count if count > 0 else 0.0


def identify_b_leg_stream(streams, codec_b, pcap_path=None):
    """Identificiraj B-leg RTP stream (nosi audio signal u codec_b formatu)."""
    config = CODEC_CONFIG[codec_b]
    tshark_name = config["tshark_name"]

    candidates = []
    for s in streams:
        if s["payload_name"].lower() != tshark_name.lower():
            continue
        if s["packets"] > 100:
            candidates.append(s)

    if not candidates:
        return None

    if pcap_path:
        is_compressed = codec_b in ("OPUS",)

        if is_compressed:


            for c in candidates:
                c["_rms"] = _stream_decoded_rms(pcap_path, c["ssrc"], codec_b)
        else:

            for c in candidates:
                c["_rms"] = _stream_signal_energy(pcap_path, c["ssrc"])


        candidates.sort(key=lambda x: x.get("_rms", 0), reverse=True)
        if candidates[0].get("_rms", 0) > 0.01:
            return candidates[0]


    fs_streams = [c for c in candidates if c["src_port"] > 10000 and c["dst_port"] < 5200]
    if fs_streams:
        return max(fs_streams, key=lambda s: s["packets"])

    return max(candidates, key=lambda s: s["packets"])


def identify_a_leg_stream(streams, codec_a, pcap_path=None):
    """Identificiraj A-leg RTP stream (pjsua A šalje audio prema FS)."""
    config = CODEC_CONFIG[codec_a]
    tshark_name = config["tshark_name"]

    candidates = []
    for s in streams:
        if s["payload_name"].lower() != tshark_name.lower():
            continue

        if 4000 <= s["src_port"] <= 5200 and s["dst_port"] > 10000 and s["packets"] > 100:
            candidates.append(s)

    if not candidates:
        return None


    if pcap_path and len(candidates) > 1:
        is_compressed = codec_a in ("OPUS",)
        if is_compressed:
            for c in candidates:
                c["_rms"] = _stream_decoded_rms(pcap_path, c["ssrc"], codec_a)
        else:
            for c in candidates:
                c["_rms"] = _stream_signal_energy(pcap_path, c["ssrc"])
        candidates.sort(key=lambda x: x.get("_rms", 0), reverse=True)
        if candidates[0].get("_rms", 0) > 0.01:
            return candidates[0]

    return max(candidates, key=lambda s: s["packets"])


def extract_rtp_payloads(pcap_path, ssrc):
    """Izvuci RTP payload bajtove za dati SSRC."""
    result = subprocess.run(
        ["tshark", "-r", pcap_path,
         "-Y", f"rtp.ssrc == {ssrc} && rtp.payload",
         "-T", "fields", "-e", "rtp.payload",
         "-E", "separator=,"],
        capture_output=True, text=True, timeout=60,
    )
    payloads = []
    for line in result.stdout.strip().split("\n"):
        hex_str = line.strip().replace(":", "")
        if hex_str:
            try:
                payloads.append(bytes.fromhex(hex_str))
            except ValueError:
                continue
    return payloads


def build_ogg_opus(payloads, sample_rate=48000, channels=1, pre_skip=3840):
    """Rekonstruiraj OGG Opus container iz RTP payload-a."""

    def ogg_page(serial, page_seq, granule, bos=False, eos=False, segments_data=None):
        """Kreiraj jednu OGG stranicu."""
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

        n_segments = len(seg_table)
        body = b"".join(segments_data)


        header = struct.pack(
            "<4sBBqIIIB",
            b"OggS",
            0,
            header_type,
            granule,
            serial,
            page_seq,
            0,
            n_segments,
        )
        header += seg_table


        page_data = header + body
        crc = ogg_crc32(page_data)

        page_data = page_data[:22] + struct.pack("<I", crc) + page_data[26:]

        return page_data


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

    serial = 0x12345678
    output = b""


    opus_head = struct.pack(
        "<8sBBHIhB",
        b"OpusHead",
        1,
        channels,
        pre_skip,
        sample_rate,
        0,
        0,
    )
    output += ogg_page(serial, 0, 0, bos=True, segments_data=[opus_head])


    vendor = b"python"
    opus_tags = struct.pack("<8sI", b"OpusTags", len(vendor)) + vendor + struct.pack("<I", 0)
    output += ogg_page(serial, 1, 0, segments_data=[opus_tags])


    granule = pre_skip
    page_seq = 2
    samples_per_frame = 960

    for i, payload in enumerate(payloads):
        granule += samples_per_frame
        is_last = (i == len(payloads) - 1)
        output += ogg_page(serial, page_seq, granule, eos=is_last, segments_data=[payload])
        page_seq += 1

    return output


def decode_raw_to_wav(raw_path, output_wav, codec):
    """Dekodiraj raw codec bajtove u WAV pomoću ffmpeg."""
    config = CODEC_CONFIG[codec]

    if codec == "OPUS":

        cmd = ["ffmpeg", "-y", "-i", raw_path, "-ar", "48000", "-ac", "1", "-f", "wav", output_wav]
    else:
        cmd = ["ffmpeg", "-y"] + config["ffmpeg_decode"] + ["-i", raw_path, "-f", "wav", output_wav]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    if result.returncode != 0:
        return False

    return os.path.exists(output_wav) and os.path.getsize(output_wav) > 100


def extract_stream_audio(pcap_path, stream, codec, output_wav):
    """Kompletna ekstrakcija: PCAP → RTP payloads → raw codec → WAV."""
    ssrc = stream["ssrc"]
    payloads = extract_rtp_payloads(pcap_path, ssrc)

    if not payloads:
        print(f"    Nema payload-a za SSRC={ssrc}")
        return False

    print(f"    {len(payloads)} paketa, {sum(len(p) for p in payloads)} bytes")

    if codec == "OPUS":

        raw_path = output_wav + ".ogg"
        ogg_data = build_ogg_opus(payloads)
        with open(raw_path, "wb") as f:
            f.write(ogg_data)
    else:

        raw_path = output_wav + ".raw"
        with open(raw_path, "wb") as f:
            for p in payloads:
                f.write(p)


    success = decode_raw_to_wav(raw_path, output_wav, codec)


    if os.path.exists(raw_path):
        os.remove(raw_path)

    return success


def extract_rtp_audio(pcap_path, codec_a, codec_b, output_dir):
    """Glavna funkcija: ekstrahiraj A-leg i B-leg audio iz PCAP fajla."""
    pcap_path = str(pcap_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    a_wav = str(output_dir / "a_leg.wav")
    b_wav = str(output_dir / "b_leg.wav")


    streams = find_rtp_streams(pcap_path)
    if not streams:
        print("  Nema RTP streamova u PCAP fajlu")
        return None, None

    print(f"  Pronađeno {len(streams)} RTP streamova")


    b_stream = identify_b_leg_stream(streams, codec_b, pcap_path)
    if b_stream:
        print(f"  B-leg: SSRC={b_stream['ssrc']}, {b_stream['src_port']}→{b_stream['dst_port']}, "
              f"{b_stream['packets']} pkts, {b_stream['payload_name']}")
        if extract_stream_audio(pcap_path, b_stream, codec_b, b_wav):
            print(f"  B-leg WAV: {b_wav}")
        else:
            print(f"  B-leg ekstrakcija neuspješna")
            b_wav = None
    else:
        print(f"  B-leg stream ({codec_b}) nije pronađen")
        b_wav = None


    a_stream = identify_a_leg_stream(streams, codec_a, pcap_path)
    if a_stream:
        print(f"  A-leg: SSRC={a_stream['ssrc']}, {a_stream['src_port']}→{a_stream['dst_port']}, "
              f"{a_stream['packets']} pkts, {a_stream['payload_name']}")
        if extract_stream_audio(pcap_path, a_stream, codec_a, a_wav):
            print(f"  A-leg WAV: {a_wav}")
        else:
            print(f"  A-leg ekstrakcija neuspješna")
            a_wav = None
    else:
        print(f"  A-leg stream ({codec_a}) nije pronađen")
        a_wav = None

    return a_wav, b_wav


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ekstrakcija audio iz PCAP RTP streamova")
    parser.add_argument("--pcap", required=True, help="PCAP fajl")
    parser.add_argument("--codec-a", required=True, help="Codec A endpointa")
    parser.add_argument("--codec-b", required=True, help="Codec B endpointa")
    parser.add_argument("--output-dir", default="/tmp/rtp_extract", help="Output direktorij")
    args = parser.parse_args()

    a_wav, b_wav = extract_rtp_audio(args.pcap, args.codec_a, args.codec_b, args.output_dir)

    if b_wav:

        r = subprocess.run(["sox", b_wav, "-n", "stat"], capture_output=True, text=True)
        print(f"\n  B-leg stat:\n{r.stderr}")
