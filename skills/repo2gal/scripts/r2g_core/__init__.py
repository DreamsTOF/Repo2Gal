"""r2g_core —— Repo2Gal skill 的确定性内核（抓取/校验/编译/打包）。

本包是 Repo2Gal 项目确定性部分的 skill 内联副本：不含 LLM 调用、CLI 与流程编排，
三轮创作全部由 harness agent 读写工作目录里的文件完成。
两侧同名模块是同一份逻辑：改动任一模块时必须同步 `Repo2Gal/repo2gal/` 与
`skills/repo2gal/scripts/r2g_core/`，否则两条分发路径会漂移。
"""

__version__ = "0.8.0"
