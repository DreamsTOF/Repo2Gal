"""演出编译内核：能力表、profile、动作级 WebGAL 编译与共享状态工具。

自 v0.7.0 起，本模块只保留确定性编译内核。LLM 计划级入口（Beat Manifest、
Performance Plan 校验/编译/插入合并）已被 :mod:`repo2gal.director` 的 Director
流程取代：三轮 LLM（创作 -> 批注 -> 导演 JSON）加确定性编译。

LLM 仍只返回受限语义 JSON；WebGAL 命令、资源路径、runtime target、坐标和时序参数
全部由确定性代码生成（``_compile_action`` 是唯一动作编译入口）。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .asset_pack import AssetPack
from .webgal_assets import figure_base_transform

DEFAULT_PROFILE = "chronicle-subtle"
PROFILES = {
    "chronicle-subtle": {
        "maxCuesPerBeatRatio": 1 / 3,
        "maxActionsPerCue": 2,
        "maxScreenEffects": 1,
        "allowDramaticShake": False,
    },
    "chronicle-cinematic": {
        "maxCuesPerBeatRatio": 1 / 2,
        "maxActionsPerCue": 3,
        "maxScreenEffects": 2,
        "allowDramaticShake": True,
    },
}

CAPABILITIES: dict[str, Any] = {
    "webgalVersion": "4.6.2",
    "figureMotions": ["none", "from-left", "from-right", "fade"],
    "figureAnimations": ["shockwaveIn", "shockwaveOut", "move-front-and-back"],
    "transitionPresets": ["shockwaveIn", "shockwaveOut"],
    "pixiEffects": ["snow", "rain", "cherryBlossoms", "heavySnow"],
    "slots": ["left", "center", "right"],
    "durations": {"instant": 0, "short": 500, "medium": 1200, "long": 2500},
    "slotTransforms": {
        "left": {"x": -500, "y": 0},
        "center": {"x": 0, "y": 0},
        "right": {"x": 500, "y": 0},
    },
}


@dataclass
class PerformanceReport:
    """导演/演出校验报告：错误可回喂 LLM 重试，degraded 表示已降级。"""

    schema_valid: bool = False
    semantic_valid: bool = False
    degraded: bool = False
    findings: list[dict[str, Any]] = field(default_factory=list)
    cue_count: int = 0
    action_count: int = 0
    compiled_command_count: int = 0
    story_hash: str | None = None
    plan_hash: str | None = None

    @property
    def errors(self) -> int:
        return sum(1 for finding in self.findings if finding.get("kind") == "error")

    def add(self, kind: str, message: str, *, cue_id: str | None = None, beat_id: str | None = None) -> None:
        finding: dict[str, Any] = {"kind": kind, "message": message}
        if cue_id is not None:
            finding["cueId"] = cue_id
        if beat_id is not None:
            finding["beatId"] = beat_id
        self.findings.append(finding)
        if kind == "error":
            self.degraded = True

    def summary(self) -> str:
        if not self.findings:
            return f"导演计划通过：{self.cue_count} 个 cue，{self.action_count} 个动作"
        buckets: dict[str, int] = {}
        for finding in self.findings:
            kind = str(finding.get("kind", "note"))
            buckets[kind] = buckets.get(kind, 0) + 1
        detail = "，".join(f"{key}×{value}" for key, value in sorted(buckets.items()))
        return f"导演计划：{detail}，保留 {self.cue_count} 个 cue"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "schemaValid": self.schema_valid,
            "semanticValid": self.semantic_valid,
            "degraded": self.degraded,
            "cueCount": self.cue_count,
            "actionCount": self.action_count,
            "compiledCommandCount": self.compiled_command_count,
            "storyHash": self.story_hash,
            "planHash": self.plan_hash,
            "webgalVersion": CAPABILITIES["webgalVersion"],
            "capabilityHash": capability_hash(),
            "findings": self.findings,
        }


def capability_hash() -> str:
    payload = json.dumps(CAPABILITIES, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_runtime_id(character: str) -> str:
    value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", character).strip("-")
    return f"fig-{value or 'character'}"


def _copy_state(state: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(state)


def _initial_state() -> dict[str, Any]:
    return {
        "background": None,
        "backgroundStatementIndex": None,
        "bgm": None,
        "figures": {},
        "figureVersions": {},
        "ambiguousFigures": [],
    }


def _merge_incoming_states(states: list[dict[str, Any]]) -> dict[str, Any]:
    """分支汇合：所有入边一致的状态才保留，不一致的角色记为 ambiguous。"""
    if len(states) == 1:
        return _copy_state(states[0])
    merged = _initial_state()
    for key in ("background", "backgroundStatementIndex", "bgm"):
        values = [state.get(key) for state in states]
        merged[key] = values[0] if all(value == values[0] for value in values) else None
    all_characters = set().union(*(state.get("figures", {}) for state in states))
    for character in sorted(all_characters):
        figures = [state.get("figures", {}).get(character) for state in states]
        if figures[0] is not None and all(figure == figures[0] for figure in figures):
            merged["figures"][character] = _copy_state(figures[0])
        else:
            merged["ambiguousFigures"].append(character)
    for character in set().union(*(state.get("figureVersions", {}) for state in states)):
        merged["figureVersions"][character] = max(
            int(state.get("figureVersions", {}).get(character, 0)) for state in states
        )
    return merged


def _asset_character_map(asset_pack: AssetPack | None) -> dict[str, dict[str, Any]]:
    if asset_pack is None:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for asset in asset_pack.assets.values():
        if asset.type != "character":
            continue
        character = asset.metadata.get("character")
        if isinstance(character, str) and character not in result:
            result[character] = {
                "character": character,
                "asset": asset.logical_id,
                "emotion": asset.metadata.get("emotion", "normal"),
            }
    return result


def _parse_json_response(raw: str) -> dict[str, Any]:
    """解析 LLM JSON 输出：剥 Markdown 围栏、拒绝重复键、要求顶层 object。"""
    text = raw.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()

    def build(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"JSON 存在重复键：{key}")
            value[key] = item
        return value

    value = json.loads(text, object_pairs_hook=build)
    if not isinstance(value, dict):
        raise ValueError("JSON 顶层必须是 object")
    return value


def _duration(value: str) -> int:
    return int(CAPABILITIES["durations"][value])


def _sync_suffix(anchor: str) -> str:
    return " -parallel" if anchor == "during" else " -next"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _asset_for_character(character: str, asset_pack: AssetPack | None) -> str | None:
    return _asset_character_map(asset_pack).get(character, {}).get("asset")


def _compile_action(action: dict[str, Any], anchor: str, asset_pack: AssetPack | None, state: dict[str, Any]) -> list[str]:
    """把一个语义动作确定性编译成 WebGAL 命令行（不含转场，转场并入 changeBg）。

    ``state`` 是当前分支的角色运行态（character -> figure），进入/退出/移动会更新它，
    供后续动作与后续 beat 编译复用 runtime id 与 framing。
    """
    kind = action["kind"]
    suffix = _sync_suffix(anchor)
    if kind == "figure.enter":
        character = action["character"]
        asset = _asset_for_character(character, asset_pack)
        if asset is None:
            return []
        runtime_id = _safe_runtime_id(character)
        framing = figure_base_transform(asset_pack.assets[asset]) if asset_pack is not None else None
        state[character] = {
            "visible": True,
            "slot": action["slot"],
            "runtimeId": runtime_id,
            "asset": asset,
            "framing": framing,
        }
        transform = copy.deepcopy(framing) if framing is not None else {
            "position": {"x": 0, "y": 0},
            "scale": {"x": 1, "y": 1},
        }
        transform["position"]["x"] += CAPABILITIES["slotTransforms"][action["slot"]]["x"]
        duration = _duration(action["duration"])
        motion = action["motion"]
        slot_flag = f" -{action['slot']}" if action["slot"] in ("left", "right") else ""
        if motion == "none":
            return [
                f"changeFigure:{asset}{slot_flag} -duration=0 -id={runtime_id}{suffix};"
            ]
        final = copy.deepcopy(transform)
        final["alpha"] = 1
        return [
            f"changeFigure:{asset}{slot_flag} -repo2galEnter={motion} -duration=0 "
            f"-id={runtime_id} -next;",
            f"setTransform:{_json(final)} -duration={duration} -target={runtime_id}{suffix};",
        ]
    if kind == "figure.exit":
        figure = state.get(action["character"], {})
        runtime_id = figure.get("runtimeId", _safe_runtime_id(action["character"]))
        state.pop(action["character"], None)
        duration = _duration(action["duration"])
        if action["motion"] == "fade":
            if anchor == "during":
                return [
                    f"setTransform:{_json({'alpha': 0})} -duration={duration} "
                    f"-target={runtime_id} -parallel;"
                ]
            return [
                f"setTransform:{_json({'alpha': 0})} -duration={duration} -target={runtime_id} -next;",
                f"changeFigure:none -id={runtime_id} -duration=0{suffix};",
            ]
        return [f"changeFigure:none -id={runtime_id} -duration=0{suffix};"]
    if kind == "figure.move":
        character = action["character"]
        figure = state.get(character, {})
        runtime_id = figure.get("runtimeId", _safe_runtime_id(character))
        state[character] = {**figure, "visible": True, "slot": action["to"], "runtimeId": runtime_id}
        framing = figure.get("framing") or {}
        framing_position = framing.get("position", {})
        slot_position = CAPABILITIES["slotTransforms"][action["to"]]
        transform: dict[str, Any] = {
            "position": {
                "x": slot_position["x"] + framing_position.get("x", 0),
                "y": framing_position.get("y", slot_position["y"]),
            }
        }
        if framing.get("scale"):
            transform["scale"] = framing["scale"]
        return [
            f"setTransform:{_json(transform)} -duration={_duration(action['duration'])} "
            f"-target={runtime_id} -ease={action['easing']}{suffix};"
        ]
    if kind == "figure.shake":
        character = action["character"]
        figure = state.get(character, {})
        runtime_id = figure.get("runtimeId", _safe_runtime_id(character))
        duration = _duration(action["duration"])
        delta = {"subtle": 24, "normal": 48, "dramatic": 80}[action["intensity"]]
        slot_position = CAPABILITIES["slotTransforms"].get(
            figure.get("slot", "center"), CAPABILITIES["slotTransforms"]["center"]
        )
        framing_position = (figure.get("framing") or {}).get("position", {})
        base_x = float(slot_position["x"] + framing_position.get("x", 0))
        base_y = float(framing_position.get("y", slot_position["y"]))
        quarter = max(1, duration // 4)
        keyframes = [
            {"duration": 0, "position": {"x": base_x, "y": base_y}},
            {"duration": quarter, "position": {"x": base_x - delta, "y": base_y}},
            {"duration": quarter, "position": {"x": base_x + delta, "y": base_y}},
            {"duration": quarter, "position": {"x": base_x - delta // 2, "y": base_y}},
            {"duration": quarter, "position": {"x": base_x, "y": base_y}},
        ]
        return [f"setTempAnimation:{_json(keyframes)} -target={runtime_id}{suffix};"]
    if kind == "figure.animate":
        figure = state.get(action["character"], {})
        runtime_id = figure.get("runtimeId", _safe_runtime_id(action["character"]))
        duration = _duration(action["duration"])
        if action["preset"] == "move-front-and-back":
            scale = (figure.get("framing") or {}).get("scale", {"x": 1, "y": 1})
            base_x = float(scale.get("x", 1))
            base_y = float(scale.get("y", 1))
            half = max(1, duration // 2)
            keyframes = [
                {"duration": 0, "scale": {"x": base_x, "y": base_y}},
                {"duration": half, "scale": {"x": round(base_x * 1.06, 3), "y": round(base_y * 1.06, 3)}},
                {"duration": half, "scale": {"x": base_x, "y": base_y}},
            ]
            return [f"setTempAnimation:{_json(keyframes)} -target={runtime_id}{suffix};"]
        if action["preset"] == "shockwaveIn":
            keyframes = [
                {"duration": 0, "shockwaveFilter": 0, "radiusAlphaFilter": 0},
                {"duration": duration, "shockwaveFilter": 3.05, "radiusAlphaFilter": 1.05},
            ]
        else:
            keyframes = [
                {"duration": 0, "shockwaveFilter": 0},
                {"duration": duration, "shockwaveFilter": 3},
            ]
        return [f"setTempAnimation:{_json(keyframes)} -target={runtime_id}{suffix};"]
    if kind == "screen.transition":
        return []
    if kind == "screen.effect":
        return ["pixiInit;", f"pixiPerform:{action['preset']};"]
    return []


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
