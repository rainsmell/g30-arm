import cv2, numpy as np, os

outdir = os.path.join(os.path.dirname(__file__), "frames")
os.makedirs(outdir, exist_ok=True)

for idx in range(6):
    for backend, bname in [(cv2.CAP_MSMF, "MSMF"), (cv2.CAP_DSHOW, "DSHOW")]:
        cap = cv2.VideoCapture(idx, backend)
        if not cap.isOpened():
            continue
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        w = cap.get(cv2.CAP_PROP_FRAME_WIDTH); h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        stats = []
        frame_ok = None
        for k in range(20):
            ret, frame = cap.read()
            if not ret:
                continue
            g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            stats.append((g.mean(), g.max(), g.std()))
            frame_ok = frame
        cap.release()
        if stats:
            a = np.array(stats)
            print(f"idx={idx} {bname}: {int(w)}x{int(h)} frames={len(stats)} "
                  f"mean={a[:,0].mean():7.2f} max={a[:,1].max():5.0f} std={a[:,2].mean():7.2f}")
            if frame_ok is not None:
                cv2.imwrite(os.path.join(outdir, f"idx{idx}_{bname}.png"), frame_ok)
        else:
            print(f"idx={idx} {bname}: opened {int(w)}x{int(h)} but 0 frames")
