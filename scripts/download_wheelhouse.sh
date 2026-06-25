#!/usr/bin/env bash
# Docker(Linux) 오프라인 빌드용 wheel 다운로드.
# 인터넷 되는 PC/Linux 에서 실행 후 wheelhouse/ 를 서버로 복사하세요.
#
#   bash scripts/download_wheelhouse.sh
#   scp -r wheelhouse kisdev@mtbt-tbot1:~/AI_FESTIVAL_OCR/
#   ssh kisdev@mtbt-tbot1 'cd AI_FESTIVAL_OCR && DOCKERFILE=Dockerfile.offline docker compose up --build -d'

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

mkdir -p wheelhouse
python3 -m pip install --upgrade pip

# Linux x86_64 + Python 3.11 컨테이너용 wheel (manylinux)
python3 -m pip download -r requirements.txt -d wheelhouse \
  --platform manylinux_2_17_x86_64 \
  --python-version 3.11 \
  --implementation cp \
  --only-binary=:all: 2>/dev/null || true

# pure-python / wheel 없는 패키지 보완
python3 -m pip download -r requirements.txt -d wheelhouse \
  --platform manylinux_2_17_x86_64 \
  --python-version 3.11 \
  --implementation cp

echo "Done: $(ls wheelhouse | wc -l) files in wheelhouse/"
