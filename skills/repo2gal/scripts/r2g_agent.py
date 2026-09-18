#!/usr/bin/env python3
"""Repo2Gal × harness 桥接：用 agent 的文件读写替换内置三轮 LLM。

五步一条线，产物全部落在 --workdir：

  prepare   抓取仓库 + 确定性选角 + 第 1 轮 prompt   -> state.json, 00-draft-prompt.md
  annotate  规范化草稿 + 第 2 轮 prompt              -> 01-draft.canonical.md, 01-beats.json,
                                                        02-annotations-prompt.md
  director  第 3 轮 prompt                           -> 03-director-prompt.md
  check     校验 agent 写的 Director Plan JSON       -> 03-director-report.json, 03-director-feedback.md
  build     确定性编译 + validator + 打包            -> 04-raw-compiled.txt, 04-clean.txt, 输出目录
  status    打印当前进度与下一步
  doctor    环境自检（内联内核、依赖、token、模板缓存、素材包平台支持）

本脚本绝不调用任何 LLM：草稿、批注、导演 JSON 全部由 harness agent 自己写文件。
退出码：0 成功 / 2 校验或用法失败 / 5 strict 降级 / 1 内部错误（与 repo2gal 契约一致）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:  # Windows 终端可能是 gbk/cp1252，中文输出不应炸掉
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

#: 本技能自带确定性内核（scripts/r2g_core），不依赖外部 Repo2Gal 仓库或已安装的 repo2gal
SKILL_ROOT = Path(__file__).resolve().parent
CORE_DIR = SKILL_ROOT / "r2g_core"


def _bootstrap() -> str:
    """把 scripts/ 放进 sys.path，导入内联内核 r2g_core，返回来源描述。"""
    if not (CORE_DIR / "__init__.py").is_file():
        raise SystemExit(
            f"[r2g] 技能文件不完整：缺少内联内核 {CORE_DIR}；"
            "请整体复制 skills/repo2gal 目录后重试"
        )
    sys.path.insert(0, str(SKILL_ROOT))
    import r2g_core  # noqa: F401

    return f"内联内核：{CORE_DIR}"


SOURCE = _bootstrap()

# 抓取阶段 r2g_core.fetcher 按 sys.executable 的兄弟路径 / PATH 找 github-backup；
# Windows 上兄弟路径是 "Scripts/github-backup"（无 .exe，匹配失败），必须把 Scripts 塞进 PATH。
os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")

try:
    from r2g_core.asset_pack import load_asset_pack  # noqa: E402
except ModuleNotFoundError as exc:
    raise SystemExit(
        f"[r2g] 当前解释器缺少依赖 {exc.name}。先运行 scripts/setup_env.py 准备 venv，"
        "并用它打印的解释器运行本脚本"
    ) from exc

from r2g_core.config import (  # noqa: E402
    DEFAULT_BACKGROUNDS,
    DEFAULT_BGM,
    GAME_MODES,
    GAME_MODE_TITLES,
    default_backup_root,
    default_output_dir,
    resolve_github_token,
    webgal_cache_dir,
)
from r2g_core.director import (  # noqa: E402
    _beat_id,
    _draft_hash,
    build_annotation_prompt,
    build_director_prompt,
    canonicalize_draft,
    compile_director,
    compile_draft_fallback,
    load_director,
    render_feedback,
    validate_director,
)
from r2g_core.errors import Repo2GalError  # noqa: E402
from r2g_core.fetcher import fetch_context, parse_repo  # noqa: E402
from r2g_core.generator import build_cast, build_prompt  # noqa: E402
from r2g_core.packager import package  # noqa: E402
from r2g_core.performance import PROFILES, PerformanceReport, save_json  # noqa: E402
from r2g_core.validator import sanitize  # noqa: E402
from r2g_core.webgal import COMPILE_COMMANDS  # noqa: E402

# 步骤文件名契约：agent 只写 RAW_*，其余由本脚本生成
RAW_DRAFT = "01-draft.md"
RAW_ANNOTATIONS = "02-annotations.md"
RAW_DIRECTOR = "03-director.json"


def _log(msg: str) -> None:
    print(f"[r2g] {msg}")


def _fail(msg: str, code: int = 1) -> None:
    print(f"[r2g] 失败：{msg}", file=sys.stderr)
    raise SystemExit(code)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _log(f"写入 {path.name}（{len(text)} 字）")


def _read(path: Path, *, optional: bool = False) -> str:
    if not path.is_file():
        if optional:
            return ""
        _fail(f"缺少输入文件 {path}（先完成上一步，或检查 --workdir）", 2)
    return path.read_text(encoding="utf-8")


def _workdir(args) -> Path:
    path = Path(args.workdir).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_state(workdir: Path) -> dict:
    return json.loads(_read(workdir / "state.json"))


def _asset_pack(state: dict):
    ref = state.get("assetPack")
    return load_asset_pack(ref, public=bool(state.get("publicAssets"))) if ref else None


def _media(pack) -> tuple[list[str], list[str], list[str]]:
    """(背景, 音乐, 立绘) 可用清单：默认素材与素材包并存，与 pipeline 一致。"""
    backgrounds = DEFAULT_BACKGROUNDS + (pack.logical_ids("background") if pack else [])
    bgm = DEFAULT_BGM + (pack.logical_ids("bgm") if pack else [])
    figures = pack.logical_ids("character") if pack else []
    return backgrounds, bgm, figures


def _catalog(pack) -> dict[str, frozenset[str]]:
    if pack is None:
        return {
            "changeBg": frozenset(DEFAULT_BACKGROUNDS),
            "changeFigure": frozenset(),
            "bgm": frozenset(DEFAULT_BGM),
        }
    catalog = pack.command_catalog()
    catalog["changeBg"] |= frozenset(DEFAULT_BACKGROUNDS)
    catalog["bgm"] |= frozenset(DEFAULT_BGM)
    return catalog


def _cast_names(state: dict) -> list[str]:
    return [name for name, _desc in state["cast"]]


def _token_source() -> tuple[str | None, str]:
    """令牌来源：环境变量 GITHUB_TOKEN 优先，其次约定的 ~/.repo2gal-token 文件。

    文件内容只用于采集，日志与 doctor 只报来源，绝不回显。
    """
    token = resolve_github_token()
    if token:
        return token, "环境变量 GITHUB_TOKEN"
    path = Path.home() / ".repo2gal-token"
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value, f"文件 {path}"
    return None, "未找到（只能 --reuse-backup）"


def _beats_meta(workdir: Path) -> dict:
    return json.loads(_read(workdir / "01-beats.json"))


# --- 步骤 1：抓取 + 选角 + 第一轮 prompt ---


def cmd_prepare(args) -> int:
    if args.mode not in GAME_MODES:
        _fail(f"未知模式 {args.mode}，可选 {sorted(GAME_MODES)}", 2)
    if args.profile not in PROFILES:
        _fail(f"未知 profile {args.profile}，可选 {sorted(PROFILES)}", 2)
    if args.public_assets and not args.asset_pack:
        _fail("--public-assets 必须与 --asset-pack 一起使用", 2)

    workdir = _workdir(args)
    pack = load_asset_pack(args.asset_pack, public=args.public_assets) if args.asset_pack else None
    if pack is not None:
        _log(f"素材包校验通过：{pack.name}@{pack.version}")

    owner, name = parse_repo(args.repo)
    backup_root = Path(args.backup_dir) if args.backup_dir else default_backup_root(owner)
    token, token_source = _token_source()
    _log(f"抓取 {owner}/{name}（mode={args.mode}，backup={backup_root}，令牌来源：{token_source}）")
    ctx = fetch_context(
        owner,
        name,
        backup_root=backup_root,
        token=token,
        organization=args.organization,
        top_threads=args.threads,
        reuse_backup=args.reuse_backup,
        mode=args.mode,
        log=_log,
        progress=lambda msg: _log(f"  {msg}"),
    )

    cast = build_cast(ctx, mode=args.mode)
    backgrounds, bgm, figures = _media(pack)
    prompt = build_prompt(
        ctx,
        cast,
        mode=args.mode,
        backgrounds=backgrounds,
        figures=figures,
        bgm=bgm,
    )
    _write(workdir / "00-draft-prompt.md", prompt)

    out = Path(args.output) if args.output else default_output_dir(name, args.mode)
    state = {
        "schemaVersion": 1,
        "owner": owner,
        "repo": name,
        "fullName": ctx.full_name,
        "mode": args.mode,
        "profile": args.profile,
        "assetPack": args.asset_pack,
        "publicAssets": bool(args.public_assets),
        "backupRoot": str(backup_root),
        "topThreads": args.threads,
        "reuseBackup": bool(args.reuse_backup),
        "organization": bool(args.organization),
        "outputDir": str(out if out.is_absolute() else Path.cwd() / out),
        "strict": bool(args.strict),
        "cast": [list(entry) for entry in cast.entries],
        "gameName": f"{ctx.full_name} {GAME_MODE_TITLES[args.mode]}",
        "gameKey": f"repo2gal_{owner}_{name}" + ("" if args.mode == "chronicle" else f"_{args.mode}"),
    }
    _write(workdir / "state.json", json.dumps(state, ensure_ascii=False, indent=2))
    _log(f"角色表：{'、'.join(_cast_names(state))}")
    _log(f"仓库：{ctx.full_name}（{ctx.language}，Star {ctx.stars}）")
    _log("下一步：读 00-draft-prompt.md 写 " + RAW_DRAFT + "，然后运行 annotate")
    return 0


# --- 步骤 2：草稿规范化 + 第二轮 prompt ---


def cmd_annotate(args) -> int:
    workdir = _workdir(args)
    state = _load_state(workdir)
    draft_raw = _read(workdir / RAW_DRAFT)
    canonical, beats = canonicalize_draft(draft_raw)
    if not beats or beats == ["（草稿为空）"]:
        _fail(f"{RAW_DRAFT} 是空草稿：每个节拍必须以一行 [B] 开头", 2)
    _write(workdir / "01-draft.canonical.md", canonical)
    _write(
        workdir / "01-beats.json",
        json.dumps({"count": len(beats), "beats": beats}, ensure_ascii=False, indent=2),
    )
    pack = _asset_pack(state)
    backgrounds, bgm, _figures = _media(pack)
    prompt = build_annotation_prompt(canonical, asset_pack=pack, backgrounds=backgrounds, bgm=bgm)
    _write(workdir / "02-annotations-prompt.md", prompt)
    _log(f"节拍数：{len(beats)}（导演 JSON 的 id 必须是 {_beat_id(1)} 到 {_beat_id(len(beats))} 一一对应）")
    _log("下一步：读 02-annotations-prompt.md 写 " + RAW_ANNOTATIONS + "，然后运行 director")
    return 0


# --- 步骤 3：第三轮 prompt ---


def cmd_director(args) -> int:
    workdir = _workdir(args)
    state = _load_state(workdir)
    canonical = _read(workdir / "01-draft.canonical.md")
    annotations = _read(workdir / RAW_ANNOTATIONS, optional=True)
    pack = _asset_pack(state)
    backgrounds, bgm, _figures = _media(pack)
    prompt = build_director_prompt(
        canonical,
        annotations,
        cast_names=sorted(_cast_names(state)),
        mode=state["mode"],
        asset_pack=pack,
        backgrounds=backgrounds,
        bgm=bgm,
        profile=state["profile"],
    )
    _write(workdir / "03-director-prompt.md", prompt)
    _log("下一步：读 03-director-prompt.md 写 " + RAW_DIRECTOR + "（纯 JSON），然后运行 check")
    return 0


# --- Director JSON 校验（check 与 build 共用） ---


def _validate_director(workdir: Path, state: dict, raw: str) -> tuple[dict | None, PerformanceReport]:
    canonical = _read(workdir / "01-draft.canonical.md")
    report = PerformanceReport(story_hash=_draft_hash(canonical))
    if not raw.strip():
        report.add("error", f"{RAW_DIRECTOR} 为空：需要输出完整的 Director Plan JSON")
        return None, report
    try:
        plan = load_director(
            raw,
            report=report,
            story_hash=_draft_hash(canonical),
            profile=state["profile"],
        )
    except Repo2GalError as exc:  # 理论上不会走到，兜住包内异常
        report.add("error", str(exc))
        return None, report
    if plan is None:
        return None, report
    pack = _asset_pack(state)
    validate_director(
        plan,
        report=report,
        draft_ids=[_beat_id(index) for index in range(1, _beats_meta(workdir)["count"] + 1)],
        cast_names=set(_cast_names(state)),
        mode=state["mode"],
        asset_pack=pack,
        asset_catalog=_catalog(pack),
        profile=state["profile"],
    )
    return plan, report


def cmd_check(args) -> int:
    workdir = _workdir(args)
    state = _load_state(workdir)
    raw = _read(workdir / args.file)
    _plan, report = _validate_director(workdir, state, raw)
    save_json(workdir / "03-director-report.json", report.to_dict())
    feedback = render_feedback(report.findings)
    _write(workdir / "03-director-feedback.md", feedback)
    _log(report.summary())
    if report.errors == 0:
        _log("校验通过：下一步运行 build")
        return 0
    for finding in report.findings:
        if finding.get("kind") == "error":
            print(f"  - {finding.get('message')}")
    _log("校验未通过：按 03-director-feedback.md 修改 " + args.file + " 后重跑 check")
    return 2


# --- 步骤 4：确定性编译 + validator + 打包 ---


def _tolerant_rename(self: Path, target) -> Path:
    """Windows 上容忍目录改名失败：先重试，再退化为复制。

    HACK: [repo2gal 用 staging.rename 原子替换模板缓存/产物目录；Windows 实时防护或索引器
    持有新解压文件的句柄时改名列连续报 WinError 5，且失败一次要重下 68MB]
    [packager 支持改名重试后删除本函数]

    实测（Windows 11 + Defender）：重试 1s/2s 后仍被拒，copytree + rmtree 可在同一次调用内
    完成替换；成功路径行为与原生 rename 完全一致。
    """
    last: OSError | None = None
    for delay in (0.0, 1.0, 2.0):
        if delay:
            time.sleep(delay)
        try:
            return original_rename(self, target)
        except OSError as exc:
            last = exc
    if not (self.is_dir() and not Path(target).exists()):
        raise last  # 非目录或目标已存在：交给原生语义报错
    _log(f"目录改名持续被拒（{last}），退化为复制：{self.name} → {Path(target).name}")
    shutil.copytree(self, target)
    shutil.rmtree(self, ignore_errors=True)
    return Path(target)


original_rename = Path.rename


def _package_tolerantly(**kwargs) -> Path:
    """只在 package() 调用窗口内替换 Path.rename，成功路径行为完全不变。"""
    Path.rename = _tolerant_rename
    try:
        return package(**kwargs)
    finally:
        Path.rename = original_rename


def cmd_build(args) -> int:
    workdir = _workdir(args)
    state = _load_state(workdir)
    pack = _asset_pack(state)
    raw = _read(workdir / args.file, optional=True)

    plan, director_report = _validate_director(workdir, state, raw)
    save_json(workdir / "03-director-report.json", director_report.to_dict())
    if plan is not None and director_report.errors == 0:
        _log("导演计划校验通过，确定性编译为 WebGAL 脚本")
        compiled = compile_director(plan, asset_pack=pack)
    elif args.fallback:
        _log(f"导演 JSON 不可用（{director_report.errors} 个错误），改用草稿确定性兜底编译")
        compiled = compile_draft_fallback(
            _beats_meta(workdir)["beats"],
            cast=_cast_names(state),
            mode=state["mode"],
        )
    else:
        _fail(
            "导演 JSON 未通过校验：先运行 check 按反馈修复，确认修不动再加 --fallback",
            2,
        )

    _write(workdir / "04-raw-compiled.txt", compiled)
    clean, report = sanitize(
        compiled,
        speakers=set(_cast_names(state)),
        allowed=COMPILE_COMMANDS,
        assets=_catalog(pack),
    )
    _write(workdir / "04-clean.txt", clean)
    _log(report.summary())
    for finding in report.findings:
        if finding.kind in ("downgrade", "warn"):
            _log(f"  第 {finding.line_no} 行：{finding.message}")
    if (state.get("strict") or args.strict) and report.downgrades:
        _fail(f"strict 模式：存在 {report.downgrades} 处降级", 5)
    if args.dry_run:
        _log("--dry-run：只编译与校验，不打包")
        return 0

    output_dir = _package_tolerantly(
        script=clean,
        output_dir=Path(state["outputDir"]),
        game_name=state["gameName"],
        game_key=state["gameKey"],
        asset_pack=pack,
        log=_log,
    )
    _log(f"产物目录：{output_dir}")
    print(f"  预览：python -m http.server -d {output_dir} 8000  →  http://localhost:8000")
    return 0


# --- 进度与自检 ---


def cmd_status(args) -> int:
    workdir = _workdir(args)
    files = [
        ("state.json", "prepare 完成"),
        ("00-draft-prompt.md", "第 1 轮 prompt"),
        (RAW_DRAFT, "agent 草稿"),
        ("01-beats.json", "annotate 完成（节拍数）"),
        ("02-annotations-prompt.md", "第 2 轮 prompt"),
        (RAW_ANNOTATIONS, "agent 批注"),
        ("03-director-prompt.md", "第 3 轮 prompt"),
        (RAW_DIRECTOR, "agent 导演 JSON"),
        ("03-director-feedback.md", "最近一次校验反馈"),
        ("04-clean.txt", "build 完成（校验后脚本）"),
    ]
    for name, note in files:
        path = workdir / name
        mark = "有" if path.is_file() else "无"
        size = f"{path.stat().st_size}B" if path.is_file() else "-"
        print(f"  [{mark}] {name:<26} {size:>8}  {note}")
    state_path = workdir / "state.json"
    if state_path.is_file():
        state = _load_state(workdir)
        print(f"  仓库={state['fullName']} mode={state['mode']} profile={state['profile']}")
        print(f"  产物目录={state['outputDir']}")
    return 0


def cmd_doctor(args) -> int:
    print(f"  python      : {sys.version.split()[0]} ({sys.executable})")
    print(f"  技能内核    : {SOURCE}")
    print(f"  采集令牌    : {_token_source()[1]}")
    supported = os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW")
    print(
        "  素材包支持  : "
        + (
            "可用（openat/O_NOFOLLOW 齐备）"
            if supported
            else "不可用（本平台缺 openat/O_NOFOLLOW，只能使用默认素材）"
        )
    )
    cache = webgal_cache_dir()
    print(f"  WebGAL 模板缓存: {cache if cache.exists() else str(cache) + '（首次 build 会联网下载）'}")
    print(f"  可用模式    : {', '.join(sorted(GAME_MODES))}")
    print(f"  可用 profile: {', '.join(sorted(PROFILES))}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="r2g_agent",
        description="Repo2Gal 的 agent 驱动版：LLM 由 harness agent 承担，本脚本只做确定性步骤。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prepare", help="抓取仓库 + 选角 + 第一轮创作 prompt")
    p.add_argument("repo", help="owner/repo 或 GitHub URL")
    p.add_argument("--workdir", required=True, help="阶段产物目录")
    p.add_argument("--mode", default="chronicle", choices=sorted(GAME_MODES))
    p.add_argument("--profile", default="chronicle-subtle", choices=sorted(PROFILES))
    p.add_argument("--output", help="WebGAL 产物目录（默认 output/<repo>[-<mode>]）")
    p.add_argument("--backup-dir", help="python-github-backup 原始数据目录")
    p.add_argument("--reuse-backup", action="store_true", help="不联网，复用已有备份")
    p.add_argument("--organization", action="store_true", help="目标 owner 是 Organization")
    p.add_argument("--threads", type=int, default=12, help="选入上下文的热门讨论数")
    p.add_argument("--asset-pack", help="本地 Asset Pack 目录或 builtin:cc0-chronicle")
    p.add_argument("--public-assets", action="store_true", help="按公开发布标准校验素材")
    p.add_argument("--strict", action="store_true", help="validator 有降级即判失败")
    p.set_defaults(func=cmd_prepare)

    p = sub.add_parser("annotate", help="规范化草稿 + 第二轮演出批注 prompt")
    p.add_argument("--workdir", required=True)
    p.set_defaults(func=cmd_annotate)

    p = sub.add_parser("director", help="第三轮 Director Plan prompt")
    p.add_argument("--workdir", required=True)
    p.set_defaults(func=cmd_director)

    p = sub.add_parser("check", help="校验 agent 写的 Director Plan JSON")
    p.add_argument("--workdir", required=True)
    p.add_argument("--file", default=RAW_DIRECTOR, help=f"待校验文件（默认 {RAW_DIRECTOR}）")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("build", help="编译 + validator +（默认）打包")
    p.add_argument("--workdir", required=True)
    p.add_argument("--file", default=RAW_DIRECTOR, help=f"导演 JSON（默认 {RAW_DIRECTOR}）")
    p.add_argument("--fallback", action="store_true", help="导演 JSON 不可用时用草稿兜底")
    p.add_argument("--strict", action="store_true", help="validator 有降级即判失败")
    p.add_argument("--dry-run", action="store_true", help="只编译与校验，不打包")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("status", help="打印工作目录进度与下一步")
    p.add_argument("--workdir", required=True)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("doctor", help="环境自检")
    p.set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Repo2GalError as exc:
        _fail(str(exc), exc.exit_code)
        return exc.exit_code  # 不可达，仅用于类型收敛
    except SystemExit:
        raise
    except Exception as exc:  # 兜底：保留可调试性，退出码 1
        import traceback

        traceback.print_exc()
        _fail(f"内部错误：{type(exc).__name__}: {exc}", 1)
        return 1


if __name__ == "__main__":
    sys.exit(main())
