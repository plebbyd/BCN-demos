# Privacy Layer + Left/Right People Counter — Demo Guide

Step-by-step instructions for installing, running, and using this demo
on a laptop or directly on a Jetson with an RTSP camera attached.

The demo does three things on every frame:

1. Detects faces with YuNet and applies a privacy mask (blur, pixelate,
   solid, etc.) — faces are masked **before** any frame leaves the
   process.
2. Detects people with OpenCV HOG, tracks them across frames, and
   maintains two counters:
   - **side count** — how many people are currently on the left vs.
     right of a vertical counting line
   - **crossing count** — running totals of tracked people who crossed
     the line going left or right (tripwire-style)
3. Optionally opens a desktop window showing the (privacy-masked) live
   stream with detection boxes, the counting line, track IDs, and a
   live counts overlay.

---

## 1. Prerequisites

| Need | Why |
|---|---|
| Python 3.10+ | Runs the app. macOS / Linux / Jetson all OK. |
| `pip` + `venv` | Isolated environment for OpenCV + pywaggle. |
| A display | Required only for `PREVIEW=1`. On Linux/Jetson, `$DISPLAY` must be set. |
| RTSP source (optional) | The lab PTZ is `rtsp://10.31.81.27:554/profile1/media.smp`. If you can't reach that subnet, use a local mp4 or your webcam instead. |

You do **not** need Docker, a GPU, PyTorch, CUDA, or a Sage node to run
this demo.

---

## 2. Install

From the project root (`privacylayer-05012026/`):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

`requirements.txt` pulls in the **GUI-enabled** `opencv-python` wheel
(not the headless variant) so the preview window can render.

### One-time env setup so the model and logs land in the project

The face detector model defaults to `/app/models/...` (the container
path). When running on a host, override it once per shell:

```bash
export YUNET_MODEL_PATH="$PWD/models/face_detection_yunet_2023mar.onnx"
export PYWAGGLE_LOG_DIR="$PWD/output"
```

The first invocation will download the ~300 KB YuNet ONNX into
`./models/` automatically. Subsequent runs are offline.

---

## 3. Run the demo

All settings are exposed twice: as `--cli_flag` and as `ENV_VAR`. Pick
whichever is more convenient.

### A. Laptop webcam (zero setup, fastest sanity check)

```bash
python main.py --stream 0 --preview 1 --detect_people 1
```

A window titled **privacy-layer preview** opens, your face is masked
(default `box` blur), and a counts panel shows in the top-left.

### B. Local mp4 file

```bash
python main.py \
  --stream ./test.mp4 \
  --preview 1 \
  --detect_people 1 \
  --method pixelate
```

### C. Lab PTZ over RTSP

```bash
python main.py \
  --stream 'rtsp://10.31.81.27:554/profile1/media.smp' \
  --preview 1 \
  --detect_people 1 \
  --line_x 0.5
```

Or with env vars (the form a Sage container would use):

```bash
STREAM='rtsp://10.31.81.27:554/profile1/media.smp' \
PREVIEW=1 \
DETECT_PEOPLE=1 \
LINE_X=0.5 \
python main.py
```

### D. Single-frame snapshot (good for debugging detection)

```bash
python main.py \
  --stream 'rtsp://10.31.81.27:554/profile1/media.smp' \
  --snapshot_only 1 \
  --preview 1
```

The window stays open until you press any key.

### E. Headless run (no preview, write blurred mp4)

```bash
python main.py \
  --stream 'rtsp://10.31.81.27:554/profile1/media.smp' \
  --output file \
  --publish_count 1
```

Output mp4 lands in `output/<timestamp>/blurred.mp4`. Published
measurements (per-frame counts) are appended to
`$PYWAGGLE_LOG_DIR/data.ndjson`.

---

## 4. Using the preview window

What you'll see:

- **Yellow boxes** — face detections (YuNet)
- **Green boxes** — person detections (HOG)
- **Green dot + `#N`** — tracked person centroid + stable track ID
- **Cyan vertical line** — the counting line at `LINE_X` × frame width
- **Top-left panel** —
  - `faces:` — face detections this frame
  - `people now: N (L:.. R:..)` — currently tracked people, split by side
  - `crossings L:.. R:..` — running totals of line crossings
  - `mask:` — active masking method, `+people` if person bodies are
    also being blurred

