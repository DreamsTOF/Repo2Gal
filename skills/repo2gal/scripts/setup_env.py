#!/usr/bin/env python3
"""Repo2Gal skill 的一次性环境准备：建 venv、装第三方依赖、打印后续必须使用的解释器。

    python scripts/setup_env.py             # 建/复用 <skill>/.venv 并安装依赖
    python scripts/setup_env.py --check     # 只检查现状，不改磁盘、不联网
    python scripts/setup_env.py --venv DIR  # 把 venv 放到别处（默认 <skill>/.venv）

为什么必须固定解释器：抓取阶段的 github-backup 可执行文件要能被 r2g_core.fetcher 找到
（它按 sys.executable 的兄弟路径 + PATH 查找），所以五个步骤全部使用本脚本打印的 venv python。
本技能自带确定性内核（scripts/r2g_core），不需要 pip install 任何 Repo2Gal 包。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

try:  # 管道/重定向时 Windows 默认 cp936，中文输出会乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
CORE_DIR = SCRIPTS_DIR / "r2g_core"

# 内联内核在导入期与抓取期真正需要的第三方模块（python-magic 只在素材包路径用，单列）
REQUIRED_MODULES = (
    "r2g_core",
    "requests",
    "click",
    "jsonschema",
    "semver",
    "packaging",
    "langcodes",
    "coloraide",
    "PIL",
    "github_backup",
)

# 显式安装清单：跳过 python-magic（Windows 无 libmagic，只有 Asset Pack MIME 校验需要它，
# 缺它时只在使用素材包时报错），其余为导入期/抓取期硬依赖。
INSTALL_PACKAGES = (
    "requests>=2.28",
    "click>=8.0",
    "github-backup>=0.65,<0.66",
    "jsonschema[format-nongpl]>=4.26,<5",
    "semver>=3.0.4,<4",
    "packaging>=26.3,<27",
    "langcodes>=3.5.1,<4",
    "coloraide>=8.11.1,<9",
    "Pillow>=12.3,<13",
)


def venv_python(venv_dir: Path) -> Path:
    for rel in ("Scripts/python.exe", "bin/python"):
        path = venv_dir / rel
        if path.is_file():
            return path
    return venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def probe(python: Path) -> tuple[bool, str]:
    """返回 (依赖是否齐全, 缺失模块名或原始报错)。"""
    code = "import " + ", ".join(REQUIRED_MODULES)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SCRIPTS_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [str(python), "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if result.returncode == 0:
        return True, ""
    tail = (result.stderr or "").strip().splitlines()
    return False, tail[-1] if tail else "未知导入错误"


def run(cmd: list[str], **kwargs) -> None:
    print("[setup] $ " + " ".join(cmd))
    result = subprocess.run(cmd, text=True, encoding="utf-8", errors="replace", **kwargs)
    if result.returncode != 0:
        raise SystemExit(f"[setup] 命令失败（退出码 {result.returncode}）：{' '.join(cmd)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="setup_env", description="Repo2Gal skill 环境准备")
    parser.add_argument("--check", action="store_true", help="只检查现状")
    parser.add_argument("--venv", help="venv 目录（默认 <skill>/.venv）")
    args = parser.parse_args(argv)

    if not (CORE_DIR / "__init__.py").is_file():
        raise SystemExit(f"[setup] 技能文件不完整：缺少内联内核 {CORE_DIR}；请整体复制 skill 目录")
    venv_dir = Path(args.venv).expanduser() if args.venv else SKILL_ROOT / ".venv"
    python = venv_python(venv_dir)
    print(f"[setup] skill root   = {SKILL_ROOT}")
    print(f"[setup] venv python  = {python}")

    if python.is_file():
        ok, detail = probe(python)
        if ok:
            print("[setup] 依赖齐全，无需改动")
        elif not args.check:
            print(f"[setup] 依赖缺失（{detail}），安装 skill 硬依赖")
            run([str(python), "-m", "pip", "install", *INSTALL_PACKAGES])
            ok, detail = probe(python)
        if not ok:
            if args.check:
                print(f"[setup] 需要准备：依赖缺失（{detail}）。运行 setup_env.py（不带 --check）修复")
                return 2
            raise SystemExit(f"[setup] 安装后仍缺依赖：{detail}")
    else:
        if args.check:
            print("[setup] 需要准备：venv 不存在。运行 setup_env.py（不带 --check）创建")
            return 2
        print("[setup] 创建 venv 并安装依赖")
        run([sys.executable, "-m", "venv", str(venv_dir)])
        run([str(python), "-m", "pip", "install", *INSTALL_PACKAGES])
        ok, detail = probe(python)
        if not ok:
            raise SystemExit(f"[setup] 安装后仍缺依赖：{detail}")

    agent = SCRIPTS_DIR / "r2g_agent.py"
    print("[setup] 完成。后续五步一律使用该解释器，例如：")
    print(f'  "{python}" "{agent}" doctor')
    return 0


if __name__ == "__main__":
    sys.exit(main())
