"""集中配置：默认值与路径常量。

所有“默认值写在哪”的问题在这里一次性解决，禁止其他模块各自硬编码目录规则。
本副本不含 LLM 相关配置：skill 版没有 LLM 客户端，三轮创作由 agent 承担。
"""

from __future__ import annotations

import os
from pathlib import Path

# 剧本生成模式。Chronicle（编年史）是默认模式；
# Overview（仓库概览）面向第一次接触项目的人；
# Quick Start（贡献者上手）带新人走完“跑起来到第一个改动”。
GAME_MODES = ("chronicle", "overview", "quickstart")
DEFAULT_GAME_MODE = "chronicle"
GAME_MODE_TITLES = {
    "chronicle": "编年史",
    "overview": "仓库概览",
    "quickstart": "贡献者上手",
}

# 不使用旁白的模式：全部台词都必须由角色亲口说出。
# Overview 自 v0.6.2 起如此（WebGAL 4.6.2 会把无说话人的文本渲染成上一句 speaker）；
# Quick Start 与之同构——带新人上手的维护者全程说话，不靠旁白解说。
NARRATION_FREE_MODES = ("overview", "quickstart")

# WebGAL 发行版自带的兼容默认素材；使用 Asset Pack 时仍与包内资源并存。
DEFAULT_BACKGROUNDS = ["bg.webp", "WebGalEnter.webp", "WebGAL_New_Enter_Image.webp"]
DEFAULT_BGM = ["s_Title.mp3"]


def env_value(name: str) -> str | None:
    """读取并去空白的环境变量；空白视为未设置。"""
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


def resolve_github_token() -> str | None:
    return env_value("GITHUB_TOKEN")


def default_backup_root(owner: str) -> Path:
    return Path(".repo2gal") / "backups" / owner


def default_output_dir(repo: str, mode: str = DEFAULT_GAME_MODE) -> Path:
    """默认产物目录；Chronicle 保持 output/<repo>，其他模式加模式后缀。"""
    path = Path("output") / repo
    return path if mode == DEFAULT_GAME_MODE else Path(f"{path}-{mode}")


def webgal_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "repo2gal"
