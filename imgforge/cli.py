"""命令行入口：不打开窗口也能批量转换。

    python -m imgforge.cli formats
    python -m imgforge.cli info 1.png 2.jpg
    python -m imgforge.cli convert 图标.png -t ico --sizes 16,32,48,256
    python -m imgforge.cli convert 素材 -t webp -o 输出 --quality 80
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path
from typing import Iterable, Optional

from . import converter, ico


# --------------------------------------------------------------- 对齐工具 --

def display_width(text: str) -> int:
    """中文按两个字符宽度计算，表格才不会歪。"""
    width = 0
    for char in text:
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


def render_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [display_width(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], display_width(cell))
    lines = ["  ".join(pad(h, widths[i]) for i, h in enumerate(headers)).rstrip()]
    lines.append("  ".join("─" * widths[i] for i in range(len(headers))))
    for row in rows:
        lines.append("  ".join(pad(cell, widths[i]) for i, cell in enumerate(row)).rstrip())
    return "\n".join(lines)


# ------------------------------------------------------------------ 命令 --

def cmd_formats(_args: argparse.Namespace) -> int:
    rows = []
    for key in converter.FORMAT_ORDER:
        spec = converter.FORMATS[key]
        bits = []
        if spec.supports_alpha:
            bits.append("透明")
        if spec.supports_animation:
            bits.append("动图")
        rows.append([spec.label, f".{spec.ext.lstrip('.')}", spec.kind, "·".join(bits) or "-", spec.note])
    print(render_table(["格式", "扩展名", "类型", "能力", "说明"], rows))
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    rows = []
    failed = 0
    for raw in args.paths:
        info = converter.probe(raw)
        if not info.ok:
            failed += 1
            rows.append([Path(raw).name, "×", info.message, "", "", ""])
            continue
        rows.append([
            Path(raw).name,
            info.format or "?",
            f"{info.width}×{info.height}",
            info.mode,
            f"{info.frames} 帧" if info.frames > 1 else "静态",
            converter.human_size(info.bytes),
        ])
    print(render_table(["文件", "格式", "尺寸", "模式", "帧", "大小"], rows))
    return 1 if failed else 0


def _collect(paths: Iterable[str], recursive: bool) -> list[Path]:
    files: list[Path] = []
    seen: set[str] = set()
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            candidates = converter.collect_images(path, recursive=recursive)
        else:
            candidates = [path]
        for candidate in candidates:
            try:
                key = str(candidate.resolve())
            except OSError:
                key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            files.append(candidate)
    return files


def parse_sizes(text: Optional[str]) -> tuple[int, ...]:
    if not text:
        return ico.DEFAULT_SIZES
    values: list[int] = []
    for chunk in text.replace("，", ",").split(","):
        chunk = chunk.strip().lower().replace("px", "")
        if not chunk:
            continue
        try:
            values.append(int(chunk))
        except ValueError as exc:
            raise SystemExit(f"尺寸写法不对：{chunk}（示例：16,32,48,256）") from exc
    try:
        clean = ico.normalize_sizes(values)
    except ico.IconError as exc:
        raise SystemExit(str(exc)) from exc
    return tuple(clean)


def cmd_convert(args: argparse.Namespace) -> int:
    target = args.target.lower().lstrip(".")
    if target == "jpeg":
        target = "jpg"
    if target not in converter.FORMATS:
        raise SystemExit(f"不支持的输出格式：{args.target}（可用：{', '.join(converter.FORMAT_ORDER)}）")

    files = _collect(args.paths, recursive=not args.no_recursive)
    if not files:
        print("没有找到可转换的图片。", file=sys.stderr)
        return 1

    spec = converter.FORMATS[target]
    options = converter.ConvertOptions(
        target=target,
        quality=args.quality if args.quality is not None else spec.default_quality,
        lossless=args.lossless,
        ico_sizes=parse_sizes(args.sizes),
        ico_fit=args.fit,
        ico_payload=args.payload,
        ico_upscale=not args.no_upscale,
        keep_metadata=not args.no_metadata,
        overwrite=args.overwrite,
        suffix=args.suffix,
    )
    out_root = Path(args.output) if args.output else None
    if out_root is not None:
        try:
            out_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"无法创建输出目录：{exc}", file=sys.stderr)
            return 1

    keep_tree = bool(args.keep_tree and out_root is not None)
    if args.dry_run:
        for path in files:
            dest, renamed = converter.resolve_destination(
                path, options, out_root,
                keep_tree=keep_tree,
                base_dir=Path(args.base) if args.base else None,
            )
            print(f"{path}  →  {dest}{'  (重名)' if renamed else ''}")
        print(f"\n共 {len(files)} 个文件，未写入磁盘（--dry-run）。")
        return 0

    rows: list[list[str]] = []
    failed = 0

    def on_result(_index: int, result: converter.ConvertResult) -> None:
        nonlocal failed
        if result.ok:
            dest = result.dest or Path("?")
            rows.append([
                result.src.name,
                f"{result.width}×{result.height}",
                converter.human_size(result.bytes),
                dest.name,
                "；".join(result.warnings) or "完成",
            ])
        else:
            failed += 1
            rows.append([result.src.name, "-", "-", "-", result.message])

    results = converter.run_batch(
        files, options, out_root,
        keep_tree=keep_tree,
        base_dir=Path(args.base) if args.base else None,
        on_result=on_result,
    )
    done = len(results) - failed
    if rows:
        print(render_table(["源文件", "尺寸", "输出大小", "输出文件", "备注"], rows))
    print()
    if failed:
        print(f"完成 {done} 个，失败 {failed} 个。", file=sys.stderr if done == 0 else sys.stdout)
        return 1
    print(f"全部完成：{done} 个文件 → {target.upper()}" + (f"（输出到 {out_root}）" if out_root else ""))
    return 0


# ------------------------------------------------------------------ 入口 --

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="imgforge",
        description="图片格式转换与多尺寸 ICO 生成（无界面模式）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    formats = sub.add_parser("formats", help="列出支持的输出格式")
    formats.set_defaults(func=cmd_formats)

    info = sub.add_parser("info", help="查看图片信息")
    info.add_argument("paths", nargs="+", help="图片文件")
    info.set_defaults(func=cmd_info)

    conv = sub.add_parser("convert", help="转换图片")
    conv.add_argument("paths", nargs="+", help="文件或文件夹")
    conv.add_argument("-t", "--target", default="ico", help="输出格式（默认 ico）")
    conv.add_argument("-o", "--output", default=None, help="输出文件夹（默认与源文件同目录）")
    conv.add_argument("--sizes", default=None, help="ICO 尺寸，逗号分隔（默认 16,24,32,48,64,128,256）")
    conv.add_argument("--fit", default="contain", choices=["contain", "fill", "stretch"], help="非正方形图片的处理方式")
    conv.add_argument("--payload", default="png", choices=["png", "bmp"], help="ICO 像素数据格式")
    conv.add_argument("--no-upscale", action="store_true", help="不放大：跳过比源图更大的尺寸")
    conv.add_argument("-q", "--quality", type=int, default=None, help="有损格式质量 1-100")
    conv.add_argument("--lossless", action="store_true", help="WebP 无损模式")
    conv.add_argument("--suffix", default="", help="输出文件名后缀，例如 @2x")
    conv.add_argument("--overwrite", action="store_true", help="允许覆盖已存在的输出文件")
    conv.add_argument("--keep-tree", action="store_true", help="保留输入目录的子文件夹结构")
    conv.add_argument("--base", default=None, help="--keep-tree 的基准目录（默认各文件自身父目录）")
    conv.add_argument("--no-recursive", action="store_true", help="文件夹不递归")
    conv.add_argument("--no-metadata", action="store_true", help="不保留 EXIF/ICC")
    conv.add_argument("--dry-run", action="store_true", help="只打印计划，不写文件")
    conv.set_defaults(func=cmd_convert)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())