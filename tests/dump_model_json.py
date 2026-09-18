"""把 webui 的模型配置导出成 JSON，供 node 端运动学测试使用。

    python tests/dump_model_json.py [输出路径]

不带参数时输出到仓库根目录的 _model.json。
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from webui import model_config  # noqa: E402


def main(argv) -> int:
    out = argv[1] if len(argv) > 1 else os.path.join(ROOT, "_model.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(model_config.model_config(), fh, ensure_ascii=False, indent=1)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
