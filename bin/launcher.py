"""MinerU Portable start page.

Scans the hardware, lets the user pick a mode (or goes straight to CPU mode
when there is no usable graphics card), starts the web UI in that mode and
sends the browser there. Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

BIN = Path(__file__).resolve().parent
ROOT = BIN.parent
LAUNCH_PORT = 7859
APP_PORT = 7860
PY_FLAGS = ["-s", "-E", "-X", "utf8"]
GIB = 1024 ** 3
WINDOWS = sys.platform == "win32"
SETTINGS = ROOT / "home" / "portable.json"
BENCH_FILE = ROOT / "home" / "bench.json"
FULL_MIN_VRAM = 11.5 * GIB      # cards sold as 12 GB report slightly less
DEFAULT_FULL_PARALLEL = 8
VRAM_MARGIN_MIB = 1000          # kept free on top of the engine's own need
VRAM_RESERVE_MIB = 1500         # a second simultaneous job waits until this much is free
CPU_ENV = {"MINERU_MODEL_ONNX_DEVICE": "cpu", "MINERU_LLAMA_CPP_BACKEND": "cpu"}

lock = threading.RLock()
state: dict = {
    "phase": "scanning",      # scanning | choose | starting | running | bench
    "message": "",
    "hw": None,
    "app": None,              # {"mode", "label", "since"}
    "lan": False,
}
app_process: subprocess.Popen | None = None
bench_process: subprocess.Popen | None = None


# ---------------------------------------------------------------- settings

def load_settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(**changes) -> None:
    data = load_settings()
    data.update(changes)
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def full_parallel() -> int:
    value = load_settings().get("full_parallel")
    return value if isinstance(value, int) and 1 <= value <= 64 else DEFAULT_FULL_PARALLEL


# ---------------------------------------------------------------- hardware

def read_hardware() -> dict:
    fake = os.environ.get("MU_FAKE_HW")  # test hook: path to a saved `hardware info --json`
    if fake:
        text = Path(fake).read_text(encoding="utf-8-sig")
    else:
        done = subprocess.run(
            [sys.executable, *PY_FLAGS, "-m", "mineru.kit.main", "hardware", "info", "--json"],
            capture_output=True, timeout=180, cwd=str(ROOT),
        )
        text = done.stdout.decode("utf-8", errors="replace")
    data, _ = json.JSONDecoder().raw_decode(text[text.index("{"):])
    return data


def summarize(data: dict) -> dict:
    """Reduce MinerU's hardware report to what the start page needs."""
    gpus = data.get("gpus") or []
    discrete = [g for g in gpus if g.get("kind") == "discrete"]
    best = max(discrete, key=lambda g: g.get("dedicated_memory_bytes") or 0, default=None)
    onnx = next((r for r in data.get("runtimes") or [] if r.get("name") == "onnx"), {}) or {}
    chosen = ((onnx.get("data") or {}).get("selected_device") or {})
    verdict = chosen.get("device")
    vram = (best or {}).get("dedicated_memory_bytes") or 0
    # MinerU's own decision wins when it made one; otherwise use its 6 GB rule.
    usable = (verdict == "gpu") if verdict in ("gpu", "cpu") else vram >= 6 * GIB
    adapter = chosen.get("adapter") or {}
    name = (best or {}).get("name") or adapter.get("name") or (gpus[0].get("name") if gpus else "")
    strong = bool(usable and best is not None and vram >= FULL_MIN_VRAM)
    cpu = data.get("cpu") or {}
    memory = data.get("memory") or {}
    if not usable:
        reason = "没有检测到可用的独立显卡" if not discrete else "显卡显存不足 6 GB，MinerU 判定用 CPU 更合适"
    elif not strong:
        reason = f"满血需要 12 GB 以上显存的独立显卡（当前约 {vram / GIB:.0f} GB）" if best else "满血需要独立显卡"
    else:
        reason = ""
    return {
        "gpu_name": name,
        "gpu_vram_gb": round(vram / GIB, 1) if vram else 0,
        "gpu_usable": bool(usable),
        "gpu_strong": strong,
        "full_reason": reason,
        "cpu_name": cpu.get("model") or "",
        "cpu_threads": cpu.get("logical_cores") or os.cpu_count() or 0,
        "ram_gb": round((memory.get("total_bytes") or 0) / GIB),
    }


