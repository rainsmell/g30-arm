import cv2, sys
cap = cv2.VideoCapture(1, cv2.CAP_MSMF)
print("opened:", cap.isOpened(), flush=True)
if cap.isOpened():
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    ok = 0
    for _ in range(30):
        r, f = cap.read()
        if r:
            ok += 1
    print("frames:", ok, flush=True)
    cap.release()
