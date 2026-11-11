"""Speed test for the parallel slot count.

Parses the same pages of one PDF once per slot count, records inference time,
GPU load and VRAM, and writes progress to a JSON file the start page reads.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

BIN = Path(__file__).resolve().parent
PY_FLAGS = ["-s", "-E", "-X", "utf8"]
INFER = re.compile(r"pages=(\d+), cost=([\d.]+)s")


class GpuSampler:
    """Reads nvidia-smi twice a second; silently does nothing on other GPUs."""

    def __init__(self) -> None:
        self.samples: list[tuple[int, int]] = []
        self.process: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        exe = shutil.which("nvidia-smi")
        if not exe:
            return
        try:
            self.process = subprocess.Popen(
                [exe, "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits", "-lms", "500"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            )
        except OSError:
            return
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        for line in self.process.stdout:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                self.samples.append((int(parts[0]), int(parts[1])))

    def stop(self) -> dict:
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if not self.samples:
            return {}
        first_busy = next((i for i, (util, _) in enumerate(self.samples) if util >= 10), 0)
        busy = [util for util, _ in self.samples[first_busy:]] or [0]
        return {
            "gpu_util_avg": round(sum(busy) / len(busy)),
            "gpu_util_max": max(busy),
            "vram_base_mib": self.samples[0][1],
            "vram_peak_mib": max(mem for _, mem in self.samples),
        }


def page_count(pdf: Path) -> int:
    import pypdfium2

    document = pypdfium2.PdfDocument(str(pdf))
    try:
        return len(document)
    finally:
        document.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--pages", type=int, default=20)
    parser.add_argument("--levels", default="4,8,16")
    parser.add_argument("--tier", default="standard")
    parser.add_argument("--out", required=True)
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args()

    pdf = Path(args.pdf)
    out = Path(args.out)
    work = Path(args.workdir)
    work.mkdir(parents=True, exist_ok=True)
    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    report: dict = {"pdf": str(pdf), "tier": args.tier, "levels": levels, "runs": [], "done": False, "started": time.time()}

    def save() -> None:
        temp = out.with_suffix(".tmp")
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        temp.replace(out)

    try:
        total = page_count(pdf)
        pages = min(args.pages, total)
        report["pages"] = pages
    except Exception as exc:
        report.update(done=True, error=f"无法读取 PDF：{exc}")
        save()
        return
    save()

    # Unrecorded warm-up so the first measured level does not pay for cold file caches.
    report["warming"] = True
    save()
    warm_env = os.environ.copy()
    warm_env.pop("MU_LLAMA_PARALLEL", None)
    try:
        with open(work / "bench-warmup.log", "wb") as log:
            subprocess.run(
                [sys.executable, *PY_FLAGS, str(BIN / "server_boot.py"), "parse", str(pdf), "-o", str(work / "bench-warmup.md"),
                 "--tier", args.tier, "--pages", f"1-{min(2, pages)}"],
                env=warm_env, stdout=log, stderr=subprocess.STDOUT, timeout=1800,
            )
    except Exception:
        pass
    report["warming"] = False
    save()

    for level in levels:
        run: dict = {"parallel": level, "status": "running"}
        report["runs"].append(run)
        save()
        log_path = work / f"bench-{level}.log"
        md_path = work / f"bench-{level}.md"
        env = os.environ.copy()
        env["MU_LLAMA_PARALLEL"] = str(level)
        command = [
            sys.executable, *PY_FLAGS, str(BIN / "server_boot.py"), "parse", str(pdf),
            "-o", str(md_path), "--tier", args.tier, "--pages", f"1-{pages}",
        ]
        sampler = GpuSampler()
        sampler.start()
        began = time.monotonic()
        try:
            with open(log_path, "wb") as log:
                code = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=3600).returncode
        except subprocess.TimeoutExpired:
            code = -1
        wall = time.monotonic() - began
        run.update(sampler.stop())
        text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
        hits = INFER.findall(text)
        infer = sum(float(cost) for _, cost in hits)
        done_pages = sum(int(count) for count, _ in hits)
        run["wall_s"] = round(wall, 1)
        run["applied"] = f"并行槽位 = {level}" in text
        if code == 0 and md_path.exists() and infer > 0:
            run.update(status="ok", infer_s=round(infer, 1), pages=done_pages, pages_per_s=round(done_pages / infer, 3))
        else:
            tail = " | ".join(line.strip() for line in text.replace("\r", "\n").splitlines()[-6:] if line.strip())
            run.update(status="failed", error=f"退出码 {code}：{tail[-400:]}")
        save()

    good = [r for r in report["runs"] if r["status"] == "ok"]
    if good:
        report["best"] = max(good, key=lambda r: r["pages_per_s"])["parallel"]
    report["done"] = True
    report["finished"] = time.time()
    save()
    for leftover in work.glob("bench-*.md"):  # keep the logs, drop the parsed output
        leftover.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
