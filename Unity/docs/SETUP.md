# GaKuna — Environment Setup Guide

How to set up a full dev environment for the GaKuna dynamic evacuation
control system.

Components covered:

| Component | Purpose | Stack |
|---|---|---|
| `Unity/` | RL training of the signage agent (PPO) | Unity 6 + ML-Agents |
| `TrainerConfig/` | Training configs + results | Python `mlagents` |
| `RPI_Server/` | Web dashboard + TCP bridge + camera detection | Python Flask |
| `ESP_Node/` | ESP32 sign hardware node (LEDs, audio, sensors) | Arduino |
| `ESP_Cam/` | ESP32-CAM image snapshot server | Arduino |
| `YOLOV8CAM/` | Standalone OpenCV debris-detection test | Python OpenCV |

---

## 1. ML-Agents training stack (Unity + Python)

Versions verified on this machine:
Unity Editor **6000.3.7f1**, `com.unity.ml-agents` **4.0.2**,
`mlagents` Python package **1.1.0**, Python **3.10.12**.

### 1.1 Install Unity

1. Install **Unity Hub**.
2. In Unity Hub, install Editor **6000.3.7f1** (match `Unity/ProjectSettings/ProjectVersion.txt`).
   Add `Windows Build Support (IL2CPP)` / `Windows Build Support (Mono)` modules.
3. In Unity Hub → **Open** → select the `Unity/` folder of this repo.

> First open generates the `Library/` folder and resolves packages — can take several minutes.
> If the folder already has a `Library/` and packages misbehave, delete `Library/` and reopen.

### 1.2 Install the ML-Agents Python trainer

`mlagents` supports **Python 3.10.1–3.10.12 only** (newer Pythons won't install torch/mlagents).

Using Miniconda/Anaconda Prompt:

```bat
:: Create the env (conda-forge avoids Anaconda's TOS prompt)
conda create -n mlagents -c conda-forge --override-channels python=3.10.12 -y
conda activate mlagents

:: Install the trainer (matches com.unity.ml-agents 4.0.2)
python -m pip install mlagents==1.1.0

:: Fix pkg_resources crash (setuptools 81+ removed it, mlagents still needs it)
python -m pip install "setuptools<81"

:: Verify
mlagents-learn --help
```

> If you created the env from Anaconda default channels instead, you may need to accept
> the channel ToS first (`conda tos accept ...`).

### 1.3 Train

1. Open the Unity project with the **MainScene** scene open.
2. From `TrainerConfig/`:

```bat
conda activate mlagents
mlagents-learn gakuna_training.yaml --run-id Gakuna_9
```

3. When the trainer reports **Waiting for the training process to connect**,
   press **Play** in Unity.
4. Monitor with TensorBoard (in a second terminal):

```bat
tensorboard --logdir TrainerConfig/results
```

5. The trained model is exported automatically to `Unity/Assets/Evacuationer.onnx`
   when training ends.

---

## 2. RPI_Server (dashboard + ESP32 bridge)

```bat
python -m venv .venv
.venv\Scripts\activate
pip install flask flask-socketio opencv-python numpy
python GAKUNA_SERVER_V3.py
```

- Dashboard: `http://<Pi-IP>:8080` (`PI_HOST` listens on all interfaces)
- ESP32 node TCP port: `5001`
- ESP32-CAM JPEG stream port: `8081`
- Nodes identify themselves with `NODE_ID`; their DHCP addresses do not need to
  be listed in `NODE_CONFIG`.
- Add one `CAMERA_CONFIG` entry per camera, setting its `id` and associated
  `node_id`. Set the camera firmware's `CAMERA_ID` to the same `id`. For
  example, `cam_2` can monitor `node_2`. Each camera connects to the Pi's IP on
  port `8081`.
- Set the camera's router SSID/password and the Pi's reserved DHCP address in
  its firmware. Adjust the calibration constants (`KNOWN_*`,
  `CALIBRATION_PIXEL_WIDTH`) as needed.

---

## 3. ESP32 nodes and cameras (Arduino)

In Arduino IDE:

1. **Boards Manager**: install `esp32` by Espressif (ESP32 / ESP32-S3).
2. **Library Manager**: install
   - `OneWire` (Paul Stoffregen)
   - `DallasTemperature` (Miles Burton)
   - `ArduinoJson` (Benoit Blanchon, v6)
   - `DFRobotDFPlayerMini` (DFRobot)
3. Flash `ESP_Node/GAKUNA_ESP_NODE.ino` (ESP32 board, correct COM port).
4. Flash `ESP_Cam/GAKUNA_ESP_CAM.ino` (select `AI Thinker ESP32-CAM` board).

Hardware (node): ESP32 + DS18B20 (GPIO15), arrow/stop LEDs (GPIO25/26/27),
DFPlayer Mini + microSD with `/mp3/0001–0004.mp3`, speaker.
WiFi/host credentials are hardcoded in the `.ino` files (`gakunapi_wifi` /
`gakunapass`; Pi IP `10.42.0.1:5001`).

---

## 4. YOLOV8CAM test script

```bat
pip install opencv-python numpy
python test.py   :: change snapshot_url to your ESP32-CAM IP first
```

(Despite the name, it uses OpenCV thresholding, not YOLO.)

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Package resolution error for `com.unity.sentis` pointing at `Library/PackageCache/...` | The manifest must not reference a local-sentis path. `com.unity.ml-agents` already brings `com.unity.ai.inference` (the renamed Sentis). Remove the `com.unity.sentis` line from `Unity/Packages/manifest.json` and `packages-lock.json`. |
| `ModuleNotFoundError: No module named 'pkg_resources'` when running `mlagents-learn` | `pip install "setuptools<81"` in the `mlagents` env. |
| `conda` complains about Terms of Service | Use conda-forge: `conda create -n mlagents -c conda-forge --override-channels python=3.10.12 -y`. |
| Dashboard loads but socket never connects | `RPI_Server/static/socketio.min.js` must exist (Socket.IO 4.x client for flask-socketio). |
| Pinned Python too new (`3.13`/`3.14`) | `mlagents` requires 3.10.1–3.10.12; recreate the env with `python=3.10.12`. |