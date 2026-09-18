---
name: repo2gal
description: 把 GitHub 仓库（README/源码/Issue/PR/Discussion/wiki/Release）变成可游玩的 WebGAL 视觉小说（galgame），三种模式：chronicle 编年史、overview 仓库概览、quickstart 贡献者上手。本技能让 harness agent 亲自担任三轮创作（剧情草稿 → 演出批注 → 导演 JSON），自带确定性内核负责抓取、Schema/语义校验、WebGAL 编译与打包。触发词：galgame、视觉小说、WebGAL、repo2gal、把仓库做成游戏、仓库编年史、给仓库做个介绍游戏。
---

# Repo2Gal（harness 版，自包含技能）

把一个 GitHub 仓库做成可游玩的 WebGAL 视觉小说。**你（harness agent）就是 Repo2Gal 原本内置的那个 LLM**：
三轮创作由你读写工作目录里的文件完成；确定性部分（抓取、选角、prompt 模板、校验、编译、打包）由本技能自带的
`scripts/r2g_core/` 完成。技能不依赖外部 Repo2Gal 仓库，也不依赖已安装的 `repo2gal` 包：
三轮创作不需要任何 LLM API Key，确定性内核不需要联网调用模型。

## 需求确认（用 ask 工具，一次问完）

允许用户全部吃默认值；用户没意见就按默认继续，不要重复追问。

| 问题 | 选项 | 默认 |
|---|---|---|
| 目标仓库 | owner/repo 或 GitHub URL | 必答 |
| 剧本模式 | chronicle 编年史（素材最全、最慢）/ overview 仓库概览（最快：是什么、怎么用）/ quickstart 贡献者上手（跑起来→改一处→提交） | chronicle |
| 演出风格 | chronicle-subtle 克制 / chronicle-cinematic 热闹 | chronicle-subtle |
| 素材包 | 不用 / 内置 CC0 示例包 `builtin:cc0-chronicle` | 不用 |
| 采集与输出 | 联网全量采集 / `--reuse-backup` 复用已有备份；产物目录 | 联网；`output/<repo>[-<mode>]` |

## 环境准备（每个会话一次）

下文 `<skill>` = 本技能目录（加载技能时给出的 base directory）。
确定性内核随技能分发在 `<skill>/scripts/r2g_core/`，**不需要 pip install 任何 Repo2Gal 包，也不需要 `REPO2GAL_HOME`**。

1. `python "<skill>/scripts/setup_env.py"` → 在 `<skill>/.venv` 建 venv 并安装依赖，打印 venv 解释器路径（首次会联网安装）；
   `--check` 只检查现状，`--venv <dir>` 可改用其他 venv 位置。
2. 之后**每一步都用这个解释器，不要换别的 python**：`"<venv-python>" "<skill>/scripts/r2g_agent.py" ...`。
3. 联网采集用 `GITHUB_TOKEN`；没设环境变量时脚本会读 `~/.repo2gal-token`（只报来源，不回显内容）。`--reuse-backup` 不需要令牌。
4. 首次 `build` 会下载固定版本 WebGAL 发行版（几十 MB，SHA-256 校验）到 `~/.cache/repo2gal`。
5. `--asset-pack` 只在支持 `openat`/`O_NOFOLLOW` 且装有 libmagic 的平台可用（Linux/macOS）；
   不具备时用默认素材（3 张背景 + 1 首 BGM，没有立绘），演出只用背景、转场与音乐。`doctor` 会报告当前平台是否支持。

## 流程（一条线，`--workdir` 只放阶段产物）

工作目录建议 `<cwd>/.repo2gal/work/<repo>-<mode>`（`.repo2gal/` 已在 .gitignore 里）；产物目录用绝对路径显式传给 `prepare --output`。
标 `[你写]` 的文件由你写；其余文件由脚本生成，不要手改。

| 步骤 | 命令 | 你要做的 | 产出 |
|---|---|---|---|
| 抓取 | `... prepare <owner/repo> --workdir W --mode M --profile P --output O [--reuse-backup] [--asset-pack builtin:cc0-chronicle]` | 等待（chronicle 全量采集可能 10 分钟以上） | `state.json`、`00-draft-prompt.md` |
| 草稿 | 读 `00-draft-prompt.md` | 按它写 `01-draft.md` | `01-draft.md` [你写] |
| 规范化 | `... annotate --workdir W` | 读 `02-annotations-prompt.md`，写 `02-annotations.md` | `01-draft.canonical.md`、`01-beats.json`、`02-annotations-prompt.md` |
| 导演 | `... director --workdir W` | 读 `03-director-prompt.md`，写 `03-director.json` | `03-director-prompt.md` |
| 校验 | `... check --workdir W` | 未通过（退出码 2）时按 `03-director-feedback.md` 改 `03-director.json` 重跑 | `03-director-report.json`、`03-director-feedback.md` |
| 打包 | `... build --workdir W [--dry-run] [--fallback]` | 3 轮 check 仍不过时改用 `--fallback` | `04-clean.txt`、WebGAL 产物目录 |

