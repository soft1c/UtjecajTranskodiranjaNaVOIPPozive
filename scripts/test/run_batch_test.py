#!/usr/bin/env python3
"""
run_batch_test.py - Brzo pokretanje N iteracija za jedan codec par.

Pokrene A i B jednom, pa izvrši N poziva sekvencijalno bez restartanja klijenata.
Eliminira overhead registracije (~5s) za svaku iteraciju.

Ukupno: ~2s setup + N * (16s poziv + 2s pauza) umjesto N * (10s setup + 16s poziv)
Za N=5: ~90s umjesto ~130s, ali pouzdanije.

Korištenje:
    python run_batch_test.py --codec-a PCMU --codec-b PCMA --test-id T006 --iterations 5
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

PJSUA_BIN = "pjsua"
SIP_DOMAIN = "127.0.0.1"
SIP_PASSWORD = "testbed1234"
ESL_HOST = "127.0.0.1"
ESL_PORT = 8021
ESL_PASSWORD = "ClueCon"
TEST_DURATION = 22


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
            raise ConnectionError("ESL auth failed")
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


def kill_pg(proc):
    if proc and proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


def start_pjsua(user, port, codec, reference_file=None, rec_file="/dev/null"):
    cmd = [
        PJSUA_BIN,
        f"--id=sip:{user}@{SIP_DOMAIN}",
        f"--registrar=sip:{SIP_DOMAIN}:5060",
        "--realm=*", f"--username={user}", f"--password={SIP_PASSWORD}",
        f"--local-port={port}",
        "--dis-codec=*", f"--add-codec={codec}",
        "--auto-answer=200",
        f"--rec-file={rec_file}", "--auto-rec",
        "--null-audio", "--no-vad", "--log-level=0",
    ]
    if reference_file:
        cmd.extend([f"--play-file={reference_file}", "--auto-play", "--auto-loop"])
    return subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, preexec_fn=os.setsid,
    )


def get_reference_file(sample_rate):
    rate = int(sample_rate)
    for name in [f"reference_{rate // 1000}k.wav", f"reference_{rate}.wav"]:
        p = REFERENCE_DIR / name
        if p.exists():
            return str(p)
    return str(REFERENCE_DIR / "reference_8k.wav")


def run_batch(codec_a, codec_b, test_id, iterations, sample_rate_a=8000, sample_rate_b=8000):
    reference_file = get_reference_file(sample_rate_a)

    print(f"\n{'='*60}")
    print(f"  {test_id} | {codec_a} -> {codec_b} | {iterations} iteracija")
    print(f"{'='*60}")

    proc_a = None
    proc_b = None
    esl = None
    cpu_proc = None
    results = []

    try:

        esl = ESLClient()
        esl.connect()


        cpu_log = str(RESULTS_DIR / f"{test_id}_batch_cpu.log")
        cpu_script = (
            f'echo "cpu_percent" > {cpu_log}; '
            f'while true; do '
            f'docker stats voip-freeswitch --no-stream '
            f'--format "{{{{.CPUPerc}}}}" >> {cpu_log}; '
            f'sleep 1; done'
        )
        cpu_proc = subprocess.Popen(
            ["bash", "-c", cpu_script],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid,
        )


        a_rec = str(RECORDINGS_DIR / f"{test_id}_a_dummy.wav")
        b_rec = str(RECORDINGS_DIR / f"{test_id}_b_dummy.wav")
        proc_a = start_pjsua("1001", 5062, codec_a, reference_file, a_rec)
        proc_b = start_pjsua("1002", 5064, codec_b, rec_file=b_rec)
        time.sleep(3)


        for _ in range(20):
            reg = esl.api("sofia status profile internal reg")
            if "1001" in reg and "1002" in reg:
                print("  A i B registrovani.")
                break
            time.sleep(0.5)
        else:
            print("  UPOZORENJE: registracija timeout")


        for iteration in range(1, iterations + 1):
            test_prefix = f"{test_id}_iter{iteration}"
            b_recording = str(RECORDINGS_DIR / f"{test_prefix}_b_recv.wav")
            fs_stereo = f"/recordings/{test_prefix}_stereo.wav"
            result_file = str(RESULTS_DIR / f"{test_prefix}.json")
            timestamp = datetime.now().isoformat()


            originate_cmd = (
                f"originate "
                f"{{absolute_codec_string={codec_a}}}"
                f"user/1001@{SIP_DOMAIN} "
                f"&bridge({{absolute_codec_string={codec_b}}}user/1002@{SIP_DOMAIN})"
            )
            success = False
            uuid = None
            for attempt in range(3):
                resp = esl.api(originate_cmd)
                if "+OK" in resp:
                    uuid = resp.split("+OK ")[-1].strip().split("\n")[0]
                    success = True
                    break

                time.sleep(2)

            if success:
                time.sleep(1)

                esl.api(f"uuid_record {uuid} start {fs_stereo}")
                time.sleep(TEST_DURATION)
                esl.api(f"uuid_record {uuid} stop {fs_stereo}")
                time.sleep(0.5)
                esl.api(f"uuid_kill {uuid}")
                time.sleep(2)


                stereo_host = RECORDINGS_DIR / f"{test_prefix}_stereo.wav"
                if stereo_host.exists() and stereo_host.stat().st_size > 1000:
                    try:
                        subprocess.run(
                            ["sox", str(stereo_host), b_recording],
                            check=True, capture_output=True,
                        )
                    except subprocess.CalledProcessError:
                        try:
                            subprocess.run(
                                ["ffmpeg", "-y", "-i", str(stereo_host), "-ac", "1", b_recording],
                                check=True, capture_output=True,
                            )
                        except subprocess.CalledProcessError:
                            success = False
                else:
                    success = False
            else:
                print(f"    Originate fail: {resp[:100]}")


            result = {
                "test_id": test_id, "iteration": iteration,
                "codec_a": codec_a, "codec_b": codec_b,
                "scenario_type": "passthrough" if codec_a == codec_b else "transcode",
                "sample_rate_a": int(sample_rate_a),
                "sample_rate_b": int(sample_rate_b),
                "timestamp": timestamp, "success": success,
                "files": {
                    "reference": reference_file,
                    "b_recording": b_recording,
                    "a_recording": "",
                    "cpu_log": str(RESULTS_DIR / f"{test_prefix}_cpu.log"),
                    "pcap": "",
                },
                "audio_quality": None, "cpu_metrics": None, "rtp_stats": None,
            }
            os.makedirs(os.path.dirname(result_file), exist_ok=True)
            with open(result_file, "w") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)

            status = "OK" if success else "FAIL"
            print(f"  [{status}] iter{iteration}")
            results.append(success)

    except Exception as e:
        print(f"  GREŠKA: {e}")
        import traceback
        traceback.print_exc()

    finally:
        print("  Čišćenje...")
        if esl:
            try:
                esl.api("hupall")
            except Exception:
                pass
            esl.close()
        kill_pg(proc_a)
        kill_pg(proc_b)
        kill_pg(cpu_proc)
        time.sleep(1)


        batch_cpu = RESULTS_DIR / f"{test_id}_batch_cpu.log"
        if batch_cpu.exists():
            import shutil
            for i in range(1, iterations + 1):
                try:
                    shutil.copy2(str(batch_cpu), str(RESULTS_DIR / f"{test_id}_iter{i}_cpu.log"))
                except Exception:
                    pass

    ok = sum(1 for r in results if r)
    print(f"  Rezultat: {ok}/{len(results)}")
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--codec-a", required=True)
    parser.add_argument("--codec-b", required=True)
    parser.add_argument("--test-id", required=True)
    parser.add_argument("--iterations", "-n", type=int, default=5)
    parser.add_argument("--sample-rate-a", type=int, default=8000)
    parser.add_argument("--sample-rate-b", type=int, default=8000)
    args = parser.parse_args()

    results = run_batch(
        args.codec_a, args.codec_b, args.test_id, args.iterations,
        args.sample_rate_a, args.sample_rate_b,
    )
    ok = sum(1 for r in results if r)
    sys.exit(0 if ok == len(results) else 1)


if __name__ == "__main__":
    main()
