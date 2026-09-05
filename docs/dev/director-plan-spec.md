# Director Plan v1：三轮生成、校验与编译边界

> 当前实现以 `repo2gal/director.py` 为准。本文描述协议、状态机、预算和 WebGAL 编译边界；
> 语法权威仍是 `docs/dev/webgal-script-reference.md`。

## 1. 为什么拆成三轮

v0.5.0 的 Performance Plan 是「剧本之后的可选演出层」，LLM 1 既要写剧情又要写
WebGAL 语法。实测中格式错误（漏 `;`、幻觉命令、标签不匹配）频繁发生。v0.7.0 改为
三轮分工：

```text
LLM 1 创作草稿（自由格式，[B] 锚点）
  -> canonicalize_draft() 确定性规范化（[b000001] 锚点）
LLM 2 自然语言演出批注（按 beat 锚定，禁止命令）
LLM 3 导演 JSON（Director Plan v1，台词逐字、演出意图受限枚举）
  -> validate_director()（Schema/状态机/能力表/预算）
  -> compile_director() 确定性生成 WebGAL
  -> validator.sanitize()（最终硬边界）
```

三轮注意力分离后，LLM 在任何一轮都不接触 WebGAL 文本；语法错误类问题由
「LLM 写文本 + validator 事后修复」变为「LLM 写受限 JSON + 普通代码编译」，
错误类型从语法错误收缩为可枚举的语义错误，且能结构化回喂第三轮重试。

## 2. 草稿契约（第一轮）

- 每个节拍一段，段首单独一行 `[B]`；每个 `[B]` 节拍是游戏里的一句话
  （一句台词、一句旁白或一次选择）。
- 台词行写作 `角色名:台词`（半角冒号，角色名必须来自角色表）；旁白写普通散文；
  选择写一行 `选择：A. … / B. …`。
- 不写任何 WebGAL 语法、舞台指令、素材引用。

`canonicalize_draft()` 把 `[B]` 标记规范化为 `b000001` 起递增的稳定锚点；没有锚点时
按空行分段兜底。**Director Plan 的 beat 必须与草稿一对一**（数量相同、id 依次递增），
合并、拆分、重排都是校验错误——这是第三轮对齐批注与台词的前提。

## 3. Director Plan v1 Schema

JSON Schema 文件：`repo2gal/schemas/director-v1.schema.json`（Draft 2020-12，
`$id` 为 `https://repo2gal.dev/schemas/director/v1.json`）。

顶层：`$schema`、`schemaVersion`（const 1）、`sceneId`、`storyHash`、`profile`、
`title`/`subtitle`（可选）、`beats[]`（1–512）。

beat 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | `b[0-9]{6}` | 与草稿锚点一一对应 |
| `kind` | `dialogue` / `narration` / `choice` | 三种语句 |
| `speaker` | string / null | 角色表白名单；narration 必须为 null |
| `text` | string | 台词/旁白/选项前文本；逐字来自草稿 |
| `stage` | `{background, bgm}` | 本 beat 前切背景/音乐，素材必须在目录中 |
| `cue` | `{anchor, actions[]}` | 至多一个 cue，anchor 为 before/during/after |
| `choices` | `[{text, target}]` | 仅 choice；target 是存在的 beat id |
| `jump` | beat id / null | 本 beat 内容后跳转；choice beat 禁止 |

`sceneId`、`storyHash`（草稿规范文本的 SHA-256）、`profile` 是当前确定性运行的绑定
字段：模型照抄错误也由 Python 修正（warning 级），不会因此报废可用计划。

### 动作（复用 v0.5.0 能力表）

`cue.actions` 的 action 形状与旧 Performance Plan v1 一致（`figure.enter/exit/move/
shake/animate`、`screen.transition`、`screen.effect`），取值必须来自
`performance.CAPABILITIES`：

- `figure.enter`：character / slot（left|center|right）/ motion（none|from-left|from-right|fade）/ duration；
- `figure.exit`：character / motion（none|fade）/ duration；
- `figure.move`：character / to / duration / easing；
- `figure.shake`：character / intensity（subtle|normal|dramatic）/ duration；
- `figure.animate`：character / preset（shockwaveIn|shockwaveOut|move-front-and-back）/ duration；
- `screen.transition`：preset / phase / duration，**必须与同 beat 的 `stage.background` 一起出现**；
- `screen.effect`：preset（snow|rain|cherryBlossoms|heavySnow）/ intensity。

## 4. 语义校验（validate_director）

错误逐条写入 `PerformanceReport`（可回喂第三轮重试），维度：

1. **beat 契约**：数量与 id 与草稿一一对应；
2. **角色白名单**：dialogue/choice 的 speaker 必须在角色表中；
3. **模式规则**：Overview 禁止 narration 与无说话人的 choice 文本；
4. **文本保留字符**：text 禁 `;` 与 `" -"`；选项文本还禁 `:` 与 `|`；
5. **素材目录**：stage.background/bgm 必须在 `changeBg`/`bgm` catalog 内；
6. **控制流**：choice/jump 目标存在；choice 与 jump 互斥；
7. **角色状态机**：沿 choose/jump 分支推进（label 处合并入边状态，不一致角色记
   ambiguous 并禁止演出），校验入场/退场/移动/动画的可见性时序；
8. **能力表与预算**：preset/motion/duration/slot 必须注册；`profile` 的
   `maxActionsPerCue`、`maxCuesPerBeatRatio`、`maxScreenEffects`、
   `allowDramaticShake` 预算照旧。

## 5. 有界重试与兜底

- 重试只作用于第三轮；每次失败的 `error` 级 findings 渲染成编号反馈（含 beatId），
  拼进下一次 prompt；次数 = `--format-retries`（默认 2，0 关闭）。
- 全部失败：`compile_draft_fallback()` 只凭第一轮草稿确定性拼出可玩脚本——
  `角色名:台词` 识别为 dialogue，其余按旁白（Overview 归给向导），清洗 `;`/`" -"`。
  产物保证可玩，报告 `degraded=true`。
- 重试是有界确定性循环（无工具调用、无动态路由），不是 agent 循环。

## 6. 确定性编译（compile_director）

- `title[|subtitle]` -> `intro:`；
- 目标 beat 自动生成 `label:<beat id>;`（标签 = beat id，模型不命名标签，死跳转类
  错误归零）；
- `stage` -> `changeBg:` / `bgm:`；`screen.transition` 直接并入同 beat 的 changeBg
  参数（`-enter=/-exit=` 与 `-enterDuration=`），不再事后插入合并；
- cue 动作按 anchor 编译：before/after 用 `-next`，during 用 `-parallel`；动作级编译
  复用 `performance._compile_action`（runtime id、framing、关键帧全部由代码生成）；
- `text` -> `角色名:台词;` / `say:文本 -clear;`；`choices` -> `choose:选项:目标|…;`；
  `jump` -> `jumpLabel:`；末尾 `end;`。

## 7. validator 边界（不变）

编译产物打包前必须经过 `validator.sanitize()`。编译产物使用 `COMPILE_COMMANDS`
白名单（`SAFE_COMMANDS` + `pixiInit`/`pixiPerform`/`setTransform`/`setTempAnimation`）；
`--script` 用户脚本仍收敛在 `SAFE_COMMANDS`。validator 对两类输入都是硬边界：
结构、跳转、素材引用与旁白 `-clear` 归一化照查，理论上编译产物永远通过
（否则是编译器 bug，属错误而非重试目标）。
