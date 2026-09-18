import traceback, sys
from pygrabber.dshow_graph import FilterGraph

def try_idx(idx):
    print(f"\n===== camera index {idx} =====")
    g = FilterGraph()
    try:
        g.add_video_input_device(idx)
    except Exception as e:
        print("add_video_input_device failed:", e); return
    dev = g.get_input_device()
    print("name:", dev.get_name())
    try:
        fmts = dev.get_formats()
        print("formats found:", len(fmts))
        for i, f in enumerate(fmts[:12]):
            print("  ", i, f)
    except Exception as e:
        print("get_formats failed:", e)
        return

    samples = []
    def cb(image):
        samples.append(image)

    try:
        g.add_sample_grabber(cb)
        g.add_null_render()
    except Exception as e:
        print("add filters failed:", e); return

    # try to force a couple of formats
    for want in range(min(len(fmts), 12)):
        samples.clear()
        try:
            dev.set_format(want)
            print(f"  set_format({want}) -> {dev.get_current_format()}")
        except Exception as e:
            print(f"  set_format({want}) failed: {e}")
            continue
        try:
            g.prepare_preview_graph()
        except Exception as e:
            print(f"  prepare_preview_graph failed for fmt {want}: {e}")
            g.remove_filters() if hasattr(g, "remove_filters") else None
            continue
        try:
            g.run()
            import time
            t0 = time.time()
            while time.time() - t0 < 3 and len(samples) < 3:
                time.sleep(0.1)
            print(f"  -> grabbed {len(samples)} samples at fmt {want}")
            g.stop()
        except Exception as e:
            print(f"  run failed for fmt {want}: {e}")
        try:
            g.stop()
        except Exception:
            pass
        if samples:
            break

for idx in (1, 0):
    try:
        try_idx(idx)
    except Exception:
        traceback.print_exc()
