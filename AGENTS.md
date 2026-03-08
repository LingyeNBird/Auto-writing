# AGENTS.md

本目录下的 `docs` 文件夹中包含以下 3 份核心文档，用于描述这个“全自动小说写作系统”项目的需求、设计和开发约束。

## 1. 需求文档

- 位置：`docs/project-requirements.md`
- 内容概述：
  - 说明项目背景、目标和使用场景
  - 定义系统的功能需求与非功能需求
  - 约束第一阶段 MVP 范围
  - 给出验收标准
  - 明确项目面向个人使用、低并发、以 Docker 本地部署为主

## 2. 设计文档

- 位置：`docs/project-design.md`
- 内容概述：
  - 说明整体技术选型与架构设计
  - 描述模块划分、状态机流程、数据实体和存储策略
  - 定义上下文管理、章节生成、审查与恢复机制
  - 明确系统默认通过 Docker / Docker Compose 运行

## 3. 开发注意事项文档

- 位置：`docs/development-notes.md`
- 内容概述：
  - 说明开发过程中的边界、约束和实现原则
  - 强调不要过度工程化
  - 强调默认采用 TDD（测试驱动开发）推进核心逻辑
  - 强调结构化 canon 与检索记忆分离
  - 规定状态机、提示词、日志、版本管理和 Docker 持久化等注意事项

## 使用建议

- 开始开发前，先阅读 `docs/project-requirements.md`，明确范围和目标
- 开始设计或搭骨架前，阅读 `docs/project-design.md`
- 实际编码时，持续参考 `docs/development-notes.md`，并按 TDD 方式推进实现，避免实现偏离方向
