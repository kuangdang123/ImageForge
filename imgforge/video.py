"""视频转换引擎：ffmpeg 探测 + 单文件 / 批量转换。

GUI 与 CLI 都只依赖这里，保证「视频格式转换」与「视频转 GIF」两条入口行为一致。
所有外部命令都用 list 参数调用（绝不拼 shell 字符串），路径含空格 / 中文都安全。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable

from .converter import human_size, safe_stem

if TYPE_CHECKING:  # 仅用于类型标注，运行时不导入 PIL（缩略图才需要）
    from PIL.Image import Image as PILImage

__all__ = [
    "VIDEO_INPUT_EXTS",
    "VIDEO_FORMATS",
    "VIDEO_FORMAT_ORDER",
    "VideoFormatSpec",
    "VideoInfo",
    "VideoOptions",
    "VideoResult",
    "VideoError",
    "find_ffmpeg",
    "find_ffprobe",
    "ffmpeg_available",
    "ffmpeg_hint",
    "reset_tool_cache",
    "probe_video",
    "convert_video",
    "run_batch",
    "collect_videos",
    "make_thumbnail",
    "auto_suffix",
    "quality_to_crf",
    "quality_to_qscale",
    "human_size",
]

_CREATIONFLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # 打包成窗口程序后不弹黑框
_PROBE_TIMEOUT = 30.0
_THUMB_TIMEOUT = 60.0
_FFMPEG_TIMEOUT = 3600.0  # 单个文件最长编码 1 小时


class VideoError(Exception):
    """可展示给用户的转换失败原因。"""


# ------------------------------------------------------------------ 注册表 --

@dataclass(frozen=True)
class VideoFormatSpec:
    key: str
    label: str
    ext: str
    kind: str          # "video" | "gif"
    video_codec: str
    audio_codec: str
    default_quality: int  # 1..100（越高越好）
    note: str


VIDEO_FORMATS: dict[str, VideoFormatSpec] = {
    "mp4": VideoFormatSpec("mp4", "MP4", ".mp4", "video", "libx264", "aac", 66,
                           "通用首选 · H.264，兼容性最好"),
    "mkv": VideoFormatSpec("mkv", "MKV", ".mkv", "video", "libx264", "aac", 66,
                           "高清收藏 · 封装灵活，可多音轨"),
    "mov": VideoFormatSpec("mov", "MOV", ".mov", "video", "libx264", "aac", 66,
                           "苹果生态 · QuickTime 容器"),
    "webm": VideoFormatSpec("webm", "WebM", ".webm", "video", "libvpx-vp9", "libopus", 45,
                            "开放格式 · VP9 体积小，编码较慢"),
    "avi": VideoFormatSpec("avi", "AVI", ".avi", "video", "mpeg4", "libmp3lame", 82,
                           "老设备 / 老软件兼容，画质一般"),
    "gif": VideoFormatSpec("gif", "GIF", ".gif", "gif", "", "", 80,
                           "动图 · 无音轨，只适合短片段"),
}

VIDEO_FORMAT_ORDER: list[str] = ["mp4", "mkv", "mov", "webm", "avi", "gif"]

VIDEO_INPUT_EXTS: set[str] = {
    ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv",
    ".flv", ".ts", ".mts", ".m2ts", ".mpg", ".mpeg", ".ogv",
}

_MUXERS = {
    "mp4": "mp4",
    "mkv": "matroska",
    "mov": "mov",
    "webm": "webm",
    "avi": "avi",
    "gif": "gif",
}

_RESOLUTION_WIDTHS = {
    "720p": 1280,
    "1080p": 1920,
    "1440p": 2560,
    "2160p": 3840,
}

_GIF_DEFAULT_FPS = 12  # GIF 没指定帧率时的默认帧率（保持原有行为）

# GIF「目标体积」的缩宽阶梯：优先保分辨率，只降帧率 / 色数，实在压不下去
# 才按下面的比例一步步缩窄（每档只缩 10% 左右，避免一下掉到 240px 看不清）。
_GIF_FIT_SCALES: tuple[float, ...] = (
    0.90, 0.82, 0.74, 0.66, 0.58, 0.50, 0.44, 0.38, 0.32, 0.26, 0.22,
)
_MAX_GIF_FIT_STEPS = 14  # 试编码档位上限（不含首档用户参数）

_FIT_MB = 1024 * 1024  # 体积换算统一用 1 MB = 1024×1024 字节

# 只有这几种有损格式在显式设置 quality 时才把 q 值写进文件名
_QUALITY_TAG_KEYS = ("mp4", "webm", "avi")


# ------------------------------------------------------------- ffmpeg 探测 --

_TOOL_CACHE: dict[tuple[str, tuple[str, ...], str], Path | None] = {}


def reset_tool_cache() -> None:
    """清掉 ffmpeg / ffprobe 的查找缓存（GUI 的「重新检测」按钮用）。"""
    _TOOL_CACHE.clear()


def _exe_name(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def _app_dirs() -> list[Path]:
    """候选应用目录：打包后的 exe 所在目录优先，其次是源码根目录。"""
    dirs: list[Path] = []
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).resolve().parent)
    else:
        dirs.append(Path(__file__).resolve().parents[1])  # 仓库根目录
        argv0 = sys.argv[0] if sys.argv else ""
        if argv0:
            try:
                dirs.append(Path(argv0).resolve().parent)
            except OSError:
                pass
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(Path(meipass))
    try:
        dirs.append(Path.cwd())
    except OSError:
        pass

    unique: list[Path] = []
    seen: set[str] = set()
    for path in dirs:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _known_installs(name: str) -> list[Path]:
    """winget / scoop / chocolatey / 手工解压等常见安装位置。"""
    exe = _exe_name(name)
    candidates: list[Path] = []

    local = os.environ.get("LOCALAPPDATA")
    if local:
        winget = Path(local) / "Microsoft" / "WinGet"
        candidates.append(winget / "Links" / exe)
        packages = winget / "Packages"
        for pattern in (f"Gyan.FFmpeg*/**/bin/{exe}", f"*FFmpeg*/**/bin/{exe}"):
            try:
                candidates.extend(sorted(packages.glob(pattern)))
            except OSError:
                continue

    for env_name in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(env_name)
        if not root:
            continue
        base = Path(root)
        candidates.extend([
            base / "ffmpeg" / "bin" / exe,
            base / "FFmpeg" / "bin" / exe,
        ])

    for env_name in ("ChocolateyInstall", "ProgramData"):
        root = os.environ.get(env_name)
        if root:
            candidates.append(Path(root) / ("chocolatey" if env_name == "ProgramData" else "") / "bin" / exe)

    candidates.extend([
        Path(r"C:\ffmpeg\bin") / exe,
        Path(r"C:\tools\ffmpeg\bin") / exe,
    ])

    home = Path.home()
    candidates.extend([
        home / "scoop" / "shims" / exe,
        home / "scoop" / "apps" / "ffmpeg" / "current" / "bin" / exe,
    ])
    return candidates


def _from_env_value(value: str, exe: str) -> Path | None:
    path = Path(value).expanduser()
    if path.is_dir():
        for candidate in (path / exe, path / "bin" / exe):
            if candidate.is_file():
                return candidate
        return None
    return path if path.is_file() else None


def _search_tool(name: str, env_names: tuple[str, ...]) -> Path | None:
    """按「环境变量 → 应用目录 → PATH → 常见安装路径」依次找可执行文件。"""
    exe = _exe_name(name)
    for env_name in env_names:
        value = os.environ.get(env_name)
        if value:
            found = _from_env_value(value, exe)
            if found is not None:
                return found
    for root in _app_dirs():
        for candidate in (root / exe, root / "bin" / exe, root / "ffmpeg" / "bin" / exe):
            if candidate.is_file():
                return candidate
    which = shutil.which(name) or shutil.which(exe)
    if which:
        return Path(which)
    for candidate in _known_installs(name):
        if candidate.is_file():
            return candidate
    return None


def _cached_tool(name: str, env_names: tuple[str, ...], finder: Callable[[], Path | None]) -> Path | None:
    try:
        cwd = str(Path.cwd())
    except OSError:  # pragma: no cover - 工作目录被删的极端情况
        cwd = ""
    key = (name, tuple(os.environ.get(item, "") for item in env_names), cwd)
    if key not in _TOOL_CACHE:
        _TOOL_CACHE[key] = finder()
    return _TOOL_CACHE[key]


def find_ffmpeg() -> Path | None:
    """找 ffmpeg 可执行文件；找不到返回 None。"""
    return _cached_tool("ffmpeg", ("IMAGEFORGE_FFMPEG",), lambda: _search_tool("ffmpeg", ("IMAGEFORGE_FFMPEG",)))


def find_ffprobe() -> Path | None:
    """找 ffprobe；找不到返回 None（此时探测自动回退到 `ffmpeg -i`）。"""
    return _cached_tool(
        "ffprobe",
        ("IMAGEFORGE_FFPROBE", "IMAGEFORGE_FFMPEG"),
        _locate_ffprobe,
    )


def _locate_ffprobe() -> Path | None:
    exe = _exe_name("ffprobe")
    explicit = os.environ.get("IMAGEFORGE_FFPROBE")
    if explicit:
        found = _from_env_value(explicit, exe)
        if found is not None:
            return found
    # ffmpeg 与 ffprobe 通常同目录，先看邻居，比全盘搜索快得多
    ffmpeg = find_ffmpeg()
    if ffmpeg is not None:
        sibling = ffmpeg.with_name(exe)
        if sibling.is_file():
            return sibling
    return _search_tool("ffprobe", ())


def ffmpeg_available() -> bool:
    return find_ffmpeg() is not None


def ffmpeg_hint() -> str:
    """未安装 ffmpeg 时给用户看的中文提示；已安装返回空串。"""
    if ffmpeg_available():
        return ""
    return "未检测到 ffmpeg，请先安装（winget install Gyan.FFmpeg.Essentials）或把 ffmpeg.exe 放到应用目录"


# ------------------------------------------------------------------ 数据类 --

@dataclass
class VideoInfo:
    path: Path
    ok: bool
    format: str = ""
    width: int = 0
    height: int = 0
    duration: float = 0.0
    fps: float = 0.0
    video_codec: str = ""
    audio_codec: str = ""
    bytes: int = 0
    message: str = ""


@dataclass
class VideoOptions:
    target: str = "mp4"
    quality: int | None = None        # 1..100，None 用 spec.default_quality
    resolution: str = "source"        # "source"|"720p"|"1080p"|"1440p"|"2160p"
    fps: int = 0                      # 0=保持原帧率；仅 gif 有意义；上限 60
    start: float = 0.0                # 秒，仅 gif 使用（截取起点）
    duration: float = 0.0             # 秒，0=到结尾；仅 gif 使用
    loop: bool = True                 # gif 是否无限循环
    audio: bool = True                # 非 gif：是否保留音轨
    suffix: str = ""
    overwrite: bool = False
    max_bytes: int = 0                # 目标体积（字节）；0=关闭自动压缩，仅 GIF 生效

    def spec(self) -> VideoFormatSpec:
        try:
            return VIDEO_FORMATS[str(self.target).strip().lower()]
        except KeyError as exc:
            raise VideoError(f"不支持的输出格式：{self.target}") from exc


@dataclass
class VideoResult:
    src: Path
    dest: Path | None = None
    ok: bool = False
    message: str = ""
    width: int = 0
    height: int = 0
    duration: float = 0.0
    bytes: int = 0
    seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Plan:
    """把用户选项夹到合法范围后的编码计划。"""

    spec: VideoFormatSpec
    quality: int
    resolution: str
    fps: int
    start: float
    duration: float
    loop: bool
    audio: bool
    max_bytes: int = 0     # GIF 目标体积；0=不限
    width: int = 0         # 真实输出宽（探测源视频后填入；0=未知）
    height: int = 0        # 真实输出高


# ------------------------------------------------------------- 画质 / 参数 --

def quality_to_crf(quality: int) -> int:
    """quality(1..100) → CRF：越高画质越好（CRF 越小），夹到 18..40。"""
    crf = round(51 - 0.44 * quality)
    return int(max(18, min(40, crf)))


def quality_to_qscale(quality: int) -> int:
    """quality(1..100) → mpeg4 的 -q:v（越小越好），夹到 2..31。"""
    qscale = round(31 - 0.28 * quality)
    return int(max(2, min(31, qscale)))


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _parse_rate(value: object) -> float:
    """把 ffprobe 的 "30000/1001" 解析成 29.97。"""
    if value is None:
        return 0.0
    text = str(value).strip()
    if not text or text in ("0/0", "N/A"):
        return 0.0
    if "/" in text:
        num, _, den = text.partition("/")
        try:
            divisor = float(den)
            if divisor == 0:
                return 0.0
            return round(float(num) / divisor, 3)
        except ValueError:
            return 0.0
    try:
        return round(float(text), 3)
    except ValueError:
        return 0.0


def _make_plan(options: VideoOptions, spec: VideoFormatSpec) -> tuple[_Plan, list[str]]:
    warnings: list[str] = []
    default_quality = _as_int(spec.default_quality, 80)
    quality = _as_int(options.quality, default_quality)
    quality = max(1, min(100, quality))
    fps = max(0, min(60, _as_int(options.fps, 0)))
    start = max(0.0, _as_float(options.start, 0.0))
    duration = max(0.0, _as_float(options.duration, 0.0))
    max_bytes = max(0, _as_int(options.max_bytes, 0))
    resolution = str(options.resolution or "source").strip().lower()
    if resolution != "source" and resolution not in _RESOLUTION_WIDTHS:
        warnings.append(f"未知分辨率 {options.resolution}，已按原始分辨率输出")
        resolution = "source"
    plan = _Plan(
        spec=spec,
        quality=quality,
        resolution=resolution,
        fps=fps,
        start=start,
        duration=duration,
        loop=bool(options.loop),
        audio=bool(options.audio),
        max_bytes=max_bytes,
    )
    return plan, warnings


def _output_size(plan: _Plan, info: VideoInfo) -> tuple[int, int]:
    """估算真实输出尺寸：与 ffmpeg 的 ``scale='min(W,iw)':-2`` 规则一致（不放大）。"""
    if not info.ok or info.width <= 0 or info.height <= 0:
        return (0, 0)
    target = _RESOLUTION_WIDTHS.get(plan.resolution)
    if not target or target >= info.width:
        return (info.width, info.height)
    height = max(2, int(round(target * info.height / info.width)))
    if height % 2:  # -2 要求高度为偶数，跟 ffmpeg 实际输出保持一致
        height += 1
    return (target, height)


def _plan_with_size(plan: _Plan, info: VideoInfo) -> _Plan:
    """把探测到的真实输出宽高填进计划（文件名标签要用）。"""
    width, height = _output_size(plan, info)
    if width <= 0 or height <= 0:
        return plan
    return replace(plan, width=width, height=height)


def _scale_filter(width: int) -> str:
    # 单引号是给 ffmpeg 的 filtergraph 解析器用的：保护 min(W,iw) 里的逗号，
    # 否则逗号会被当成两个滤镜的分隔符。传给 subprocess 的是 list，不经过 shell。
    return f"scale='min({width},iw)':-2"


def _gif_filter(
    fps: int,
    width: int | None,
    colors: int = 256,
    dither: str = "bayer",
    *,
    dedupe: bool = False,
    global_palette: bool = False,
    denoise: bool = True,
) -> str:
    """GIF 滤镜链：fps →（去重）→ 缩放 →（去噪）→ palettegen/paletteuse。

    - ``dedupe``：插入 ``mpdecimate`` 丢掉静止重复帧（录屏/幻灯片能省 30–60%，
      帧时间戳保持不变，播放速度不变）。
    - ``global_palette``：用 ``stats_mode=full`` 让所有帧共用一张调色板，文件更小
      （代价是渐变略有色带）；压体积时总是打开。
    - ``denoise``：缩小后做一次轻量时域去噪（``hqdn3d``）。实拍/生成视频里的
      颗粒噪点在调色板量化后会被放大成大量碎色块，是 GIF 体积最大的隐形来源；
      去噪后同样清晰度下体积能小 25–35%（文字边缘基本不受影响）。
    - 缩放用 ``flags=area``：缩小时按面积平均，比 lanczos 更不容易把噪点保留成
      高频细节，进一步利于 LZW 压缩。
    """
    if width:
        scale = f"scale='min({width},iw)':-2:flags=area"
    else:
        scale = "scale=iw:-2:flags=area"
    colors = max(2, min(256, int(colors)))
    mode = "full" if global_palette else "diff"
    if colors < 256:
        palettegen = f"palettegen=max_colors={colors}:stats_mode={mode}"
    else:
        palettegen = f"palettegen=stats_mode={mode}"
    if colors < 256 or str(dither).strip().lower() == "none":
        paletteuse = "paletteuse=dither=none"
    else:
        paletteuse = "paletteuse=dither=bayer:bayer_scale=3"
    chain = [f"fps={fps}"]
    if dedupe:
        chain.append("mpdecimate=hi=768:lo=320:frac=0.33")
    chain.append(scale)
    if denoise:
        chain.append("hqdn3d=2:2:6:6")
    return (
        ",".join(chain) + ","
        f"split[a][b];[a]{palettegen}[p];"
        f"[b][p]{paletteuse}"
    )


@dataclass(frozen=True)
class _GifRung:
    """GIF 自动压缩的一档参数：宽上限 / 帧率 / 调色板色数 / 抖动方式。"""

    width: int | None   # None = 原始宽度
    fps: int
    colors: int
    dither: str = "bayer"
    dedupe: bool = False
    denoise: bool = True

    def text(self) -> str:
        """给用户看的一档说明，例如 ``宽 480 · 15 fps · 128 色``。"""
        width = f"宽 {self.width}" if self.width else "宽 原始宽度"
        return f"{width} · {self.fps} fps · {self.colors} 色"


@dataclass
class _GifFitOutcome:
    """自动压缩的试编码结果。"""

    path: Path | None = None        # 留下的一档临时文件（由调用方推进到目标）
    rung: _GifRung | None = None    # 对应档位（命中档 / 最小档）
    bytes: int = 0
    within_limit: bool = False      # 是否压到了 max_bytes 以内
    attempts: int = 0               # 实际尝试了几档
    cancelled: bool = False
    timed_out: bool = False
    stderr: str = ""


def _not_lighter(candidate: _GifRung, previous: _GifRung) -> bool:
    """候选档是否没有比上一档更轻（宽 / 帧率 / 色数全都不降，且不是同一档）。"""
    if candidate == previous:
        return True
    unknown_width = 10 ** 9  # 原始宽度按「无限大」参与比较
    pairs = (
        (candidate.width or unknown_width, previous.width or unknown_width),
        (candidate.fps, previous.fps),
        (candidate.colors, previous.colors),
    )
    return all(cur >= prev for cur, prev in pairs)


_GIF_FIT_WIDTHS: tuple[float, ...] = (
    1.0, 0.95, 0.90, 0.85, 0.80, 0.75, 0.70, 0.65,
    0.60, 0.55, 0.50, 0.45, 0.40, 0.35, 0.30, 0.25,
)
_GIF_FIT_FPS: tuple[int, ...] = (15, 12, 10, 8, 6, 5, 4)
_GIF_FIT_COLORS: tuple[int, ...] = (64, 32)
_MAX_GIF_FIT_STEPS = 6       # 最多真的编码几档（预测已把起点压到目标附近）


def _gif_score(width: int, fps: int, colors: int) -> float:
    """档位「清晰度」评分：分辨率优先，帧率次之，色数只做轻微加权。

    讲解类视频里，文字能不能看清几乎完全由分辨率决定，帧率只影响「顺不顺」；
    所以宽度的权重最高（线性），帧率给 0.45 次方，色数只给 0.12 次方
    （64 色比 32 色真正多用的字节并不多，但能明显减少暗部色带）。
    """
    return width * (fps ** 0.30) * ((colors / 32.0) ** 0.25)


def _gif_fit_ladder(plan: _Plan) -> list[_GifRung]:
    """按「清晰度评分」从高到低生成候选档位（预测命中哪档就从哪档开始试）。

    旧实现按「先降帧率、再缩宽」的固定顺序走，常常在还有 20% 余量时停在一个
    偏保守的档位；现在把 宽 × 帧率 × 色数 的组合全部枚举出来，先算评分排序，
    再用样片预测体积，直接从最可能命中的档位开始，命中即停、偏大就继续往下降。
    """
    requested_width = plan.width if plan.width > 0 else _RESOLUTION_WIDTHS.get(plan.resolution)
    requested_fps = plan.fps or _GIF_DEFAULT_FPS
    base_fps = min(requested_fps, 15)
    source_width = requested_width or 1920

    rungs: list[_GifRung] = []
    for scale in _GIF_FIT_WIDTHS:
        width = None if requested_width is None and scale >= 1.0 else max(
            96, int(round(source_width * scale / 2)) * 2
        )
        for fps in _GIF_FIT_FPS:
            if fps > base_fps:
                continue
            for colors in _GIF_FIT_COLORS:
                rungs.append(
                    _GifRung(width, fps, colors, "bayer" if colors >= 256 else "none")
                )
    rungs.sort(key=lambda r: _gif_score(r.width or source_width, r.fps, r.colors), reverse=True)
    return rungs


_PREDICT_WIDTH = 1280         # 预测用的试编码宽度（越接近目标越准）
_PREDICT_FPS = 10             # 预测用的试编码帧率
_PREDICT_COLORS = 64          # 预测用的调色板色数
_PREDICT_SAMPLE = 2.5         # 预测用的采样时长（秒）
_PREDICT_AREA_EXP = 3.7       # 体积随宽度增长的指数（实测噪声/细节丰富的素材近似三次以上）
_PREDICT_SAFETY = 0.98        # 预测用样片略偏保守，乘 0.82 后留 2% 余量


def _predict_gif_rung(
    ffmpeg: Path,
    source: Path,
    plan: _Plan,
    rungs: list[_GifRung],
    duration: float,
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> int:
    """用一小段样片预编码，估算各档体积，直接跳到「评分最高且能装下」的档位。

    逐档全片试编码在 1080p / 18s 的片子上要 3–5 分钟；先花约 2 秒编一段 640 宽的
    样片，按「像素数 ^1.9 × 帧率 × 色数^0.55」外推，通常一次就能落到目标附近，
    后面仍然逐档验证、必要时继续降档，所以预测不准只是多试一两次，不影响正确性。
    """
    if duration <= 0 or plan.max_bytes <= 0 or not rungs:
        return 0
    sample_seconds = min(_PREDICT_SAMPLE, duration)
    if sample_seconds <= 0:
        return 0
    import tempfile

    handle, name = tempfile.mkstemp(prefix="imgforge-predict-", suffix=".gif")
    os.close(handle)
    temp = Path(name)
    # 片头常有淡入 / 静止，从 35% 处取 2.5 秒更接近全片平均复杂度
    sample_start = max(0.0, duration * 0.35)
    sample_plan = replace(plan, start=sample_start, duration=sample_seconds, max_bytes=0)
    rung = _GifRung(_PREDICT_WIDTH, _PREDICT_FPS, _PREDICT_COLORS, "none")
    try:
        code, _stderr, cancelled, _timed_out = _run_ffmpeg(
            _build_command(ffmpeg, source, temp, sample_plan, rung),
            timeout=_FFMPEG_TIMEOUT,
            should_cancel=should_cancel,
        )
        if cancelled or code != 0 or not temp.is_file() or temp.stat().st_size <= 0:
            return 0
        sample_bytes = temp.stat().st_size
    finally:
        _discard(temp)

    duration_ratio = duration / sample_seconds
    for index, candidate in enumerate(rungs):
        width = candidate.width or _PREDICT_WIDTH
        predicted = (
            sample_bytes
            * duration_ratio
            * (width / _PREDICT_WIDTH) ** _PREDICT_AREA_EXP
            * (candidate.fps / _PREDICT_FPS)
            * (candidate.colors / _PREDICT_COLORS) ** 0.55
            * 0.82  # 样片取自 35% 处，比全片平均更复杂，实测偏高约 1.22 倍
        )
        if predicted <= plan.max_bytes * _PREDICT_SAFETY:
            return index
    return len(rungs) - 1


def _thumb_filter(box: int) -> str:
    # 长边不超过 box：先框住一个 box×box 的方框再等比缩小
    return (
        f"scale='min({box},iw)':'min({box},ih)':"
        "force_original_aspect_ratio=decrease"
    )


def _build_command(
    ffmpeg: Path,
    src: Path,
    part: Path,
    plan: _Plan,
    gif: _GifRung | None = None,
) -> list[str]:
    spec = plan.spec
    cmd = [
        str(ffmpeg), "-hide_banner", "-nostdin", "-y",
        "-nostats", "-v", "error", "-progress", "pipe:1",
    ]

    if spec.kind == "gif":
        if gif is None:  # 不走自动压缩时沿用原来的参数（分辨率按档位宽度上限夹）
            gif = _GifRung(
                _RESOLUTION_WIDTHS.get(plan.resolution),
                plan.fps or _GIF_DEFAULT_FPS,
                256,
            )
        if plan.start > 0:
            cmd += ["-ss", f"{plan.start:.3f}"]
        cmd += ["-i", str(src)]
        if plan.duration > 0:
            cmd += ["-t", f"{plan.duration:.3f}"]
        vf = _gif_filter(
            gif.fps,
            gif.width,
            gif.colors,
            gif.dither,
            dedupe=gif.dedupe,
            global_palette=gif.dedupe,
            denoise=gif.denoise,
        )
        cmd += ["-an", "-vf", vf]
        if gif.dedupe:
            # mpdecimate 会丢帧，必须让它按原始时间戳写每帧延迟，否则播放速度会变
            cmd += ["-fps_mode", "vfr"]
        cmd += [
            "-loop", "0" if plan.loop else "-1",
            "-f", _MUXERS["gif"],
            str(part),
        ]
        return cmd

    cmd += ["-i", str(src)]
    width = _RESOLUTION_WIDTHS.get(plan.resolution)
    if width:
        cmd += ["-vf", _scale_filter(width)]

    if spec.key in ("mp4", "mkv", "mov"):
        cmd += [
            "-c:v", "libx264", "-preset", "medium",
            "-crf", str(quality_to_crf(plan.quality)),
            "-pix_fmt", "yuv420p",
        ]
        if spec.key in ("mp4", "mov"):
            cmd += ["-movflags", "+faststart"]
        if plan.audio:
            cmd += ["-c:a", "aac", "-b:a", "160k"]
        else:
            cmd += ["-an"]
    elif spec.key == "webm":
        cmd += [
            "-c:v", "libvpx-vp9",
            "-crf", str(quality_to_crf(plan.quality)),
            "-b:v", "0", "-row-mt", "1",
        ]
        if plan.audio:
            cmd += ["-c:a", "libopus", "-b:a", "96k"]
        else:
            cmd += ["-an"]
    elif spec.key == "avi":
        cmd += ["-c:v", "mpeg4", "-q:v", str(quality_to_qscale(plan.quality))]
        if plan.audio:
            cmd += ["-c:a", "libmp3lame", "-b:a", "128k"]
        else:
            cmd += ["-an"]
    else:  # pragma: no cover - 注册表与分支一一对应，防御性保底
        raise VideoError(f"不支持的输出格式：{spec.key}")

    # 显式指定封装格式：临时文件用 .part 结尾，ffmpeg 无法从扩展名推断
    cmd += ["-f", _MUXERS[spec.key], str(part)]
    return cmd


# ------------------------------------------------------------------ 探测 --

def _text(data: bytes | str | None) -> str:
    if not data:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", "replace")


def _run_tool(cmd: list[str], *, timeout: float) -> subprocess.CompletedProcess | None:
    """跑一条短命令（探测 / 截图）；超时或启动失败返回 None。"""
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            creationflags=_CREATIONFLAGS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def probe_video(path: Path | str) -> VideoInfo:
    """读取视频元信息：优先 ffprobe，缺失时用 `ffmpeg -i` 的 stderr 回退。"""
    return _probe_video(Path(path), with_audio=True)


def _probe_video(file: Path, *, with_audio: bool = True) -> VideoInfo:
    info = VideoInfo(path=file, ok=False)
    try:
        info.bytes = file.stat().st_size
    except OSError:
        info.message = "文件不存在"
        return info

    ffprobe = find_ffprobe()
    if ffprobe is not None and _probe_with_ffprobe(info, ffprobe, file, with_audio=with_audio):
        return info
    ffmpeg = find_ffmpeg()
    if ffmpeg is not None and _probe_with_ffmpeg(info, ffmpeg, file):
        return info

    if ffmpeg is None and ffprobe is None:
        info.message = ffmpeg_hint()
    else:
        info.message = "无法解析该视频（可能损坏或不是视频）"
    return info


def _probe_with_ffprobe(info: VideoInfo, ffprobe: Path, file: Path, *, with_audio: bool) -> bool:
    done = _run_tool(
        [
            str(ffprobe), "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", "-select_streams", "v:0",
            str(file),
        ],
        timeout=_PROBE_TIMEOUT,
    )
    if done is None or not done.stdout:
        return False
    try:
        data = json.loads(_text(done.stdout))
    except (TypeError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    streams = data.get("streams") or []
    if not streams:
        return False

    stream = streams[0]
    fmt = data.get("format") or {}
    width = _as_int(stream.get("width"), 0)
    height = _as_int(stream.get("height"), 0)
    duration = _as_float(fmt.get("duration"), 0.0)
    if duration <= 0:
        duration = _as_float(stream.get("duration"), 0.0)
    info.format = (file.suffix.lstrip(".") or "VIDEO").upper()
    info.width, info.height = width, height
    info.duration = round(duration, 3)
    info.fps = _parse_rate(stream.get("avg_frame_rate")) or _parse_rate(stream.get("r_frame_rate"))
    info.video_codec = str(stream.get("codec_name") or "")
    if with_audio:
        info.audio_codec = _probe_audio_codec(ffprobe, file)
    info.ok = (width > 0 and height > 0) or duration > 0
    return info.ok


def _probe_audio_codec(ffprobe: Path, file: Path) -> str:
    """单独查一次音轨编码（主探测只选了视频流）。失败不影响结果。"""
    done = _run_tool(
        [
            str(ffprobe), "-v", "error", "-print_format", "json",
            "-show_streams", "-select_streams", "a:0", str(file),
        ],
        timeout=_PROBE_TIMEOUT,
    )
    if done is None or not done.stdout:
        return ""
    try:
        data = json.loads(_text(done.stdout))
    except (TypeError, ValueError):
        return ""
    streams = data.get("streams") or []
    if not streams:
        return ""
    return str(streams[0].get("codec_name") or "")


_TEXT_DURATION = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_TEXT_VIDEO = re.compile(r"Stream #\d+:\d+.*?: Video:\s*([\w.-]+).*?(\d{2,5})x(\d{2,5})")
_TEXT_FPS = re.compile(r"(\d+(?:\.\d+)?)\s*fps")
_TEXT_AUDIO = re.compile(r"Stream #\d+:\d+.*?: Audio:\s*([\w.-]+)")


def _probe_with_ffmpeg(info: VideoInfo, ffmpeg: Path, file: Path) -> bool:
    """无 ffprobe 时的回退：`ffmpeg -i` 会把元信息打到 stderr（退出码为 1，属正常）。"""
    done = _run_tool(
        [str(ffmpeg), "-hide_banner", "-nostdin", "-i", str(file)],
        timeout=_PROBE_TIMEOUT,
    )
    if done is None:
        return False
    text = _text(done.stderr) or _text(done.stdout)
    if not text:
        return False

    width = height = 0
    duration = 0.0
    fps = 0.0
    video_codec = ""
    audio_codec = ""
    for line in text.splitlines():
        if duration <= 0:
            match = _TEXT_DURATION.search(line)
            if match:
                duration = (
                    int(match.group(1)) * 3600
                    + int(match.group(2)) * 60
                    + float(match.group(3))
                )
        if not video_codec:
            match = _TEXT_VIDEO.search(line)
            if match:
                video_codec = match.group(1)
                width = int(match.group(2))
                height = int(match.group(3))
                rate = _TEXT_FPS.search(line)
                if rate:
                    fps = round(float(rate.group(1)), 3)
        if not audio_codec:
            match = _TEXT_AUDIO.search(line)
            if match:
                audio_codec = match.group(1)

    if width <= 0 and duration <= 0:
        return False
    info.format = (file.suffix.lstrip(".") or "VIDEO").upper()
    info.width, info.height = width, height
    info.duration = round(duration, 3)
    info.fps = fps
    info.video_codec = video_codec
    info.audio_codec = audio_codec
    info.ok = (width > 0 and height > 0) or duration > 0
    return info.ok


# ------------------------------------------------------------ 输出路径 --

def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a).lower() == str(b).lower()


def _fit_mb_label(max_bytes: int) -> str:
    """max_bytes → 文件名里的 MB 数字：8 MB 记作 ``8``，7.8 MB 记作 ``7.8``。"""
    mb = round(max_bytes / _FIT_MB, 1)
    return f"{max(mb, 0.1):g}"


def auto_suffix(options: VideoOptions, plan: _Plan) -> str:
    """按输出参数生成文件名标签，例如 ``_mp4_1080p_q88`` / ``_gif_480p_15fps_fit8mb``。

    用户希望从文件名就能看出「这个视频是按什么参数转出来的」，所以格式、分辨率、
    GIF 帧率 / 目标体积、有损画质都默认写进名字里，而不是只改扩展名。
    """
    parts = [str(plan.spec.key)]
    if plan.resolution == "source":
        if plan.width > 0 and plan.height > 0:
            parts.append(f"{plan.width}x{plan.height}")
        else:  # 探测失败时退化成标记，别把 0x0 写进名字
            parts.append("source")
    else:
        parts.append(plan.resolution)
    if plan.spec.kind == "gif":
        parts.append(f"{plan.fps or _GIF_DEFAULT_FPS}fps")
        if plan.max_bytes > 0:
            parts.append(f"fit{_fit_mb_label(plan.max_bytes)}mb")
    elif plan.spec.key in _QUALITY_TAG_KEYS and options.quality is not None:
        parts.append(f"q{plan.quality}")
    return "_" + "_".join(parts)


def _rename_for_rung(
    dest: Path,
    source: Path,
    options: VideoOptions,
    plan: _Plan,
    rung: _GifRung,
) -> Path:
    """自动压缩后按**实际生效**的帧率 / 宽度重命名，避免名字写 30fps 实际 10fps。"""
    width = plan.width if rung.width is None or plan.width <= 0 else min(plan.width, rung.width)
    height = plan.height
    if plan.width > 0 and plan.height > 0 and width != plan.width:
        # 等比缩放，文件名里的高要跟着宽一起变，否则会出现「1536x1080」这种假尺寸
        height = max(2, int(round(plan.height * width / plan.width / 2)) * 2)
    fit_plan = replace(plan, fps=rung.fps, width=width, height=height)
    tag = auto_suffix(options, fit_plan)
    stem = safe_stem(source.stem) + str(options.suffix or "") + tag
    target = dest.with_name(stem + dest.suffix)
    if _same_path(target, dest):
        return dest
    index = 2
    while target.exists() and index < 1000:
        target = dest.with_name(f"{stem} ({index}){dest.suffix}")
        index += 1
    try:
        os.replace(dest, target)
    except OSError:
        return dest
    return target


def _resolve_destination(
    source: Path,
    options: VideoOptions,
    out_root: str | os.PathLike[str] | None,
    plan: _Plan,
) -> tuple[Path, bool]:
    """算出输出路径；返回 (路径, 是否因重名/同名源文件而改过名)。"""
    spec = plan.spec
    stem = safe_stem(source.stem) + str(options.suffix or "") + auto_suffix(options, plan)
    dest_dir = Path(out_root) if out_root else source.parent
    candidate = dest_dir / f"{stem}{spec.ext}"

    renamed = False
    # 目标与源同名（含扩展名）时强制改名，绝不覆盖源视频
    if _same_path(candidate, source):
        candidate = dest_dir / f"{stem}_{spec.key}{spec.ext}"
        renamed = True
    if options.overwrite and not _same_path(candidate, source):
        return candidate, renamed

    index = 2
    while candidate.exists():
        candidate = dest_dir / f"{stem} ({index}){spec.ext}"
        renamed = True
        index += 1
    return candidate, renamed


# ------------------------------------------------------------ ffmpeg 执行 --

_OUT_TIME = re.compile(rb"out_time_(?:ms|us)=(-?\d+)")


def _progress_fraction(raw: bytes, total_seconds: float) -> float | None:
    """把 `-progress` 的 out_time_ms（单位其实是微秒）换算成 0..1。"""
    match = _OUT_TIME.search(raw)
    if match is None:
        return None
    try:
        micros = int(match.group(1))
    except (TypeError, ValueError):
        return None
    if micros < 0 or total_seconds <= 0:
        return None
    return min(1.0, micros / (total_seconds * 1_000_000.0))


def _safe_call(callback: Callable[..., None], *args: object) -> None:
    try:
        callback(*args)
    except Exception:  # noqa: BLE001 - 进度回调属于 UI 层，异常不应中断转换
        pass


def _cancel_requested(should_cancel: Callable[[], bool] | None) -> bool:
    """取消回调同属 UI 层：回调本身抛异常时按「未取消」处理。"""
    if should_cancel is None:
        return False
    try:
        return bool(should_cancel())
    except Exception:  # noqa: BLE001 - 与 _run_ffmpeg 的单次编码语义保持一致
        return False


def _run_ffmpeg(
    cmd: list[str],
    *,
    timeout: float = _FFMPEG_TIMEOUT,
    on_progress: Callable[[float], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    progress_total: float = 0.0,
) -> tuple[int, str, bool, bool]:
    """执行 ffmpeg，返回 (returncode, stderr, 是否取消, 是否超时)。

    没有进度 / 取消需求时走 subprocess.run（简单可靠）；
    需要流式进度或中途 terminate 时走 Popen —— 这是唯一能一边读
    `-progress pipe:1` 一边停进程的方式。
    """
    if on_progress is None and should_cancel is None:
        try:
            done = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
                creationflags=_CREATIONFLAGS,
            )
        except subprocess.TimeoutExpired:
            return (-1, "", False, True)
        except OSError as exc:
            raise VideoError(f"无法启动 ffmpeg：{exc}") from exc
        return (done.returncode, _text(done.stderr), False, False)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            creationflags=_CREATIONFLAGS,
        )
    except OSError as exc:
        raise VideoError(f"无法启动 ffmpeg：{exc}") from exc

    cancelled = [False]
    timed_out = [False]
    stderr_parts: list[bytes] = []

    def drain_stderr() -> None:
        try:
            while True:
                chunk = proc.stderr.read(8192)  # type: ignore[union-attr]
                if not chunk:
                    break
                stderr_parts.append(chunk)
        except (OSError, ValueError):
            pass

    def stop(*, cancel: bool) -> None:
        if cancel:
            cancelled[0] = True
        else:
            timed_out[0] = True
        try:
            proc.terminate()
        except OSError:
            return
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass

    reader = threading.Thread(target=drain_stderr, name="ffmpeg-stderr", daemon=True)
    reader.start()
    timer = threading.Timer(timeout, stop, kwargs={"cancel": False})
    timer.daemon = True
    timer.start()

    last_fraction = -1.0
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            if cancelled[0] or timed_out[0]:
                break
            if _cancel_requested(should_cancel):
                stop(cancel=True)
                break
            if on_progress is not None and progress_total > 0:
                fraction = _progress_fraction(raw, progress_total)
                if fraction is not None and fraction >= last_fraction + 0.01:
                    last_fraction = fraction
                    _safe_call(on_progress, fraction)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            stop(cancel=False)
    finally:
        timer.cancel()
        reader.join(timeout=10)
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass

    if (
        on_progress is not None
        and progress_total > 0
        and not cancelled[0]
        and not timed_out[0]
        and proc.returncode == 0
        and last_fraction < 1.0
    ):
        _safe_call(on_progress, 1.0)

    return (proc.returncode, _text(b"".join(stderr_parts)), cancelled[0], timed_out[0])


# ------------------------------------------------------------ 单文件入口 --

def _progress_total_seconds(info: VideoInfo, plan: _Plan) -> float:
    total = 0.0
    if info.ok and info.duration > 0:
        total = float(info.duration)
    if plan.start > 0 and total > 0:
        total = max(0.1, total - plan.start)
    if plan.duration > 0:
        total = plan.duration if total <= 0 else min(total, plan.duration)
    return total


def _failure_message(stderr: str) -> str:
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    detail = lines[-1] if lines else ""
    if len(detail) > 240:
        detail = detail[:240] + "…"
    if detail:
        return f"ffmpeg 处理失败：{detail}（视频可能损坏或编码不受支持）"
    return "ffmpeg 处理失败（视频可能损坏或编码不受支持）"


def _discard(path: Path | None) -> None:
    if path is None:
        return
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _encode_gif_fit(
    ffmpeg: Path,
    source: Path,
    part: Path,
    plan: _Plan,
    *,
    duration: float = 0.0,
    progress_total: float = 0.0,
    on_progress: Callable[[float], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> _GifFitOutcome:
    """GIF 目标体积：先预测、再逐档试编码，返回命中（或最小）一档留下的临时文件。

    每档先编码到独立的 ``*.part`` 临时文件，用 ``st_size <= max_bytes`` 判断；
    命中即停，全部不中则保留最小的一档。预测只用来决定**从哪一档开始**，正确性
    仍由真实文件大小兜底。整个试编码过程的进度均匀铺在 0.05~0.95（最后一档完成
    后的 1.0 由调用方用现有收尾逻辑上报）。临时文件的清理由这里的 finally 负责：
    取消 / 超时 / 编码失败都不会在输出目录留下 .part。
    """
    outcome = _GifFitOutcome()
    rungs = _gif_fit_ladder(plan)
    start = _predict_gif_rung(ffmpeg, source, plan, rungs, duration, should_cancel=should_cancel)
    if start > 0:
        rungs = rungs[start:]
    step = 0.9 / max(1, len(rungs))
    temps: list[Path] = []
    candidate: Path | None = None
    promoted: Path | None = None

    def encode(rung: _GifRung, index: int) -> tuple[str, int]:
        """编码一档，返回 (状态, 字节数)；状态 ∈ ok / cancelled / timed_out / failed。"""
        temp = part.with_name(f"{part.stem}.{index}.part")
        temps.append(temp)
        base = 0.05 + index * step

        def report(fraction: float, _base: float = base) -> None:
            if on_progress is None:
                return
            value = _base + min(1.0, max(0.0, float(fraction))) * step
            _safe_call(on_progress, min(0.95, value))

        outcome.attempts = index + 1
        code, stderr, cancelled, timed_out = _run_ffmpeg(
            _build_command(ffmpeg, source, temp, plan, rung),
            timeout=_FFMPEG_TIMEOUT,
            on_progress=report if on_progress is not None else None,
            should_cancel=should_cancel,
            progress_total=progress_total if on_progress is not None else 0.0,
        )
        if cancelled:
            return "cancelled", 0
        if timed_out:
            return "timed_out", 0
        if code != 0 or not temp.is_file() or temp.stat().st_size == 0:
            outcome.stderr = stderr or outcome.stderr
            _discard(temp)
            return "failed", 0
        return "ok", temp.stat().st_size

    try:
        for index, rung in enumerate(rungs):
            if _cancel_requested(should_cancel):  # 每次尝试前都检查取消
                outcome.cancelled = True
                return outcome
            status, size = encode(rung, index)
            if status == "cancelled":
                outcome.cancelled = True
                return outcome
            if status == "timed_out":
                outcome.timed_out = True
                return outcome
            if status == "failed":
                continue  # 这一档编码失败，继续降档试；全部失败时由调用方报错
            temp = temps[-1]
            if candidate is None or size < outcome.bytes:
                if candidate is not None:
                    _discard(candidate)  # 只留最小的一档，临时文件不会越积越多
                candidate, outcome.rung, outcome.bytes = temp, rung, size
            else:
                _discard(temp)  # 降档后反而更大（调色板/抖动导致），丢弃这一档
            if size <= plan.max_bytes:  # 命中目标体积
                outcome.within_limit = True
                # 命中得比较轻松（不到上限的 85%）说明还有余量，回头把更高评分
                # （更清晰）的档位逐级试一遍，能装下就换成更好的；最多回溯 3 档，
                # 保证总耗时可控。
                if size <= plan.max_bytes * 0.85:
                    for back in range(1, 2):
                        if index - back < 0:
                            break
                        status2, size2 = encode(rungs[index - back], index - back)
                        if status2 == "ok" and size2 <= plan.max_bytes:
                            _discard(candidate)
                            candidate, outcome.rung, outcome.bytes = (
                                temps[-1], rungs[index - back], size2
                            )
                            break
                break
        if candidate is not None:
            outcome.path = candidate  # 命中档，或全部不达标时最小的一档
            promoted = candidate      # 所有权交给调用方推进，finally 不再删
        return outcome
    finally:
        for temp in temps:
            if temp != promoted:
                _discard(temp)


def convert_video(
    src: str | os.PathLike[str],
    options: VideoOptions,
    out_root: str | os.PathLike[str] | None = None,
    *,
    on_progress: Callable[[float], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> VideoResult:
    """转换单个视频；GUI 只用前三个参数，进度 / 取消是给 run_batch 用的可选钩子。"""
    source = Path(src)
    result = VideoResult(src=source)
    started = time.monotonic()

    spec = VIDEO_FORMATS.get(str(options.target or "").strip().lower())
    if spec is None:
        result.message = f"不支持的输出格式：{options.target}"
        return result
    if not source.is_file():
        result.message = "文件不存在"
        return result
    if source.suffix.lower() not in VIDEO_INPUT_EXTS:
        result.message = f"不支持读取 {source.suffix or '该'} 格式"
        return result
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        result.message = ffmpeg_hint()
        return result

    plan, plan_warnings = _make_plan(options, spec)
    result.warnings.extend(plan_warnings)
    # 文件名要写真实输出尺寸（resolution="source" 时），GIF 目标体积也要知道时长
    # 来分配进度，所以这里统一探测一次源视频。
    info = _probe_video(source, with_audio=False)
    plan = _plan_with_size(plan, info)

    if spec.kind == "gif":
        if plan.audio:
            result.warnings.append("GIF 不含音轨，已忽略音频设置")
        if plan.resolution in ("1440p", "2160p"):
            result.warnings.append("GIF 分辨率较高，文件可能偏大")
    elif plan.fps:
        result.warnings.append("帧率设置只对 GIF 生效，已按原帧率输出")
    gif_fit = spec.kind == "gif" and plan.max_bytes > 0

    part: Path | None = None
    try:
        try:
            dest, _renamed = _resolve_destination(source, options, out_root, plan)
            result.dest = dest
            dest.parent.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            result.dest = None
            result.message = "没有写入权限（输出目录不可写）"
            return result
        except OSError as exc:
            result.dest = None
            result.message = f"无法准备输出目录：{exc}"
            return result

        part = dest.with_name(dest.name + ".part")
        progress_total = _progress_total_seconds(info, plan) if on_progress is not None else 0.0

        if gif_fit:
            outcome = _encode_gif_fit(
                ffmpeg,
                source,
                part,
                plan,
                duration=info.duration,
                progress_total=progress_total,
                on_progress=on_progress,
                should_cancel=should_cancel,
            )
            if outcome.cancelled:
                result.message = "已取消"
                return result
            if outcome.timed_out:
                result.message = "转换超时（视频过长或系统繁忙）"
                return result
            if outcome.path is None or outcome.rung is None:
                result.message = _failure_message(outcome.stderr)
                return result
            part = outcome.path  # 已编码好的成品临时文件，下面照常推进到目标
            if on_progress is not None and progress_total > 0:
                _safe_call(on_progress, 1.0)  # 试编码过程只铺 0.05~0.95
            if outcome.within_limit and outcome.attempts > 1:
                result.warnings.append(
                    f"已自动压缩到 {human_size(outcome.bytes)}（{outcome.rung.text()}）"
                )
            elif not outcome.within_limit:
                result.warnings.append(
                    f"已尽量压缩，仍有 {human_size(outcome.bytes)}（{outcome.rung.text()}）"
                )
        else:
            cmd = _build_command(ffmpeg, source, part, plan)
            code, stderr, cancelled, timed_out = _run_ffmpeg(
                cmd,
                timeout=_FFMPEG_TIMEOUT,
                on_progress=on_progress,
                should_cancel=should_cancel,
                progress_total=progress_total,
            )
            if cancelled:
                result.message = "已取消"
                return result
            if timed_out:
                result.message = "转换超时（视频过长或系统繁忙）"
                return result
            if code != 0 or not part.is_file() or part.stat().st_size == 0:
                result.message = _failure_message(stderr)
                return result

        # 极小概率的并发重名：转换期间目标被别的任务占了，再让一次名字
        if not options.overwrite and dest.exists():
            dest, _renamed = _resolve_destination(source, options, out_root, plan)
            result.dest = dest
        try:
            os.replace(part, dest)
        except PermissionError:
            result.message = "没有写入权限（文件可能被占用）"
            return result
        except OSError as exc:
            result.message = f"写入失败：{exc}"
            return result

        if gif_fit and outcome.rung is not None:
            dest = _rename_for_rung(dest, source, options, plan, outcome.rung)
            result.dest = dest

        result.ok = True
        result.message = "完成"
        try:
            result.bytes = dest.stat().st_size
        except OSError:
            result.bytes = 0
        meta = _probe_video(dest, with_audio=False)
        if meta.ok:
            result.width, result.height = meta.width, meta.height
            result.duration = meta.duration
        if spec.kind == "gif" and not gif_fit and result.bytes > 8 * 1024 * 1024:
            result.warnings.append(
                f"GIF 体积偏大（{human_size(result.bytes)}），可降低分辨率或帧率"
            )
    except VideoError as exc:
        result.message = str(exc)
    except PermissionError:
        result.message = "没有写入权限（文件可能被占用）"
    except OSError as exc:
        result.message = f"转换失败：{exc}"
    except Exception as exc:  # noqa: BLE001 - 单个坏文件不应拖垮整批
        result.message = f"意外错误：{exc}"
    finally:
        if not result.ok:
            _discard(part)
        result.seconds = round(time.monotonic() - started, 2)
    return result


# ------------------------------------------------------------ 批量入口 --

def run_batch(
    paths: Iterable[str | os.PathLike[str]],
    options: VideoOptions,
    out_root: str | os.PathLike[str] | None = None,
    *,
    on_result: Callable[[int, VideoResult], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> list[VideoResult]:
    """批量转换；should_cancel 为真时终止当前 ffmpeg 并把剩余项标记为已取消。"""
    items = [Path(p) for p in paths]
    results: list[VideoResult] = []
    for index, path in enumerate(items):
        if should_cancel is not None and should_cancel():
            results.append(VideoResult(src=path, message="已取消"))
            if on_result is not None:
                _safe_call(on_result, index, results[-1])
            continue
        result = convert_video(
            path,
            options,
            out_root,
            on_progress=on_progress,
            should_cancel=should_cancel,
        )
        results.append(result)
        if on_result is not None:
            _safe_call(on_result, index, result)
    return results


# ------------------------------------------------------------------ 收集 --

_NATURAL = re.compile(r"(\d+)")


def _natural_key(path: Path) -> list[object]:
    """natural 排序键：clip2 排在 clip10 前面，大小写不敏感。"""
    parts = _NATURAL.split(str(path).lower())
    return [int(part) if part.isdigit() else part for part in parts]


def collect_videos(
    root: str | os.PathLike[str],
    recursive: bool = True,
) -> list[Path]:
    """递归收集视频文件（natural 排序，大小写不敏感）。"""
    folder = Path(root)
    pattern = "**/*" if recursive else "*"
    try:
        files = [
            path
            for path in folder.glob(pattern)
            if path.is_file() and path.suffix.lower() in VIDEO_INPUT_EXTS
        ]
    except OSError:
        return []
    return sorted(files, key=_natural_key)


# ------------------------------------------------------------------ 缩略图 --

def make_thumbnail(src: Path | str, size: int = 80) -> "PILImage | None":
    """取视频中点一帧做缩略图；失败返回 None。返回的是 PIL.Image。"""
    file = Path(src)
    if not file.is_file():
        return None
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        return None

    try:
        box = max(16, min(512, int(size)))
    except (TypeError, ValueError):
        box = 80

    info = _probe_video(file, with_audio=False)
    midpoint = (info.duration / 2.0) if info.ok and info.duration > 0 else 0.0
    attempts = [midpoint] if midpoint <= 0 else [midpoint, 0.0]

    with tempfile.TemporaryDirectory(prefix="imgforge-thumb-") as tmp:
        shot = Path(tmp) / "frame.png"
        for seek in attempts:
            cmd = [str(ffmpeg), "-hide_banner", "-nostdin", "-y", "-v", "error"]
            if seek > 0:
                cmd += ["-ss", f"{seek:.3f}"]
            cmd += [
                "-i", str(file),
                "-an", "-vframes", "1",
                "-vf", _thumb_filter(box),
                str(shot),
            ]
            _run_tool(cmd, timeout=_THUMB_TIMEOUT)
            if shot.is_file() and shot.stat().st_size > 0:
                break
        if not (shot.is_file() and shot.stat().st_size > 0):
            return None
        try:
            from PIL import Image
        except ImportError:  # pragma: no cover - 正常运行环境一定装了 Pillow
            return None
        try:
            with Image.open(shot) as img:
                img.load()
                return img.convert("RGB") if img.mode not in ("RGB", "RGBA") else img.copy()
        except Exception:  # noqa: BLE001 - 缩略图失败不该影响主流程
            return None
