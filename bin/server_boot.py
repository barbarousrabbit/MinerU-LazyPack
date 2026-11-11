"""Entry point used instead of `python -m mineru.kit.main` when a parallel
slot count is requested.

MinerU's llama.cpp engine decodes blocks in `n_parallel` slots (default 4) and
offers no config key for it. When MU_LLAMA_PARALLEL is set, this wrapper passes
that value to the engine at construction time, then hands over to the normal
MinerU command line. MinerU's files are not modified. If the hook point is
missing (for example after an upgrade) it prints a notice and runs stock MinerU.
"""

from __future__ import annotations

import os
import sys


def _install_parallel_hook(slots: int) -> bool:
    try:
        from mineru.model.vlm import runtime
    except Exception as exc:  # pragma: no cover - depends on installed MinerU
        print(f"[MinerU Portable] 并行设置未生效（无法导入 VLM 运行时：{exc}）", flush=True)
        return False
    singleton = getattr(runtime, "ModelSingleton", None)
    original = getattr(singleton, "_create_model", None)
    if original is None:
        print("[MinerU Portable] 并行设置未生效（此版本 MinerU 的内部结构已变化）", flush=True)
        return False

    def create_model(self, backend, *args, **kwargs):
        if backend == "llama-cpp-engine" and "n_parallel" not in kwargs:
            kwargs["n_parallel"] = slots
            print(f"[MinerU Portable] llama.cpp 并行槽位 = {slots}", flush=True)
        return original(self, backend, *args, **kwargs)

    singleton._create_model = create_model
    return True


def main() -> None:
    raw = os.environ.get("MU_LLAMA_PARALLEL", "").strip()
    if raw:
        try:
            slots = int(raw)
        except ValueError:
            slots = 0
        if 1 <= slots <= 64:
            _install_parallel_hook(slots)
    from mineru.kit.main import main as mineru_main

    sys.argv = ["mineru-kit", *sys.argv[1:]]
    mineru_main()


if __name__ == "__main__":
    main()
