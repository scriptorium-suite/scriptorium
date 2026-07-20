# 产品设计借鉴与致谢

[English](ACKNOWLEDGEMENTS.md)

本文是非规范性说明，用来记录影响 Scriptorium 产品思考的公开项目，并明确区分
“设计启发”与“已经实现”。当前能力边界仍以 README、可复现测试和
`scriptorium-spec` 中的版本化契约为准。

Scriptorium 为独立实现。下列项目不是套件的内置依赖；列出它们不表示代码复用、
能力等价、现成集成、官方合作、上游背书或关联关系。

## 本轮调研强化的产品判断

Scriptorium 不把自己做成“全自动科学家”或包办一切的单体科研应用，而是坚持：

- **连续性优先于自治。** 保存足够且已经确认的项目状态，让下一次 Agent 协作无需
  重放全部聊天历史即可继续推进。
- **证据优先于流畅表达。** 模型输出在来源和审查状态清楚之前，只是候选内容。
- **显式关卡优先于隐藏自动化。** 预览、执行、Agent fill 和人工审批是不同状态转换。
- **文件继续拥有权威性。** Markdown、PDF、代码和版本化交换文件不依赖专有数据库
  或某一个 Agent 宿主也能继续使用。
- **交付物派生自已确认状态。** 论文、总结和幻灯片应记录所用 checkpoint、claim、
  source、Skill 版本和人工审查状态。
- **研究方法放在 Skill，信任边界放在核心。** Skill 承载研究方法；组件负责确定性
  校验、来源追踪、隐私和写入边界。

## 已通过一手来源核验的参考项目