def read_vram() -> tuple[int, int] | None:
    """(used MiB, total MiB) of the largest NVIDIA card, or None when it cannot be read."""
    fake = os.environ.get("MU_FAKE_VRAM")  # test hook: file holding "used,total"
    if fake:
        try:
            used, total = (int(x) for x in Path(fake).read_text().strip().split(","))
            return used, total
        except Exception:
            return None
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        done = subprocess.run([exe, "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                              capture_output=True, text=True, timeout=8)
        rows = []
        for line in done.stdout.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                rows.append((int(parts[0]), int(parts[1])))
        return max(rows, key=lambda row: row[1]) if rows else None
    except Exception:
        return None


def vram_need(slots: int) -> int:
    """MiB the engine is expected to take at this slot count: measured by the speed
    test when available, otherwise an estimate (about 1.5 GB plus 0.45 GB per slot)."""
    measured = load_settings().get("vram_delta") or {}
    value = measured.get(str(slots))
    return int(value) if isinstance(value, (int, float)) and value > 0 else 1500 + 450 * slots


def fit_parallel(wanted: int) -> tuple[int, str]:
    """Largest slot count not above `wanted` that fits the memory free right now."""
    reading = read_vram()
    if reading is None:
        return wanted, ""
    used, total = reading
    free = total - used
    for slots in sorted({wanted, 12, 8, 6, 4}, reverse=True):
        if slots <= wanted and vram_need(slots) + VRAM_MARGIN_MIB <= free:
            if slots == wanted:
                return slots, ""
            return slots, (f"显存当前空闲约 {free / 1024:.1f} GB，放不下并行 {wanted} 路，"
                           f"已自动降为 {slots} 路。关闭占用显存的程序后重新进入可恢复。")
    return 0, (f"显存当前空闲只有约 {free / 1024:.1f} GB，不够满血模式使用，已改用智能模式。"
               "关闭占用显存的程序后可再切回满血。")


def mode_config(mode: str) -> dict:
    if mode == "full":
        return {"label": "满血", "default_tier": "standard", "parallel": full_parallel(), "workers": 2,
                "preload": True, "cpu": False}
    if mode == "smart":
        return {"label": "智能", "default_tier": "standard", "parallel": 0, "workers": 1, "preload": False, "cpu": False}
    return {"label": "CPU", "default_tier": "basic", "parallel": 0, "workers": 1, "preload": False, "cpu": True}


# ---------------------------------------------------------------- processes

def port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def kill_tree(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        if WINDOWS:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=30)
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        process.wait(timeout=20)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def stop_old_instances(port: int) -> str:
    """Stops an earlier MinerU still holding the port. Leaves other programs alone."""
    if not port_busy(port):
        return ""
    if not WINDOWS:
        return f"端口 {port} 被占用"
    script = (
        f"$ids = Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty OwningProcess -Unique; foreach ($id in $ids) { "
        "$p = Get-CimInstance Win32_Process -Filter \"ProcessId=$id\"; Write-Output \"$id`t$($p.CommandLine)\" }"
    )
    try:
        done = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, timeout=60)
        lines = done.stdout.decode("utf-8", errors="replace").splitlines()
    except Exception as exc:
        return f"端口 {port} 被占用，且无法查询占用者：{exc}"
    foreign = []
    for line in lines:
        pid, _, command = line.partition("\t")
        if not pid.strip().isdigit():
            continue
        lowered = command.lower()
        if "webui_app.py" in lowered or "mineru" in lowered:
            subprocess.run(["taskkill", "/PID", pid.strip(), "/T", "/F"], capture_output=True, timeout=30)
            print(f"[启动页] 已关闭之前运行的 MinerU（进程 {pid.strip()}）", flush=True)
        else:
            foreign.append(command.strip()[:80] or pid)
    for _ in range(20):
        if not port_busy(port):
            return ""
        time.sleep(0.5)
    return f"端口 {port} 被其他程序占用：{'；'.join(foreign) or '未知程序'}。请关闭它后重试。"


def stop_app() -> None:
    global app_process
    with lock:
        process, app_process = app_process, None
        state["app"] = None
    kill_tree(process)


def start_app(mode: str) -> None:
    global app_process
    stop_app()
    problem = stop_old_instances(APP_PORT)
    if problem:
        with lock:
            state.update(phase="choose", message=problem)
        return
    note = ""
    config = mode_config(mode)
    if mode == "full":
        slots, note = fit_parallel(config["parallel"])
        if slots:
            config["parallel"] = slots
        else:
            mode, config = "smart", mode_config("smart")
    host = "0.0.0.0" if state["lan"] else "127.0.0.1"
    command = [
        sys.executable, *PY_FLAGS, str(BIN / "webui_app.py"),
        "--server-name", host, "--server-port", str(APP_PORT),
        "--output-dir", str(ROOT / "output"), "--tier", "auto", "--no-browser",
        "--mode-label", config["label"], "--default-tier", config["default_tier"],
        "--workers", str(config["workers"]), "--launcher-url", f"http://127.0.0.1:{LAUNCH_PORT}/",
        "--vram-reserve-mib", str(VRAM_RESERVE_MIB),
    ]
    if config["parallel"]:
        command += ["--parallel", str(config["parallel"])]
    if config["preload"]:
        command.append("--preload")
    env = os.environ.copy()
    for key, value in CPU_ENV.items():
        if config["cpu"]:
            env[key] = value
        else:
            env.pop(key, None)
    extra = {} if WINDOWS else {"start_new_session": True}
    process = subprocess.Popen(command, cwd=str(ROOT), env=env, **extra)
    with lock:
        app_process = process
        state.update(phase="starting", message=note,
                     app={"mode": mode, "label": config["label"], "parallel": config["parallel"], "since": time.time()})
    save_settings(last_mode=mode)
    threading.Thread(target=watch_app, args=(process,), daemon=True).start()


def watch_app(process: subprocess.Popen) -> None:
    while True:
        code = process.poll()
        with lock:
            if app_process is not process:
                return
            if code is not None:
                state.update(phase="choose", app=None,
                             message=f"引擎已退出（退出码 {code}）。报错内容在黑色窗口里，可以换一个模式再试。")
                return
        if state["phase"] == "starting":
            try:
                with urlopen(f"http://127.0.0.1:{APP_PORT}/", timeout=2) as reply:
                    if reply.status == 200:
                        with lock:
                            if app_process is process:
                                state["phase"] = "running"
            except Exception:
                pass
        time.sleep(1)


def pick_bench_pdf(requested: str) -> Path | None:
    requested = requested.strip().strip('"')
    if requested:
        path = Path(requested).expanduser()
        return path if path.is_file() else None
    fixed = ROOT / "bench" / "source.pdf"
    if fixed.is_file():
        return fixed
    recent = sorted((ROOT / "output" / "gradio" / "sources").glob("*/*.pdf"),
                    key=lambda p: p.stat().st_mtime, reverse=True)
    if recent:
        return recent[0]
    sample = ROOT / "samples" / "sample.pdf"
    return sample if sample.is_file() else None


def start_bench(requested: str) -> str:
    global bench_process
    hw = state["hw"] or {}
    if not hw.get("gpu_strong"):
        return "测速只对满血模式有意义，需要 12 GB 以上显存的独立显卡。"
    pdf = pick_bench_pdf(requested)
    if pdf is None:
        return "找不到用于测速的 PDF，请填写一个 PDF 的完整路径。"
    stop_app()
    problem = stop_old_instances(APP_PORT)
    if problem:
        return problem
    levels = "4,8,12,16" if hw.get("gpu_vram_gb", 0) >= 15 else "4,8"
    tier = os.environ.get("MU_BENCH_TIER", "standard")  # test hook
    BENCH_FILE.unlink(missing_ok=True)
    command = [
        sys.executable, *PY_FLAGS, str(BIN / "bench.py"), "--pdf", str(pdf), "--levels", levels,
        "--tier", tier, "--out", str(BENCH_FILE), "--workdir", str(ROOT / "home" / "bench-logs"),
    ]
    extra = {} if WINDOWS else {"start_new_session": True}
    process = subprocess.Popen(command, cwd=str(ROOT), **extra)
    with lock:
        bench_process = process
        state.update(phase="bench", message="")
    threading.Thread(target=watch_bench, args=(process,), daemon=True).start()
    return ""


def read_bench() -> dict | None:
    try:
        return json.loads(BENCH_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def watch_bench(process: subprocess.Popen) -> None:
    process.wait()
    report = read_bench() or {}
    best = report.get("best")
    deltas = {
        str(run["parallel"]): run["vram_peak_mib"] - run["vram_base_mib"]
        for run in report.get("runs") or []
        if run.get("status") == "ok" and run.get("vram_peak_mib") and run.get("vram_base_mib") is not None
    }
    if deltas:
        save_settings(vram_delta=deltas)
    if isinstance(best, int):
        save_settings(full_parallel=best)
        note = f"测速完成，满血模式的并行数已设为 {best}。"
    else:
        note = "测速没有得到可用结果：" + (report.get("error") or "详见下方表格。")
    with lock:
        state.update(phase="choose", message=note)


def cancel_bench() -> None:
    kill_tree(bench_process)


def scan() -> None:
    try:
        summary = summarize(read_hardware())
        note = ""
    except Exception as exc:
        summary = summarize({})
        note = f"硬件扫描失败（{exc}），按无显卡处理。"
    with lock:
        state.update(hw=summary, phase="choose", message=note)
    print(f"[启动页] 显卡：{summary['gpu_name'] or '无'}  显存：{summary['gpu_vram_gb']} GB  "
          f"可用：{summary['gpu_usable']}  满血：{summary['gpu_strong']}", flush=True)
    if not summary["gpu_usable"]:
        start_app("cpu")  # nothing to choose on a machine without a usable graphics card


def snapshot() -> dict:
    with lock:
        data = json.loads(json.dumps(state))
    data["app_url"] = f"http://127.0.0.1:{APP_PORT}/"
    data["full_parallel"] = full_parallel()
    data["last_mode"] = load_settings().get("last_mode")
    data["theme"] = saved_theme()
    data["bench"] = read_bench()
    return data


# ---------------------------------------------------------------- web page

LIGHT_TOKENS = """
  --base:#f3f3f4; --glow:rgba(249,115,22,.20); --haze:rgba(0,0,0,.06);
  --glass:rgba(255,255,255,.58); --glass-strong:rgba(255,255,255,.78); --edge:rgba(255,255,255,.95);
  --hairline:rgba(0,0,0,.09); --shadow:0 22px 44px -26px rgba(0,0,0,.30),0 1px 2px rgba(0,0,0,.05);
  --fg:#101012; --muted:#5d5d66; --accent:#f26a12; --accent-deep:#d9530a; --accent-soft:rgba(242,106,18,.12);
  --field:rgba(255,255,255,.82); --field-edge:rgba(0,0,0,.13);
  --btn:linear-gradient(180deg,#fb8a3c,#ee6410); --btn-fg:#ffffff;
  --btn-shadow:0 8px 18px -10px rgba(242,106,18,.8),inset 0 1px 0 rgba(255,255,255,.35);
  --pick:#101012; --pick-fg:#ffffff;
"""
DARK_TOKENS = """
  --base:#08080a; --glow:rgba(249,115,22,.21); --haze:rgba(255,255,255,.05);
  --glass:rgba(255,255,255,.055); --glass-strong:rgba(28,28,32,.62); --edge:rgba(255,255,255,.11);
  --hairline:rgba(255,255,255,.10); --shadow:0 26px 60px -28px rgba(0,0,0,.9),inset 0 1px 0 rgba(255,255,255,.05);
  --fg:#f4f4f5; --muted:#a3a3ad; --accent:#ff8a3d; --accent-deep:#f97316; --accent-soft:rgba(255,138,61,.16);
  --field:rgba(0,0,0,.34); --field-edge:rgba(255,255,255,.14);
  --btn:linear-gradient(180deg,#ff8f45,#ef6a14); --btn-fg:#170b02;
  --btn-shadow:0 10px 24px -12px rgba(249,115,22,.85),inset 0 1px 0 rgba(255,255,255,.3);
  --pick:#f4f4f5; --pick-fg:#08080a;
"""

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="__THEME__"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>MinerU 4 启动</title>
<style>
:root{__LIGHT__}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){__DARK__}}
:root[data-theme="dark"]{__DARK__}
*{box-sizing:border-box}
html{color-scheme:light dark}
body{margin:0;min-height:100vh;color:var(--fg);
  font:15px/1.65 "Segoe UI Variable Text","Segoe UI","HarmonyOS Sans SC","MiSans","Microsoft YaHei UI","PingFang SC",sans-serif;
  background-color:var(--base);
  background-image:radial-gradient(1100px 620px at 88% -12%,var(--glow),transparent 62%),
    radial-gradient(760px 520px at -8% 38%,var(--haze),transparent 64%),
    radial-gradient(900px 600px at 60% 118%,var(--glow),transparent 66%);
  background-repeat:no-repeat;background-attachment:fixed}
