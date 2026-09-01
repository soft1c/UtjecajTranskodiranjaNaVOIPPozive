#!/usr/bin/env python3
"""
run_single_test.py - Pokretanje jednog VoIP transcoding testa.

Flow:
  1. Verify FreeSWITCH spreman (ESL)
  2. Start PCAP capture (dumpcap/tshark na lo)
  3. Start CPU monitoring (docker stats)
  4. Registruj Endpoint B (pjsua, auto-answer, codec_b)
  5. Registruj Endpoint A (pjsua, auto-answer, play ref audio, codec_a)
  6. ESL originate: FS zove A sa codec_a, bridge na B sa codec_b
  7. Čekaj ~15s playback
  8. Hangup, stop sve
  9. Pokreni extract_and_compare analizu
  10. Snimi rezultat JSON

Korištenje:
    python run_single_test.py --codec-a PCMU --codec-b G722 --test-id T006 --iteration 1
"""

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
RECORDINGS_DIR = PROJECT_DIR / "audio" / "recorded"
REFERENCE_DIR = PROJECT_DIR / "audio" / "reference"
RESULTS_DIR = PROJECT_DIR / "results" / "raw"
ANALYSIS_DIR = PROJECT_DIR / "scripts" / "analysis"

sys.path.insert(0, str(ANALYSIS_DIR))

ESL_HOST = "127.0.0.1"
ESL_PORT = 8021
ESL_PASSWORD = "ClueCon"

PJSUA_BIN = "pjsua"
SIP_DOMAIN = "127.0.0.1"
SIP_PASSWORD = "testbed1234"

TEST_DURATION = 10
REG_TIMEOUT = 8


class ESLClient:
    def __init__(self):
        self.sock = None

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        self.sock.connect((ESL_HOST, ESL_PORT))
        self._recv()
        self._send(f"auth {ESL_PASSWORD}")
        resp = self._recv()
        if "+OK" not in resp:
            raise ConnectionError(f"ESL auth failed: {resp}")
        return self

    def api(self, cmd):
        self._send(f"api {cmd}")
        return self._recv()

    def _send(self, data):
        self.sock.sendall(f"{data}\n\n".encode())

    def _recv(self):
        data = b""
        while True:
            try:
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                data += chunk
                if b"\n\n" in data:
                    break
            except socket.timeout:
                break
        return data.decode("utf-8", errors="replace")

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass


