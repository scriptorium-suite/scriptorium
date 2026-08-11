# Scriptorium

[![CI](https://github.com/scriptorium-suite/scriptorium/actions/workflows/ci.yml/badge.svg)](https://github.com/scriptorium-suite/scriptorium/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/scriptorium-suite/scriptorium)](https://github.com/scriptorium-suite/scriptorium/releases)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

Scriptorium 是一个本地优先、模型中立的项目控制层。它解决的问题不是“再做一个聊天机器人”，而是让人、AI Agent、本地资料、文献工具和交付物持续使用同一个可恢复、可审查、可追溯的项目状态。

![Scriptorium 套件总览](docs/assets/suite-overview.svg)

## 为什么需要它

AI Agent 很适合协助复杂项目，但大量关键上下文会散落在聊天记录、临时文件和个人目录里：这次用了哪些资料，哪些判断已经被接受，哪些仍然只是推测，下次应该从哪里继续。Scriptorium 把这些过程沉淀成一个本地项目工作区，让后续协作可以接得上。

当前第一个经过验证的场景是科研和 AI4Science 风格工作流，因为科研对来源、证据、文献、人工审查和交付物要求更高。但 Scriptorium 的口径并不限制在科研上，它也可以服务工程系统维护、产品调研、个人大型知识项目，或者任何需要长期 AI 协作记忆的复杂工作。

## 当前公开版能做什么

当前稳定版重点完成产品流程的前半段：创建干净工作区，把本地资料纳入管理，安装可协同组件，并让后续 AI 会话从明确的项目状态恢复。

| 能力 | 当前状态 |
| --- | --- |
| 工作区初始化 | 稳定：创建项目目录、配置、审批入口和本地状态文件夹。 |
| 资料盘点与迁移 | 稳定：对选定 Markdown/PDF 先预览，再安全复制。 |
| 项目恢复 | 稳定：通过 Provenance 获取有边界的上下文胶囊，而不是直接加载原始历史。 |
| 组件安装 | 稳定：从组件目录安装固定版本的套件组件。 |
| Capture 安装 | 稳定：从 Provenance release asset 安装浏览器对话导出扩展。 |
| 证据审批与执行闭环 | 开发分支中：不作为当前稳定版主流程宣传。 |
| PPT / 汇报生成 | 可选组件方向；当前版本暂不作为主线重点。 |

## Windows 快速开始

Scriptorium 面向有一定 GitHub 开源项目配置基础的用户。

```powershell
git clone https://github.com/scriptorium-suite/scriptorium.git
cd scriptorium
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\scriptorium.exe doctor
```

创建一个项目工作区：

```powershell
.\.venv\Scripts\scriptorium.exe init `
  --workspace D:\Research\demo-workspace `
  --project-name "My long-running project"
```

先预览本地资料，不直接复制：

```powershell
.\.venv\Scripts\scriptorium.exe inventory `
  --workspace D:\Research\demo-workspace `
  --source D:\Research\notes `
  --json
```

后续 AI 会话从项目状态恢复：

```powershell
.\.venv\Scripts\scriptorium.exe resume `
  --workspace D:\Research\demo-workspace
```

## 套件如何组织

Scriptorium 是统一入口，其余组件保持独立可用。组件之间通过 [scriptorium-spec](https://github.com/scriptorium-suite/scriptorium-spec) 定义的公开文件契约协作，而不是互相调用私有内部 API。

| 仓库 | 作用 |
| --- | --- |
| [scriptorium](https://github.com/scriptorium-suite/scriptorium) | 产品入口、工作区生命周期、组件目录、安装 profile、状态检查和恢复命令。 |
| [scriptorium-spec](https://github.com/scriptorium-suite/scriptorium-spec) | JSON Schema、样例和约定，是套件协同的数据契约。 |
| [Provenance](https://github.com/foxsplendid/Provenance) | 本地项目记忆、资料摄取、脱敏、搜索、上下文胶囊、MCP 和会话回写。 |
| [steward](https://github.com/scriptorium-suite/steward) | 文献和资料治理组件，负责阅读、审查、proposal 和 handoff 文件。 |
| [Academic-Slides-Agent](https://github.com/foxsplendid/Academic-Slides-Agent) | 可选的汇报和幻灯片方向组件；当前稳定流程不依赖它。 |

Capture 不是独立仓库。它是 Provenance 发布出来的一个小型浏览器导出工具，可以通过 Scriptorium 组件目录单独安装。

## 产品流程

```text
本地笔记 / PDF / AI 对话
        │
        ▼
scriptorium init + inventory + migration preview
        │
        ▼
本地项目状态
        │
        ├── Provenance：可恢复上下文和本地记忆
        ├── Steward：文献与资料 handoff
        └── Spec：组件间共享契约
        │
        ▼
后续 AI 会话从有边界的 context capsule 继续
```

核心原则很简单：原始资料、AI 生成草稿、用户确认过的项目记忆必须分开。当前稳定版不要求用户信任不可见的 Agent 内部状态。

## 独立安装组件

查看可用安装 profile：

```powershell
.\.venv\Scripts\scriptorium.exe install --list
```

只安装 Capture：

```powershell
.\.venv\Scripts\scriptorium.exe install capture `
  --target D:\Tools\scriptorium-capture `
  --run
```

安装核心运行组件：

```powershell
.\.venv\Scripts\scriptorium.exe install core `
  --target D:\Tools\scriptorium-core `
  --run
```

## 文档

- [架构与验收说明](docs/architecture-and-acceptance.zh-CN.md)
- [合成案例](docs/case-study.zh-CN.md)
- [展示证据](docs/showcase/README.zh-CN.md)
- [致谢](ACKNOWLEDGEMENTS.zh.md)

## 隐私与安全口径

Scriptorium 默认本地优先。核心流程不要求托管账号，也不会把你的私有研究材料上传。浏览器导出、Zotero、Obsidian 和 PPT 生成都是可选集成。公开样例全部是合成和无害化内容。

项目仍处在早期阶段。对重要工作区请保留备份；涉及写入的命令应先 preview；AI 生成内容应经过人工审查后再作为项目记忆。

## 开发与验证

运行本地测试：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

发布前建议至少执行：

```powershell
.\.venv\Scripts\scriptorium.exe doctor
.\.venv\Scripts\scriptorium.exe install --list
```

## 许可证

Apache-2.0。见 [LICENSE](LICENSE)。
