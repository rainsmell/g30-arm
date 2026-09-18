import cv2, numpy as np, os, threading, time

outdir = os.path.join(os.path.dirname(__file__), "frames")
os.makedirs(outdir, exist_ok=True)

class Grab:
    def __init__(self, idx, backend, w, h, fourcc, label):
        self.label = label
        self.cap = cv2.VideoCapture(idx, backend)
        self.ok_open = self.cap.isOpened()
        if self.ok_open:
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        self.res = []; self.frame = None; self.done = False
    def run(self):
        t0 = time.time()
        while time.time() - t0 < 6 and len(self.res) < 15:
            ret, fr = self.cap.read()
            if ret and fr is not None:
                g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
                self.res.append((g.mean(), g.max(), g.std()))
                self.frame = fr
        self.done = True
    def release(self):
        try: self.cap.release()
        except Exception: pass

def probe(idx, backend, bname, w, h, fourcc):
    g = Grab(idx, backend, w, h, fourcc, "")
    if not g.ok_open:
        print(f"idx{idx} {bname} {w}x{h} {fourcc}: cannot open"); return
    th = threading.Thread(target=g.run, daemon=True); th.start()
    th.join(timeout=8)
    if g.res:
        a = np.array(g.res)
        print(f"idx{idx} {bname} {w}x{h} {fourcc}: frames={len(g.res)} mean={a[:,0].mean():.2f} std={a[:,2].mean():.2f}")
        if g.frame is not None:
            cv2.imwrite(os.path.join(outdir, f"idx{idx}_{bname}_{w}x{h}_{fourcc}.png"), g.frame)
    else:
        print(f"idx{idx} {bname} {w}x{h} {fourcc}: open ok, 0 frames (timeout)")
    g.release()

print("== camera index 1 (0C45 arm cam) ==")
for fourcc in ("MJPG", "YUY2"):
    for (w, h) in [(640,480), (1600,900), (1600,1200), (320,240), (1280,720)]:
        probe(1, cv2.CAP_MSMF, "MSMF", w, h, fourcc)
print("== camera index 0 (built-in, control) ==")
probe(0, cv2.CAP_MSMF, "MSMF", 640, 480, "MJPG")
