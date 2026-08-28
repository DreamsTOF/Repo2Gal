"""Overview（仓库概览）模式的离线测试。

覆盖：模式常量、确定性选角、概览上下文渲染、Overview prompt、
fetcher 的目录树/项目文件提取、概览专用备份 flags 与 pipeline/CLI 映射。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import repo2gal.fetcher as fetcher
from repo2gal.config import DEFAULT_GAME_MODE, GAME_MODES, GAME_MODE_TITLES
from repo2gal.errors import FetchError, UsageError
from repo2gal.fetcher import (
    NARRATIVE_BACKUP_FLAGS,
    OVERVIEW_BACKUP_FLAGS,
    Contributor,
    RepoContext,
    Thread,
    _build_file_tree,
    context_from_backup,
    fetch_context,
)
from repo2gal.generator import build_cast, build_prompt, render_overview_context
from repo2gal.performance import extract_beats
from repo2gal.pipeline import RunOptions, run_pipeline

EXAMPLE_PACK = "builtin:cc0-chronicle"


def make_ctx() -> RepoContext:
    return RepoContext(
        owner="acme",
        name="widget",
        description="A useful widget",
        language="Rust",
        stars=42,
        created_at="2020-01-01",
        topics=["demo"],
        readme_excerpt="# Widget\n安装后即可使用",
        file_tree="src/\n  main.rs\nREADME.md",
        project_files="### pyproject.toml\n[tool]\nname = 'widget'",
        contributors=[Contributor("alice", 100)],
        threads=[
            Thread(
                number=1,
                title="一段旧争论",
                kind="issue",
                state="closed",
                author="alice",
                created_at="2021-01-01",
                comment_count=5,
                body="讨论历史",
            )
        ],
    )


# --- 模式常量与选角 ---

def test_mode_constants_are_centralized():
    assert DEFAULT_GAME_MODE == "chronicle"
    assert "overview" in GAME_MODES
    assert GAME_MODE_TITLES["overview"] == "仓库概览"


def test_overview_cast_is_guide_without_history_contributors():
    ctx = make_ctx()
    chronicle = build_cast(ctx)
    overview = build_cast(ctx, mode="overview")

    assert "alice" in chronicle.names
    assert "alice" not in overview.names
    assert "widget" in overview.names
    assert "Rust" in overview.names
    assert any("新手村向导" in description for _, description in overview.entries)


# --- 概览上下文与 prompt ---

def test_overview_context_prioritizes_readme_tree_and_project_files():
    text = render_overview_context(make_ctx())
    assert "## README 摘录" in text
    assert "## 目录结构" in text
    assert "## 根级项目文件摘录" in text
    assert "## 社区讨论" not in text
    assert "一段旧争论" not in text


def test_build_prompt_uses_overview_template_and_context():
    prompt = build_prompt(make_ctx(), build_cast(make_ctx(), mode="overview"), mode="overview")
    assert "新手村向导" in prompt
    assert "仓库概览" in prompt
    assert "先看功能" in prompt or "先看安装" in prompt
    assert "必须写成 `角色名:台词`" in prompt
    assert "不要输出任何 `say:` 行" in prompt
    assert "禁止输出 `say:` 行" in prompt
    assert "src/" in prompt
    assert "编年史" not in prompt.split("# 任务")[0]
    with pytest.raises(UsageError, match="未知剧本模式"):
        build_prompt(make_ctx(), build_cast(make_ctx()), mode="nope")


# --- fetcher 的确定性概览提取 ---

def test_context_builds_file_tree_and_project_files(tmp_path):
    backup = tmp_path / "repositories" / "widget"
    source = backup / "repository"
    source.mkdir(parents=True)
    (source / "README.md").write_text("# Widget\n一个小工具", encoding="utf-8")
    (source / "pyproject.toml").write_text("[project]\nname = 'widget'\n", encoding="utf-8")
    (source / "main.py").write_text("print('hi')", encoding="utf-8")
    noise = source / "node_modules"
    noise.mkdir()
    (noise / "dep.js").write_text("// big dependency", encoding="utf-8")

    ctx = context_from_backup("acme", "widget", backup, mode="overview")

    assert "README.md" in ctx.file_tree
    assert "main.py" in ctx.file_tree
    assert "node_modules" not in ctx.file_tree
    assert "pyproject.toml" in ctx.project_files
    assert "name = 'widget'" in ctx.project_files


def test_overview_mode_allows_backup_without_community_data(tmp_path):
    backup = tmp_path / "repositories" / "widget"
    source = backup / "repository"
    source.mkdir(parents=True)
    (source / "main.py").write_text("print('hi')", encoding="utf-8")

    ctx = context_from_backup("acme", "widget", backup, mode="overview")
    assert "main.py" in ctx.file_tree

    with pytest.raises(FetchError, match="素材不足"):
        context_from_backup("acme", "widget", backup)


def test_build_file_tree_filters_ignored_dirs_and_limits_depth():
    files = [
        "src/core/deep/inner/extra/file.txt",
        "src/main.py",
        "node_modules/dep/index.js",
        "dist/bundle.js",
        "README.md",
    ]
    text = _build_file_tree(Path("unused"), files)
    assert "src/" in text
    assert "main.py" in text
    assert "README.md" in text
    assert "node_modules" not in text
    assert "dist" not in text
    assert "更深层" in text


def test_fetch_context_uses_overview_only_backup_flags(tmp_path, monkeypatch):
    backup = tmp_path / "repositories" / "widget"
    source = backup / "repository"
    source.mkdir(parents=True)
    (source / "README.md").write_text("# Widget", encoding="utf-8")
    captured = {}

    monkeypatch.setattr(fetcher, "fetch_repository_metadata", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        fetcher,
        "run_backup",
        lambda *args, **kwargs: captured.update(kwargs) or backup,
    )
    fetch_context("acme", "widget", backup_root=tmp_path, token="ghp_test", mode="overview")

    flags = tuple(captured["backup_flags"])
    assert flags == OVERVIEW_BACKUP_FLAGS
    assert "--repositories" in flags and "--wikis" in flags and "--releases" in flags
    assert "--issues" not in flags and "--discussions" not in flags
    assert "--all" not in flags


def test_fetch_context_keeps_full_flags_for_chronicle(tmp_path, monkeypatch):
    backup = tmp_path / "repositories" / "widget"
    source = backup / "repository"
    source.mkdir(parents=True)
    (source / "README.md").write_text("# Widget", encoding="utf-8")
    captured = {}

    monkeypatch.setattr(fetcher, "fetch_repository_metadata", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        fetcher,
        "run_backup",
        lambda *args, **kwargs: captured.update(kwargs) or backup,
    )
    fetch_context("acme", "widget", backup_root=tmp_path, token="ghp_test")

    assert tuple(captured["backup_flags"]) == NARRATIVE_BACKUP_FLAGS


# --- pipeline ---

class FakeLLM:
    def __init__(self, text="say:这是概览。\nend;\n"):
        self.text = text
        self.calls = []

    def complete(self, prompt, *, temperature=0.8):
        self.calls.append(prompt)
        return self.text


def test_pipeline_overview_runs_mode_specific_prompt_and_packaging(tmp_path):
    llm = FakeLLM()
    captured = {}
    options = RunOptions(
        owner="acme",
        repo="widget",
        output_dir=tmp_path / "out",
        backup_root=tmp_path / "backup",
        mode="overview",
        reuse_backup=True,
        api_key=None,
    )

    def fake_fetch(options_, log, progress):
        captured["fetch_mode"] = options_.mode
        return make_ctx()

    def fake_package(clean, output_dir, **kwargs):
        captured["game_name"] = kwargs["game_name"]
        captured["game_key"] = kwargs["game_key"]
        captured["clean"] = clean
        return output_dir

    artifacts = run_pipeline(
        options,
        llm_client=llm,
        fetch_fn=fake_fetch,
        package_fn=fake_package,
    )

    assert captured["fetch_mode"] == "overview"
    assert "新手村向导" in artifacts.prompt
    assert "先看功能" in artifacts.prompt or "先看安装" in artifacts.prompt
    assert "alice" not in artifacts.cast.names
    assert captured["game_name"] == "acme/widget 仓库概览"
    assert captured["game_key"] == "repo2gal_acme_widget_overview"
    assert captured["clean"].endswith("end;\n")


def test_pipeline_overview_with_asset_pack_advertises_logical_ids(tmp_path):
    script = tmp_path / "story.txt"
    script.write_text("changeBg:background.archive;\nend;\n", encoding="utf-8")
    options = RunOptions(
        owner="acme",
        repo="widget",
        output_dir=tmp_path / "out",
        backup_root=tmp_path / "backup",
        mode="overview",
        reuse_backup=True,
        script=script,
        asset_pack=EXAMPLE_PACK,
        public_assets=True,
        strict=True,
        dry_run=True,
        api_key=None,
    )
    artifacts = run_pipeline(
        options,
        fetch_fn=lambda *args: make_ctx(),
        package_fn=lambda *args, **kwargs: pytest.fail("不应打包"),
    )
    assert "新手村向导" in artifacts.prompt
    assert "background.archive" in artifacts.prompt
    assert artifacts.report is not None
    assert artifacts.report.downgrades == 0
    assert artifacts.output_dir is None


def test_pipeline_overview_supports_performance_plan(tmp_path):
    script = tmp_path / "story.txt"
    script.write_text("say:第一句。\nend;\n", encoding="utf-8")
    manifest = extract_beats("say:第一句。;\nend;\n", speakers={"widget"})
    plan = {
        "$schema": "https://repo2gal.dev/schemas/performance-plan/v1.json",
        "schemaVersion": 1,
        "sceneId": "start",
        "storyHash": manifest.story_hash,
        "profile": "chronicle-subtle",
        "cues": [
            {
                "id": "cue000001",
                "beatId": "b000001",
                "anchor": "during",
                "actions": [{"kind": "screen.effect", "preset": "snow", "intensity": "subtle"}],
            }
        ],
    }

    class TwoStageLLM:
        def __init__(self):
            self.calls = []

        def complete(self, prompt, *, temperature=0.8):
            self.calls.append((prompt, temperature))
            return __import__("json").dumps(plan)

    options = RunOptions(
        owner="acme",
        repo="widget",
        output_dir=tmp_path / "out",
        backup_root=tmp_path / "backup",
        mode="overview",
        reuse_backup=True,
        script=script,
        performance=True,
        api_key=None,
    )
    artifacts = run_pipeline(
        options,
        llm_client=TwoStageLLM(),
        fetch_fn=lambda *args: make_ctx(),
        package_fn=lambda clean, output, **kwargs: output,
    )
    assert "pixiInit;" in artifacts.clean
    assert artifacts.performance_report is not None
    assert artifacts.performance_report.semantic_valid is True


def test_pipeline_rejects_unknown_mode_before_fetch(tmp_path):
    called = []
    options = RunOptions(
        owner="acme",
        repo="widget",
        output_dir=tmp_path / "out",
        backup_root=tmp_path / "backup",
        mode="nope",
        reuse_backup=True,
    )
    with pytest.raises(UsageError, match="未知剧本模式"):
        run_pipeline(options, fetch_fn=lambda *args: called.append(1))
    assert not called