def wait_for_fs(timeout=20):
    """Čekaj da FS bude spreman."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            esl = ESLClient()
            esl.connect()
            r = esl.api("sofia status")
            esl.close()
            if "RUNNING" in r:
                return True
        except (ConnectionRefusedError, ConnectionError, socket.timeout, OSError):
            pass
        time.sleep(1)
    raise TimeoutError("FreeSWITCH not ready")


def get_reference_file(codec):
    """Vrati referentni audio za dati codec."""
    rate_map = {"PCMU": 8000, "PCMA": 8000, "G722": 16000, "GSM": 8000, "OPUS": 48000}
    rate = rate_map.get(codec, 8000)
    path = REFERENCE_DIR / f"reference_{rate // 1000}k.wav"
    if path.exists():
        return str(path)
    return str(REFERENCE_DIR / "reference_8k.wav")


def start_pjsua(user, port, codec, recording_path, play_file=None):
    """Pokreni pjsua endpoint."""
    cmd = [
        PJSUA_BIN,
        f"--id=sip:{user}@{SIP_DOMAIN}",
        f"--registrar=sip:{SIP_DOMAIN}:5060",
        "--realm=*",
        f"--username={user}",
        f"--password={SIP_PASSWORD}",
        f"--local-port={port}",
        "--dis-codec=*",
        f"--add-codec={codec}",
        "--auto-answer=200",
        f"--rec-file={recording_path}",
        "--auto-rec",
        "--null-audio",
        "--no-vad",
        "--log-level=2",
    ]
    if play_file:
        cmd += [f"--play-file={play_file}", "--auto-play", "--auto-loop"]

    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, preexec_fn=os.setsid,
    )
    return proc


def start_capture(output_path, duration, rtp_port_filter=None):
    """Start packet capture na loopback. Opcionalno filtrira po portovima."""
    if rtp_port_filter:
        cap_filter = rtp_port_filter
    else:
        cap_filter = "udp portrange 4000-65535"
    for tool in ["dumpcap", "tshark"]:
        try:
            subprocess.run(["which", tool], capture_output=True, check=True)
            if tool == "dumpcap":
                cmd = ["dumpcap", "-i", "lo", "-f", cap_filter, "-w", str(output_path),
                       "-a", f"duration:{duration}"]
            else:
                cmd = ["tshark", "-i", "lo", "-f", cap_filter, "-w", str(output_path),
                       "-a", f"duration:{duration}"]
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    preexec_fn=os.setsid)
            return proc
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    raise RuntimeError("Neither dumpcap nor tshark found")


def start_cpu_monitor(output_path):
    """CPU monitoring via docker stats."""
    script = (
        f'while true; do '
        f'docker stats voip-freeswitch --no-stream '
        f'--format "{{{{.CPUPerc}}}}" >> {output_path}; '
        f'sleep 1; done'
    )
    proc = subprocess.Popen(
        ["bash", "-c", script],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        preexec_fn=os.setsid,
    )
    return proc


def wait_registration(esl, user, timeout=REG_TIMEOUT):
    """Čekaj SIP registraciju."""
    start = time.time()
    while time.time() - start < timeout:
        r = esl.api("sofia status profile internal reg")
        if user in r:
            return True
        time.sleep(0.5)
    return False


def kill_proc(proc):
    """Kill process group."""
    if proc and proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


def run_test(codec_a, codec_b, test_id, iteration, a_user="1001", b_user="2001",
             a_port=5062, b_port=5072):
    """Izvrši jedan test i vrati rezultat dict."""
    timestamp = datetime.now().isoformat()
    prefix = f"{test_id}_iter{iteration}"

    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    a_rec = str(RECORDINGS_DIR / f"{prefix}_a_recv.wav")
    b_rec = str(RECORDINGS_DIR / f"{prefix}_b_recv.wav")
    pcap_file = str(RESULTS_DIR / f"{prefix}.pcap")
    cpu_log = str(RESULTS_DIR / f"{prefix}_cpu.log")
    result_file = str(RESULTS_DIR / f"{prefix}.json")
    analysis_dir = str(RECORDINGS_DIR / f"{prefix}_analysis")

    reference_file = get_reference_file(codec_a)

    print(f"\n{'='*60}")
    print(f"  {test_id} iter{iteration} | {codec_a} → {codec_b}")
    print(f"{'='*60}")

    proc_a = proc_b = cap_proc = cpu_proc = None
    esl = None
    success = False

    try:

        subprocess.run(
            ["pkill", "-f", f"pjsua.*--local-port={a_port}"],
            capture_output=True,
        )
        subprocess.run(
            ["pkill", "-f", f"pjsua.*--local-port={b_port}"],
            capture_output=True,
        )
        time.sleep(1)


        wait_for_fs()
        esl = ESLClient()
        esl.connect()
        esl.api("hupall")
        esl.api(f"sofia profile internal flush_inbound_reg {a_user}@{SIP_DOMAIN} reboot")
        esl.api(f"sofia profile internal flush_inbound_reg {b_user}@{SIP_DOMAIN} reboot")
        time.sleep(1)


        cap_proc = start_capture(pcap_file, TEST_DURATION + 10)
        time.sleep(1)


        cpu_proc = start_cpu_monitor(cpu_log)


        proc_b = start_pjsua(b_user, b_port, codec_b, b_rec)
        time.sleep(3)
        if not wait_registration(esl, b_user):
            print(f"  WARN: {b_user} not registered")


        proc_a = start_pjsua(a_user, a_port, codec_a, a_rec, play_file=reference_file)
        time.sleep(3)
        if not wait_registration(esl, a_user):
            print(f"  WARN: {a_user} not registered")


        orig_cmd = (
            f"originate "
            f"{{absolute_codec_string={codec_a}}}user/{a_user}@{SIP_DOMAIN} "
            f"&bridge({{absolute_codec_string={codec_b}}}user/{b_user}@{SIP_DOMAIN})"
        )
        print(f"  Originate: {codec_a}→{codec_b}")
        orig_result = esl.api(orig_cmd)
        if "ERR" in orig_result or "INVALID" in orig_result:
            print(f"  Originate failed: {orig_result[:200]}")
            raise RuntimeError("Originate failed")


        time.sleep(2)
        channels = esl.api("show channels count")
        print(f"  Channels: {channels.strip()}")
        print(f"  Playing audio for {TEST_DURATION}s...")
        time.sleep(TEST_DURATION)


        esl.api("hupall")
        time.sleep(2)
        success = True

    except Exception as e:
        print(f"  ERROR: {e}")
    finally:
        if esl:
            esl.close()
        for p in [proc_a, proc_b, cpu_proc, cap_proc]:
            kill_proc(p)
        time.sleep(1)


    quality_metrics = None
    if success and os.path.exists(pcap_file) and os.path.getsize(pcap_file) > 500:
        try:
            from extract_and_compare import analyze_pcap
            quality_metrics = analyze_pcap(pcap_file, codec_a, codec_b, analysis_dir)
            if "error" in quality_metrics:
                print(f"  Analysis error: {quality_metrics['error']}")
                success = False
        except Exception as e:
            print(f"  Analysis exception: {e}")
            import traceback
            traceback.print_exc()
            success = False


    cpu_metrics = None
    if os.path.exists(cpu_log):
        try:
            values = []
            with open(cpu_log) as f:
                for line in f:
                    line = line.strip().replace("%", "")
                    try:
                        values.append(float(line))
                    except ValueError:
                        continue
            if values:
                cpu_metrics = {
                    "avg_percent": round(float(np.mean(values)), 2),
                    "max_percent": round(float(max(values)), 2),
                    "min_percent": round(float(min(values)), 2),
                    "std_percent": round(float(np.std(values)), 2),
                    "n_samples": len(values),
                }
        except Exception:
            pass


    result = {
        "test_id": test_id,
        "iteration": iteration,
        "codec_a": codec_a,
        "codec_b": codec_b,
        "scenario_type": "passthrough" if codec_a == codec_b else "transcode",
        "timestamp": timestamp,
        "success": success,
        "quality_metrics": quality_metrics,
        "cpu_metrics": cpu_metrics,
        "files": {
            "pcap": pcap_file,
            "cpu_log": cpu_log,
            "reference": reference_file,
        },
    }

    with open(result_file, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    status = "OK" if success else "FAIL"
    pesq = quality_metrics.get("pesq_mos", "N/A") if quality_metrics else "N/A"
    print(f"  [{status}] PESQ={pesq}")
    return result



import numpy as np


def main():
    parser = argparse.ArgumentParser(description="Run single VoIP transcoding test")
    parser.add_argument("--codec-a", required=True)
    parser.add_argument("--codec-b", required=True)
    parser.add_argument("--test-id", default="T000")
    parser.add_argument("--iteration", type=int, default=1)
    parser.add_argument("--a-user", default="1001")
    parser.add_argument("--b-user", default="2001")
    parser.add_argument("--a-port", type=int, default=5062)
    parser.add_argument("--b-port", type=int, default=5072)
    args = parser.parse_args()

    result = run_test(
        codec_a=args.codec_a, codec_b=args.codec_b,
        test_id=args.test_id, iteration=args.iteration,
        a_user=args.a_user, b_user=args.b_user,
        a_port=args.a_port, b_port=args.b_port,
    )
    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
