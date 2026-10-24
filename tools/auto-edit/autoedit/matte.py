"""Person matte (README depth look; SOP-11c: local MediaPipe selfie_multiclass_256x256).

Two jobs:
  - face boxes at title times, so front titles land under the chin and never over the face (SOP-11b rule 4, 8;
    README layout `combo_y` clear of the chin),
  - a matte video, so a title can sit behind the speaker (SOP-11b rule 1, 2: callouts behind the head).
SOP-11c T6: smooth the matte over time (weights 1 2 1), erode slightly, blur 0.8.
"""
import subprocess
import urllib.request
from pathlib import Path

import numpy as np

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_multiclass_256x256/"
             "float32/latest/selfie_multiclass_256x256.tflite")
# categories: 0 background, 1 hair, 2 body skin, 3 face skin, 4 clothes, 5 accessories


def model_path(root):
    p = Path(root) / "models" / "selfie_multiclass_256x256.tflite"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(MODEL_URL, p)
    return p


class Segmenter:
    def __init__(self, root):
        import mediapipe as mp
        from mediapipe.tasks import python as mpt
        from mediapipe.tasks.python import vision
        self.mp = mp
        opts = vision.ImageSegmenterOptions(base_options=mpt.BaseOptions(model_asset_path=str(model_path(root))),
                                            output_category_mask=False, output_confidence_masks=True)
        self.seg = vision.ImageSegmenter.create_from_options(opts)

    def masks(self, rgb):
        img = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        r = self.seg.segment(img)
        return [np.squeeze(np.asarray(c.numpy_view())) for c in r.confidence_masks]

    def close(self):
        self.seg.close()


def face_box(masks):
    """(top of head, chin, left, right) as shares of the frame, or None when nobody is in frame."""
    head = np.maximum(masks[1], masks[3])
    ys, xs = np.where(head > 0.5)
    if len(ys) < 50:
        return None
    face = masks[3] > 0.5
    fy = np.where(face.any(axis=1))[0]
    chin = (fy.max() if len(fy) else ys.max()) / head.shape[0]
    return dict(top=ys.min() / head.shape[0], chin=float(chin), left=xs.min() / head.shape[1],
                right=xs.max() / head.shape[1])


def face_boxes(seg, video, times, frame_fn):
    out = {}
    for t in times:
        try:
            m = seg.masks(frame_fn(video, t, 540))
            out[round(t, 3)] = face_box(m)
        except Exception:
            out[round(t, 3)] = None
    return out


def matte_video(seg, src, dst, size, fps):
    """Write a grey person-matte video the size of src, frame for frame (SOP-11c T6 smoothing)."""
    w, h = size
    sw, sh = (360, 640) if h > w else (640, 360)
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(src), "-vf", f"scale={sw}:{sh}", "-f", "rawvideo",
                            "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{sw}x{sh}",
                            "-r", str(fps), "-i", "-", "-vf",
                            f"tmix=frames=3:weights=1 2 1,erosion,gblur=sigma=0.8,scale={w}:{h}:flags=bicubic",
                            "-c:v", "libx264", "-crf", "12", "-preset", "veryfast", "-pix_fmt", "gray", str(dst)],
                           stdin=subprocess.PIPE)
    n = sw * sh * 3
    while True:
        buf = dec.stdout.read(n)
        if len(buf) < n:
            break
        rgb = np.frombuffer(buf, np.uint8).reshape(sh, sw, 3)
        m = seg.masks(rgb)
        person = np.clip(1.0 - m[0], 0, 1)
        enc.stdin.write((person * 255).astype(np.uint8).tobytes())
    enc.stdin.close()
    enc.wait()
    dec.wait()
    if enc.returncode:
        raise RuntimeError("matte encode failed")
