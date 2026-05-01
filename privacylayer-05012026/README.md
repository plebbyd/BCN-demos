# privacy_layer

A privacy-preserving image and video capture plugin for the
[Sage / Waggle](https://sagecontinuum.org) edge-computing environment.
Detects human faces in real time and applies a configurable mask &mdash;
box blur, Gaussian blur, median blur, pixelation, or full block-out
&mdash; on the device, before any frames are saved or transmitted.

## How it works

1. A frame is read from the configured source (Waggle camera, RTSP
   stream, or video file).
2. The frame is optionally downscaled for detection only &mdash; the
   full-resolution frame is preserved for masking.
3. [YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet),
   loaded through OpenCV's DNN module, detects every face in the frame.
4. Each face box is padded outward (default 15%) to cover hairlines,
   ears, and detection wobble.
5. The selected masking method is applied to each padded region.
6. The blurred frame is discarded, written to a local mp4, or uploaded
   as a periodic snapshot to Beehive, depending on output mode.

YuNet is ~300 KB and runs on CPU at hundreds of frames per second.
There is no PyTorch or CUDA dependency.

## Files

| File | Purpose |
|---|---|
| `main.py` | Plugin entry point &mdash; argument parsing, camera open, frame loop, preview window |
| `privacy_layer.py` | Face detector loader, detection function, blur method registry |
| `people_counter.py` | HOG person detector, centroid tracker, left/right crossing counter |
| `Dockerfile` | Container build &mdash; CPU-only, multi-arch |
| `requirements.txt` | Python dependencies |
| `sage.yaml` | Sage plugin manifest for ECR |
| `ecr-meta/` | Long-form description (Science / AI@Edge / Ontology) |

## Configuration

Every setting is exposed as both a CLI flag (`--name`) and an environment
variable (`NAME`). Environment variables are the typical interface for
plugins running in containers.

### Source

| Variable | Default | Notes |
|---|---|---|
| `STREAM` | _empty_ | File path or `rtsp://...` / `http://...` URL. Takes precedence over `CAMERA`. |
| `CAMERA` | _empty_ | Named Waggle camera (e.g. `top`, `left`). Used when running on a Sage node. |
| `SNAPSHOT_ONLY` | `0` | If `1`, capture one frame and exit. |

### Detection

| Variable | Default | Notes |
|---|---|---|
| `CONF` | `0.6` | Face detection confidence threshold (0&ndash;1). |
| `DETECT_WIDTH` | `640` | Downscale frames to this width before detection. `0` = detect at full resolution. |

### Masking

| Variable | Default | Options / Notes |
|---|---|---|
| `METHOD` | `box` | `box`, `gaussian`, `median`, `pixelate`, `solid`. |
| `BLUR_STRENGTH` | `25` | Higher = more aggressive obscuring. Ignored for `solid`. |
| `PAD_FRAC` | `0.15` | Fraction of padding around each face box. |

### People counting

| Variable | Default | Notes |
|---|---|---|
| `DETECT_PEOPLE` | `1` | If `1`, run OpenCV HOG person detection and track left/right counts. Set to `0` for face-only behavior. |
| `BLUR_PEOPLE` | `0` | If `1`, apply the privacy mask to the full person body (in addition to faces). |
| `LINE_X` | `0.5` | Vertical counting line as a fraction of frame width. `0.5` = center. Centroids crossing this line increment the L/R crossing counters. |

### Preview window

| Variable | Default | Notes |
|---|---|---|
| `PREVIEW` | `0` | If `1`, open a desktop window showing the (privacy-masked) stream with detection boxes, the counting line, and live counts. Press `q` or `ESC` to quit. |
| `PREVIEW_WIDTH` | `1280` | Resize preview window to this width. `0` = native resolution. |

### Output

| Variable | Default | Notes |
|---|---|---|
| `OUTPUT` | `none` | `none`, `file`, `upload`, or `both`. |
| `OUT_PATH` | `blurred.mp4` | Output filename when `OUTPUT` includes `file`. Saved under `output/<timestamp>/`. |
| `UPLOAD_EVERY` | `150` | Upload one snapshot every N frames when `OUTPUT` includes `upload`. |
| `PUBLISH_COUNT` | `0` | If `1`, publish per-frame face/people counts to Beehive (`privacy.faces.count`, `people.count`, `people.side.left/right`, `people.cross.left/right`). |

### Masking method comparison

- `box` &mdash; default. Cheap averaging filter; visually close to Gaussian
  at matching strength. Recommended for most deployments.
- `gaussian` &mdash; smooth and natural-looking. The most expensive method
  per frame.
- `median` &mdash; posterized "oil painting" effect. Preserves face-region
  edges.
- `pixelate` &mdash; classic mosaic censor. Most visually recognizable as
  a privacy effect.
- `solid` &mdash; full black mask. Strongest privacy guarantee.

## Building

```bash
docker build -t privacy-layer .
```

The build pre-downloads the YuNet ONNX model into the image so the
container runs offline on edge nodes.

## Running

### On a video file

```bash
docker run --rm \
  -v "$PWD/test.mp4":/app/test.mp4 \
  -v "$PWD/output":/app/output \
  -e STREAM=/app/test.mp4 \
  -e OUTPUT=file \
  privacy-layer
```

### On an RTSP camera

Verified working with an Amcrest IP camera over a direct Ethernet link.
The plugin opens the stream through OpenCV's RTSP support (FFMPEG-backed):

```bash
docker run --rm \
  -v "$PWD/output":/app/output \
  -e STREAM='rtsp://admin:PASSWORD@192.168.1.108:554/cam/realmonitor?channel=1&subtype=1' \
  -e OUTPUT=file \
  -e PUBLISH_COUNT=1 \
  -e PYWAGGLE_LOG_DIR=/app/output \
  privacy-layer
```

`subtype=1` requests the camera's sub-stream (lower resolution, lower
bitrate). Use `subtype=0` for the main stream.

