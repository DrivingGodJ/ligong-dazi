# Railway 上线手册（单服务版）

这版把网页、API、管理后台放在同一个 Railway 服务里。首次不需要域名、GitHub Pages、服务器运维或模型 API 额度。先用“纯规则模式”试运行；确认用量和付款方式后，再决定是否开启付费 AI。

## 第一次上线

1. 用自己的 GitHub 账号登录 [Railway](https://railway.com/)，创建项目，选择 **Deploy from GitHub repo**，连接 `DrivingGodJ/ligong-dazi` 的 `main` 分支。Railway 会自动识别仓库根目录的 `Dockerfile`。
2. **先挂载持久化存储，再开放网址。** 在服务上添加 Volume，挂载路径准确填写 `/app/data`。数据库、活动照片和后台保存的 AI 设置都在这里。未挂载或路径填错时，程序会拒绝在 Railway 上启动，避免把真实数据写到临时磁盘。
3. 在服务的 Variables 中添加两个私密变量，不要写进 GitHub、群聊或聊天记录：

   | 变量名 | 填写内容 |
   | --- | --- |
   | `DAZI_JWT_SECRET` | 密码管理器生成的至少 32 位随机字符串；以后保持不变，改变后所有用户都要重新登录 |
   | `DAZI_ADMIN_PASSWORD` | 仅管理员知道的至少 20 位独立口令；不要与 GitHub、Railway 或普通用户密码相同 |

   `Dockerfile` 已把运行模式设为生产环境、AI 设为纯规则模式，并把数据库、照片和后台设置指向 `/app/data`；不用复制本机 `.env`，更不要把以前发在聊天里的 Key 放上去。
4. 在服务设置里把 Healthcheck Path 设为 `/health/ready`，副本数保持 **1**。SQLite 和当前定时任务尚不适合多副本同时运行。
5. 在 Networking → Public Networking 点击 **Generate Domain**。Railway 会提供 HTTPS 网址。先访问 `/health/ready`，应看到 `status: ready` 和 `agent_mode: deterministic`；再打开首页注册一个新测试账号。
6. 管理后台地址是“同一网址后加 `/admin`”。首次进入需要步骤 3 的管理员口令。后台可以看人数、匹配记录和切换 AI 服务；API Key 只在后台输入，不会返回浏览器。切换结果保存在 Volume，重启后仍会生效。
7. 在 Railway 服务设置里确认连接的分支是 `main`、自动部署已开启，并启用 **Wait for CI**。以后同学的 PR 合并到 `main`，先通过 GitHub 自动检查，再由 Railway 更新。

## 成本与数据提醒

- Railway 试用资格和额度以账号页面为准，不能把试用当作长期免费的数据库。正式邀请真实用户前，要确认持续付款方式，并启用 Volume 备份。
- 数据只存于所连的 Volume。不要删除或清空 Volume；部署新代码前，尤其是修改数据结构前，先做备份。
- 初期保持纯规则模式，避免公开注册后的滥用直接消耗模型额度。之后在 `/admin` 小额测试新 Key，再观察使用量。
- 这是小范围试运行配置。公开面向更多用户之前，还需要补充隐私说明、用户反馈和滥用处理流程。

## 简单排错

- 页面打不开：看 Railway 部署日志；若提示“必须先挂载 /app/data”，返回第 2 步。
- 提示缺少 `DAZI_JWT_SECRET` 或 `DAZI_ADMIN_PASSWORD`：返回第 3 步，保存变量并重新部署。
- 首页能打开却不能注册：检查 `/health/ready` 是否正常，不要运行演示数据脚本到生产环境。
- 管理口令忘了：在 Railway Variables 中替换 `DAZI_ADMIN_PASSWORD` 并部署；不要删除数据库 Volume。
