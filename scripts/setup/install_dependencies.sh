#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "============================================"
echo "  VoIP Transcoding Testbed - Setup"
echo "============================================"
echo "Projektni direktorij: $PROJECT_DIR"
echo ""

echo "[1/5] Instalacija sistemskih paketa..."
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    sox libsox-fmt-all \
    tshark \
    sysstat \
    build-essential \
    libasound2-dev \
    libssl-dev \
    libopus-dev \
    libgsm1-dev \
    libspeex-dev \
    libspeexdsp-dev \
    python3-pip \
    python3-venv \
    espeak-ng \
    ffmpeg \
    curl \
    git

echo "  Sistemski paketi instalirani."

echo ""
echo "[2/5] Kreiranje Python virtualnog okruženja..."
cd "$PROJECT_DIR"
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "  venv kreiran."
else
    echo "  venv već postoji, preskačem."
fi

source venv/bin/activate

echo "  Instalacija Python paketa..."
pip install --upgrade pip
pip install -r "$PROJECT_DIR/requirements.txt"
echo "  Python paketi instalirani."

echo ""
echo "[3/5] Kompajliranje pjsua iz PJSIP sourcea..."
if command -v pjsua &>/dev/null; then
    echo "  pjsua je već instaliran: $(which pjsua)"
else
    bash "$SCRIPT_DIR/build_pjsua.sh"
fi

echo ""
echo "[4/5] Kreiranje FreeSWITCH Docker image-a..."
cd "$PROJECT_DIR/docker"
docker compose build
echo "  Docker image kreiran."

echo ""
echo "[5/5] Priprema referentnog audio fajla..."
REFERENCE_DIR="$PROJECT_DIR/audio/reference"

if [ ! -f "$REFERENCE_DIR/reference_8k.wav" ] || \
   [ ! -f "$REFERENCE_DIR/reference_16k.wav" ] || \
   [ ! -f "$REFERENCE_DIR/reference_48k.wav" ]; then
    echo "  Generisanje sintetiziranog govornog uzorka..."
    REFERENCE_TEXT="The birch canoe slid on the smooth planks. Glue the sheet to the dark blue background. These days a chicken leg is a rare dish. Rice is often served in round bowls. The juice of lemons makes fine punch."
    espeak-ng -v en-us -s 145 -w "$REFERENCE_DIR/reference_source.wav" "$REFERENCE_TEXT"
    ffmpeg -y -i "$REFERENCE_DIR/reference_source.wav" -af "apad=pad_dur=20" -t 20 \
        -ar 48000 -ac 1 -sample_fmt s16 "$REFERENCE_DIR/reference_48k.wav"

    ffmpeg -y -i "$REFERENCE_DIR/reference_48k.wav" \
        -ar 16000 -ac 1 -sample_fmt s16 "$REFERENCE_DIR/reference_16k.wav"
    ffmpeg -y -i "$REFERENCE_DIR/reference_48k.wav" \
        -ar 8000 -ac 1 -sample_fmt s16 "$REFERENCE_DIR/reference_8k.wav"

    rm -f "$REFERENCE_DIR/reference_source.wav"
    echo "  Referentni govorni uzorak je pripremljen."
    echo "  Fajlovi: reference_48k.wav, reference_16k.wav, reference_8k.wav"
else
    echo "  Referentni audio već postoji, preskačem."
fi

echo ""
echo "============================================"
echo "  Setup završen!"
echo "============================================"
echo ""
echo "Sljedeći koraci:"
echo "  1. Pokrenuti FreeSWITCH:  cd docker && docker compose up -d"
echo "  2. Provjeriti status:     docker exec voip-freeswitch fs_cli -x 'sofia status'"
echo "  3. Pokrenuti test:        source venv/bin/activate && python scripts/test/run_single_test.py"
echo ""