Keys:

| Key | Action |
|---|---|
| `q` or `ESC` | Quit cleanly (releases the writer if `--output file`). |

The pixels written to disk / uploaded to Beehive are the
**privacy-masked frame without any overlay**. Detection boxes, IDs,
and counts are drawn only on the preview canvas.

---

## 5. Common knobs

| Flag / env | Default | What it does |
|---|---|---|
| `--method` / `METHOD` | `box` | `box`, `gaussian`, `median`, `pixelate`, `solid` |
| `--blur_strength` / `BLUR_STRENGTH` | `25` | Higher = more aggressive obscuring |
| `--blur_people` / `BLUR_PEOPLE` | `0` | If `1`, mask the entire person body, not just the face |
| `--detect_people` / `DETECT_PEOPLE` | `1` | Set to `0` for the original face-only behavior |
| `--line_x` / `LINE_X` | `0.5` | Counting line position (fraction of frame width) |
| `--detect_width` / `DETECT_WIDTH` | `640` | Detect at this width for speed; `0` = full resolution |
| `--preview_width` / `PREVIEW_WIDTH` | `1280` | Resize the preview window; `0` = native |
| `--publish_count` / `PUBLISH_COUNT` | `0` | Publish per-frame counts to Beehive (or local ndjson) |
| `--output` / `OUTPUT` | `none` | `none`, `file`, `upload`, `both` |

---

## 6. Demo recipes

### "Privacy mode" — heavy mask, full bodies, preview only

```bash
python main.py --stream 0 \
  --preview 1 \
  --detect_people 1 \
  --blur_people 1 \
  --method pixelate \
  --blur_strength 60
```

### "Tripwire counter" — count people walking left vs right past a line

```bash
python main.py \
  --stream 'rtsp://10.31.81.27:554/profile1/media.smp' \
  --preview 1 \
  --detect_people 1 \
  --line_x 0.4 \
  --output file \
  --publish_count 1
```

`output/<timestamp>/blurred.mp4` will hold the masked recording, and
`output/data.ndjson` will hold per-frame counts (face count, people
count, side splits, crossing totals).

### "Face-only baseline" — original behavior, no people detection

```bash
python main.py --stream 0 --preview 1 --detect_people 0
```

---

## 7. Troubleshooting

**`cv2.error: ... function is not implemented` when opening the preview**

You have the headless OpenCV wheel installed. Re-install the GUI build:

```bash
pip uninstall opencv-python opencv-python-headless -y
pip install opencv-python
```

**Black preview / no window appears on Jetson**

`$DISPLAY` is unset (e.g. running over plain SSH). Either run from the
Jetson's local desktop session, or use `ssh -X` and re-run.

**`urllib.error.PermissionError: ... /app/models/...`**

You forgot to set `YUNET_MODEL_PATH` to a writable location. Set it
once per shell as shown in step 2.

**`Could not open RTSP stream`**

- Confirm the URL is reachable: `ffprobe rtsp://10.31.81.27:554/profile1/media.smp`
- The lab PTZ is on `10.31.81.0/24` — you must be on that subnet or
  routed in.
- For testing, fall back to `--stream 0` (webcam) or `--stream ./vid.mp4`.

**HOG misses people / lots of false boxes**

HOG is intentionally simple. Tune by:

- Lowering `--detect_width` (e.g. `--detect_width 480`) for speed
- Raising the `min_weight` threshold inside `detect_people()` in
  `people_counter.py` for fewer false positives
- Or swap `detect_people()` for an ONNX SSD/YOLO detector — the rest of
  the pipeline (tracker, counter, preview) stays the same.

**Crossings counter never increments**

The tracker needs to keep the same `#ID` on a person across frames. If
people walk through the frame too quickly, raise
`max_disappeared` / `max_distance` in `CentroidTracker(...)` inside
`main.py`.

---

## 8. What gets sent off-device

When `--publish_count 1` is set, only these aggregate scalars are
published per frame:

- `privacy.faces.count`
- `privacy.method`
- `people.count`
- `people.side.left`, `people.side.right`
- `people.cross.left`, `people.cross.right`

No bounding boxes, no embeddings, no identities — and the masked frame
is the only image that can ever leave the process.
