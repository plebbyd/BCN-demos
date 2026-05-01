#!/usr/bin/env python3
# face-blur + people-count edge app for Sage / Waggle
# - source: STREAM (file / rtsp / http) or named/default waggle Camera
# - detects faces with YuNet (optionally on a downscaled copy)
# - optionally detects people with OpenCV HOG and counts left/right
#   side-split + tracked tripwire crossings
# - masks every face (and optionally every person) with a selectable
#   method (box / gaussian / median / pixelate / solid)
# - optional desktop preview window with detections + counts overlay
# - output modes: none | file | upload | both

import os
import cv2
import argparse
import datetime

from waggle.plugin import Plugin
from waggle.data.vision import Camera

from privacy_layer import (
    load_face_detector,
    detect_faces,
    blur_regions,
    BLUR_METHODS,
)
from people_counter import (
    load_person_detector,
    detect_people,
    CentroidTracker,
    CrossingCounter,
)


OUTPUT_MODES = ("none", "file", "upload", "both")


def str2bool(x):
    return str(x).lower() in {"1", "true", "t", "yes", "y"}


def parse_args():
    p = argparse.ArgumentParser(description="face-blur + people-count edge app")

    # source
    p.add_argument("--stream", default=os.getenv("STREAM", ""),
                   help="file path or rtsp/http url")
    p.add_argument("--camera", default=os.getenv("CAMERA", ""),
                   help="waggle camera name (e.g., left/right)")
    p.add_argument("--snapshot_only", type=str2bool,
                   default=str2bool(os.getenv("SNAPSHOT_ONLY", "0")),
                   help="process one frame and exit")

    # face detection
    p.add_argument("--conf", type=float,
                   default=float(os.getenv("CONF", "0.6")),
                   help="face detection confidence threshold")
    p.add_argument("--detect_width", type=int,
                   default=int(os.getenv("DETECT_WIDTH", "640")),
                   help="downscale frame to this width before detection "
                        "(0 = detect at full resolution)")

    # people detection / counting
    p.add_argument("--detect_people", type=str2bool,
                   default=str2bool(os.getenv("DETECT_PEOPLE", "1")),
                   help="run HOG person detection + left/right counting")
    p.add_argument("--blur_people", type=str2bool,
                   default=str2bool(os.getenv("BLUR_PEOPLE", "0")),
                   help="apply privacy mask to full person body, not just face")
    p.add_argument("--line_x", type=float,
                   default=float(os.getenv("LINE_X", "0.5")),
                   help="vertical counting line as fraction of frame width "
                        "(0.5 = center)")

    # masking
    p.add_argument("--method",
                   default=os.getenv("METHOD", "box"),
                   choices=sorted(BLUR_METHODS.keys()),
                   help="face masking method")
    p.add_argument("--blur_strength", type=int,
                   default=int(os.getenv("BLUR_STRENGTH", "25")))
    p.add_argument("--pad_frac", type=float,
                   default=float(os.getenv("PAD_FRAC", "0.15")),
                   help="padding fraction around each face box")

    # preview
    p.add_argument("--preview", type=str2bool,
                   default=str2bool(os.getenv("PREVIEW", "0")),
                   help="show a desktop window with detections + counts "
                        "(requires non-headless OpenCV and a display)")
    p.add_argument("--preview_width", type=int,
                   default=int(os.getenv("PREVIEW_WIDTH", "1280")),
                   help="resize preview window to this width "
                        "(0 = native resolution)")

    # output
    p.add_argument("--output",
                   default=os.getenv("OUTPUT", "none"),
                   choices=OUTPUT_MODES,
                   help="what to do with blurred frames: "
                        "none=discard, file=write mp4, "
                        "upload=periodic snapshot to Beehive, both=file+upload")
    p.add_argument("--out_path", default=os.getenv("OUT_PATH", "blurred.mp4"),
                   help="output mp4 filename when --output includes 'file'")
    p.add_argument("--upload_every", type=int,
                   default=int(os.getenv("UPLOAD_EVERY", "150")),
                   help="upload one blurred snapshot every N frames "
                        "(only used when --output includes 'upload')")
    p.add_argument("--publish_count", type=str2bool,
                   default=str2bool(os.getenv("PUBLISH_COUNT", "0")),
                   help="if true, publish per-frame face/people counts to Beehive")
    return p.parse_args()


