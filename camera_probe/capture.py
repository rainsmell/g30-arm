import subprocess, os, sys, glob

FFMPEG = r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"
ALT = r"@device_pnp_\\?\usb#vid_0c45&pid_636b&mi_00#6&315a9f63&0&0000#{65e8773d-8f56-11d0-a3b9-00a0c9223196}\global"

outdir = os.path.join(os.path.dirname(__file__), "frames")
os.makedirs(outdir, exist_ok=True)
for f in glob.glob(os.path.join(outdir, "*.png")):
    os.remove(f)

# try MJPEG 640x480 and YUYV 640x480
runs = [
    ("mjpeg", ["-vcodec", "mjpeg"]),
    ("yuyv", []),
]
for tag, vc in runs:
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "info",
           "-f", "dshow", "-video_size", "640x480", "-framerate", "30"] + vc + \
          ["-i", "video=" + ALT, "-frames:v", "10", "-y",
           os.path.join(outdir, tag + "_%03d.png")]
    print("=" * 30, tag)
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(p.stderr[-2500:])

files = sorted(glob.glob(os.path.join(outdir, "*.png")))
print("produced:", [os.path.basename(f) for f in files])
try:
    import numpy as np
    from PIL import Image
    for f in files:
        a = np.asarray(Image.open(f).convert("L"), dtype=np.float32)
        print(f"{os.path.basename(f):20s} mean={a.mean():7.2f} max={a.max():5.0f} std={a.std():7.2f}")
except Exception as e:
    print("stats failed:", e)
