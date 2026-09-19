# 参与开发

感谢你愿意一起完善「理工搭子局」。即使不是后端或 AI 方向，也可以从界面、文案、测试、交互流程和使用说明开始贡献。

## 推荐协作流程

1. 在 GitHub 页面点击 **Fork**，把项目复制到自己的账号。
2. 从 `main` 新建一个分支，例如 `feature/improve-profile` 或 `fix/invitation-list`。
3. 在自己的分支完成修改并自行检查。
4. 推送分支，在 GitHub 上向本仓库的 `main` 提交 Pull Request。
5. 根据审核意见继续修改；更新会自动出现在同一个 Pull Request 中。

请让一次 Pull Request 只解决一个相对完整的问题，标题尽量直接说明结果。

## 本地启动

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

```bash
cp .env.example .env
uv sync --dev
uv run python -m scripts.seed_demo
uv run uvicorn app.main:app --reload
```

浏览器打开：

- 产品页面：<http://127.0.0.1:8000/>
- 管理后台：<http://127.0.0.1:8000/admin>
- 接口说明：<http://127.0.0.1:8000/docs>

没有购买模型额度也可以开发和测试。默认规则 Agent 会在本地完成匹配流程。

## 提交前检查

```bash
uv run ruff check .
uv run pytest -q
node --check web/app.js
node --check web/admin.js
```

如果电脑没有安装 Node.js，可以在 Pull Request 中说明；GitHub 仍会自动执行前端脚本检查。

## Pull Request 请写清楚

- 改了什么，以及为什么要改。
- 如何验证，列出亲自试过的操作。
- 是否影响数据库、接口格式或现有数据。
- 界面有变化时附修改前后截图。
- 尚未解决的问题或需要其他同学重点审核的地方。

## 安全与隐私

- 不要提交 `.env`、真实 API Key、密码、Token、Cookie 或个人信息。
- 不要提交 `data/` 下的数据库、缓存、日志或本机配置。
- 新配置项请只把空值或示例值写入 `.env.example`。
- Agent 的写操作必须继续保留“用户确认后执行”的边界。
- 如果发现密钥或个人数据误传，请不要在公开 Issue 中粘贴原文，直接联系仓库维护者处理。

## 代码约定

- 面向用户的文字优先使用普通、易懂的中文。
- 新功能应补充对应测试；修复问题时最好先增加能复现问题的测试。
- 不要让模型直接执行创建活动、邀请或加入等写操作，这些操作必须经过用户确认接口。
- 尽量保持改动小而清晰，避免顺手重写无关文件。
