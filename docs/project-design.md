# 全自动小说写作系统项目设计文档

## 1. 文档目的

本文档描述个人版全自动小说写作系统的技术设计方案，包括总体架构、模块划分、数据组织、运行流程、上下文管理、存储策略和故障恢复机制。

## 2. 设计原则

本项目采用以下设计原则：

- 本地优先，避免不必要的外部基础设施
- 单体优先，避免过早拆分服务
- 可恢复优先，所有长流程都必须可中断恢复
- 可检查优先，关键资产应能直接查看和备份
- 一致性优先，长篇逻辑完整性高于单章写作速度
- 轻量优先，在个人使用场景中避免过度设计

## 3. 技术选型

### 3.1 选型结论

- `Python 3.12`：主开发语言
- `FastAPI`：Web 服务与 API
- `Jinja2 + HTMX + Alpine.js`：轻量 WebUI
- `SQLite`：主数据库
- `SQLAlchemy`：数据访问层
- `Alembic`：数据库迁移
- `LiteLLM`：统一模型调用入口
- `SQLite FTS5`：全文检索
- `本地 embedding 文件或本地表存储`：语义检索基础
- `本地文件系统`：长文本资产与生成工件存储
- `Docker + Docker Compose`：标准运行与部署方式

### 3.2 选型理由

#### Python 3.12

- LLM 生态成熟
- 适合快速实现文本处理、摘要、抽取、检索等能力
- 与 FastAPI、SQLAlchemy、LiteLLM 集成顺畅

#### FastAPI

- 结构清晰，适合控制台型应用
- 方便实现 API 与服务端渲染页面
- 后续若扩展异步调用和任务接口，迁移成本低

#### Jinja2 + HTMX + Alpine.js

- 对个人项目足够轻量
- 适合后台控制台场景
- 维护成本明显低于前后端完全分离方案

#### SQLite

- 单用户、低并发场景完全够用
- 零运维、易备份、易迁移
- 适合保存项目状态、任务记录和索引元数据

#### LiteLLM

- 可统一接入多个模型提供方
- 可在项目中统一封装模型调用策略
- 便于后续切换模型而不影响业务流程

#### Docker + Docker Compose

- 保证宿主机环境一致，降低本地安装成本
- 适合个人项目的一键启动和迁移
- 便于显式管理 Web、Worker 与持久化目录

## 4. 总体架构

系统采用“单体应用 + 独立 worker”的结构。

### 4.1 组成

- `Web 应用进程`
  - 提供页面和 API
  - 管理项目、查看进度、执行控制动作
- `Worker 进程`
  - 执行长流程任务
  - 按状态机逐步推进小说生成
- `SQLite 数据库`
  - 保存状态、元数据、索引和任务信息
- `本地文件系统`
  - 保存大文本资产和运行工件
- `Docker / Docker Compose`
  - 作为默认启动方式组织容器、卷挂载和环境变量

### 4.2 架构目标

- 避免一个进程同时承担 UI 和长任务，降低阻塞风险
- 让状态、资产、执行过程分离，便于恢复与调试
- 让运行结果既能程序读取，也能人工查看
- 让部署方式稳定可复制，尽量减少宿主机环境差异

### 4.3 Docker 运行形态

第一阶段建议采用 Docker Compose 运行，至少包含以下服务：

- `web`
  - 对外提供 Web 页面和 API
- `worker`
  - 执行长任务状态机

两个服务共享以下挂载：

- `data/`：项目资产目录
- `logs/`：日志目录
- `storage/`：SQLite 数据库与检索相关文件目录

设计要求：

- 容器应设计为无状态，业务数据必须落在宿主机挂载目录
- `web` 和 `worker` 使用相同镜像或相同代码版本，避免运行逻辑不一致
- 容器删除或重建后，小说资产与运行状态仍可恢复

## 5. 模块划分

建议按如下模块组织系统。

### 5.1 project 模块

负责项目管理：

- 创建项目
- 读取项目
- 项目配置存取
- 项目目录初始化

### 5.2 planner 模块

