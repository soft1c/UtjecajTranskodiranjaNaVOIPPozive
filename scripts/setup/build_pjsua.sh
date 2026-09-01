#!/bin/bash
set -euo pipefail

BUILD_DIR="/tmp/pjsip-build"
PJSIP_VERSION="2.14"

echo "=========================================="
echo "  Kompajliranje pjsua (PJSIP $PJSIP_VERSION)"
echo "=========================================="

echo "[1/4] Provjera build zavisnosti..."
sudo apt-get install -y --no-install-recommends \
    build-essential \
    libasound2-dev \
    libssl-dev \
    libopus-dev \
    libgsm1-dev \
    libspeex-dev \
    libspeexdsp-dev \
    libsrtp2-dev \
    uuid-dev

echo ""
echo "[2/4] Kloniranje PJSIP repozitorija..."
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"
git clone --depth 1 --branch "$PJSIP_VERSION" https://github.com/pjsip/pjproject.git 2>/dev/null \
    || git clone --depth 1 https://github.com/pjsip/pjproject.git
cd pjproject

echo ""
echo "[3/4] Konfiguracija i kompajliranje..."

cat > pjlib/include/pj/config_site.h << 'EOF'
/* Uključi Opus codec */
#define PJMEDIA_HAS_OPUS_CODEC 1

/* Uključi GSM codec */
#define PJMEDIA_HAS_GSM_CODEC 1

/* Uključi Speex codec */
#define PJMEDIA_HAS_SPEEX_CODEC 1

/* Uključi iLBC codec */
#define PJMEDIA_HAS_ILBC_CODEC 1

/* Uključi G.722 codec */
#define PJMEDIA_HAS_G722_CODEC 1

/* Uključi G.711 codec */
#define PJMEDIA_HAS_G711_CODEC 1

/* Povećaj max codec count */
#define PJMEDIA_CODEC_MAX_TYPES 32

/* Veći sound buffer za stabilniji audio */
#define PJMEDIA_SOUND_BUFFER_COUNT 32
EOF

./configure \
    --prefix=/usr/local \
    --with-external-gsm \
    --with-external-speex \
    --with-external-opus \
    --with-external-srtp

make dep
make -j"$(nproc)"

echo ""
echo "[4/4] Instalacija..."
sudo make install

sudo ldconfig

if ! /usr/local/bin/pjsua --help > /dev/null 2>&1; then
    echo "UPOZORENJE: pjsua sa shared libs ne radi, kopiram statički binary..."
    PJSUA_BIN=$(find pjsip-apps/bin -name 'pjsua-*' -type f | head -1)
    if [ -z "$PJSUA_BIN" ]; then
        echo "GREŠKA: pjsua binary nije pronađen!"
        exit 1
    fi
    sudo cp "$PJSUA_BIN" /usr/local/bin/pjsua
    sudo chmod 755 /usr/local/bin/pjsua
fi

rm -rf "$BUILD_DIR"

echo ""
echo "=========================================="
echo "  pjsua uspješno instaliran!"
echo "=========================================="
echo "Lokacija: $(which pjsua)"
pjsua --version 2>/dev/null || echo "(verzija se prikazuje pri pokretanju)"
