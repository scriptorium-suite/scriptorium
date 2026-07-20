# Public Alpha 展示与录制说明

本目录只承载可公开、可复现的**合成 demo** 证据。它不能包含真实论文、Zotero 库、
Agent 对话、用户名、本机路径、终端历史、通知、环境变量值或 API Key。

## 当前材料

- [`demo-poster.svg`](demo-poster.svg)：从机器报告派生的展示图，不是产品 UI 截图；
- [`evidence-manifest.json`](evidence-manifest.json)：来源提交、CI、版本、哈希和隐私声明；
- [`evidence/demo-report.json`](evidence/demo-report.json)：Windows E2E 产生的机器报告快照。
- [`evidence/windows-uat-summary.json`](evidence/windows-uat-summary.json)：隔离 Windows
  源码安装、P1 安全回归与已知边界的脱敏验收摘要。

## 90 秒视频分镜

| 时间 | 画面 | 旁白重点 |
|---|---|---|
| 0–8 秒 | 标题与痛点 | Scriptorium 解决跨会话研究连续性，不是新的聊天框 |
| 8–18 秒 | 文件契约架构图 | 组件不互调内部代码，每类事实只有一个 master |
| 18–38 秒 | 真实运行 `doctor` 与 `demo` | 无 API Key、Zotero、Obsidian 或在线模型 |
| 38–52 秒 | Steward 综述 | 3 条记录中选中 2 条，主题负例被排除 |
| 52–66 秒 | Provenance 搜索与 MCP | 展示本地 FTS5 命中和 current-context |
| 66–78 秒 | `demo-report.json` | 9 阶段、6 断言、9 产物和固定版本 |
| 78–90 秒 | 限制与 CTA | 不代表科学结论；Agent host、Lectern、OS 网络隔离未由该 demo 验证 |

视频建议作为 GitHub Release asset 发布，仓库只保存轻量 poster 与可核验证据，避免把大体积
视频写入 Git 历史。

## 真实截图清单

1. `doctor --target demo` 的版本与能力就绪结果；
2. `demo` 实际阶段与完成行；
3. 生成后的 workspace / provenance / report 文件树；
4. Steward 综述中的两篇文献、负例排除说明和权威引用表；
5. Provenance 搜索命中与 MCP current-context；
6. 报告中的 `passed`、合成标记、断言和 limitations。

截图必须来自专用合成目录。原始截图与带标注版本分开保存；裁剪、加速或省略步骤必须明确
标注。不要把 Lectern 仓库内的 vendored exemplar 当作本项目运行证据；Lectern 需要另行
生成并打开真实 synthetic `.pptx`，显示可编辑对象后才能纳入展示。

## 录制前门禁

- 使用新建 Windows 账号、Windows Sandbox 或等价的干净虚拟环境；
- 显式清空或重定向 `PROVENANCE_*`、`SCRIPTORIUM_*`、Agent home 与 provider 凭据变量；
- 只克隆公开仓库，并用无凭据 HTTPS 验证可访问性；
- 录制前确认路径中不含真实用户名，桌面没有通知或私人窗口；
- 运行隐私扫描并核对 `evidence-manifest.json`；
- 若任何命令解析到合成根之外，立即停止，不继续摄取或写入。

## 不得使用的表述

- “全自动科研操作系统”——当前是按需 pull、人审驱动的 Public Alpha；
- “完全离线、绝不联网”——只能陈述本 demo 不实现网络动作，OS 级出站未观测；
- “已完成真实科研”——这里只验证工程链路，不验证科学结论；
- “Codex 与 Claude Code 完全对等”——Claude live SessionEnd 路径仍缺证据；
- “Lectern 已在旗舰 demo 跑通”——当前 demo 明确未调用 Lectern。
