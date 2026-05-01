# people_counter.py
# Person detection (OpenCV HOG) + lightweight centroid tracker
# + left/right crossing counter.
#
# Kept dependency-free on purpose: HOG is shipped inside OpenCV, so
# adding people-counting does not pull in PyTorch/CUDA or another model
# download. Accuracy is modest; swap detect_people() for a DNN if needed.

from collections import OrderedDict

import cv2
import numpy as np


# ---------- detector ----------

def load_person_detector():
    """Return an OpenCV HOG descriptor primed with the default people SVM."""
    hog = cv2.HOGDescriptor()
    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    return hog


def detect_people(detector, frame, detect_width=None, min_weight=0.4):
    """Detect people in `frame`. If `detect_width` is set and the frame is
    wider, run detection on a downscaled copy and rescale boxes back.

    Returns list of (x, y, w, h) ints in original-frame coordinates.
    """
    if frame is None or frame.size == 0:
        return []

    h, w = frame.shape[:2]
    img = frame
    scale = 1.0
    if detect_width and w > detect_width:
        scale = detect_width / w
        img = cv2.resize(frame, (detect_width, int(h * scale)))

    rects, weights = detector.detectMultiScale(
        img, winStride=(8, 8), padding=(8, 8), scale=1.05,
    )
    if len(rects) == 0:
        return []

    boxes = []
    scores = []
    for (x, y, bw, bh), wt in zip(rects, weights):
        wt_val = float(wt) if np.isscalar(wt) else float(wt[0])
        if wt_val < min_weight:
            continue
        boxes.append([
            int(x / scale), int(y / scale),
            int(bw / scale), int(bh / scale),
        ])
        scores.append(wt_val)

    if not boxes:
        return []

    idxs = cv2.dnn.NMSBoxes(boxes, scores, 0.0, 0.4)
    if idxs is None or len(idxs) == 0:
        return []
    keep = np.array(idxs).flatten()
    return [tuple(boxes[i]) for i in keep]


# ---------- centroid tracker ----------

class CentroidTracker:
    """Assigns stable integer IDs to detection centroids across frames.

    A detection is matched to the nearest existing object within
    `max_distance` pixels; unmatched detections register new IDs and
    objects unseen for `max_disappeared` frames are dropped.
    """

    def __init__(self, max_disappeared: int = 20, max_distance: float = 80.0):
        self.next_id = 0
        self.objects: "OrderedDict[int, tuple]" = OrderedDict()
        self.disappeared: "OrderedDict[int, int]" = OrderedDict()
        self.max_disappeared = max_disappeared
        self.max_distance = max_distance

    def _register(self, centroid):
        self.objects[self.next_id] = tuple(centroid)
        self.disappeared[self.next_id] = 0
        self.next_id += 1

    def _deregister(self, oid):
        self.objects.pop(oid, None)
        self.disappeared.pop(oid, None)

    def update(self, boxes):
        """Step the tracker with this frame's boxes; return id->centroid."""
        if len(boxes) == 0:
            for oid in list(self.disappeared.keys()):
                self.disappeared[oid] += 1
                if self.disappeared[oid] > self.max_disappeared:
                    self._deregister(oid)
            return self.objects

        input_centroids = np.array(
            [(x + w / 2.0, y + h / 2.0) for (x, y, w, h) in boxes],
            dtype=np.float32,
        )

        if len(self.objects) == 0:
            for c in input_centroids:
                self._register(c)
            return self.objects

        obj_ids = list(self.objects.keys())
        obj_centroids = np.array(list(self.objects.values()), dtype=np.float32)

        # pairwise distance: rows = existing objects, cols = new detections
        D = np.linalg.norm(
            obj_centroids[:, None, :] - input_centroids[None, :, :], axis=2,
        )

        rows = D.min(axis=1).argsort()
        cols = D.argmin(axis=1)[rows]

        used_rows, used_cols = set(), set()
        for r, c in zip(rows, cols):
            if r in used_rows or c in used_cols:
                continue
            if D[r, c] > self.max_distance:
                continue
            oid = obj_ids[r]
            self.objects[oid] = tuple(input_centroids[c])
            self.disappeared[oid] = 0
            used_rows.add(r)
            used_cols.add(c)

        for r in set(range(D.shape[0])) - used_rows:
            oid = obj_ids[r]
            self.disappeared[oid] += 1
            if self.disappeared[oid] > self.max_disappeared:
                self._deregister(oid)

        for c in set(range(D.shape[1])) - used_cols:
            self._register(input_centroids[c])

        return self.objects


# ---------- crossing counter ----------

class CrossingCounter:
    """Count tracked centroids crossing a vertical line.

    `left_count` increments when an object moves R -> L across the line
    (i.e. ends up on the left side); `right_count` is the opposite.
    """

    def __init__(self, line_x: int):
        self.line_x = line_x
        self.left_count = 0
        self.right_count = 0
        self.last_side: "dict[int, str]" = {}

    def update(self, objects):
        for oid, (cx, _cy) in objects.items():
            side = "L" if cx < self.line_x else "R"
            prev = self.last_side.get(oid)
            if prev is None:
                self.last_side[oid] = side
                continue
            if prev != side:
                if side == "L":
                    self.left_count += 1
                else:
                    self.right_count += 1
                self.last_side[oid] = side

        live = set(objects.keys())
        for oid in list(self.last_side.keys()):
            if oid not in live:
                self.last_side.pop(oid, None)

    def side_split(self, objects):
        """Return (left_now, right_now): count of currently-tracked objects
        with their centroid on each side of the line."""
        left = right = 0
        for _oid, (cx, _cy) in objects.items():
            if cx < self.line_x:
                left += 1
            else:
                right += 1
        return left, right
