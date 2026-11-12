# Optional vision models

`download_models.sh` fetches the MobileNet-SSD (VOC) Caffe model used by
`agv_pi/vision/person_detector.py`. The files are git-ignored. Enable with
`agv.vision.person_detection: true` in `config.yaml`. Without them the AGV
still runs; only the person classifier is disabled (motion detection, LiDAR
and IR safety are unaffected).