URL-encode any special characters in the password (`@` &rarr; `%40`,
`:` &rarr; `%3A`, `/` &rarr; `%2F`).

### Live preview on the Jetson desktop

The preview window draws the current frame (already privacy-masked),
detection boxes for faces (yellow) and people (green), the counting
line, and a live overlay panel:

- `faces:` — number of YuNet detections this frame
- `people now: N (L:.. R:..)` — currently-tracked people split by side
- `crossings L:.. R:..` — running totals of tripwire crossings since
  the process started
- `mask:` — active masking method (`+people` if person-bodies are also
  blurred)

Run from the Jetson desktop (or any terminal with `$DISPLAY` exported)
against the lab PTZ:

```bash
docker run --rm \
  --net=host \
  -e DISPLAY=$DISPLAY \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  -v "$PWD/output":/app/output \
  -e STREAM='rtsp://10.31.81.27:554/profile1/media.smp' \
  -e PREVIEW=1 \
  -e DETECT_PEOPLE=1 \
  -e LINE_X=0.5 \
  -e PUBLISH_COUNT=1 \
  -e PYWAGGLE_LOG_DIR=/app/output \
  privacy-layer
```

If the X11 socket bind-mount is rejected by the host, run
`xhost +local:docker` once on the desktop session.

To run without Docker (virtualenv on the Jetson):

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
STREAM='rtsp://10.31.81.27:554/profile1/media.smp' \
PREVIEW=1 DETECT_PEOPLE=1 \
python main.py
```

`requirements.txt` pins **`opencv-python`** (not the headless wheel) so
`cv2.imshow` is available. If you only need server-mode (no preview),
swap it back to `opencv-python-headless` for a smaller image.

### On a Sage node

```bash
sudo pluginctl build .
sudo pluginctl run --name privacy-layer <image-tag>
```

Note: Waggle/Sage node runtime flags are still being tested. The exact
`pluginctl run ... -- --camera ... --output ...` form is not yet
verified on a live node, so this section intentionally keeps a minimal
command until node-side validation is complete.

## Output

### File mode

A blurred mp4 is written to `output/<timestamp>/blurred.mp4` (or whatever
`OUT_PATH` is set to). Useful for processing pre-recorded video.

### Upload mode

One blurred JPG is encoded and shipped to Beehive every `UPLOAD_EVERY`
frames.

Note: end-to-end Beehive upload/query validation on a commissioned
Sage node is still in progress, so API query examples are intentionally
omitted until this path is verified.

### Measurements

When `PUBLISH_COUNT=1`, the plugin publishes:

- `privacy.faces.count` &mdash; integer count of faces detected per frame
- `privacy.method` &mdash; the masking method active for the run
- `people.count` &mdash; people detected this frame
- `people.side.left` / `people.side.right` &mdash; people currently
  tracked on each side of the counting line
- `people.cross.left` / `people.cross.right` &mdash; running totals of
  tracked centroids that crossed the line in each direction

The plugin **does not** publish bounding boxes, face embeddings, identity
labels, demographic estimates, or any other derived attribute that could
be used for re-identification.

## Local development without a Sage node

The plugin works on any Linux box with Docker. When pywaggle's `Camera`
class fails to resolve a source &mdash; typically because
`/run/waggle/data-config.json` is missing &mdash; the code falls back to
opening the source through raw OpenCV. This lets the same image run
unchanged on a developer laptop, a plain Jetson Thor, or a fully
configured Sage Blade.

You will see this log line once on startup when the fallback activates:
pywaggle Camera rejected source (...); using cv2 fallback

It is harmless and indicates dev mode; on a real Sage node it will not
appear.

For local testing, set `PYWAGGLE_LOG_DIR` to capture published
measurements to a local `data.ndjson` file instead of routing them to a
real Beehive.

## Verified configurations

| Hardware | Source | Method | Status |
|---|---|---|---|
| Jetson Thor (arm64), Docker 29.2.1 | RTSP from Amcrest IP camera | `box` | Working |
| Jetson Thor (arm64), Docker 29.2.1 | Local mp4 file | `box`, `gaussian`, `pixelate`, `solid`, `median` | Working |

## Known caveats

- The Amcrest's RTSP authentication is sensitive to special characters
  in the password &mdash; URL-encode if needed.
- `cv2.VideoWriter` uses the `mp4v` codec by default. Some players
  require `avc1`; if the output mp4 won't play, change the FOURCC in
  `make_writer()`.
- The plugin does not currently publish a heartbeat in `none` output
  mode, so a deployed node has no observable signal that the plugin is
  alive other than the scheduler's container status. Set
  `PUBLISH_COUNT=1` if you want continuous health visibility.
- `Camera()` in older pywaggle versions does not accept HTTP URLs; the
  cv2 fallback handles them transparently.

## License

MIT
