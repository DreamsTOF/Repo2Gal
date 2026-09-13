# Repo2Gal 用户指南

给从宣传视频、搜索引擎或朋友推荐过来的新朋友：这一页告诉你怎么玩、怎么生成自己的作品，
以及常见问题。

## 1. 这是什么

Repo2Gal 把一个 GitHub 仓库变成一部**可游玩的视觉小说**。当前有三种剧本模式：

- **Chronicle（编年史，默认）**：讲述项目为何诞生、经历过哪些争论、社区如何演变。
  素材来自源码、README、Issue、PR、Discussion、wiki 与 Release。
- **Overview（仓库概览）**：面向第一次接触项目的玩家，由“新手村向导”介绍项目定位、
  核心特性、安装与快速开始、目录结构和继续深入入口；该模式不使用旁白，所有介绍
  都由向导角色亲口说出。素材来自源码、README、目录树、根级项目文件、Release 与
  wiki，采集更轻量。
- **Quick Start（贡献者上手）**：面向想给项目交第一个改动的人，由项目化身带路：
  准备开发环境、跑测试、看代码地图、遵守提交流程，最后从一个真实的
  `good first issue` 起步任务开始动手；同样不使用旁白。素材来自源码、README、
  目录树与项目文件，以及开放的新人友好 Issue。

三种模式的角色都由代码确定性推导（不交给模型胡编），产物都是纯静态网站，
双击或任意静态托管即可游玩。

**先玩一局在线演示**（就是本项目自己的编年史，用当前版本代码 dogfooding 生成）：

👉 **https://repo2gal.rhopaper.top/demo**

## 2. 怎么玩

演示与产物都基于 WebGAL 引擎：

- 点击画面 / 空格 / 回车推进对话；按 `Ctrl` 快进；
- 右上角菜单可以**存档、读档、快进、自动播放**，还有**流程图**（当前单章作品只有一个入口节点）与鉴赏；
- 分支选择用鼠标点击选项。

## 3. 生成你自己的作品

### 准备

1. Python 3.10+ 与 git；使用 Asset Pack 时还需系统 `libmagic`（Debian/Ubuntu：`libmagic1`）
   及 `openat`/`O_NOFOLLOW` 支持；
2. `GITHUB_TOKEN`：GitHub 个人访问令牌（Discussion 走 GraphQL，必须认证）；
3. `REPO2GAL_API_KEY`：任意 OpenAI 兼容服务的 API Key（DeepSeek、Kimi、本地 vLLM 等）。

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
export GITHUB_TOKEN=github_pat_xxx
export REPO2GAL_API_KEY=sk-xxx

.venv/bin/repo2gal owner/repo          # 全流程：采集 -> 生成 -> 校验 -> 打包
python3 -m http.server -d output/<repo> 8000   # 本地预览
```

### 选择剧本模式

```bash
.venv/bin/repo2gal owner/repo                    # Chronicle 编年史（默认）
.venv/bin/repo2gal owner/repo --mode overview    # Overview 仓库概览
.venv/bin/repo2gal owner/repo --mode quickstart  # Quick Start 贡献者上手
```

Chronicle 产物默认写入 `output/<repo>`，其他模式写入 `output/<repo>-<模式名>`
（如 `output/<repo>-overview`、`output/<repo>-quickstart`），互不覆盖；也可以用
`--output` 显式指定。Overview 的采集范围只有源码、Release 与 wiki，Quick Start 只有
源码、Issue 与评论、wiki，都不拉取不需要的社区数据，首次运行通常快很多；复用已有完整
备份时也会跳过无关解析。三种模式共用同一套 validator、Asset Pack、动态演出与打包流程。

Quick Start 的起步任务取自带 `good first issue`、`help wanted`、`beginner` 等标签的
开放 Issue，数量由 `--threads` 控制（默认 12）；仓库里没有这类标签时，剧本会改为讲解
如何自己筛选合适的任务，不会编造 Issue 编号。

### 不花钱 / 离线玩法

```bash
.venv/bin/repo2gal vuejs/core --dry-run        # 只抓数据并打印 prompt，不调用 LLM
.venv/bin/repo2gal vuejs/core --mode overview --dry-run   # 查看 Overview prompt
.venv/bin/repo2gal vuejs/core --mode quickstart --dry-run # 查看 Quick Start prompt
.venv/bin/repo2gal vuejs/core --script my_story.txt   # 手写剧本走完打包流程
.venv/bin/repo2gal vuejs/core --reuse-backup   # 复用上次原始备份，不联网
```

全部命令行选项、模式矩阵与退出码见 [`README.md`](../README.md#快速开始)。

### 三轮生成与动态演出

剧本由三轮 LLM 协作生成，演出不再需要单独开启：

1. **创作**：第一轮只写故事草稿（自由格式，每个节拍一行 `[B]`），不接触任何 WebGAL 语法；
2. **批注**：第二轮阅读草稿，用自然语言为节拍添加演出批注（出场、动画、转场、特效、音乐）；
3. **导演 JSON**：第三轮把草稿与批注落成受限的 Director Plan JSON；普通代码把它确定性编译成 WebGAL。

```bash
.venv/bin/repo2gal owner/repo \
  --profile chronicle-subtle        # 演出风格与预算（默认）
  --format-retries 2                # 导演 JSON 校验失败时打回第三轮的重试次数（默认 2）