def make_writer(frame, out_path, fps=30.0):
    """Create a VideoWriter sized to the first frame; return (writer, full_path)."""
    H, W = frame.shape[:2]
    out_dir = os.path.join(
        "output", datetime.datetime.now().strftime("%Y%m%d%H%M%S"),
    )
    os.makedirs(out_dir, exist_ok=True)
    full_path = os.path.join(out_dir, os.path.basename(out_path))
    writer = cv2.VideoWriter(
        full_path,
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (W, H),
    )
    print(f"writing video to {full_path}")
    return writer, full_path


def process_frame(frame, face_detector, person_detector, tracker, counter,
                  args):
    """Run all detection / blurring / tracking on `frame` in-place.

    Returns dict with face_count, people_count, side_left, side_right,
    cross_left, cross_right, face_boxes, person_boxes, tracked.
    """
    face_boxes = detect_faces(face_detector, frame,
                              detect_width=args.detect_width or None)

    person_boxes = []
    tracked = {}
    if person_detector is not None:
        person_boxes = detect_people(
            person_detector, frame,
            detect_width=args.detect_width or None,
        )
        tracked = tracker.update(person_boxes)
        counter.line_x = int(frame.shape[1] * args.line_x)
        counter.update(tracked)

    # Apply privacy masking AFTER detection so the detector sees real pixels.
    if args.blur_people and person_boxes:
        blur_regions(frame, person_boxes,
                     method=args.method,
                     strength=args.blur_strength,
                     pad_frac=args.pad_frac)
    blur_regions(frame, face_boxes,
                 method=args.method,
                 strength=args.blur_strength,
                 pad_frac=args.pad_frac)

    side_left, side_right = (0, 0)
    if person_detector is not None:
        side_left, side_right = counter.side_split(tracked)

    return {
        "face_count": len(face_boxes),
        "people_count": len(person_boxes),
        "side_left": side_left,
        "side_right": side_right,
        "cross_left": counter.left_count if counter else 0,
        "cross_right": counter.right_count if counter else 0,
        "face_boxes": face_boxes,
        "person_boxes": person_boxes,
        "tracked": tracked,
    }