负责前期规划：

- 归一化用户输入
- 生成故事 premise
- 生成世界观设定
- 生成角色卡
- 生成主线结构
- 生成分章大纲

### 5.3 canon 模块

负责结构化真相管理：

- 角色状态
- 世界规则
- 时间线事件
- 关系变化
- 伏笔与线程状态
- 事实版本和来源记录

### 5.4 chapter 模块

负责章节级生成与版本管理：

- 章节上下文包生成
- 章节正文生成
- 草稿版本保存
- 锁定章节版本

### 5.5 review 模块

负责章节后处理：

- 摘要生成
- 事实抽取
- 一致性检查
- 风格问题检查
- 重写建议生成

### 5.6 retrieval 模块

负责上下文检索与打包：

- 全文检索
- embedding 检索
- 结构化条件检索
- 章节上下文包组装

### 5.7 workflow 模块

负责运行状态机：

- NovelRun 状态推进
- ChapterRun 状态推进
- 检查点写入
- 重试逻辑
- 恢复逻辑

### 5.8 ui 模块

负责页面渲染和控制动作：

- 项目列表页
- 项目详情页
- 章节状态页
- 冲突中心页
- 运行日志页

## 6. 存储设计

系统采用“数据库 + 文件系统”混合存储。

### 6.1 SQLite 中保存的内容

- 项目基本信息
- 运行状态
- 章节状态
- 角色元数据
- 时间线索引
- 事实索引
- 冲突记录
- 检索元数据
- 日志索引

### 6.2 文件系统中保存的内容

建议按项目目录保存。

示例结构：

```text
data/
  projects/
    <project_id>/
      project.json
      story_bible.md
      world/
        rules.yaml
        locations.yaml
      characters/
        protagonist.yaml
        antagonist.yaml
      outlines/
        master_outline.md
        chapter_001.md
      chapters/
        001/
          draft_v1.md
          draft_v2.md
          summary.md
          facts.yaml
          review.json
      reports/
        continuity_report.json
        final_review.md
```

### 6.3 设计原因

- 大文本放文件更直观
- 易于手动查看、比较和备份
- 避免把所有文本资产都塞进数据库造成维护不便
- 更适合通过 Docker volume 或 bind mount 持久化到宿主机

## 7. 核心数据实体

### 7.1 Project

字段建议：

- id
- name
- chapter_count
- theme_notes
- status
- created_at
- updated_at

### 7.2 NovelRun

字段建议：

- id
- project_id
- current_state
- retry_count
- last_error
- started_at
- finished_at

### 7.3 Chapter

字段建议：

- id
- project_id
- chapter_number
- title
- state
- active_draft_version
- last_error

### 7.4 Character

字段建议：

- id
- project_id
- name
- role_type
- public_profile
- private_motivation
- current_status
- knowledge_boundary

### 7.5 CanonFact

字段建议：

- id
- project_id
- fact_type
- subject
- predicate
- object
- source_chapter
- confidence
- status

### 7.6 TimelineEvent

字段建议：

- id
- project_id
- chapter_number
- sequence_index
- actors
- summary
- consequences

### 7.7 ContinuityIssue

字段建议：

- id
- project_id
- chapter_number
- severity
- issue_type
- description
- evidence
- resolution_status

## 8. 运行流程设计

### 8.1 全局流程

建议将整本小说流程建模为 `NovelRun` 状态机。

推荐状态：

- `INIT`
- `INPUT_NORMALIZED`
- `BIBLE_READY`
- `CHARACTERS_READY`
- `MASTER_OUTLINE_READY`
- `CHAPTERS_RUNNING`
- `GLOBAL_REVIEW`
- `FINALIZED`
- `FAILED`

### 8.2 章节流程

建议将每章流程建模为 `ChapterRun` 状态机。

推荐状态：

- `PLANNED`
- `CONTEXT_PACKED`
- `DRAFTED`
- `SUMMARIZED`
- `FACTS_EXTRACTED`
- `CONTINUITY_CHECKED`
- `REVISED`
- `LOCKED`
- `FAILED`

