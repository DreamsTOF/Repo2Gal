# 请求目录（`requests/`）

一个文件 = 一个仓库的一份 galgame。**文件名就是站点子域名**：

```
requests/vue-core.yml   →   https://vue-core.repo2gal-gallery.pages.dev
                            https://vue-core.repo2gal-gallery.pages.dev/demo   （直链，跳过首页）
```

## 怎么提

1. Fork 本仓库，新建一个分支；
2. 复制 `requests/_template.yml` 为 `requests/<你的 slug>.yml`（下划线开头的文件不会被当成请求）；
3. 填好字段，提交 PR；
4. `requests-validate` 会在 PR 里贴一条校验评论（格式 + 目标仓库是否公开），**不消耗 LLM、不需要任何密钥**；
5. 合并到 `main` 后，`requests-run` 自动挑出本次改动的请求、逐个生成并部署，
   链接会出现在 Actions 运行的摘要里（同一 slug 重复合并 = 覆盖同一个站点）。

## 字段

| 字段 | 必填 | 取值 | 说明 |
| --- | --- | --- | --- |
| `repo` | ✅ | `owner/name` | 目标仓库，**必须公开**（校验时会实际访问 GitHub 确认） |
| `mode` | ✅ | `overview` / `chronicle` / `quickstart` | `overview` 概览（是什么、怎么用、代码怎么组织）；`chronicle` 编年史（诞生、争论、社区演变）；`quickstart` 贡献者上手（跑起来 → 改一处 → 提交） |
| `profile` | | `chronicle-subtle`（默认）/ `chronicle-cinematic` | 演出风格：克制 / 热闹 |
| `asset_pack` | | `none`（默认）/ `builtin:cc0-chronicle` | 素材包；用素材包时会按公开发布标准校验许可证 |
| `force` | | `true` / `false`（默认） | 无视"已生成过"的判断，强制重做这个站点 |

写错的字段名**不会**被静默忽略，PR 校验会直接报出来。

## slug 规则

- 3–40 位，只用小写字母、数字、连字符，首尾必须是字母或数字；
- 不能用 `main` / `master` / `www` / `production` / `repo2gal-gallery`（会撞上 Pages 的生产分支或平台名）；
- 同一个 slug 就是同一个站点，重复提交等于覆盖（旧产物会被新部署替换）。

## 重复请求怎么处理（已经生成过的不会再生成）

两层去重，都不需要人工记账：

1. **同一个内容只允许一个 slug**：同一个（`repo`, `mode`, `profile`, `asset_pack`）在 `requests/` 下出现两次，
   PR 校验会直接报错 —— 要么删掉多余的，要么改 `mode`/`profile` 让它成为另一个站点。
2. **同一个站点已用同一个源提交生成过就跳过**：每个站点根目录有一份 `site-meta.json`，
   记着上次用的源提交（`sourceCommit`）。合并请求时，若该站点的 `site-meta.json` 与目标仓库
   当前默认分支 HEAD 一致，就跳过生成、不花 LLM。
   - 强制重做：在请求文件里写 `force: true`（维护者也可以手动跑 `requests-run` 并勾选 `force`）；
   - 目标仓库有了新提交 → 下次合并会自动重新生成（这就是"刷新"的方式）；
   - 状态跟着产物走，不需要 CI 回写仓库，因此和分支保护（要求 PR、禁止直推）不冲突。

多个人（或多个 fork）重复提同一个仓库：内容重复的会被规则 1 挡住；同一个 slug 重复提会在合并时被规则 2 跳过。

## 陌生人提 PR 会怎样

- 只有仓库**写权限**的人能合并，合并才触发生成 —— 这是唯一的闸门，花的是本仓库配置的 LLM 配额；
- 别人 fork 出来的仓库**没有 secrets**，所以只能提 PR，不能自己触发生成；
- 校验 workflow 只 checkout 基线分支的 `tools/`，PR 内容仅作为**数据**读入（不执行 PR 里的代码）；
- fork PR 默认拿不到写评论的权限，报告会降级到 Job Summary，结论一样可见。

## 一次请求要多久、花什么

- 采集 + 三轮 LLM 创作 + 严格校验 + 打包 + 部署：小仓库大约 **10–30 分钟**；
- 用的是本仓库配置的 LLM 与 Cloudflare 配额（`REPO2GAL_API_KEY`、`CLOUDFLARE_API_TOKEN` 等 secrets）；
- `chronicle` 模式要抓 Issue/PR/Discussion，明显比 `overview` 慢。

## 本地先自查（可选）

```bash
pip install pyyaml
python3 tools/validate_requests.py --all                 # 校验 requests/ 下全部请求
python3 tools/validate_requests.py requests/vue-core.yml  # 只校验一个
python3 tools/plan_requests.py --all                      # 看会生成哪些站点（不触发任何东西）
```

## 出问题了看哪里

| 现象 | 去哪看 |
| --- | --- |
| PR 里校验评论报格式错 | 按评论里的字段提示改，`requests/README.md` 有完整取值表 |
| 合并后没有生成 | `requests-run` 的 `plan` 任务日志会写明"本次 push 改动"里挑到了什么 |
| 生成失败 | `generate-galgame` 任务日志；LLM 超时/限流会表现为重试或失败 |
| 站点 404 | 部署完成即生效，链接形如 `https://<slug>.repo2gal-gallery.pages.dev`；确认 slug 拼写 |