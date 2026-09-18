import cv2, numpy as np, os, glob

outdir = os.path.join(os.path.dirname(__file__), "frames")
os.makedirs(outdir, exist_ok=True)

devs = [
    ("arm_0c45", 0),
]
# enumerate indices
for i in range(6):
    cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
    if cap.isOpened():
        w = cap.get(cv2.CAP_PROP_FRAME_WIDTH); h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        print(f"index {i}: opened {int(w)}x{int(h)}")
    cap.release()

for name, idx in devs:
    for backend, bname in [(cv2.CAP_DSHOW, "DSHOW"), (cv2.CAP_MSMF, "MSMF")]:
        cap = cv2.VideoCapture(idx, backend)
        if not cap.isOpened():
            print(f"{name} {bname}: cannot open")
            continue
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        ok = 0; stats = []
        for k in range(30):
            ret, frame = cap.read()
            if not ret:
                continue
            ok += 1
            g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            stats.append((g.mean(), g.max(), g.std()))
            if k == 10:
                cv2.imwrite(os.path.join(outdir, f"{name}_{bname}.png"), frame)
        cap.release()
        if stats:
            a = np.array(stats)
            print(f"{name} {bname}: frames={ok} mean={a[:,0].mean():.2f} max={a[:,1].max():.0f} std={a[:,2].mean():.2f}")
        else:
            print(f"{name} {bname}: opened but 0 frames")