### 8.3 重试策略

- 每一步应记录尝试次数
- 可重试步骤可设置上限
- 超出上限后进入 `FAILED` 或 `MANUAL_REVIEW_REQUIRED`
- 已完成并产出稳定结果的步骤不应重复执行

## 9. 上下文管理设计

这是系统最重要的部分之一。

### 9.1 上下文分层

每次章节写作不直接拼整本书，而是构造一个 `Chapter Packet`。

建议分层：

- `System Layer`：全局写作角色和任务说明
- `Project Layer`：本书主题、风格、题材、禁区
- `Canon Layer`：世界观规则、角色状态、时间线约束
- `Chapter Layer`：本章目标、冲突、推进点、限制
- `Output Layer`：输出格式、篇幅、禁止项

### 9.2 Chapter Packet 内容

建议至少包括：

- 本章大纲
- 本章目标
- 上一章详细摘要
- 最近几章滚动摘要
- 相关角色卡
- 相关时间线事件
- 当前未解决线程
- 本章必须推进的伏笔
- 禁止违背的 canon 约束

### 9.3 为什么不能只靠向量检索

纯向量检索更适合“找到相关片段”，不适合“定义小说真相”。

因此系统必须区分：

- `结构化真相`：谁做了什么、谁知道什么、规则是否成立
- `模糊参考`：类似语气、相关描写、可借用情境

结论：必须并行使用结构化 canon 与检索型 memory。

## 10. 模型调用设计

建议按任务类型路由不同模型，而不是单模型包打天下。

### 10.1 任务分类

- `强模型`
  - 故事总纲
  - 关键角色设定
  - 难章节改写
  - 终稿整合
- `中等模型`
  - 常规章节写作
- `便宜模型`
  - 摘要
  - 事实抽取
  - 初步一致性检查

### 10.2 模型调用封装

建议统一封装为 `LLMClient`，负责：

- 模型名称映射
- 参数模板
- 失败重试
- 响应记录
- 成本记录

## 11. WebUI 设计

由于系统是个人使用，UI 应以控制台为核心。

建议页面：

- 项目列表页
- 新建项目页
- 项目总览页
- 章节进度页
- 角色与设定页
- 冲突中心页
- 运行日志页

每个页面应以信息可读和操作可控为优先，不追求复杂交互。

## 12. 部署与打包设计

### 12.1 默认部署方式

默认部署方式为 Docker Compose，而不是裸机手工运行。

### 12.2 目录挂载建议

建议至少挂载以下宿主机目录：

- `./data:/app/data`
- `./logs:/app/logs`
- `./storage:/app/storage`

其中：

- `data` 用于保存项目正文、设定、报告等长文本资产
- `logs` 用于保存运行日志
- `storage` 用于保存 SQLite 数据库、检索文件、缓存型中间文件

### 12.3 环境变量建议

建议通过 `.env` 管理：

- 模型 API Key
- 默认模型名称
- 运行目录
- 日志级别
- 应用端口

### 12.4 容器设计注意事项

- 不要把 SQLite 文件放在未挂载的容器内部目录
- 不要把项目资产只写入容器临时层
- 不要让 `web` 和 `worker` 使用不同的挂载路径约定
- 容器启动脚本应明确区分 Web 模式和 Worker 模式

## 13. 故障恢复设计

### 12.1 检查点

每个关键步骤结束后写入检查点：

- 输入摘要
- 输出摘要
- 产物路径
- 当前状态
- 重试次数
- 最近错误

### 12.2 恢复原则

- 能接着跑就不要从头跑
- 对外部调用应尽量做到可重复记录
- 不覆盖旧产物，新结果以新版本保存

## 14. 未来演进方向

当现有方案出现明显瓶颈时，可按顺序演进：

1. `SQLite -> PostgreSQL`
2. `本地 embedding -> 专用向量存储`
3. `HTMX 控制台 -> React/Next.js`
4. `简单状态机 -> 更强的工作流引擎`

但在个人版阶段，不建议提前引入这些复杂度。
