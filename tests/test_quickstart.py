"""Quick Start（贡献者上手）模式的离线测试。

覆盖：模式常量与无旁白约束、确定性选角、上手上下文渲染、quickstart prompt、
起步任务标签筛选、贡献者入口文件提取（含嵌套路径与 CI 工作流发现）、
quickstart 专用备份 flags、导演层无旁白校验与 pipeline/CLI 映射。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

import repo2gal.cli as cli
import repo2gal.fetcher as fetcher
from repo2gal.config import (
    DEFAULT_GAME_MODE,
    GAME_MODES,
    GAME_MODE_TITLES,
    NARRATION_FREE_MODES,
    default_output_dir,
)
from repo2gal.director import (
    DIRECTOR_SCHEMA_URI,
    build_director_prompt,
    compile_draft_fallback,
    load_director,
    validate_director,
)
from repo2gal.errors import FetchError, UsageError
from repo2gal.fetcher import (
    NARRATIVE_BACKUP_FLAGS,
    OVERVIEW_BACKUP_FLAGS,
    QUICKSTART_BACKUP_FLAGS,
    Contributor,
    RepoContext,
    Thread,
    _discover_workflow_files,
    _is_starter_issue,
    context_from_backup,
    fetch_context,
)
from repo2gal.generator import build_cast, build_prompt, render_quickstart_context
from repo2gal.performance import PerformanceReport
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
        readme_excerpt="# Widget\n用可编辑模式安装后即可开发",
        file_tree="src/\n  main.rs\nREADME.md",
        contributor_files="### CONTRIBUTING.md\n提交前请跑测试",
        starter_issues=[
            Thread(
                number=7,
                title="补一个边界用例",
                kind="issue",
                state="open",
                author="alice",
                created_at="2021-01-01",
                comment_count=2,
                body="很小的改动，适合第一次贡献",
                labels=["good first issue"],
            )
        ],
        contributors=[Contributor("alice", 3)],
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


def write_issue(path: Path, number: int, *, state: str, labels: list[str], title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "number": number,
                "title": title,
                "state": state,
                "user": {"login": "alice"},
                "created_at": "2021-01-01T00:00:00Z",
                "comments": 1,
                "body": "改动很小，适合新人",
                "labels": [{"name": name} for name in labels],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


# --- 模式常量、默认目录与无旁白约束 ---

def test_mode_constants_are_centralized():
    assert DEFAULT_GAME_MODE == "chronicle"
    assert "quickstart" in GAME_MODES
    assert GAME_MODE_TITLES["quickstart"] == "贡献者上手"
    assert "quickstart" in NARRATION_FREE_MODES
    assert default_output_dir("widget", "quickstart") == Path("output") / "widget-quickstart"


def test_quickstart_cast_keeps_mentor_and_language_sprite():
    ctx = make_ctx()
    chronicle = build_cast(ctx)
    quickstart = build_cast(ctx, mode="quickstart")

    assert "widget" in quickstart.names
    assert "Rust" in quickstart.names
    # 历史争论角色不进上手剧本，但维护者会真实出场
    assert "alice" in chronicle.names
    assert "alice" in quickstart.names
    assert any("上手流程" in description for _, description in quickstart.entries)
    assert any("维护者之一" in description for _, description in quickstart.entries)


# --- 上手上下文与 prompt ---

def test_quickstart_context_prioritizes_contributor_material_and_tasks():
    text = render_quickstart_context(make_ctx())
    assert "## 贡献者入口文件摘录" in text
    assert "CONTRIBUTING.md" in text
    assert "## 目录结构" in text
    assert "## 起步任务" in text
    assert "#7 补一个边界用例" in text
    assert "good first issue" in text
    assert "## 社区讨论" not in text
    assert "一段旧争论" not in text


def test_quickstart_context_without_starter_issues_forbids_invented_numbers():
    ctx = make_ctx()
    ctx.starter_issues = []
    text = render_quickstart_context(ctx)
    assert "不要编造编号" in text


def test_build_prompt_uses_quickstart_template_and_context():
    ctx = make_ctx()
    prompt = build_prompt(ctx, build_cast(ctx, mode="quickstart"), mode="quickstart")
    assert "贡献者上手" in prompt
    assert "第一个改动" in prompt
    assert "角色名:台词" in prompt
    assert "不要写没有说话人的旁白" in prompt
    assert "src/" in prompt
    assert "补一个边界用例" in prompt
    assert "编年史" not in prompt.split("# 任务")[0]
    with pytest.raises(UsageError, match="未知剧本模式"):
        build_prompt(make_ctx(), build_cast(make_ctx()), mode="nope")


def test_director_prompt_marks_quickstart_as_narration_free():
    prompt = build_director_prompt(
        "[b000001]\nwidget:你好。",
        "[b000001] 无",
        cast_names=["widget"],
        mode="quickstart",
        asset_pack=None,
        backgrounds=[],
        bgm=[],
        profile="chronicle-subtle",
    )
    assert "不使用旁白" in prompt
    assert "客观叙述" not in prompt


# --- 起步任务筛选 ---

@pytest.mark.parametrize(
    "label",
    ["good first issue", "Good First Issue", "good-first-issue", "help wanted", "beginner", "starter"],
)
def test_is_starter_issue_accepts_known_labels(label):
    assert _is_starter_issue(Thread(1, "t", "issue", "open", "a", "2021-01-01", 0, "", labels=[label]))


@pytest.mark.parametrize("label", ["bug", "documentation", "wontfix", "help wanted later"])
def test_is_starter_issue_rejects_unrelated_labels(label):
    assert not _is_starter_issue(
        Thread(1, "t", "issue", "open", "a", "2021-01-01", 0, "", labels=[label])
    )


def test_context_from_backup_selects_open_starter_issues(tmp_path):
    backup = tmp_path / "repositories" / "widget"
    source = backup / "repository"
    source.mkdir(parents=True)
    (source / "README.md").write_text("# Widget", encoding="utf-8")
    (source / "main.py").write_text("print('hi')", encoding="utf-8")
    (source / "CONTRIBUTING.md").write_text("# 贡献指南\n先跑测试", encoding="utf-8")
    workflow = source / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: CI\non: [push]\n", encoding="utf-8")

    write_issue(backup / "issues" / "00001.json", 1, state="open", labels=["good first issue"], title="入手任务")
    write_issue(backup / "issues" / "00002.json", 2, state="closed", labels=["good first issue"], title="已完成")
    write_issue(backup / "issues" / "00003.json", 3, state="open", labels=["bug"], title="普通缺陷")
    write_issue(backup / "issues" / "00009.json", 9, state="open", labels=["help wanted"], title="更晚的任务")

    ctx = context_from_backup("acme", "widget", backup, mode="quickstart")

    assert [issue.number for issue in ctx.starter_issues] == [9, 1]
    assert ctx.threads == []
    assert "CONTRIBUTING.md" in ctx.contributor_files
    assert "先跑测试" in ctx.contributor_files
    assert ".github/workflows/ci.yml" in ctx.contributor_files
    assert "name: CI" in ctx.contributor_files
    # 维护者活动只来自起步任务
    assert [c.login for c in ctx.contributors] == ["alice"]


def test_context_from_backup_limits_starter_issues_by_top_threads(tmp_path):
    backup = tmp_path / "repositories" / "widget"
    source = backup / "repository"
    source.mkdir(parents=True)
    (source / "README.md").write_text("# Widget", encoding="utf-8")
    for number in range(1, 6):
        write_issue(
            backup / "issues" / f"{number:05d}.json",
            number,
            state="open",
            labels=["good first issue"],
            title=f"任务 {number}",
        )

    ctx = context_from_backup("acme", "widget", backup, mode="quickstart", top_threads=2)
    assert [issue.number for issue in ctx.starter_issues] == [5, 4]


def test_overview_mode_does_not_collect_contributor_material(tmp_path):
    backup = tmp_path / "repositories" / "widget"
    source = backup / "repository"
    source.mkdir(parents=True)
    (source / "README.md").write_text("# Widget", encoding="utf-8")
    (source / "CONTRIBUTING.md").write_text("# 贡献指南", encoding="utf-8")
    write_issue(backup / "issues" / "00001.json", 1, state="open", labels=["good first issue"], title="入手任务")

    ctx = context_from_backup("acme", "widget", backup, mode="overview")
    assert ctx.contributor_files == ""
    assert ctx.starter_issues == []


def test_quickstart_mode_requires_contributor_material(tmp_path):
    backup = tmp_path / "repositories" / "widget"
    (backup / "repository").mkdir(parents=True)

    with pytest.raises(FetchError, match="素材不足"):
        context_from_backup("acme", "widget", backup, mode="quickstart")


def test_discover_workflow_files_is_sorted_and_limited(tmp_path):
    files = [
        ".github/workflows/release.yml",
        ".github/workflows/ci.yml",
        ".github/workflows/lint.yaml",
        ".github/workflows/deploy.yml",
        ".github/workflows/notes.txt",
        "docs/ci.yml",
    ]
    assert _discover_workflow_files(tmp_path, files) == (
        ".github/workflows/ci.yml",
        ".github/workflows/deploy.yml",
        ".github/workflows/lint.yaml",
    )
    assert _discover_workflow_files(tmp_path, []) == ()


# --- 采集 flags ---

def test_fetch_context_uses_quickstart_only_backup_flags(tmp_path, monkeypatch):
    backup = tmp_path / "repositories" / "widget"
    (backup / "repository").mkdir(parents=True)
    (backup / "repository" / "README.md").write_text("# Widget", encoding="utf-8")
    captured = {}

    monkeypatch.setattr(fetcher, "fetch_repository_metadata", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        fetcher,
        "run_backup",
        lambda *args, **kwargs: captured.update(kwargs) or backup,
    )
    fetch_context("acme", "widget", backup_root=tmp_path, token="ghp_test", mode="quickstart")

    flags = tuple(captured["backup_flags"])
    assert flags == QUICKSTART_BACKUP_FLAGS
    assert "--repositories" in flags and "--issues" in flags and "--issue-comments" in flags
    assert "--wikis" in flags
    assert "--pulls" not in flags and "--discussions" not in flags and "--releases" not in flags
    assert "--all" not in flags
    assert flags != OVERVIEW_BACKUP_FLAGS and flags != NARRATIVE_BACKUP_FLAGS


# --- 导演层无旁白规则 ---

QUICKSTART_DRAFT = "[B]\nwidget:你好，我是 widget。\n[B]\nRust:先用可编辑模式装好开发依赖。\n"
QUICKSTART_ANNOTATIONS = ""

QUICKSTART_PLAN = {
    "$schema": DIRECTOR_SCHEMA_URI,
    "schemaVersion": 1,
    "sceneId": "start",
    "storyHash": "sha256:" + "0" * 64,
    "profile": "chronicle-subtle",
    "title": "Widget 上手",
    "subtitle": "",
    "beats": [
        {"id": "b000001", "kind": "dialogue", "speaker": "widget", "text": "你好，我是 widget。"},
        {"id": "b000002", "kind": "dialogue", "speaker": "Rust", "text": "先用可编辑模式装好开发依赖。"},
    ],
}

_ASSET_CATALOG = {
    "changeBg": frozenset({"bg.webp"}),
    "changeFigure": frozenset(),
    "bgm": frozenset({"s_Title.mp3"}),
}


def validate_plan(plan: dict, *, mode: str) -> PerformanceReport:
    report = PerformanceReport(story_hash="sha256:" + "0" * 64)
    loaded = load_director(
        json.dumps(plan, ensure_ascii=False),
        report=report,
        story_hash="sha256:" + "0" * 64,
        profile="chronicle-subtle",
    )
    assert loaded is not None
    validate_director(
        loaded,
        report=report,
        draft_ids=["b000001", "b000002"],
        cast_names={"widget", "Rust"},
        mode=mode,
        asset_pack=None,
        asset_catalog=_ASSET_CATALOG,
        profile="chronicle-subtle",
    )
    return report


def test_validate_rejects_narration_in_quickstart():
    plan = json.loads(json.dumps(QUICKSTART_PLAN, ensure_ascii=False))
    plan["beats"][0] = {"id": "b000001", "kind": "narration", "speaker": None, "text": "这是旁白。"}
    report = validate_plan(plan, mode="quickstart")
    assert report.errors >= 1
    assert any("贡献者上手" in finding["message"] for finding in report.findings)


def test_validate_quickstart_rejects_speakerless_choice_text():
    plan = json.loads(json.dumps(QUICKSTART_PLAN, ensure_ascii=False))
    plan["beats"][1] = {
        "id": "b000002",
        "kind": "choice",
        "text": "选一个方向",
        "choices": [{"text": "先跑测试", "target": "b000001"}],
    }
    report = validate_plan(plan, mode="quickstart")
    assert any("禁止无说话人的 choice 文本" in finding["message"] for finding in report.findings)


def test_fallback_quickstart_assigns_guide():
    script = compile_draft_fallback(["这是说明。"], cast=["widget", "Rust"], mode="quickstart")
    assert script.startswith("widget:这是说明。;")
    assert "say:" not in script


# --- pipeline 与 CLI ---

class FakeLLM:
    def __init__(self, texts=None):
        self.texts = list(texts) if texts is not None else []
        self.calls = []

    def complete(self, prompt, *, temperature=0.8):
        self.calls.append((prompt, temperature))
        if not self.texts:
            raise AssertionError(f"FakeLLM 响应耗尽（第 {len(self.calls)} 次调用）")
        return self.texts.pop(0)


def quickstart_llm(plan: dict | None = None):
    return FakeLLM(
        [
            QUICKSTART_DRAFT,
            QUICKSTART_ANNOTATIONS,
            json.dumps(plan or QUICKSTART_PLAN, ensure_ascii=False),
        ]
    )


def quickstart_options(tmp_path, **overrides) -> RunOptions:
    values = {
        "owner": "acme",
        "repo": "widget",
        "output_dir": tmp_path / "out",
        "backup_root": tmp_path / "backup",
        "mode": "quickstart",
        "reuse_backup": True,
        "api_key": None,
    }
    values.update(overrides)
    return RunOptions(**values)


def test_pipeline_quickstart_runs_mode_specific_prompt_and_packaging(tmp_path):
    llm = quickstart_llm()
    captured = {}

    def fake_fetch(options_, log, progress):
        captured["fetch_mode"] = options_.mode
        return make_ctx()

    def fake_package(clean, output_dir, **kwargs):
        captured["game_name"] = kwargs["game_name"]
        captured["game_key"] = kwargs["game_key"]
        captured["clean"] = clean
        return output_dir

    artifacts = run_pipeline(
        quickstart_options(tmp_path),
        llm_client=llm,
        fetch_fn=fake_fetch,
        package_fn=fake_package,
    )

    assert captured["fetch_mode"] == "quickstart"
    assert len(llm.calls) == 3
    assert "贡献者上手" in artifacts.prompt
    assert "补一个边界用例" in artifacts.prompt
    assert artifacts.director_report.semantic_valid is True
    assert captured["game_name"] == "acme/widget 贡献者上手"
    assert captured["game_key"] == "repo2gal_acme_widget_quickstart"
    assert captured["clean"].endswith("end;\n")
    assert "say:" not in captured["clean"]  # Quick Start 不使用旁白


def test_pipeline_quickstart_rejects_narration_and_falls_back_to_guide(tmp_path):
    plan = json.loads(json.dumps(QUICKSTART_PLAN, ensure_ascii=False))
    plan["beats"][0] = {"id": "b000001", "kind": "narration", "speaker": None, "text": "这是旁白。"}
    llm = FakeLLM(
        [QUICKSTART_DRAFT, QUICKSTART_ANNOTATIONS, json.dumps(plan, ensure_ascii=False)] + ["bad"] * 2
    )

    artifacts = run_pipeline(
        quickstart_options(tmp_path),
        llm_client=llm,
        fetch_fn=lambda *args: make_ctx(),
        package_fn=lambda clean, output, **kwargs: output,
    )

    assert artifacts.director_plan is None
    assert artifacts.director_report.degraded is True
    assert "widget:" in artifacts.clean
    assert "say:" not in artifacts.clean  # 兜底同样遵守 Quick Start 无旁白


def test_cli_maps_quickstart_mode_and_output_dir(monkeypatch, tmp_path):
    captured = {}

    class _Artifacts:
        output_dir = None
        report = None
        prompt = "prompt"

    monkeypatch.setattr(
        cli,
        "run_pipeline",
        lambda options, **kwargs: captured.update(options=options) or _Artifacts(),
    )
    result = CliRunner().invoke(cli.main, ["acme/widget", "--mode", "quickstart"])

    assert result.exit_code == 0, result.output
    assert captured["options"].mode == "quickstart"
    assert captured["options"].output_dir == Path("output") / "widget-quickstart"