main{max-width:880px;margin:0 auto;padding:28px 18px 64px}
.slab{background:var(--glass);border:1px solid var(--edge);border-radius:18px;box-shadow:var(--shadow);
  -webkit-backdrop-filter:blur(22px) saturate(150%);backdrop-filter:blur(22px) saturate(150%)}
.hero{position:relative;background:var(--glass-strong);border-radius:22px;padding:40px 24px 34px;text-align:center;margin-bottom:18px}
.hero h1{margin:0;font-family:"Segoe UI Variable Display","Segoe UI","HarmonyOS Sans SC","MiSans","Microsoft YaHei UI","PingFang SC",sans-serif;
  font-size:clamp(28px,4.4vw,40px);font-weight:600;letter-spacing:-.012em;line-height:1.25}
.hero p{margin:10px 0 0;color:var(--muted)}
.theme{position:absolute;top:14px;right:14px;display:inline-flex;border:1px solid var(--hairline);border-radius:999px;padding:2px;background:var(--glass);font-size:12.5px}
.theme button{all:unset;cursor:pointer;padding:2px 11px;border-radius:999px;color:var(--muted)}
.theme button.on{background:var(--pick);color:var(--pick-fg)}
.theme button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.card{padding:18px 22px;margin-bottom:18px}
.status{display:flex;gap:10px;align-items:center;font-weight:600;min-height:28px}
.dot{flex:none;width:10px;height:10px;border-radius:50%;background:var(--accent);box-shadow:0 0 0 4px var(--accent-soft);animation:beat 1.1s ease-in-out infinite}
.dot.still{animation:none}
@keyframes beat{50%{opacity:.3}}
@media (prefers-reduced-motion: reduce){.dot{animation:none}button.mode{transition:none}}
.note{color:var(--accent-deep);margin:8px 0 0}
.hw{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(0,.6fr) minmax(0,1.9fr) minmax(0,.5fr);gap:10px 26px}
.hw span{display:block;color:var(--muted);font-size:12.5px}
.hw b{font-weight:600}
.modes{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:640px){.modes{grid-template-columns:1fr}.theme{position:static;margin-bottom:14px}.hw{grid-template-columns:1fr 1fr}}
button.mode{appearance:none;-webkit-appearance:none;font:inherit;color:inherit;text-align:left;cursor:pointer;display:block;width:100%;
  padding:22px 24px;transition:transform .15s ease,border-color .15s ease}