| 公开项目 | 借鉴的机制 | Scriptorium 的吸收方式与当前边界 |
|---|---|---|
| [ResearchLoop](https://github.com/plan-lab-szu/ResearchLoop) 及其[技术报告](https://arxiv.org/abs/2605.28282) | 持久化研究状态、基于证据的 claim 准入、closeout 与 manuscript binding | Scriptorium 已把 append-only timeline 与需人工批准的高价值 claim 分开，但尚未形成完整的“研究问题—证据—论文入稿”账本。 |
| [SNL-UCSB literature-survey-skill](https://github.com/SNL-UCSB/literature-survey-skill) | Intent、Triage、分层阅读、Synthesize、扩库审批、认知偏差检查、比较矩阵和依赖分析 | Scriptorium 已有 staged `reading-note`、`review` 与 `lineage-graph` 契约；显式调研意图、时间预算、invariant matrix 和扩库审批仍是候选增量。Scriptorium 使用自己的文件契约和隐私边界，不把 NotebookLM 设为必需后端。 |
| [paperpipe](https://github.com/hummat/paperpipe) | 在本地分离 PDF、LaTeX 源码、公式、摘要、笔记和图件，并通过公开工具向 Agent 提供精确资料 | Steward 与现有契约已分离原始资料和派生产物；公式级实现核验及完整的本地“论文实现数据库”尚不是当前能力。 |
| [SignalGraph](https://github.com/zhiliscope/SignalGraph) | 关系保留来源句子，抽取器与图存储分离 | `lineage-graph` 的边已经携带 evidence，并采用版本化文件契约；Scriptorium 不宣称已经提供通用科研知识图谱或完整关系抽取。 |
| [PaperSpine](https://github.com/WUBING2023/PaperSpine) | 贡献优先、结果映射贡献、可恢复分阶段写作和审稿人视角关卡 | 这些原则适合作为后续 manuscript/delivery gate；Scriptorium 目前不宣称具备完整论文流水线、审稿验证或“可投稿”保证。 |
| [Citation Check](https://github.com/serenakeyitan/citation-check-skill) | 先抽取全部事实性论断，再逐条核验；精确数字检查与细分核验状态 | Scriptorium 的人工审批不等于引用核验。未来交付关卡应记录 claim-source 支撑、数字一致性、冲突与未解决项。 |
| [Academic Research Skills](https://github.com/Imbad0202/academic-research-skills) 及其[架构说明](https://github.com/Imbad0202/academic-research-skills/blob/main/docs/ARCHITECTURE.md) | 人工 checkpoint、完整性关卡、Material Passport 和研究/写作/审稿分阶段工作流 | Scriptorium 只研究这些机制，不照搬其大型多 Agent 架构；共享 artifact manifest 与显式交付关卡仍是未来工作。 |
| [Anthropic Skill Creator](https://github.com/anthropics/skills/tree/main/skills/skill-creator) | 渐进式加载，以及用 baseline、客观断言和人工评审持续迭代 Skill | Scriptorium 已交付一份跨宿主 canonical research skill 与托管安装器，但还缺公开的 with-skill/baseline 评测框架和成熟度证据。 |

第二优先级的可选能力参考包括：[PaperQA2](https://github.com/Future-House/paper-qa)
的科研文献检索、[Nature Skills](https://github.com/Yuan1z0825/nature-skills) 的 Skill
打包与成熟度标签、[Speaker](https://github.com/AI272/speaker) 对真实 PPTX 的演讲稿
后处理，以及 [SciPilot Figure Skill](https://github.com/Haojae/scipilot-figure-skill)
的数据优先选图和视觉 QA。它们是未来可能采用的适配器或方法，不是当前 core 依赖，
也不构成现有能力声明。

初始调研中的 “MycEvo” 无法通过官方仓库或文档唯一核验，因此本文没有使用该名称。
这里引用的是已经核验的 ResearchLoop；没有一手证据时，不应把两个名称当作别名。

## 对路线图的具体影响

这些参考项目最终强化的是四个关键用户时刻，而不是一张无限扩张的功能清单：

| 用户时刻 | 需要获得的结果 | 当前证据 | 紧邻缺口 |
|---|---|---|---|
| 迁移 | 把模糊直觉或已有 Markdown 项目转为显式项目脊柱，同时不夺走用户的数据所有权 | 版本化 project 契约、确定性且 no-clobber 的 `scriptorium init`、合成 demo 与显式、仅元数据的 `scriptorium inventory` 路由预览 | 适配器级、需要人工审阅的迁移清单与执行路径 |
| 恢复 | 给 Agent 一份小而新的 context capsule，而不是重放全部历史 | Provenance portfolio/current-context MCP 与独立的、不暴露内容的控制面 `scriptorium status` | 面向用户的项目 context capsule：目标、活跃问题、已确认依据、冲突和下一步 |
| 收尾 | 把完成的 Agent 会话转成 append-only timeline 与待审高价值 claim | 公共 `scriptorium pull`、canonical pending-fill 指引、`Approvals.md`、Skill 定向验证和跨仓 E2E | 获得干净 GitHub CI 证据，并用外部用户与真实项目重复验证 |
| 交付 | 从已审查项目状态派生论文结构、阶段总结或幻灯片 | Steward handoff 契约与可选 Lectern 路径 | 共享 artifact manifest，以及交付前 claim/引用/完整性关卡 |

因此，紧邻工作应优先完成项目 context-capsule/resume 体验、适配器级迁移的人审
执行路径、公开 Skill 评测框架、
artifact manifest 和交付完整性检查。通用的
`ResearchQuestion -> Hypothesis -> Claim -> Evidence -> Gap -> Action` 可以先作为
基于现有文件的 Skill 输出验证，不应在用户闭环尚未证明价值前立即扩张出大量新 schema。
通用知识图谱、默认向量 RAG 和大型自治多 Agent 写作流水线都放在更后面。

## 署名与复用原则

- 本轮只借鉴抽象机制并链接原项目，没有纳入上游代码、Prompt、模板、schema、图片
  或 benchmark 结果。
- 未来如要实质复用，必须固定上游 revision，复核对应许可证，保留版权/许可证说明，
  并写入第三方清单。
- Academic Research Skills 使用 CC BY-NC 4.0。考虑到 Scriptorium 可能服务未来创业
  或商业工作，在未取得额外许可前，不复制其具有表达性的 Prompt、schema、脚本或模板。
- 未提供明确许可证的仓库按保守的默认版权处理：可以链接和讨论，不复制其实现或文档。
- 所有产品名与商标归各自权利人；致谢不代表背书。
