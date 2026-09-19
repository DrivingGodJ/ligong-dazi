# 理工搭子局

这是「理工搭子局」的 FastAPI 后端 MVP。它把搭子匹配设计成一个可审计的执行型 Agent：Agent 可以查询已有活动、查询可用用户、计算匹配度并生成推荐；创建活动、发送邀请、加入活动等写操作必须等用户确认后才执行。

当前无需购买模型 API。默认的 `deterministic` 模式会使用与参赛方案一致的可解释评分规则完成整条流程。以后配置兼容 OpenAI Tool Calling 协议的模型地址和 Key 后，模型可自主选择查询工具、设置候选范围并在安全评分结果内重新排序；调用失败时会自动回退到规则 Agent。

## 已实现能力

- 邮箱注册、登录、JWT 鉴权与个人画像
- 活动创建、查询、参与者和容量管理
- `personal_requirement` 自然语言个性化需求
- 匹配 Agent：查询已有活动、查询无时间冲突用户、计算匹配度
- 可解释评分：时间 30%、活动 25%、地点 15%、兴趣 15%、人数偏好 5%、社交偏好 5%、信用 5%
- 用户确认后创建活动、发送邀请或加入已有活动
- 邀请接受/拒绝、满员自动成局、活动提醒
- 会后互评与信用流水
- 拉黑关系、时间冲突过滤、敏感属性不参与自动排序
- Agent 运行记录与工具调用审计
- SQLite 本地零配置启动，PostgreSQL 生产环境可替换

## 项目结构

```text
app/
  main.py       应用入口、生命周期与健康检查
  api.py        REST API、权限和业务事务
  agent.py      规则 Agent、模型 Agent、工具白名单与回退
  matching.py   候选检索、个性化需求解析、可解释评分
  models.py     用户、活动、邀请、匹配、信用等数据表
  schemas.py    请求与响应校验
  core.py       配置、数据库、密码与 JWT
tests/          无模型额度也能运行的端到端测试
```

## 本地启动

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

```bash
cp .env.example .env
uv sync --dev
uv run uvicorn app.main:app --reload
```

打开：

- 简易前端：<http://127.0.0.1:8000/>
- 本地管理后台：<http://127.0.0.1:8000/admin>
- API 文档：<http://127.0.0.1:8000/docs>
- 存活检查：<http://127.0.0.1:8000/health/live>
- 就绪检查：<http://127.0.0.1:8000/health/ready>

首次启动会在 `data/` 下创建 SQLite 数据库。正式部署时建议关闭 `DAZI_AUTO_CREATE_SCHEMA` 并使用迁移工具管理 PostgreSQL 表结构。

需要演示数据时可运行：

```bash
uv run python -m scripts.seed_demo
```

脚本只允许在开发或测试环境执行，并会输出本地演示账号。重复运行不会重复创建用户和示例活动。

## 简易前端试玩

前端和 API 由同一个服务提供，不需要安装 Node.js 或另外启动开发服务器。它支持：

- 注册、登录与一键填入演示账号
- 编辑兴趣、常去地点和社交方式
- 提交结构化活动需求与自然语言个性化要求
- 查看 Agent 的实际工具调用、个性化要求处理结果和候选解释
- 选择候选并确认建局、邀请或加入已有活动
- 切换演示账号查看和处理收到的邀请
- 查看近期活动及成局状态

建议先用“小曾”发起一次匹配并邀请“小王”，退出后切换“小王”账号，在“收到的邀请”中接受。

## 本地管理后台

管理后台面向不熟悉服务器和命令行的使用者，只在开发或测试环境、并且从运行服务的本机访问时开放。它支持：

- 在 DeepSeek、OpenAI、纯规则模式和其他 OpenAI 兼容服务之间切换
- 填写或替换 API Key；已经保存的 Key 不会重新返回给浏览器
- 设置模型名和兼容接口地址
- 用一句极短请求测试连接，并把余额不足、密钥错误或网络问题翻译成普通语言
- 查看注册人数、匹配次数、完成次数和待处理邀请
- 以“查找了多少活动、找到了多少同学、是否启用备用规则”等普通语言展示 Agent 运行记录

保存后无需重启，下一次匹配会立即使用新设置。连接测试会产生极少量模型调用费用；单纯保存设置不会调用模型。配置仍写入本机 `.env`，权限收紧为仅当前用户可读写。

## 核心流程

1. `POST /api/v1/auth/register` 注册并取得 Bearer Token。
2. `PATCH /api/v1/users/me` 补充兴趣、常驻地点、社交方式等画像。
3. `POST /api/v1/matches/preview` 提交结构化需求与可选的自然语言个性化要求。
4. Agent 调用只读工具，返回候选、匹配分、分项原因和 `agent_run_id`。
5. `POST /api/v1/matches/{match_request_id}/confirm` 由用户确认候选。
6. 后端才会创建活动、发送邀请或加入已有活动。
7. 被邀请者调用 `POST /api/v1/invitations/{invitation_id}/respond`。
8. 活动后调用 `POST /api/v1/feedback`，结果写入信用流水。

通过 `GET /api/v1/agent-runs/{agent_run_id}` 可以回放本次 Agent 调用了哪些工具、返回多少候选，以及用户最终批准了哪些写操作。

## 个性化需求

请求示例：

```json
{
  "category": "羽毛球",
  "starts_at": "2026-10-01T20:00:00+08:00",
  "ends_at": "2026-10-01T22:00:00+08:00",
  "location": "南区体育馆",
  "people_needed": 2,
  "title": "今晚南区羽毛球",
  "personal_requirement": "希望找同院系、安静一点、守时且也喜欢羽毛球的搭子"
}
```

没有模型 Key 时，规则解析器目前能识别信用、同院系、同年级、安静/外向社交方式和已知兴趣词。无法识别的文字会原样列入 `unresolved`，不会悄悄忽略或猜测。涉及性别、民族、籍贯、身体健康等敏感条件不会进入自动排序，并在 `ignored_for_safety` 中说明。

## 接入模型 API

服务支持兼容 OpenAI Chat Completions Tool Calling 协议的提供方：

```dotenv
DAZI_AI_PROVIDER=openai_compatible
DAZI_AI_API_KEY=你的密钥
DAZI_AI_BASE_URL=https://你的服务地址/v1
DAZI_AI_MODEL=模型名称
```

`DAZI_AI_PROVIDER=auto` 时，有 Key 就调用模型，没有 Key 就走规则 Agent。模型在预览阶段只能调用三个只读工具：

- `search_activities`
- `search_users`
- `calculate_match`

所有写工具都由确认接口执行，模型无法绕过人工确认。可通过 `GET /api/v1/agent-tools` 查看完整工具清单及其权限分类。

## PostgreSQL

将数据库地址改为异步 PostgreSQL 驱动即可：

```dotenv
DAZI_DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/ligong_dazi
```

密码和模型 Key 只能放在本地 `.env` 或部署平台的 Secret 中，不要提交到仓库。

## 验证

```bash
uv run pytest
uv run ruff check .
```

测试覆盖注册登录、画像更新、无额度匹配、个性化需求、人工确认、邀请响应、信用更新、审计轨迹和关键越权拦截。

## 参与协作

项目欢迎同学通过 Fork 和 Pull Request 一起完善。开始前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，里面包含本地启动、分支建议、提交前检查和敏感信息注意事项。

每个 Pull Request 都会自动检查后端测试、代码规范和前端脚本语法。涉及界面变化时，请在 PR 中附上截图，方便大家快速审核。
