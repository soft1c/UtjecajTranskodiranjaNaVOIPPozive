#!/usr/bin/env python3
"""
parse_rtp_stats.py - Parsiranje tshark RTP stream statistika iz PCAP fajla.

Koristi tshark za analizu RTP streamova iz snimljenog mrežnog prometa.
Ekstrahuje: broj paketa, izgubljeni paketi, jitter, max delta.

Korištenje:
    python parse_rtp_stats.py --input capture.pcap --output result.json
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def analyze_rtp_streams(pcap_path):
    """
    Pokreni tshark analizu RTP streamova na PCAP fajlu.
    Vraća listu RTP stream statistika.
    """

    try:
        subprocess.run(["tshark", "--version"], capture_output=True, check=True)
    except FileNotFoundError:
        print("  GREŠKA: tshark nije instaliran. Instalirajte: sudo apt install tshark")
        return []


    cmd = ["tshark", "-r", str(pcap_path), "-q", "-z", "rtp,streams"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    if result.returncode != 0:
        print(f"  GREŠKA tshark: {result.stderr}")
        return []

    return parse_tshark_output(result.stdout)


def parse_tshark_output(output):
    """
    Parsiraj tshark 'rtp,streams' izlaz.

    Format izlaza:
    ========================= RTP Streams ========================
        Start time      End time    Src addr  Port    Dest addr  Port  SSRC  Payload  Pkts  Lost  Max Delta(ms)  Max Jitter(ms)  Mean Jitter(ms)  Problems?
        ...
    """
    streams = []
    in_data = False

    for line in output.split("\n"):
        line = line.strip()


        if "Start time" in line and "Src addr" in line:
            in_data = True
            continue

        if not in_data or not line or line.startswith("="):
            continue



        parts = line.split()
        if len(parts) < 12:
            continue

        try:
            stream = {
                "src_addr": parts[2] if len(parts) > 2 else None,
                "src_port": int(parts[3]) if len(parts) > 3 else None,
                "dst_addr": parts[4] if len(parts) > 4 else None,
                "dst_port": int(parts[5]) if len(parts) > 5 else None,
                "ssrc": parts[6] if len(parts) > 6 else None,
                "payload_type": parts[7] if len(parts) > 7 else None,
                "packets": int(parts[8]) if len(parts) > 8 else 0,
                "lost": int(parts[9]) if len(parts) > 9 else 0,
                "max_delta_ms": float(parts[10]) if len(parts) > 10 else 0.0,
                "max_jitter_ms": float(parts[11]) if len(parts) > 11 else 0.0,
                "mean_jitter_ms": float(parts[12]) if len(parts) > 12 else 0.0,
            }


            total = stream["packets"] + stream["lost"]
            stream["loss_percent"] = round(
                (stream["lost"] / total * 100) if total > 0 else 0.0, 2
            )

            streams.append(stream)
        except (ValueError, IndexError):
            continue

    return streams


def aggregate_streams(streams):
    """Agregiraj statistike svih RTP streamova."""
    if not streams:
        return {
            "num_streams": 0,
            "total_packets": 0,
            "total_lost": 0,
            "loss_percent": 0.0,
            "mean_jitter_ms": 0.0,
            "max_jitter_ms": 0.0,
            "max_delta_ms": 0.0,
            "streams": [],
        }

    total_packets = sum(s["packets"] for s in streams)
    total_lost = sum(s["lost"] for s in streams)

    return {
        "num_streams": len(streams),
        "total_packets": total_packets,
        "total_lost": total_lost,
        "loss_percent": round(
            (total_lost / (total_packets + total_lost) * 100)
            if (total_packets + total_lost) > 0
            else 0.0,
            2,
        ),
        "mean_jitter_ms": round(
            sum(s["mean_jitter_ms"] for s in streams) / len(streams), 3
        ),
        "max_jitter_ms": round(max(s["max_jitter_ms"] for s in streams), 3),
        "max_delta_ms": round(max(s["max_delta_ms"] for s in streams), 3),
        "streams": streams,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Parsiranje RTP statistika iz PCAP fajla"
    )
    parser.add_argument("--input", required=True, help="Putanja do PCAP fajla")
    parser.add_argument("--output", help="Putanja do JSON fajla za ažuriranje")
    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"  UPOZORENJE: PCAP fajl ne postoji: {args.input}")
        return


    streams = analyze_rtp_streams(args.input)
    stats = aggregate_streams(streams)

    print(f"  RTP statistike ({stats['num_streams']} streamova):")
    print(f"    Ukupno paketa:  {stats['total_packets']}")
    print(f"    Izgubljeno:     {stats['total_lost']} ({stats['loss_percent']}%)")
    print(f"    Mean jitter:    {stats['mean_jitter_ms']} ms")
    print(f"    Max jitter:     {stats['max_jitter_ms']} ms")
    print(f"    Max delta:      {stats['max_delta_ms']} ms")

    for i, s in enumerate(streams):
        print(f"    Stream {i+1}: {s['src_addr']}:{s['src_port']} -> "
              f"{s['dst_addr']}:{s['dst_port']} | "
              f"Payload: {s['payload_type']} | "
              f"Pkts: {s['packets']} | Lost: {s['lost']} | "
              f"Jitter: {s['mean_jitter_ms']}ms")


    if args.output and Path(args.output).exists():
        with open(args.output, "r") as f:
            data = json.load(f)
        data["rtp_stats"] = stats
        with open(args.output, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"  RTP statistike ažurirane u: {args.output}")
    else:
        print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
