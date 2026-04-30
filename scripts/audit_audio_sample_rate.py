#!/usr/bin/env python3
"""审计飞书语音样本的采样率与编码。

用途
----
当真实飞书 OPUS 录音可用时，对一批样本做批量探测，辅助选择 ASR 配置：

- 方案 A：所有样本都是 16kHz → 沿用 ``fun-asr-realtime``，不动
- 方案 A：所有样本都是 8kHz → 切到 ``fun-asr-flash-8k-realtime``
- 方案 B：样本采样率混合（48k / 24k / 16k / 8k 都有） → 在 service 层加 ``ffmpeg -ar 16000`` 转码

依赖
----
- 系统已安装 ``ffprobe``（ffmpeg 套件之一）

用法
----

    # 单个文件
    python3 scripts/audit_audio_sample_rate.py path/to/sample.opus

    # 目录递归
    python3 scripts/audit_audio_sample_rate.py data/workspace/sessions/

    # 多个路径混合
    python3 scripts/audit_audio_sample_rate.py a.opus b.opus dir/

输出
----
- 每个文件一行：路径 / codec / sample_rate / channels / 状态
- 末尾汇总：通过数 / 警告数 / 失败数 + 推荐方案
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# fun-asr-realtime 要求的采样率（Hz）
TARGET_SAMPLE_RATE = 16000
# 飞书音频常见扩展名 + 占位扩展名（listener 写入的 {file_key}.audio）
AUDIO_EXTENSIONS = {".opus", ".audio", ".wav", ".mp3", ".m4a", ".aac", ".amr"}


@dataclass(frozen=True)
class ProbeResult:
    path: Path
    codec: str | None
    sample_rate: int | None
    channels: int | None
    status: str  # "PASS" | "WARN_OFF_RATE" | "WARN_NO_AUDIO" | "FAIL"
    detail: str | None = None


def _ffprobe(path: Path) -> ProbeResult:
    """对单个文件调用 ffprobe，提取首条 audio stream 的关键字段."""
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_streams",
                "-select_streams",
                "a:0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        return ProbeResult(path, None, None, None, "FAIL", "ffprobe not installed")
    except subprocess.TimeoutExpired:
        return ProbeResult(path, None, None, None, "FAIL", "ffprobe timed out")

    if proc.returncode != 0:
        return ProbeResult(
            path,
            None,
            None,
            None,
            "FAIL",
            (proc.stderr or "").strip() or f"exit code {proc.returncode}",
        )
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        return ProbeResult(path, None, None, None, "FAIL", f"json: {exc}")

    streams = data.get("streams") or []
    if not streams:
        return ProbeResult(path, None, None, None, "WARN_NO_AUDIO", "no audio stream")

    s = streams[0]
    codec = s.get("codec_name")
    sr_str = s.get("sample_rate")
    sr = int(sr_str) if sr_str and str(sr_str).isdigit() else None
    ch = s.get("channels")
    if sr == TARGET_SAMPLE_RATE:
        status = "PASS"
    else:
        status = "WARN_OFF_RATE"
    return ProbeResult(path, codec, sr, ch, status)


def _collect_files(inputs: list[Path]) -> list[Path]:
    files: list[Path] = []
    for p in inputs:
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            for child in sorted(p.rglob("*")):
                if child.is_file() and child.suffix.lower() in AUDIO_EXTENSIONS:
                    files.append(child)
    return files


def _recommend(results: list[ProbeResult]) -> str:
    """根据实测分布给出 ASR 采样率配置建议."""
    rates = [r.sample_rate for r in results if r.sample_rate is not None]
    if not rates:
        return "推荐：暂无可用样本，无法判定。先确认 ffprobe 安装与样本路径。"
    counter = Counter(rates)
    total = sum(counter.values())
    if list(counter.keys()) == [TARGET_SAMPLE_RATE]:
        return (
            f"推荐：方案 A — 全部 {total} 个样本为 16kHz，沿用 fun-asr-realtime 即可。"
        )
    if list(counter.keys()) == [8000]:
        return (
            f"推荐：方案 A — 全部 {total} 个样本为 8kHz，"
            "建议切换为 fun-asr-flash-8k-realtime，并把 sample_rate 改为 8000。"
        )
    most, count = counter.most_common(1)[0]
    if len(counter) == 1:
        return (
            f"推荐：方案 B — 全部 {total} 个样本均为 {most}Hz（非 16k 也非 8k），"
            "需在 service 层加 ffmpeg 转码到 16k。"
        )
    dist = ", ".join(f"{r}Hz×{c}" for r, c in counter.most_common())
    return (
        f"推荐：方案 B — 样本采样率混合（{dist}），"
        "service 层加 ffmpeg -ar 16000 -f wav 统一转码到 16kHz。"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="批量审计飞书 OPUS 样本采样率",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="文件或目录（目录会递归扫描已知音频后缀）",
    )
    args = parser.parse_args(argv)

    files = _collect_files(args.paths)
    if not files:
        print("⚠ 未找到任何音频文件（请检查路径与扩展名）", file=sys.stderr)
        return 2

    print(f"扫描到 {len(files)} 个音频文件，开始 ffprobe…\n")
    results: list[ProbeResult] = []
    for f in files:
        r = _ffprobe(f)
        results.append(r)
        sr_text = f"{r.sample_rate}Hz" if r.sample_rate else "—"
        ch_text = f"{r.channels}ch" if r.channels else "—"
        codec_text = r.codec or "—"
        line = (
            f"[{r.status:14s}] {f}  codec={codec_text}  sr={sr_text}  ch={ch_text}"
        )
        if r.detail:
            line += f"  detail={r.detail}"
        print(line)

    counter = Counter(r.status for r in results)
    print()
    print(
        "汇总：PASS={pass_} WARN_OFF_RATE={off} WARN_NO_AUDIO={noa} FAIL={fail}".format(
            pass_=counter.get("PASS", 0),
            off=counter.get("WARN_OFF_RATE", 0),
            noa=counter.get("WARN_NO_AUDIO", 0),
            fail=counter.get("FAIL", 0),
        )
    )
    print()
    print(_recommend(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
