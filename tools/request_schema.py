"""`requests/<slug>.yml` 的格式定义与校验。

同一套规则被三处复用，避免"文档写的"和"实际执行的"漂移：

- `tools/validate_requests.py`：PR 校验（不需要 secret，不消耗 LLM）
- `tools/plan_requests.py`：合并到 main 后挑选要生成的请求并产出 matrix
- `requests/README.md`：写给贡献者的字段表

只依赖标准库 + PyYAML（两个 CLI 会在 runner 上 `pip install pyyaml`）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

REQUESTS_DIR = "requests"
ROOT = Path(__file__).resolve().parent.parent

#: slug 同时是 CF Pages 预览分支名，所以必须能当 DNS 标签用：小写字母/数字/连字符
SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])$"
#: 目标仓库标识
REPO_PATTERN = r"^[A-Za-z0-9._-]{1,50}/[A-Za-z0-9._-]{1,50}$"

MODES = ("overview", "chronicle", "quickstart")
PROFILES = ("chronicle-subtle", "chronicle-cinematic")
ASSET_PACKS = ("none", "builtin:cc0-chronicle")

REQUIRED_KEYS = ("repo", "mode")
OPTIONAL_KEYS = ("profile", "asset_pack")
ALLOWED_KEYS = REQUIRED_KEYS + OPTIONAL_KEYS
DEFAULTS = {"profile": "chronicle-subtle", "asset_pack": "none"}

#: 这些 slug 会踩到 Pages 的生产分支或平台保留名
RESERVED_SLUGS = ("main", "master", "production", "www", "repo2gal-gallery")


@dataclass(frozen=True)
class Request:
    """一个通过校验的请求。"""

    slug: str
    repo: str
    mode: str
    profile: str
    asset_pack: str
    path: Path

    @property
    def site_url(self) -> str:
        return f"https://{self.slug}.repo2gal-gallery.pages.dev"

    def as_matrix_row(self) -> dict:
        return {
            "slug": self.slug,
            "repo": self.repo,
            "mode": self.mode,
            "profile": self.profile,
            "asset_pack": self.asset_pack,
        }


class RequestError(Exception):
    """请求文件不合法；消息面向贡献者，可直接贴进 PR 评论。"""


def display_path(path: Path) -> str:
    """尽量显示成仓库内相对路径：CI 日志和 PR 评论里可读，也不泄漏本机路径。"""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _is_request_file(path: Path) -> bool:
    """`requests/` 下、非 `_` 开头、`.yml/.yaml` 结尾才算请求；README、模板被排除。"""
    return (
        path.suffix.lower() in (".yml", ".yaml")
        and not path.name.startswith("_")
        and path.parent.name == REQUESTS_DIR
    )


def iter_request_files(root: Path) -> list[Path]:
    directory = root / REQUESTS_DIR
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.is_file() and _is_request_file(p))


def filter_request_files(paths: list[Path]) -> list[Path]:
    return [p for p in paths if _is_request_file(p)]


def parse_request(path: Path) -> Request:
    """校验单个请求文件；不通过就抛 RequestError。"""
    slug = path.stem
    problems: list[str] = []

    import re

    if not re.fullmatch(SLUG_PATTERN, slug):
        problems.append(
            f"文件名 `{path.name}` 不是合法 slug：只能用 3–40 位小写字母/数字/连字符，"
            "且首尾必须是字母或数字（例如 `vue-core.yml`）"
        )
    if slug in RESERVED_SLUGS:
        problems.append(f"slug `{slug}` 是保留名（会撞上 Pages 生产分支或平台名），请换一个")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RequestError(f"`{display_path(path)}` YAML 解析失败：{exc}") from exc

    if raw is None:
        raise RequestError(f"`{display_path(path)}` 是空文件；至少要有 repo 和 mode")
    if not isinstance(raw, dict):
        raise RequestError(
            f"`{display_path(path)}` 顶层必须是 mapping（`key: value` 列表），收到 {type(raw).__name__}"
        )

    unknown = [key for key in raw if key not in ALLOWED_KEYS]
    if unknown:
        problems.append(
            f"不认识的字段 {unknown}；可用字段只有 {list(ALLOWED_KEYS)}（拼错字段名不会被静默忽略）"
        )

    for key in REQUIRED_KEYS:
        if not str(raw.get(key, "")).strip():
            problems.append(f"缺少必填字段 `{key}`")

    repo = str(raw.get("repo", "")).strip()
    if repo and not re.fullmatch(REPO_PATTERN, repo):
        problems.append(f"`repo: {repo}` 不是 `owner/name` 形式")

    mode = str(raw.get("mode", "")).strip()
    if mode and mode not in MODES:
        problems.append(f"`mode: {mode}` 非法；只能是 {' / '.join(MODES)}")

    profile = str(raw.get("profile", DEFAULTS["profile"])).strip()
    if profile not in PROFILES:
        problems.append(f"`profile: {profile}` 非法；只能是 {' / '.join(PROFILES)}")

    asset_pack = str(raw.get("asset_pack", DEFAULTS["asset_pack"])).strip()
    if asset_pack not in ASSET_PACKS:
        problems.append(f"`asset_pack: {asset_pack}` 非法；只能是 {' / '.join(ASSET_PACKS)}")

    if problems:
        raise RequestError(f"`{display_path(path)}`：\n  - " + "\n  - ".join(problems))

    return Request(
        slug=slug,
        repo=repo,
        mode=mode,
        profile=profile,
        asset_pack=asset_pack,
        path=path,
    )