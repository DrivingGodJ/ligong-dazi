**简体中文** | [English](README.en.md)

# 理工搭子局

面向校园活动的搭子匹配应用：发现同校区活动、寻找合适的伙伴，在确认后创建活动、发出邀请或申请加入。

[打开网站](https://ligong-dazi.zeabur.app/) · [下载安卓独立应用版](https://ligong-dazi.zeabur.app/downloads/ligong-dazi-native.apk) · [发行记录](https://github.com/DrivingGodJ/ligong-dazi/releases)

## 功能特性

- **可解释匹配**：根据活动、地点、时间、兴趣、信用和社交偏好生成候选，支持自然语言需求。
- **活动流程**：活动广场、邀请与入局申请、改期投票、日历导出、提醒和集合照片。
- **个人主页**：兴趣与水平、头像、个人介绍，以及活动和审核评价形成的系统画像。
- **可信协作**：写操作在用户确认后执行；模型查询使用只读工具，保留 Agent 运行审计。
- **多端使用**：网页、可添加到主屏幕的界面和内置网页资源的安卓应用。

## 快速开始

直接打开网站注册，选择南京或江阴校区并填写基础画像。默认规则匹配无需购买模型 API。安卓用户可下载独立应用版；iPhone 可用 Safari 添加到主屏幕。

本地开发需要 **Python 3.12+** 和 [uv](https://docs.astral.sh/uv/)，从仓库根目录执行：

```bash
cp .env.example .env
uv sync --dev
uv run uvicorn app.main:app --reload
```

打开 [本地网页](http://127.0.0.1:8000/)、[管理后台](http://127.0.0.1:8000/admin) 或 [API 文档](http://127.0.0.1:8000/docs)。首次启动会在 `data/` 创建 SQLite 数据库。可用 `uv run python -m scripts.seed_demo` 添加开发/测试演示数据。

## 使用说明

1. 浏览本校区活动，或提交活动类型、时间与个人需求；匹配阶段地点可留空。
2. 查看候选、分项原因和参与者资料，核对后确认加入、发布或邀请；创建新活动前必须补全地点。
3. 在“我的活动”处理安排、改期与退出；临近开始前一小时内取消会扣信用分，开始后不能取消。
4. 活动后提交评价，严重或矛盾评价会进入第三人复核。

通知需主动授权；拒绝推送仍可查看站内消息。安卓包内界面变更需要重新构建发布，私人账号和活动数据不作离线缓存。关联签名、升级和推送配置见完整参考。

生产环境需正确配置验证码、管理员访问、受信代理和持久存储。学生卡、头像与集合照片按各自权限处理，不应把开发配置直接用于公网。模型密钥放在 `.env` 或部署平台 Secret 中；敏感属性不参与 Agent 自动排序。账户、数据保留、身份申诉和信用规则见[完整参考](docs/readme-reference.zh-CN.md)。

## 开发与验证

```bash
uv run pytest
uv run ruff check .
```

默认 SQLite 适合本地启动；生产可改用 PostgreSQL，并使用迁移工具管理表结构。支持兼容 OpenAI Tool Calling 的模型服务，未配置时仍可使用规则匹配。真实 Android/iOS 操作、安装升级和推送送达需要设备验收，构建通过不等于可靠送达。

## 文档与支持

- [完整功能、权限、同步与维护参考](docs/readme-reference.zh-CN.md)
- [Zeabur 部署](DEPLOY_ZEABUR.md) · [Railway 部署](DEPLOY_RAILWAY.md)
- [更新记录](CHANGELOG.md) · [参与贡献](CONTRIBUTING.md)
- [问题反馈](https://github.com/DrivingGodJ/ligong-dazi/issues)

## 许可与致谢

当前仓库未提供独立的整仓许可证。公开可查看不等于获得任意再分发或商用授权；使用前需向权利人确认。
