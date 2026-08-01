from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import List, Optional

from .repository import LibraryRepository
from .catalog import TitleCatalog
from .config import load_config
from .executor import execute_plan, rollback
from .planner import build_plan
from .resolver import Resolver
from .scanner import scan_media


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AutoAnime v3 安全番剧整理器")
    parser.add_argument("source", nargs="?", help="季度文件夹、下载目录或单个视频文件")
    parser.add_argument("--output", help="媒体库输出目录")
    parser.add_argument("--config", help="v3 配置文件")
    parser.add_argument("--aliases", help="用户别名 JSON（覆盖内置目录）")
    parser.add_argument("--mode", choices=["link", "copy", "move"], help="整理方式")
    parser.add_argument("--apply", action="store_true", help="实际执行；不加时永远只预览")
    parser.add_argument("--no-cache", action="store_true", help="忽略已有识别结果")
    parser.add_argument("--report-json", help="输出完整计划 JSON")
    parser.add_argument("--rollback", metavar="LOG", help="按操作日志回滚后退出")
    parser.add_argument("--database-reset", "--cache-reset", action="store_true", help="清空 v3 资料库后退出")
    return parser


def _print_summary(plan, log_path: Path, apply: bool) -> None:
    counts = Counter(entry.action for entry in plan)
    print("扫描结果：%d 项计划" % len(plan))
    for action in ("organize", "review", "conflict", "skip"):
        print("  %-8s %d" % (action, counts.get(action, 0)))
    print("模式：%s" % ("已执行" if apply else "仅预览（未改动任何媒体文件）"))
    print("操作日志：%s" % log_path)
    reviews = [entry for entry in plan if entry.action in {"review", "conflict"}]
    if reviews:
        print("需要人工确认的前 30 项：")
        for entry in reviews[:30]:
            print("  [%s] %s :: %s" % (entry.action, entry.source.name, entry.reason))


def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = _parser()
    args = parser.parse_args(argv)
    project_root = Path(__file__).resolve().parent.parent
    config = load_config(Path(args.config).resolve() if args.config else None, project_root)
    if args.rollback:
        with LibraryRepository(config.database_path) as repository:
            restored = rollback(Path(args.rollback).resolve(), repository)
        print("已回滚 %d 个文件；v3 资料库状态已同步。" % restored)
        return 0
    with LibraryRepository(config.database_path) as cache:
        if args.database_reset:
            cache.reset()
            print("v3 SQLite 资料库已清空；媒体文件未修改。")
            return 0
        if not args.source:
            parser.error("缺少 source")
        source = Path(args.source).resolve()
        output = Path(args.output).resolve() if args.output else config.output_root
        if output is None:
            output = source.parent / "AutoAnimeLibrary" if source.is_file() else source.parent / (source.name + "_Library")
        output = output.resolve()
        if output == source or (source.is_dir() and source in output.parents):
            parser.error("输出目录不能等于输入目录或位于输入目录内部")
        catalog = TitleCatalog.load(config.alias_file, Path(args.aliases).resolve() if args.aliases else None)
        resolver = Resolver(catalog, config, cache)
        media = scan_media(source, output)
        resolutions = [resolver.resolve(item, use_cache=not args.no_cache) for item in media]
        cache.flush()
        plan = build_plan(resolutions, output)
        if args.report_json:
            report_path = Path(args.report_json).resolve()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps([entry.to_dict() for entry in plan], ensure_ascii=False, indent=2), encoding="utf-8"
            )
        mode = args.mode or config.mode
        log_path = execute_plan(plan, mode, bool(args.apply), cache, config.operation_dir)
        _print_summary(plan, log_path, bool(args.apply))
        return 2 if any(entry.action in {"review", "conflict"} for entry in plan) else 0