button.mode:hover:not(:disabled){transform:translateY(-2px);border-color:var(--accent)}
button.mode:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
button.mode:disabled{opacity:.5;cursor:not-allowed}
button.mode h2{margin:0 0 6px;font-size:22px;font-weight:600;display:flex;gap:10px;align-items:center}
button.mode p{margin:0;color:var(--muted);font-size:13.5px}
button.mode.full{background:linear-gradient(180deg,var(--accent-soft),transparent 55%),var(--glass)}
button.mode.full h2{color:var(--accent-deep)}
.tag{font-size:11.5px;font-weight:600;border:1px solid var(--hairline);border-radius:999px;padding:0 9px;color:var(--muted)}
.why{color:var(--accent-deep);font-size:12.5px;margin-top:10px}
.links{margin:14px 4px 18px;display:flex;gap:20px;flex-wrap:wrap;font-size:13.5px}
a,button.link{all:unset;cursor:pointer;color:var(--fg);border-bottom:1px solid var(--hairline)}
a:hover,button.link:hover{color:var(--accent-deep);border-color:var(--accent)}
a:focus-visible,button.link:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
table{width:100%;border-collapse:collapse;font-size:13.5px;margin-top:12px;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--hairline)}
th{color:var(--muted);font-weight:500}
tr.best td{color:var(--accent-deep);font-weight:600}
input[type=text]{width:100%;background:var(--field);color:var(--fg);border:1px solid var(--field-edge);border-radius:10px;padding:9px 12px;font:inherit;margin:10px 0}
input[type=text]:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
button.small{all:unset;cursor:pointer;background:var(--btn);color:var(--btn-fg);box-shadow:var(--btn-shadow);border-radius:10px;padding:7px 18px;font-weight:600}
button.small.ghost{background:var(--glass);box-shadow:none;border:1px solid var(--field-edge);color:var(--fg)}
button.small:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.sub{color:var(--muted);margin:4px 0 0}
.foot{color:var(--muted);font-size:12.5px;text-align:center;margin:26px 0 0}
.foot a{color:var(--muted)}
[hidden]{display:none!important}
</style></head><body>
<main>
  <header class="slab hero">
    <div class="theme" role="group" aria-label="外观">
      <button data-theme="system">跟随系统</button><button data-theme="light">浅色</button><button data-theme="dark">深色</button>
    </div>
    <h1>MinerU 4：文档提取</h1>
    <p>MinerU 懒人包：解压即用的本地文档解析。选择运行模式后进入页面，全部在本机运行，文件不上传。</p>
  </header>
  <section class="slab card">
    <div class="status"><span class="dot" id="dot"></span><span id="status">正在扫描硬件…</span></div>
    <div class="note" id="note" hidden></div>
    <div class="links" id="back" hidden style="margin:10px 0 0"><a id="backlink" href="#">返回已打开的页面</a></div>
  </section>
  <section class="slab card" id="hwcard" hidden><div class="hw">
    <div><span>显卡</span><b id="hwgpu"></b></div><div><span>显存</span><b id="hwvram"></b></div>
    <div><span>处理器</span><b id="hwcpu"></b></div><div><span>内存</span><b id="hwram"></b></div>
  </div></section>
  <div id="chooser" hidden>
    <div class="modes" id="modegrid">
      <button class="slab mode" id="smart"><h2>智能 <span class="tag" id="smarttag" hidden>上次使用</span></h2>
        <p>按硬件自动选择解析等级，稳妥配置，显存占用约 3 GB。任何独立显卡都能用。</p></button>
      <button class="slab mode full" id="full"><h2>满血 <span class="tag" id="fulltag" hidden>上次使用</span></h2>
        <p id="fulltext"></p><div class="why" id="fullwhy" hidden></div></button>
    </div>
    <div class="links"><button class="link" id="cpu">显卡异常？改用 CPU 运行（慢，但一定能用）</button></div>
  </div>
  <section class="slab card" id="benchcard" hidden>
    <b>测速调优</b>
    <p class="sub">用同一份 PDF 的前 20 页，分别以不同并行数各跑一遍，自动把最快的设为满血模式的并行数。期间引擎会停止，整个过程约 5 到 10 分钟。</p>
    <div id="benchform"><input type="text" id="benchpath" placeholder="PDF 完整路径；留空则使用最近解析过的 PDF">
      <button class="small" id="benchgo">开始测速</button></div>
    <div id="benchrun" hidden style="margin-top:12px"><button class="small ghost" id="benchstop">停止测速</button></div>
    <div id="benchout"></div>
  </section>
  <p class="foot">MinerU 懒人包　<a href="https://github.com/barbarousrabbit/MinerU-LazyPack" target="_blank" rel="noopener">barbarousrabbit/MinerU-LazyPack</a>　基于开源 MinerU 解析引擎</p>