随时 `... status --workdir W` 看进度、`... doctor` 看环境（内核来源、令牌、模板缓存、素材包平台支持）。

### 草稿规则（第一轮）

- 严格按 `00-draft-prompt.md` 的格式：每个节拍以单独一行 `[B]` 开头；台词写 `角色名:台词`（半角冒号，角色名逐字来自角色表）；选择写一行 `选择：A. … / B. …`；**草稿里绝不写 WebGAL 语法、背景、立绘、动画**。
- 忠于素材：角色名、版本号、术语、争论观点全部来自 prompt 里的仓库资料，宁缺勿造。
- 长度按 prompt 要求（chronicle 120–200 节拍）。可拆成多次追加写入 `01-draft.md`，但最终文件必须完整。
- 台词里不能出现 `;`、`|`、" -"（空格+连字符）。

### 批注规则（第二轮）

自然语言，按节拍给演出建议（背景/音乐/立绘出入场/移动/转场/特效/节奏），不写 WebGAL 命令、不新增剧情；没有演出价值的节拍直接说明「无演出」。整篇留空也合法（脚本按「无批注」处理）。

### 导演 JSON 规则（第三轮）

- 只输出一个 JSON（允许 ```json 围栏，不要夹解释文字）；`beats` 的数量与 id 必须与草稿逐一对应：`b000001`…`b0000NN`，不合并、不拆分、不重排。
- 台词逐字来自草稿；`speaker` 逐字来自 `state.json` 的 `cast`；背景/音乐逐字来自 prompt 的可用素材；动作取值只能来自 prompt 里的 capability registry。
- overview / quickstart 不用旁白：所有文本都必须有 speaker。
- 文本禁止 `;` 与 " -"；选项文本还禁止 `:` 与 `|`。

## 硬边界

- **绝不**调用外部 LLM：三轮创作全部由你自己写文件完成；不要设 `REPO2GAL_API_KEY`，也不要去找 `repo2gal` CLI。
- **绝不**跳过 `check`，**绝不**绕过 validator：只有 `04-clean.txt` 会进打包。
- **绝不**在导演轮改台词、加角色、改素材名——`check` 会用结构化反馈打回。
- `check` 最多重试 3 轮；仍失败就用 `build --fallback`（草稿确定性兜底，产物依然可玩）。

## `check` 失败 → 修法

| 反馈 | 修法 |
|---|---|
| beat 必须与草稿一一对应 | id 序列与数量严格对齐 `01-beats.json` |
| speaker 不在角色表中 | 逐字使用 `state.json` 里的 cast 名单 |
| 该模式不使用旁白 | overview/quickstart 把 narration 文本改成向导的 dialogue |
| 文本包含禁止字符 | 去掉 `;`、` -`；选项文本还要去掉 `:`、`|` |
| 背景/音乐素材不可用 | 只用 prompt「可用素材」里的名字 |
| 角色没有可用立绘 / 尚未入场 | 只用有立绘的角色，先 `figure.enter` 再 move/shake/animate |
| cue 超过 profile 预算 | 每个 cue ≤ maxActionsPerCue（subtle 2 / cinematic 3），总 cue 数也有比例上限 |
| 未注册的 preset | 取值只能来自 capability registry |

## 收尾

产物目录（`state.json` 的 `outputDir`）是纯静态站点：`python -m http.server -d <outputDir> 8000` → http://localhost:8000 即可游玩。
可选：用 `--strict` 重跑 `build` 确认 validator 零降级；把 `04-clean.txt` 给用户看剧本。

## 技能范围

- 自带：`scripts/r2g_core/`（fetcher 采集、generator 选角与第一轮 prompt、director 三轮协议与编译、validator、packager、asset_pack、webgal_assets、performance）、`prompts/`、`schemas/`、`legal/`、内置 CC0 素材包。
- 不含（上游 Repo2Gal 仓库里与 agent 驱动无关、已刻意剥离的部分）：LLM 客户端、`repo2gal` CLI、自动 pipeline 编排、多场景切分。
  skill 版把「三轮创作」交给 agent，把「其余一切」交给确定性内核，两者之间只有 `--workdir` 里的文件契约。