def render_overlay(frame, info, args, counter):
    """Return a copy of `frame` with detection boxes, counting line, and
    counts overlaid. Used for the preview window only -- the video/upload
    output keeps the un-annotated (privacy-masked) frame."""
    canvas = frame.copy()
    H, W = canvas.shape[:2]

    # face boxes (yellow)
    for (x, y, bw, bh) in info["face_boxes"]:
        cv2.rectangle(canvas, (x, y), (x + bw, y + bh), (0, 255, 255), 2)
        cv2.putText(canvas, "face", (x, max(0, y - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1,
                    cv2.LINE_AA)

    # person boxes (green) with track IDs
    if args.detect_people:
        for (x, y, bw, bh) in info["person_boxes"]:
            cv2.rectangle(canvas, (x, y), (x + bw, y + bh), (0, 255, 0), 2)
        for oid, (cx, cy) in info["tracked"].items():
            cv2.circle(canvas, (int(cx), int(cy)), 4, (0, 255, 0), -1)
            cv2.putText(canvas, f"#{oid}", (int(cx) + 6, int(cy) - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1,
                        cv2.LINE_AA)

        # vertical counting line
        lx = counter.line_x
        cv2.line(canvas, (lx, 0), (lx, H), (255, 200, 0), 2)

    # counts panel (top-left)
    panel_lines = [
        f"faces: {info['face_count']}",
    ]
    if args.detect_people:
        panel_lines += [
            f"people now: {info['people_count']}  "
            f"(L:{info['side_left']}  R:{info['side_right']})",
            f"crossings  L:{info['cross_left']}  R:{info['cross_right']}",
        ]
    panel_lines.append(f"mask: {args.method}"
                       + ("  +people" if args.blur_people else ""))

    pad = 8
    line_h = 24
    panel_h = pad * 2 + line_h * len(panel_lines)
    panel_w = 380
    overlay = canvas.copy()
    cv2.rectangle(overlay, (0, 0), (panel_w, panel_h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, canvas, 0.45, 0, canvas)
    for i, text in enumerate(panel_lines):
        cv2.putText(canvas, text, (pad, pad + line_h * (i + 1) - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1,
                    cv2.LINE_AA)

    if args.preview_width and W > args.preview_width:
        scale = args.preview_width / W
        canvas = cv2.resize(canvas,
                            (args.preview_width, int(H * scale)))
    return canvas


def maybe_upload(plugin, frame, frame_idx, sample_ts):
    """Write blurred frame to /tmp and upload it to Beehive."""
    tmp = f"/tmp/blurred_{frame_idx}.jpg"
    cv2.imwrite(tmp, frame)
    plugin.upload_file(tmp, timestamp=sample_ts)


def publish_metrics(plugin, info, sample_ts):
    plugin.publish("privacy.faces.count", info["face_count"],
                   timestamp=sample_ts)
    plugin.publish("people.count", info["people_count"],
                   timestamp=sample_ts)
    plugin.publish("people.side.left", info["side_left"],
                   timestamp=sample_ts)
    plugin.publish("people.side.right", info["side_right"],
                   timestamp=sample_ts)
    plugin.publish("people.cross.left", info["cross_left"],
                   timestamp=sample_ts)
    plugin.publish("people.cross.right", info["cross_right"],
                   timestamp=sample_ts)


def init_preview(window_name="privacy-layer preview"):
    """Create the cv2 preview window. Returns the window name on success
    or None if the OpenCV build has no GUI support (headless wheel)."""
    try:
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        return window_name
    except cv2.error as e:
        print(f"preview disabled: cv2.namedWindow failed ({e}). "
              "Install non-headless opencv-python and ensure $DISPLAY is set.")
        return None


def main():
    args = parse_args()
    print(f"face-blur: method={args.method} conf={args.conf} "
          f"strength={args.blur_strength} pad={args.pad_frac} "
          f"detect_width={args.detect_width or 'full'} "
          f"output={args.output} preview={args.preview} "
          f"detect_people={args.detect_people} blur_people={args.blur_people}")

    do_file = args.output in ("file", "both")
    do_upload = args.output in ("upload", "both")

    face_detector = load_face_detector(conf_threshold=args.conf)
    person_detector = load_person_detector() if args.detect_people else None
    tracker = CentroidTracker() if args.detect_people else None
    counter = CrossingCounter(line_x=0) if args.detect_people else None

    source = args.stream or args.camera or None
    print(f"source: {source if source else 'default-camera'}")

    window_name = init_preview() if args.preview else None

    # ---------- snapshot mode ----------
    if args.snapshot_only:
        with Plugin() as plugin:
            sample = Camera(source).snapshot()
            frame = sample.data
            info = process_frame(frame, face_detector, person_detector,
                                 tracker, counter, args)
            if args.publish_count:
                publish_metrics(plugin, info, sample.timestamp)
            if do_file:
                _, path = make_writer(frame, args.out_path)
                cv2.imwrite(path.replace(".mp4", ".jpg"), frame)
            if do_upload:
                maybe_upload(plugin, frame, 0, sample.timestamp)
            if window_name is not None:
                preview = render_overlay(frame, info, args, counter)
                cv2.imshow(window_name, preview)
                print("preview shown; press any key to close.")
                cv2.waitKey(0)
                cv2.destroyAllWindows()
        print("done. processed 1 frame")
        return

    # ---------- streaming mode ----------
    writer = None
    frame_idx = 0
    try:
        with Plugin() as plugin, Camera(source) as camera:
            if args.publish_count:
                plugin.publish("privacy.method", args.method)

            for sample in camera.stream():
                frame = sample.data
                if frame is None:
                    continue
                frame_idx += 1

                info = process_frame(frame, face_detector, person_detector,
                                     tracker, counter, args)

                if args.publish_count:
                    publish_metrics(plugin, info, sample.timestamp)

                if do_file:
                    if writer is None:
                        writer, _ = make_writer(frame, args.out_path)
                    writer.write(frame)

                if do_upload and frame_idx % args.upload_every == 0:
                    maybe_upload(plugin, frame, frame_idx, sample.timestamp)

                if window_name is not None:
                    preview = render_overlay(frame, info, args, counter)
                    cv2.imshow(window_name, preview)
                    # waitKey is required to actually paint the window;
                    # 'q' or ESC quits the loop on the desktop.
                    key = cv2.waitKey(1) & 0xFF
                    if key in (ord("q"), 27):
                        print("quit requested from preview window")
                        break

                if frame_idx % 50 == 0:
                    print(
                        f"frame {frame_idx}  faces={info['face_count']}  "
                        f"people={info['people_count']}  "
                        f"L/R now={info['side_left']}/{info['side_right']}  "
                        f"L/R cross={info['cross_left']}/{info['cross_right']}"
                    )
    finally:
        if writer is not None:
            writer.release()
        if window_name is not None:
            cv2.destroyAllWindows()
        print(f"done. processed {frame_idx} frames")


if __name__ == "__main__":
    main()
