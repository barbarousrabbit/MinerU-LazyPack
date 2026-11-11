"""MinerU Portable web page.

Official MinerU single-file page plus a batch tab, served by one Gradio app.
MinerU's own source is not modified: this script only imports it. If composing
the page fails (for example after upgrading MinerU), it falls back to the
official page without the batch tab.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import zipfile
from pathlib import Path

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
TIER_LABELS = {
    "flash": "flash（极速，只抽文字层）",
    "basic": "basic（小模型 OCR，可纯 CPU）",
    "standard": "standard（高，推荐，需显卡）",
    "advanced": "advanced（最高质量，最慢）",
}
OUT_SINGLE = "单个 .md（图片内嵌）"
OUT_FOLDER = "文件夹（md + 图片 + JSON）"

BATCH_CSS = r"""
/* ============ MinerU Portable skin: orange on black and white, frosted panels ============ */
:root:root {
  --mu-base: #f3f3f4;
  --mu-glow: rgba(249, 115, 22, .20);
  --mu-haze: rgba(0, 0, 0, .06);
  --mu-glass: rgba(255, 255, 255, .56);
  --mu-glass-strong: rgba(255, 255, 255, .76);
  --mu-edge: rgba(255, 255, 255, .95);
  --mu-hairline: rgba(0, 0, 0, .09);
  --mu-shadow: 0 22px 44px -26px rgba(0, 0, 0, .30), 0 1px 2px rgba(0, 0, 0, .05);
  --mu-fg: #101012;
  --mu-muted: #5d5d66;
  --mu-title: #2b2b31;
  --mu-accent: #f26a12;
  --mu-accent-deep: #d9530a;
  --mu-accent-soft: rgba(242, 106, 18, .12);
  --mu-chip: #141416;
  --mu-chip-fg: #ffffff;
  --mu-fill-1: rgba(255, 255, 255, .66);
  --mu-fill-2: rgba(255, 255, 255, .40);
  --mu-block: rgba(255, 255, 255, .50);
  --mu-label: rgba(255, 255, 255, .82);
  --mu-field: rgba(255, 255, 255, .80);
  --mu-field-focus: rgba(255, 255, 255, .96);
  --mu-field-edge: rgba(0, 0, 0, .13);
  --mu-check: #ffffff;
  --mu-check-edge: rgba(0, 0, 0, .28);
  --mu-option: rgba(255, 255, 255, .62);
  --mu-option-hover: rgba(255, 255, 255, .94);
  --mu-code: rgba(0, 0, 0, .05);
  --mu-row-a: rgba(255, 255, 255, .34);
  --mu-row-b: rgba(255, 255, 255, .64);
  --mu-btn: linear-gradient(180deg, #fb8a3c, #ee6410);
  --mu-btn-hover: linear-gradient(180deg, #f97a24, #d9530a);
  --mu-btn-edge: rgba(180, 70, 0, .35);
  --mu-btn-fg: #ffffff;
  --mu-btn-shadow: 0 8px 18px -10px rgba(242, 106, 18, .8), inset 0 1px 0 rgba(255, 255, 255, .35);
  --mu-btn2: rgba(255, 255, 255, .72);
  --mu-btn2-hover: #ffffff;
  --mu-btn2-edge: rgba(0, 0, 0, .13);
  --mu-btn2-edge-hover: rgba(0, 0, 0, .30);
  --mu-display: "Segoe UI Variable Display", "Segoe UI", "HarmonyOS Sans SC", "MiSans", "Microsoft YaHei UI", "PingFang SC", sans-serif;
}
:root.dark:root, :root:root .dark {
  --mu-base: #08080a;
  --mu-glow: rgba(249, 115, 22, .21);
  --mu-haze: rgba(255, 255, 255, .05);
  --mu-glass: rgba(255, 255, 255, .055);
  --mu-glass-strong: rgba(28, 28, 32, .62);
  --mu-edge: rgba(255, 255, 255, .11);
  --mu-hairline: rgba(255, 255, 255, .10);
  --mu-shadow: 0 26px 60px -28px rgba(0, 0, 0, .9), inset 0 1px 0 rgba(255, 255, 255, .05);
  --mu-fg: #f4f4f5;
  --mu-muted: #a3a3ad;
  --mu-title: #e4e4e7;
  --mu-accent: #ff8a3d;
  --mu-accent-deep: #f97316;
  --mu-accent-soft: rgba(255, 138, 61, .16);
  --mu-chip: rgba(255, 255, 255, .09);
  --mu-chip-fg: #ffffff;
  --mu-fill-1: rgba(22, 22, 25, .62);
  --mu-fill-2: rgba(22, 22, 25, .38);
  --mu-block: rgba(255, 255, 255, .04);
  --mu-label: rgba(32, 32, 36, .88);
  --mu-field: rgba(0, 0, 0, .34);
  --mu-field-focus: rgba(0, 0, 0, .5);
  --mu-field-edge: rgba(255, 255, 255, .14);
  --mu-check: rgba(0, 0, 0, .4);
  --mu-check-edge: rgba(255, 255, 255, .30);
  --mu-option: rgba(255, 255, 255, .05);
  --mu-option-hover: rgba(255, 255, 255, .11);
  --mu-code: rgba(255, 255, 255, .06);
  --mu-row-a: rgba(255, 255, 255, .025);
  --mu-row-b: rgba(255, 255, 255, .065);
  --mu-btn: linear-gradient(180deg, #ff8f45, #ef6a14);
  --mu-btn-hover: linear-gradient(180deg, #ffa05e, #f97316);
  --mu-btn-edge: rgba(255, 170, 110, .35);
  --mu-btn-fg: #170b02;
  --mu-btn-shadow: 0 10px 24px -12px rgba(249, 115, 22, .85), inset 0 1px 0 rgba(255, 255, 255, .3);
  --mu-btn2: rgba(255, 255, 255, .07);
  --mu-btn2-hover: rgba(255, 255, 255, .14);
  --mu-btn2-edge: rgba(255, 255, 255, .14);
  --mu-btn2-edge-hover: rgba(255, 255, 255, .32);
}
/* Map the page framework's variables onto the tokens. Declared for both themes on purpose:
   dark values live on <body>, so anything set only on <html> would be shadowed there. */
:root:root, :root.dark:root, :root:root .dark {
  --body-background-fill: transparent;
  --body-text-color: var(--mu-fg);
  --body-text-color-subdued: var(--mu-muted);
  --background-fill-primary: var(--mu-fill-1);
  --background-fill-secondary: var(--mu-fill-2);
  --border-color-primary: var(--mu-hairline);
  --border-color-accent: var(--mu-accent);
  --border-color-accent-subdued: var(--mu-accent-soft);
  --color-accent: var(--mu-accent);
  --color-accent-soft: var(--mu-accent-soft);
  --link-text-color: var(--mu-accent-deep);
  --link-text-color-hover: var(--mu-accent);
  --link-text-color-active: var(--mu-accent-deep);
  --link-text-color-visited: var(--mu-accent-deep);
  --block-background-fill: var(--mu-block);
  --block-border-color: var(--mu-hairline);
  --block-label-background-fill: var(--mu-label);
  --block-label-border-color: var(--mu-hairline);
  --block-label-text-color: var(--mu-muted);
  --block-title-text-color: var(--mu-title);
  --block-info-text-color: var(--mu-muted);
  --block-radius: 14px;
  --container-radius: 14px;
  --panel-background-fill: var(--mu-fill-2);
  --panel-border-color: var(--mu-hairline);
  --input-background-fill: var(--mu-field);
  --input-background-fill-focus: var(--mu-field-focus);
  --input-background-fill-hover: var(--mu-field);
  --input-border-color: var(--mu-field-edge);
  --input-border-color-hover: var(--mu-field-edge);
  --input-border-color-focus: var(--mu-accent);
  --input-shadow-focus: 0 0 0 3px var(--mu-accent-soft);
  --input-radius: 10px;
  --checkbox-background-color: var(--mu-check);
  --checkbox-background-color-selected: var(--mu-accent);
  --checkbox-border-color: var(--mu-check-edge);
  --checkbox-border-color-hover: var(--mu-accent);
  --checkbox-border-color-focus: var(--mu-accent);
  --checkbox-border-color-selected: var(--mu-accent);
  --checkbox-label-background-fill: var(--mu-option);
  --checkbox-label-background-fill-hover: var(--mu-option-hover);
  --checkbox-label-background-fill-selected: var(--mu-option);
  --checkbox-label-border-color: var(--mu-hairline);
  --checkbox-label-text-color: var(--mu-fg);
  --checkbox-border-radius: 5px;
  --code-background-fill: var(--mu-code);
  --table-border-color: var(--mu-hairline);
  --table-even-background-fill: var(--mu-row-a);
  --table-odd-background-fill: var(--mu-row-b);
  --table-text-color: var(--mu-fg);
  --table-row-focus: var(--mu-accent-soft);
  --table-radius: 12px;
  --slider-color: var(--mu-accent);
  --loader-color: var(--mu-accent);
  --button-large-radius: 12px;
  --button-medium-radius: 10px;
  --button-small-radius: 9px;
  --button-primary-background-fill: var(--mu-btn);
  --button-primary-background-fill-hover: var(--mu-btn-hover);
  --button-primary-border-color: var(--mu-btn-edge);
  --button-primary-border-color-hover: var(--mu-btn-edge);
  --button-primary-text-color: var(--mu-btn-fg);
  --button-primary-text-color-hover: var(--mu-btn-fg);
  --button-primary-shadow: var(--mu-btn-shadow);
  --button-primary-shadow-hover: var(--mu-btn-shadow);
  --button-primary-shadow-active: none;
  --button-secondary-background-fill: var(--mu-btn2);
  --button-secondary-background-fill-hover: var(--mu-btn2-hover);
  --button-secondary-border-color: var(--mu-btn2-edge);
  --button-secondary-border-color-hover: var(--mu-btn2-edge-hover);
  --button-secondary-text-color: var(--mu-fg);
  --button-secondary-text-color-hover: var(--mu-fg);
  --button-secondary-shadow: none;
  --button-secondary-shadow-hover: none;
  --button-secondary-shadow-active: none;
  --button-cancel-shadow: none;
  --error-background-fill: var(--mu-fill-1);
}

/* Longhands only: the page's CSS loader drops a var() shorthand that is followed by a longhand. */
body {
  background-color: var(--mu-base) !important;
  background-image:
    radial-gradient(1100px 620px at 88% -12%, var(--mu-glow), transparent 62%),
    radial-gradient(760px 520px at -8% 38%, var(--mu-haze), transparent 64%),
    radial-gradient(900px 600px at 60% 118%, var(--mu-glow), transparent 66%) !important;
  background-repeat: no-repeat !important;
  background-attachment: fixed !important;
}
gradio-app, .gradio-container { background: transparent !important; }

/* MinerU's own panel variables */
.gradio-container.gradio-container {
  --mineru-accent: var(--mu-accent);
  --mineru-panel: var(--mu-glass);
  --mineru-panel-border: var(--mu-edge);
  --mineru-status-panel-bg: var(--background-fill-primary);
  --mineru-status-latest-border: var(--mu-hairline);
}
.mineru-control-column, .mineru-preview-pane, .mineru-markdown-pane, .mu-panel {
  background: var(--mu-glass) !important;
  border: 1px solid var(--mu-edge) !important;
  border-radius: 18px !important;
  box-shadow: var(--mu-shadow);
  -webkit-backdrop-filter: blur(22px) saturate(150%);
  backdrop-filter: blur(22px) saturate(150%);
}
.mu-panel { padding: 14px !important; }
.form { background: transparent !important; border-color: var(--mu-hairline) !important; }
.mineru-rendered-html-frame { border-radius: 12px; }

/* header: frosted slab, black or white type, solid pills */
.mineru-header-html { position: relative; z-index: 30; }
.mineru-demo-header {
  --mu-hero-bg: var(--mu-glass-strong);
  --mu-hero-fg: var(--mu-fg);
  --mineru-header-button-bg: var(--mu-chip) !important;
  border: 1px solid var(--mu-edge);
  border-radius: 22px !important;
  box-shadow: var(--mu-shadow);
  padding: 34px 24px 30px !important;
  color: var(--mu-fg) !important;
  -webkit-backdrop-filter: blur(26px) saturate(150%);
  backdrop-filter: blur(26px) saturate(150%);
}
.mineru-demo-header.mu-hero h1 {
  font-family: var(--mu-display) !important;
  font-size: clamp(30px, 4vw, 42px) !important;
  letter-spacing: -.012em;
}
.mineru-demo-header.mu-hero .link-block .external-link { color: var(--mu-chip-fg); border-color: var(--mu-hairline); }
.mineru-demo-header.mu-hero .external-link:hover,
.mineru-demo-header.mu-hero .external-link[aria-expanded="true"] { background: var(--mu-accent-deep) !important; color: #fff; }
.mineru-demo-header.mu-hero .mineru-header-popover {
  background: var(--mu-glass-strong); border-color: var(--mu-edge); box-shadow: var(--mu-shadow);
  -webkit-backdrop-filter: blur(22px); backdrop-filter: blur(22px);
}
.mineru-demo-header.mu-hero .mineru-header-popover .mineru-header-popover-link { color: var(--mu-fg); }
.mineru-demo-header.mu-hero .mineru-header-popover-link:hover { background: var(--mu-accent-soft); }

/* tier capsule and page range: keep the colour, move it into the orange family */
.mineru-tier-capsule.mineru-tier-capsule {
  --tier-accent: #ee6410; --tier-bg: rgba(255, 255, 255, .72); --tier-text: #17171a; --tier-muted: #6b6b74;
  --tier-border: var(--mu-hairline); --tier-track: rgba(0, 0, 0, .09); --tier-dot: rgba(0, 0, 0, .28); --tier-hover: rgba(242, 106, 18, .10);
}
.dark .mineru-tier-capsule.mineru-tier-capsule {
  --tier-accent: #ff8a3d; --tier-bg: rgba(255, 255, 255, .06); --tier-text: #f4f4f5; --tier-muted: #a3a3ad;
  --tier-border: var(--mu-hairline); --tier-track: rgba(255, 255, 255, .13); --tier-dot: rgba(255, 255, 255, .34); --tier-hover: rgba(255, 138, 61, .14);
}
.mineru-tier-capsule.mineru-tier-capsule[data-tier="advanced"] { --tier-accent: #d6360a; }
.dark .mineru-tier-capsule.mineru-tier-capsule[data-tier="advanced"] { --tier-accent: #ffb04a; }
.mineru-tier-capsule.mineru-tier-capsule[data-tier="advanced"] .mineru-tier-fill { background: linear-gradient(105deg, #f7a531, #f26a12 52%, #d6360a); }
.mineru-tier-device-tooltip.mineru-tier-device-tooltip { background: #17171a; color: #ffffff; }
.dark .mineru-tier-device-tooltip.mineru-tier-device-tooltip { background: #f4f4f5; color: #17171a; }
.mineru-kit-page-range.mineru-kit-page-range {
  --page-accent: #ee6410; --page-bg: rgba(255, 255, 255, .72); --page-text: #17171a; --page-muted: #6b6b74;
  --page-border: var(--mu-hairline); --page-track: rgba(0, 0, 0, .09); --page-hover: rgba(242, 106, 18, .10);
}
.dark .mineru-kit-page-range.mineru-kit-page-range {
  --page-accent: #ff8a3d; --page-bg: rgba(255, 255, 255, .06); --page-text: #f4f4f5; --page-muted: #a3a3ad;
  --page-border: var(--mu-hairline); --page-track: rgba(255, 255, 255, .13); --page-hover: rgba(255, 138, 61, .14);
}

/* mode bar */
.mu-single-tab .mineru-header-html { display: none !important; }
.mu-mode-bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; justify-content: space-between; padding: 2px 4px; font-size: 13px; color: var(--mu-muted); }
.mu-mode-bar .mu-chips, .mu-mode-bar .mu-tools { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.mu-mode-bar .mu-chip { border: 1px solid var(--mu-hairline); background: var(--mu-glass); border-radius: 999px; padding: 2px 12px; color: var(--mu-fg); }
.mu-mode-bar .mu-chip.mu-mode { border-color: transparent; background: var(--mu-accent); color: #fff; font-weight: 600; }
.mu-mode-bar a { text-decoration: none; color: var(--mu-fg); }
.mu-mode-bar .mu-switch { border: 1px solid var(--mu-hairline); border-radius: 999px; padding: 2px 12px; background: var(--mu-glass); }
.mu-mode-bar .mu-switch:hover { border-color: var(--mu-accent); color: var(--mu-accent-deep); }
.mu-theme { display: inline-flex; border: 1px solid var(--mu-hairline); border-radius: 999px; padding: 2px; background: var(--mu-glass); }
.mu-theme a { padding: 1px 11px; border-radius: 999px; color: var(--mu-muted); }
.mu-theme a.on { background: var(--mu-fg); color: var(--mu-base); }
.mu-theme a:focus-visible, .mu-mode-bar .mu-switch:focus-visible { outline: 2px solid var(--mu-accent); outline-offset: 2px; }

.mu-batch-hint { color: var(--mu-muted); font-size: 13px; line-height: 1.6; }
.mu-batch-status { font-size: 15px; }
footer { opacity: .75; }
"""


PACK_REPO = "https://github.com/barbarousrabbit/MinerU-LazyPack"
PACK_BADGE = "https://img.shields.io/github/stars/barbarousrabbit/MinerU-LazyPack.svg"
PACK_HOME = "https://github.com/barbarousrabbit"
ENGINE_REPO = "https://github.com/opendatalab/MinerU"
ENGINE_BADGE = "https://img.shields.io/github/stars/opendatalab/MinerU.svg"
ENGINE_HOME = "https://mineru.net/?source=online"
SUBTITLE_TEXT = (
    "MinerU Lazy Pack: local document parsing that works right after unzipping. Double-click to start, "
    "the graphics card is detected automatically, with single-file and batch parsing and LAN access.",
    "MinerU 懒人包：解压即用的本地文档解析。双击启动，自动识别显卡，支持单文件、批量处理和局域网访问，文件不上传。",
)
SUPPORT_TEXT = (
    "Packed and maintained by barbarousrabbit, built on the open-source MinerU engine. "
    "If it helps, a star is welcome.",
    "由 barbarousrabbit 整理维护，基于开源 MinerU 解析引擎。觉得好用，欢迎点亮 ⭐️",
)
MEMORY_MARKERS = ("parse_oom", "out of memory", "outofmemory", "outofdevicememory", "out_of_device_memory",
                  "vram", "显存", "failed to allocate", "bad_alloc", "bad allocation", "device memory",
                  "memory exhausted")


def looks_like_memory_error(error: BaseException) -> bool:
    text = f"{getattr(error, 'code', '')} {error}".lower()
    return any(marker in text for marker in MEMORY_MARKERS) or bool(re.search(r"\boom\b", text))


class VramProbe:
    """Free-memory reading for NVIDIA cards through nvidia-smi. Other cards report nothing,
    and the guard then relies on its fixed limits."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._at = 0.0
        self._value: tuple[int, int] | None = None
        self._exe = shutil.which("nvidia-smi")

    def read(self) -> tuple[int, int] | None:
        """(used MiB, total MiB) of the largest card, cached for three seconds."""
        fake = os.environ.get("MU_FAKE_VRAM")  # test hook: file holding "used,total"
        if fake:
            try:
                used, total = (int(x) for x in Path(fake).read_text().strip().split(","))
                return used, total
            except Exception:
                return None
        if not self._exe:
            return None
        with self._lock:
            if time.monotonic() - self._at < 3:
                return self._value
            value = None
            try:
                done = subprocess.run(
                    [self._exe, "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=8,
                )
                rows = []
                for line in done.stdout.splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                        rows.append((int(parts[0]), int(parts[1])))
                if rows:
                    value = max(rows, key=lambda row: row[1])
            except Exception:
                value = None
            self._at, self._value = time.monotonic(), value
            return value


class Gate:
    """One door for every parse job, whichever tab or device it comes from.

    At most `limit` jobs run together. A second job is only let in while the card
    still has `reserve_mib` free; otherwise it waits in line. One job can always run.
    """

    def __init__(self, limit: int, probe: VramProbe | None, reserve_mib: int) -> None:
        self.limit = max(1, limit)
        self.probe = probe
        self.reserve_mib = reserve_mib
        self.active = 0
        self.peak = 0
        self.notice = ""
        self._changed = asyncio.Condition()

    async def _headroom(self) -> bool:
        if self.probe is None:
            return True
        reading = await asyncio.to_thread(self.probe.read)
        if reading is None:
            return True
        used, total = reading
        return total - used >= self.reserve_mib

    async def _wait(self) -> None:
        try:
            await asyncio.wait_for(self._changed.wait(), timeout=3)
        except asyncio.TimeoutError:
            pass

    async def enter(self, on_wait) -> None:
        told = False
        async with self._changed:
            while self.active > 0 and not (self.active < self.limit and await self._headroom()):
                if not told:
                    on_wait()
                    told = True
                await self._wait()
            self.active += 1
            self.peak = max(self.peak, self.active)

    async def leave(self) -> None:
        async with self._changed:
            self.active -= 1
            self._changed.notify_all()
            if os.environ.get("MU_GUARD_DEBUG"):
                print(f"[guard] job done, running={self.active} peak={self.peak} limit={self.limit}", flush=True)

    def degrade(self, why: str) -> None:
        self.limit = 1
        self.notice = why
        print(f"[显存保护] {why}", flush=True)


class GuardedClient:
    """Wraps MinerU's client so every job passes the gate, and a crashed or
    memory-starved engine is restarted in the conservative configuration once."""

    def __init__(self, engine, gate: Gate) -> None:
        self.engine = engine
        self.gate = gate

    @property
    def capabilities(self):
        return self.engine.client.capabilities

    async def read_runtime_device(self):
        return await self.engine.client.read_runtime_device()

    async def parse_file(self, path, *, tier, page_range, ocr_mode="auto", status_callback=None):
        from mineru.kit.gradio.status import STATUS_QUEUED_LOCALLY

        emit = status_callback or (lambda _message: None)

        async def attempt():
            return await self.engine.client.parse_file(
                path, tier=tier, page_range=page_range, ocr_mode=ocr_mode, status_callback=status_callback
            )

        await self.gate.enter(lambda: emit(STATUS_QUEUED_LOCALLY))
        try:
            generation = self.engine.generation
            try:
                if not self.engine.alive():
                    raise RuntimeError("解析引擎没有在运行")
                return await attempt()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Another job may already have restarted the engine under this one.
                replaced = self.engine.generation != generation
                crashed = not self.engine.alive()
                if not (replaced or crashed or looks_like_memory_error(error)):
                    raise
                if not replaced:
                    cause = "解析引擎意外退出" if crashed else "显存不足"
                    self.gate.degrade(f"{cause}，已自动改用保守配置重新启动引擎（默认并行，逐个处理）。")
                    await self.engine.restart_safe()
                return await attempt()  # one retry; a second failure is reported to the user
        finally:
            await self.gate.leave()


def _is_local(request) -> bool:
    client = getattr(request, "client", None)
    host = getattr(client, "host", None)
    return host in LOCAL_HOSTS


def _collect(folder: Path, recursive: bool, suffixes: set[str], skip_dir: Path | None) -> list[Path]:
    walker = folder.rglob("*") if recursive else folder.glob("*")
    found = []
    for path in walker:
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        if path.name.startswith("~$"):
            continue
        if skip_dir is not None and skip_dir in path.resolve().parents:
            continue
        found.append(path)
    return sorted(found, key=lambda p: str(p).lower())


def _zip_dir(directory: Path, target: Path) -> Path:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path != target:
                archive.write(path, path.relative_to(directory))
    return target


def build_batch_tab(gr, client, capabilities, *, output_root: Path, lan: bool, workers: int = 1, default_tier: str | None = None, notice=lambda: ""):
    """批量处理页签：递归找文件，逐个解析，输出目录结构与源目录对齐。"""
    from mineru.filetypes import PARSEABLE_EXTENSIONS
    from mineru.parser.writer import FileBasedDataWriter
    from mineru.types import TIERS, select_default_task_tier

    suffixes = {f".{ext}" for ext in PARSEABLE_EXTENSIONS}
    tiers = [tier for tier in TIERS if tier in capabilities.tiers]
    if default_tier not in tiers:
        default_tier = select_default_task_tier(capabilities.tiers) or tiers[0]
    workers = max(1, int(workers))
    batch_root = output_root / "batch"

    with gr.Row():
        with gr.Column(scale=2, min_width=300, elem_classes=["mu-panel"]):
            folder_box = gr.Textbox(
                label="文件夹路径（本机）",
                placeholder=r"例如 D:\资料\论文，直接粘贴路径",
                info="会自动找出里面所有 PDF、图片、Word、PPT、Excel 等文件",
            )
            upload_box = gr.File(
                label="或者上传多个文件",
                file_count="multiple",
                file_types=sorted(suffixes),
                type="filepath",
                height=140,
            )
            tier_box = gr.Radio(
                choices=[(TIER_LABELS.get(t, t), t) for t in tiers],
                value=default_tier,
                label="解析等级",
            )
            with gr.Row():
                recursive_box = gr.Checkbox(value=True, label="包含子文件夹")
                skip_box = gr.Checkbox(value=True, label="跳过已有结果")
            format_box = gr.Radio(choices=[OUT_SINGLE, OUT_FOLDER], value=OUT_SINGLE, label="输出形式")
            workers_box = gr.Slider(
                minimum=1, maximum=max(workers, 2), step=1, value=workers, label="同时处理文件数",
                info="多个文件并行解析，显卡占用更高", visible=workers > 1,
            )
            outdir_box = gr.Textbox(
                label="输出目录（可留空）",
                placeholder="留空则输出到 output\\batch\\源文件夹名",
            )
            with gr.Row():
                start_btn = gr.Button("开始批量转换", variant="primary", min_width=120)
                stop_btn = gr.Button("停止", min_width=90)
            hint = "输出目录的层级与源文件夹保持一致，每个文件对应一个同名结果。"
            if lan:
                hint += "<br>当前为局域网模式：文件夹路径和自定义输出目录只能在本机浏览器使用，其他设备请用上传。"
            gr.HTML(f'<div class="mu-batch-hint">{hint}</div>')
        with gr.Column(scale=5, min_width=420, elem_classes=["mu-panel"]):
            status_box = gr.Markdown("等待任务。选择文件夹或上传文件后点击开始。", elem_classes=["mu-batch-status"])
            table = gr.Dataframe(
                headers=["文件", "状态", "耗时(秒)", "说明"],
                datatype=["str", "str", "str", "str"],
                value=[],
                interactive=False,
                wrap=True,
                max_height=520,
            )
            zip_box = gr.File(label="结果打包下载", visible=False, interactive=False)

    def summary(done: int, total: int, ok: int, failed: int, skipped: int, tail: str) -> str:
        return f"**进度 {done}/{total}**　成功 {ok}　失败 {failed}　跳过 {skipped}　{tail}"

    async def run_batch(folder, uploads, tier, recursive, skip_existing, out_format, out_dir, parallel_files, request: gr.Request):
        local = _is_local(request)
        folder = (folder or "").strip().strip('"')
        out_dir = (out_dir or "").strip().strip('"')
        uploads = [Path(p) for p in (uploads or [])]
        hide_zip = gr.update(value=None, visible=False)

        if folder and not local:
            yield "文件夹路径只能在运行 MinerU 的这台电脑上使用，其他设备请改用上传。", [], hide_zip
            return
        if out_dir and not local:
            yield "自定义输出目录只能在运行 MinerU 的这台电脑上使用。", [], hide_zip
            return

        jobs: list[tuple[Path, Path]] = []  # (source, relative path used for output)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if folder:
            source_dir = Path(folder).expanduser()
            if not source_dir.is_dir():
                yield f"找不到文件夹：{folder}", [], hide_zip
                return
            source_dir = source_dir.resolve()
            target_dir = Path(out_dir).expanduser().resolve() if out_dir else batch_root / (source_dir.name or "root")
            for path in _collect(source_dir, bool(recursive), suffixes, target_dir):
                jobs.append((path, path.relative_to(source_dir)))
        else:
            target_dir = Path(out_dir).expanduser().resolve() if out_dir else batch_root / f"upload-{stamp}"
        for path in uploads:
            if path.suffix.lower() in suffixes:
                jobs.append((path, Path(path.name)))
        if not jobs:
            yield "没有找到可解析的文件。请填写文件夹路径或上传文件。", [], hide_zip
            return

        target_dir.mkdir(parents=True, exist_ok=True)
        rows = [[str(rel), "排队", "", ""] for _, rel in jobs]
        total = len(jobs)
        count = {"ok": 0, "failed": 0, "skipped": 0}
        running: dict[int, float] = {}
        limit = asyncio.Semaphore(max(1, min(workers, int(parallel_files or 1))))
        began = time.monotonic()

        def line(tail: str = "") -> str:
            done = count["ok"] + count["failed"] + count["skipped"]
            text = summary(done, total, count["ok"], count["failed"], count["skipped"], tail)
            return f"{text}\n\n{notice()}" if notice() else text

        async def one(index: int, source: Path, rel: Path) -> None:
            if out_format == OUT_SINGLE:
                dest = (target_dir / rel).with_suffix(".md")
                exists = dest.is_file()
            else:
                dest = (target_dir / rel).with_suffix("")
                exists = (dest / "markdown.md").is_file()
            if skip_existing and exists:
                count["skipped"] += 1
                rows[index][1:] = ["跳过", "", "结果已存在"]
                return
            async with limit:
                started = time.monotonic()
                running[index] = started
                rows[index][1:] = ["解析中", "0", ""]
                try:
                    result = await client.parse_file(source, tier=tier, page_range="")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if out_format == OUT_SINGLE:
                        await asyncio.to_thread(dest.write_text, result.markdown(), "utf-8")
                    else:
                        if dest.exists():
                            await asyncio.to_thread(shutil.rmtree, dest)
                        dest.mkdir(parents=True)
                        await asyncio.to_thread(result.save, FileBasedDataWriter(str(dest)))
                    count["ok"] += 1
                    cost = time.monotonic() - started
                    pages = len(result.pages)
                    rows[index][1:] = ["完成", f"{cost:.1f}", f"{pages} 页，{pages / cost:.2f} 页/秒 → {dest.name}"]
                except asyncio.CancelledError:
                    rows[index][1:] = ["已停止", f"{time.monotonic() - started:.1f}", ""]
                    raise
                except Exception as exc:  # one bad file must not stop the batch
                    count["failed"] += 1
                    rows[index][1:] = ["失败", f"{time.monotonic() - started:.1f}", str(exc)[:200]]
                finally:
                    running.pop(index, None)

        yield line(f"输出目录：`{target_dir}`"), rows, hide_zip
        pending = {asyncio.ensure_future(one(i, src, rel)) for i, (src, rel) in enumerate(jobs)}
        try:
            while pending:
                _, pending = await asyncio.wait(pending, timeout=1.0)
                now = time.monotonic()
                for index, started in list(running.items()):
                    rows[index][2] = f"{now - started:.0f}"
                names = "、".join(str(jobs[i][1]) for i in list(running)[:3])
                yield line(f"当前：{names}" if names else ""), rows, hide_zip
        except asyncio.CancelledError:
            for task in pending:
                task.cancel()
            raise

        tail = f"全部结束，用时 {time.monotonic() - began:.0f} 秒，结果在：`{target_dir}`"
        zip_update = hide_zip
        if not local or not folder:
            # uploads (or a remote browser) cannot reach the output folder, so hand back a zip
            archive = batch_root / f"mineru-batch-{stamp}.zip"
            await asyncio.to_thread(_zip_dir, target_dir, archive)
            zip_update = gr.update(value=str(archive), visible=True)
        yield line(tail), rows, zip_update

    # annotations are strings here (from __future__), so give Gradio the real class
    run_batch.__annotations__["request"] = gr.Request

    run_event = start_btn.click(
        run_batch,
        inputs=[folder_box, upload_box, tier_box, recursive_box, skip_box, format_box, outdir_box, workers_box],
        outputs=[status_box, table, zip_box],
        concurrency_limit=1,
        api_visibility="private",
    )
    stop_btn.click(
        lambda: "已停止。已完成的结果保留，再次开始时勾选“跳过已有结果”可以续跑。",
        outputs=[status_box],
        cancels=[run_event],
        api_visibility="private",
    )


HERO_GRADIENT = "background: linear-gradient(45deg, #007bff 0%, #0056b3 100%);"
HERO_WHITE = "color: #ffffff !important;"

THEME_JS = r"""
(({element, launcher}) => {
  const current = new URL(location.href).searchParams.get('__theme');
  const active = (current === 'light' || current === 'dark') ? current : 'system';
  element.querySelectorAll('a[data-theme]').forEach((link) => {
    link.classList.toggle('on', link.dataset.theme === active);
    link.addEventListener('click', () => {
      if (launcher && ['127.0.0.1', 'localhost'].includes(location.hostname)) {
        try { navigator.sendBeacon(launcher + 'api/theme?set=' + link.dataset.theme); } catch (error) {}
      }
    });
  });
})
"""


def _retheme_header(markup: str) -> str:
    """Swaps the header's inline blue banner and white type for theme variables.

    Works on the rendered copy only. If MinerU changes the markup, the header is
    returned untouched and keeps its original look.
    """
    if HERO_GRADIENT not in markup or HERO_WHITE not in markup or 'class="mineru-demo-header"' not in markup:
        return markup
    markup = markup.replace(HERO_GRADIENT, "background: var(--mu-hero-bg);")
    markup = markup.replace(HERO_WHITE, "color: var(--mu-hero-fg) !important;")
    return markup.replace('class="mineru-demo-header"', 'class="mineru-demo-header mu-hero"')


def _relink_header(markup: str) -> str:
    """Points the header's code button, star badge and homepage button at the pack's
    own pages. The model and paper menus keep pointing at the model that actually runs."""
    for engine, pack in ((ENGINE_BADGE, PACK_BADGE), (ENGINE_REPO, PACK_REPO), (ENGINE_HOME, PACK_HOME)):
        markup = markup.replace(engine, pack)
    return markup


def _mode_bar(args) -> str:
    chips = []
    if args.mode_label:
        chips.append(f'<span class="mu-chip mu-mode">{args.mode_label}模式</span>')
    if args.parallel:
        chips.append(f'<span class="mu-chip">并行 {args.parallel} 路</span>')
    if args.workers > 1:
        chips.append(f'<span class="mu-chip">批量同时 {args.workers} 个文件</span>')
    chips.append('<span class="mu-chip" title="任务统一排队；显存紧张时自动改为逐个处理；引擎崩溃会以保守配置自动重启">显存保护</span>')
    tools = [
        '<span class="mu-theme" role="group" aria-label="外观">'
        '<a data-theme="system" href="?__theme=system">跟随系统</a>'
        '<a data-theme="light" href="?__theme=light">浅色</a>'
        '<a data-theme="dark" href="?__theme=dark">深色</a></span>'
    ]
    if args.launcher_url:
        tools.append(f'<a class="mu-switch" href="{args.launcher_url}?switch=1">切换模式</a>')
    return (f'<div class="mu-mode-bar"><div class="mu-chips">{"".join(chips)}</div>'
            f'<div class="mu-tools">{"".join(tools)}</div></div>')


def launch(args) -> None:
    import gradio as gr
    from mineru.kit.gradio import app as official
    from mineru.kit.gradio import i18n as official_text
    from mineru.kit.gradio.client import GradioArtifactClient, ManagedLocalApiServer, V1ArtifactClient
    from mineru.kit.gradio.lifecycle import with_setup_loop

    output_root = Path(args.output_dir).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    lan = args.server_name not in LOCAL_HOSTS
    boot = Path(__file__).with_name("server_boot.py")

    class Server(ManagedLocalApiServer):
        """Starts the API server through server_boot.py so the slot count applies."""

        slots = 0

        def _command(self, port, upload_dir):
            command = super()._command(port, upload_dir)
            if not self.slots or not boot.is_file() or "mineru.kit.main" not in command:
                return command
            at = command.index("mineru.kit.main")
            return [command[0], "-s", "-E", "-X", "utf8", str(boot), *command[at + 1:]]

    class Engine:
        """Owns the API server process and the client talking to it."""

        def __init__(self) -> None:
            self.server = None
            self.client = None
            self.safe = False
            self.generation = 0
            self._restart = asyncio.Lock()

        def _spawn(self, slots: int, workers: int, preload: bool):
            if slots:
                os.environ["MU_LLAMA_PARALLEL"] = str(slots)
            else:
                os.environ.pop("MU_LLAMA_PARALLEL", None)
            server = Server(tier=args.tier, concurrency=max(1, workers), preload_models=preload)
            server.slots = slots
            return server, server.start()

        def start(self) -> None:
            self.server, url = self._spawn(args.parallel, args.workers, args.preload)
            primary = V1ArtifactClient(api_url=url, api_key=None)
            asyncio.run(primary.discover())
            self.client = GradioArtifactClient(primary)
            self.safe = not args.parallel and args.workers <= 1

        def alive(self) -> bool:
            process = getattr(self.server, "process", None)
            return process is not None and process.poll() is None

        async def restart_safe(self) -> None:
            """Fresh engine with MinerU's own defaults: stock slot count, one job at a time."""
            async with self._restart:
                if self.safe and self.alive():
                    return
                old = self.server
                await asyncio.to_thread(old.stop)
                server, url = await asyncio.to_thread(self._spawn, 0, 1, False)
                primary = V1ArtifactClient(api_url=url, api_key=None)
                await primary.discover()
                self.server, self.client, self.safe = server, GradioArtifactClient(primary), True
                self.generation += 1
                print("[显存保护] 引擎已用保守配置重新启动。", flush=True)

        def stop(self) -> None:
            if self.server is not None:
                self.server.stop()

    engine = Engine()
    try:
        engine.start()
        gate = Gate(args.workers, VramProbe(), args.vram_reserve_mib)
        client = GuardedClient(engine, gate)

        if args.default_tier and args.default_tier in client.capabilities.tiers and hasattr(official, "_default_tier"):
            official._default_tier = lambda _capabilities: args.default_tier
        for key, text in (("header_subtitle", SUBTITLE_TEXT), ("header_support_text", SUPPORT_TEXT)):
            if key in getattr(official_text, "MESSAGES", {}):
                official_text.MESSAGES[key] = text
        if hasattr(official, "_render_header"):
            stock_header = official._render_header
            official._render_header = lambda: _relink_header(stock_header())

        def fallback(reason: str) -> None:
            print(f"[MinerU Portable] 批量页签加载失败，改用官方页面：{reason}", flush=True)
            demo = official.build_gradio_app(
                client, client.capabilities, output_root=output_root, enable_example=False,
                runtime_device_loader=client.read_runtime_device,
            )
            official._launch_gradio_app(
                demo, server_name=args.server_name, server_port=args.server_port, output_root=output_root
            )

        try:
            single = official.build_gradio_app(
                client, client.capabilities, output_root=output_root, enable_example=False,
                runtime_device_loader=client.read_runtime_device,
            )
            kwargs = dict(single._mineru_kit_launch_kwargs)
            kwargs["css"] = (kwargs.get("css") or "") + BATCH_CSS
            with gr.Blocks(title="MinerU 4：文档提取") as demo:
                gr.HTML(
                    _retheme_header(official._render_header()),
                    elem_classes=["mineru-header-html"],
                    js_on_load=f"({official._resource_text('gradio_header.js')})({{element}})",
                    apply_default_css=False,
                )
                gr.HTML(
                    _mode_bar(args),
                    js_on_load=f"({THEME_JS})({{element, launcher: {json.dumps(args.launcher_url)}}})",
                    apply_default_css=False,
                )
                with gr.Tabs():
                    with gr.Tab("单文件处理", elem_classes=["mu-single-tab"]):
                        single.render()
                    with gr.Tab("批量处理"):
                        build_batch_tab(
                            gr, client, client.capabilities, output_root=output_root, lan=lan,
                            workers=args.workers, default_tier=args.default_tier, notice=lambda: gate.notice,
                        )
            demo.queue(default_concurrency_limit=1)
        except Exception:
            traceback.print_exc()
            fallback("见上方报错")
            return

        @with_setup_loop
        def serve() -> None:
            demo.launch(
                server_name=args.server_name,
                server_port=args.server_port,
                allowed_paths=[str(output_root)],
                inbrowser=not args.no_browser,
                **kwargs,
            )

        serve()
    finally:
        engine.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="MinerU Portable web page")
    parser.add_argument("--server-name", default="127.0.0.1")
    parser.add_argument("--server-port", type=int, default=7860)
    parser.add_argument("--output-dir", default="./output")
    parser.add_argument("--tier", default="auto")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--parallel", type=int, default=0, help="llama.cpp slots; 0 keeps MinerU's default")
    parser.add_argument("--workers", type=int, default=1, help="files parsed at the same time in the batch tab")
    parser.add_argument("--preload", action="store_true", help="load models at startup")
    parser.add_argument("--default-tier", default="")
    parser.add_argument("--mode-label", default="")
    parser.add_argument("--launcher-url", default="")
    parser.add_argument("--vram-reserve-mib", type=int, default=1500,
                        help="free VRAM a second simultaneous job needs before it may start")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    launch(args)


if __name__ == "__main__":
    main()
