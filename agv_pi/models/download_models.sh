#!/usr/bin/env bash
# Downloads the optional MobileNet-SSD person detector (Caffe, ~23 MB) used when
# agv.vision.person_detection: true. Run from the repo root:
#   bash agv_pi/models/download_models.sh
set -euo pipefail
cd "$(dirname "$0")"
BASE="https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/master"
curl -fL -o MobileNetSSD_deploy.prototxt "$BASE/deploy.prototxt"
curl -fL -o MobileNetSSD_deploy.caffemodel "https://github.com/chuanqi305/MobileNet-SSD/raw/master/mobilenet_iter_73000.caffemodel"
ls -lh MobileNetSSD_deploy.*