```

第三轮校验失败时会把结构化错误清单反馈给模型重试；重试耗尽后使用第一轮草稿的确定性
兜底编译，产物仍可游玩。当前实现支持立绘进入/退出、语义槽位移动、摇晃、注册的预设动画、
背景转场和场景生命周期 Pixi 效果。模型不生成 WebGAL 命令、坐标、文件名或 runtime ID，
这些全部由 Python 确定性编译。

内置角色素材虽然保留全身原图，但 manifest 带有归一化 `framing` 标注。最终 WebGAL 产物会
默认居中放大为半身构图，腿部藏在画面下方；移动和动画不会重置成全身视图。

调试阶段产物按需保存：

```bash
.venv/bin/repo2gal owner/repo \
  --save-stage-outputs debug/stages   # 草稿、批注、导演 JSON 各次尝试、反馈与校验报告
```

`--strict` 控制最终 WebGAL validator 出现降级时是否以退出码 5 拒绝产物。

### 使用本地素材包

仓库提供一套可公开发布的 CC0 示例素材（Chronicle/Overview 均可使用）：

```bash
.venv/bin/repo2gal assets validate builtin:cc0-chronicle --public
.venv/bin/repo2gal owner/repo \
  --asset-pack builtin:cc0-chronicle --public-assets
```

`--public-assets` 会拒绝 `LicenseRef-Proprietary` 和其他 `LicenseRef-*`；仅在本地使用自有但
不可再分发的素材时，可以不加该选项。最终产物会生成 `THIRD_PARTY_NOTICES.md`，并把原始
manifest、LICENSE、NOTICE 与 evidence 保存到 `third_party/asset-packs/`。

创建自己的包：

```bash
.venv/bin/repo2gal assets init ./my-pack
# 填写 repo2gal-pack.json，加入媒体并更新 SHA-256、LICENSE、NOTICE
.venv/bin/repo2gal assets validate ./my-pack
```

当前一次只支持一个本地包，素材类型限背景、立绘与 BGM；不下载 Git 包、不调用 AI 生成，
也不执行素材包里的脚本。不传 `--asset-pack` 时仍走 WebGAL 默认素材路径；传入包后，默认
背景/BGM 仍可在普通场景中与包内逻辑 ID 一起使用。

### 一次生成要多久、花多少

- Chronicle 采集耗时取决于仓库的 Issue/PR/Discussion 规模：全量第一次可能较慢，
  重跑走上游增量备份；Overview 只采源码、Release 与 wiki，Quick Start 只采源码、
  Issue 与评论、wiki，通常都快得多；
- 三种模式每次都跑三轮 LLM（创作草稿 → 演出批注 → 导演 JSON），单次运行最多
  3 + `--format-retries` 次调用（默认共 5 次），成本约为早期单轮流程的 2.5 倍起，
  取决于所选模型与上下文长度；Overview 与 Quick Start 的上下文通常更短；
- `--dry-run` 完全不花钱，适合先看 prompt 与素材质量；`--save-stage-outputs` 可保存
  三轮各次尝试与校验报告，便于排查质量波动。

## 4. 常见问题

**Q：为什么必须提供 GitHub Token？**
采集依赖成熟的 `python-github-backup`（MIT），Chronicle 模式的 Discussion 走 GraphQL
接口，必须认证。Token 只用于调用官方 API，通过 0600 权限的临时文件传给上游，
不出现在进程列表与日志。

**Q：生成的剧本靠谱吗？**
所有事实来自仓库真实数据；生成后强制过 validator——WebGAL 对未知命令不报错，
而是把命令名当角色名渲染，因此 validator 是硬边界：白名单外的命令一律降级为旁白，
所有旁白统一补 `-clear`（否则会继承上一句角色的名字），跳转目标缺失会注释，
缺 `end;` 会补齐。`--strict` 下存在任何降级即拒绝打包。

**Q：生成作品的版权归谁？**
Repo2Gal 程序代码为 GPL-3.0；WebGAL 引擎为 MPL-2.0；剧本内容按你所选模型的服务条款。
外部素材保持素材包声明的许可证，不会被改标 GPL。详见仓库根目录 `LICENSE`、产物中的
`THIRD_PARTY_NOTICES.md` 与各上游许可声明。

**Q：只有 3 张背景、1 首 BGM？**
这是不传 `--asset-pack` 时的兼容默认值。v0.4.0 已支持单个本地 Asset Pack，并内置两张
CC0 背景、一张透明立绘和一首 BGM 的示例。Git/AI Provider 仍在路线图中，见
`docs/dev/asset-pack-spec.md`。

**Q：报错了怎么办？**
看退出码：2 用法错误、3 采集失败、4 LLM 失败、5 `--strict` 校验降级、6 打包失败。
错误信息已脱敏，可直接贴到 Issue 里求助。
