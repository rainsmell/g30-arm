#!/usr/bin/env python
"""只读快照：读全部 6 个关节的状态与固件身份。**不发送任何写命令。**

用法::

    python examples/read_all_status.py [COM8]
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from robot_arm import RobotArm  # noqa: E402


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM8"

    with RobotArm(port, verbose=False) as arm:
        print("== 固件身份校验 ==")
        verify = arm.verify_all(raise_on_error=False)
        for key, result in verify.items():
            if result["ok"]:
                print("  %-3s OK   %s  fault=%d" % (key, result["identity"], result["fault"]))
            else:
                print("  %-3s FAIL %s" % (key, result["error"]))

        print("\n== 状态快照 ==")
        for key, status in arm.status().items():
            print("  %s: %s" % (key, status))

        print("\n== JSON ==")
        print(json.dumps(arm.status(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