</main>
<script>
const $ = id => document.getElementById(id);
const switching = new URLSearchParams(location.search).has('switch');
let chosen = null, last = null, theme = document.documentElement.dataset.theme || 'system';
async function post(path, body){
  const r = await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-MU':'1'},body:JSON.stringify(body||{})});
  return r.json();
}
function paintTheme(){
  document.documentElement.dataset.theme = theme;
  document.querySelectorAll('.theme button').forEach(b => b.classList.toggle('on', b.dataset.theme === theme));
}
document.querySelectorAll('.theme button').forEach(b => b.onclick = () => { theme = b.dataset.theme; paintTheme(); post('/api/theme',{set:theme}); });
paintTheme();
function appUrl(s){ return s.app_url + (theme === 'system' ? '' : '?__theme=' + theme); }
function benchTable(b){
  if(!b) return '';
  if(b.error) return '<p class="note">'+b.error+'</p>';
  if(b.warming) return '<p class="sub">正在预热（加载模型）…</p>';
  let rows = (b.runs||[]).map(r=>{
    const best = b.best===r.parallel ? ' class="best"' : '';
    if(r.status==='running') return '<tr><td>'+r.parallel+' 路</td><td colspan="5">正在跑…</td></tr>';
    if(r.status!=='ok') return '<tr><td>'+r.parallel+' 路</td><td colspan="5">失败：'+(r.error||'')+'</td></tr>';
    return '<tr'+best+'><td>'+r.parallel+' 路</td><td>'+r.infer_s+' 秒</td><td>'+r.pages_per_s+' 页/秒</td><td>'+
      (r.gpu_util_avg!=null? r.gpu_util_avg+'%':'-')+'</td><td>'+(r.vram_peak_mib!=null? (r.vram_peak_mib/1024).toFixed(1)+' GB':'-')+'</td><td>'+
      (b.best===r.parallel?'最快，已采用':'')+'</td></tr>';
  }).join('');
  return '<table><tr><th>并行数</th><th>推理用时（'+(b.pages||'')+' 页）</th><th>速度</th><th>显卡占用均值</th><th>显存峰值</th><th></th></tr>'+rows+'</table>';
}
function render(s){
  last = s;
  const hw = s.hw, running = s.phase==='running', starting = s.phase==='starting';
  $('dot').className = 'dot' + (running||s.phase==='choose' ? ' still':'');
  let text = '正在扫描硬件…';
  if(s.phase==='choose') text = hw && !hw.gpu_usable ? '没有可用显卡，将以 CPU 模式运行' : '请选择运行模式';
  if(starting) text = '正在以「'+s.app.label+'」模式启动引擎，通常需要几十秒…（已等 '+Math.round(Date.now()/1000-s.app.since)+' 秒）';
  if(running) text = '「'+s.app.label+'」模式运行中';
  if(s.phase==='bench') text = '正在测速，请不要关闭黑色窗口…';
  $('status').textContent = text;
  $('note').hidden = !s.message; $('note').textContent = s.message||'';
  $('back').hidden = !running; $('backlink').href = appUrl(s);
  if(hw){
    $('hwcard').hidden = false;
    $('hwgpu').textContent = hw.gpu_name || '未检测到独立显卡';
    $('hwvram').textContent = hw.gpu_vram_gb ? hw.gpu_vram_gb+' GB' : '-';
    $('hwcpu').textContent = (hw.cpu_name||'-') + (hw.cpu_threads? '（'+hw.cpu_threads+' 线程）':'');
    $('hwram').textContent = hw.ram_gb ? hw.ram_gb+' GB' : '-';
    $('chooser').hidden = s.phase==='bench' || s.phase==='scanning' || (!hw.gpu_usable && s.phase!=='choose');
    $('modegrid').hidden = !hw.gpu_usable;
    $('cpu').textContent = hw.gpu_usable ? '显卡异常？改用 CPU 运行（慢，但一定能用）' : '重新以 CPU 模式启动';
    $('smart').disabled = starting; $('cpu').disabled = starting;
    $('full').disabled = !hw.gpu_strong || starting;
    $('fulltext').textContent = '并行 '+s.full_parallel+' 路解码，一份文档更快，显卡占用更高；批量可同时处理 2 个文件。带显存保护：显存不够会自动降档，不会硬撑。';
    $('fullwhy').hidden = hw.gpu_strong; $('fullwhy').textContent = hw.full_reason||'';
    $('smarttag').hidden = s.last_mode!=='smart'; $('fulltag').hidden = s.last_mode!=='full';
    $('benchcard').hidden = !hw.gpu_strong;
    $('benchform').hidden = s.phase==='bench'; $('benchrun').hidden = s.phase!=='bench';
    $('benchout').innerHTML = benchTable(s.bench);
  }
  if(running && (!switching || chosen)) location.href = appUrl(s);
}
async function tick(){
  try{ render(await (await fetch('/api/state',{cache:'no-store'})).json()); }catch(e){ $('status').textContent='启动页已关闭（黑色窗口被关掉了）'; $('dot').className='dot still'; }
  setTimeout(tick, 1000);
}
for(const mode of ['smart','full','cpu']) $(mode).onclick = async () => {
  chosen = mode;
  if(last && last.phase==='running' && last.app.mode===mode){ location.href = appUrl(last); return; }
  await post('/api/start',{mode});
};
$('benchgo').onclick = async () => { const r = await post('/api/bench',{path:$('benchpath').value}); if(r.error) alert(r.error); };
$('benchstop').onclick = () => post('/api/bench/stop');
tick();
</script></body></html>
"""


def saved_theme() -> str:
    value = load_settings().get("theme")
    return value if value in ("light", "dark") else "system"


def render_page() -> bytes:
    page = PAGE.replace("__LIGHT__", LIGHT_TOKENS).replace("__DARK__", DARK_TOKENS).replace("__THEME__", saved_theme())
    return page.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:  # keep the console quiet
        pass

    def _send(self, body: bytes, kind: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: dict, code: int = 200) -> None:
        self._send(json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", code)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(render_page(), "text/html; charset=utf-8")
        elif path == "/api/state":
            self._json(snapshot())
        elif path == "/api/ping":
            self._json({"mineru_portable": True})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/api/theme":
            # Also reached by a header-less beacon from the main page; it only stores a display choice.
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            choice = ""
            if "set=" in self.path:
                choice = self.path.split("set=", 1)[1].split("&", 1)[0]
            else:
                try:
                    choice = str(json.loads(raw or b"{}").get("set") or "")
                except Exception:
                    choice = ""
            if choice in ("system", "light", "dark"):
                save_settings(theme=choice)
            self._json({"theme": saved_theme()})
            return
        if self.headers.get("X-MU") != "1":  # a plain cross-site form cannot set this header
            self._json({"error": "forbidden"}, 403)
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            body = {}
        path = self.path.split("?", 1)[0]
        hw = state["hw"] or {}
        if path == "/api/start":
            mode = body.get("mode")
            if mode not in ("smart", "full", "cpu") or state["phase"] in ("scanning", "bench"):
                self._json({"error": "现在不能切换模式"}, 409)
            elif mode == "full" and not hw.get("gpu_strong"):
                self._json({"error": hw.get("full_reason") or "这台电脑不满足满血模式的条件"}, 409)
            elif mode == "smart" and not hw.get("gpu_usable"):
                self._json({"error": "没有可用显卡"}, 409)
            else:
                threading.Thread(target=start_app, args=(mode,), daemon=True).start()
                self._json({"ok": True})
        elif path == "/api/stop":
            stop_app()
            with lock:
                state.update(phase="choose", message="")
            self._json({"ok": True})
        elif path == "/api/bench":
            if state["phase"] in ("scanning", "bench"):
                self._json({"error": "现在不能开始测速"}, 409)
            else:
                problem = start_bench(str(body.get("path") or ""))
                self._json({"error": problem} if problem else {"ok": True})
        elif path == "/api/bench/stop":
            cancel_bench()
            self._json({"ok": True})
        else:
            self._json({"error": "not found"}, 404)


def already_running() -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{LAUNCH_PORT}/api/ping", timeout=2) as reply:
            return bool(json.loads(reply.read()).get("mineru_portable"))
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="MinerU Portable start page")
    parser.add_argument("--lan", action="store_true", help="let other devices on the network open the web UI")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    url = f"http://127.0.0.1:{LAUNCH_PORT}/"
    if port_busy(LAUNCH_PORT):
        if already_running():
            print("[启动页] MinerU 已经在运行，直接打开页面。", flush=True)
            if not args.no_browser:
                webbrowser.open(url)
            return
        print(f"[启动页] 端口 {LAUNCH_PORT} 被其他程序占用，无法启动。", flush=True)
        sys.exit(1)

    state["lan"] = args.lan
    server = ThreadingHTTPServer(("127.0.0.1", LAUNCH_PORT), Handler)
    atexit.register(stop_app)
    atexit.register(cancel_bench)
    threading.Thread(target=scan, daemon=True).start()
    print(f"[启动页] {url}", flush=True)
    if not args.no_browser:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        stop_app()
        cancel_bench()


if __name__ == "__main__":
    main()
