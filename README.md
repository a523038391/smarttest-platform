# 智能测试平台

面向软件测试团队的一体化质量平台，覆盖需求、用例、计划、缺陷、AI 辅助设计、接口与 Web UI 自动化、调度、执行和报告。

## 设计目标

- 建立“需求 → 测试用例 → 自动化脚本 → 执行记录 → 缺陷 → 回归验证”的完整追溯链。
- 使用 Vue 3 + TypeScript + FastAPI + MySQL + Redis + Celery。
- 通过 Docker 隔离的独立 Runner 执行 pytest、requests 和 Playwright。
- 实时展示日志、进度、截图、错误和报告，并支持断线续传。
- 支持 GitHub、GitLab、Jenkins 事件触发和质量门禁。
- 对账号、Token、密码等秘密进行信封加密，并实施多租户、项目、角色、数据和操作权限控制。

## 文档

- [平台总体设计](docs/platform-design.md)

## 当前实现

- FastAPI 健康检查与 Runs API。
- 首管理员初始化、PBKDF2-SHA256 密码哈希、HttpOnly Cookie 会话及业务 API 登录保护。
- 版本化 Task、Event、Result 协议模型。
- Run/Attempt 状态机、幂等创建与取消语义。
- Vue 3 + TypeScript 工作台和执行中心，支持 SSE 实时进度、日志、错误与产物展示。
- SQLAlchemy/Alembic Run、Attempt、Event 持久化仓储与 Celery/Redis 编排入口。
- 容器内 `RunnerAgent`，支持 HTTP、pytest、Playwright Adapter、顺序事件和统一结果协议。
- `RunnerManager` 负责校验执行身份、推进 Run/Attempt 状态、持久化事件及最终结果快照。
- `DockerExecutor` 以非 root、只读根文件系统、最小权限和资源上限运行一次性 Runner 容器。
- Attempt 心跳租约及 Celery Beat Reaper 自动将失联执行收敛为 `LOST/INFRA_ERROR`。
- 截图与报告支持宿主侧限额校验、本地存储或受信任 Presigned URL 上传。
- 需求与测试用例支持项目范围查询、状态机、乐观并发、不可变修订历史及多对多追溯关联。
- AI 用例生成通过可替换 Provider 返回严格校验的候选数据，不直接发布正式用例。
- 自动化脚本支持 HTTP、pytest、Playwright 引擎，保存受限入口、源码引用和 SHA-256 摘要，并保留不可变修订历史。
- 自动化源码可通过 `/api/v1/automation-sources` 存入项目隔离的本地内容寻址存储，Runner 物化时会再次校验引用、摘要和入口路径。
- 测试用例与自动化脚本支持同项目多对多幂等关联，形成“需求 → 用例 → 脚本”追溯链。
- 项目执行环境支持环境变量与公共参数、完整不可变修订、草稿/启用/归档状态机及乐观并发。
- 每个秘密使用独立随机 DEK 进行 AES-256-GCM 加密，再由配置的 32 字节 KEK 独立封装；API 仅返回不透明引用。
- Secret Broker 签发绑定 Attempt、Runner、环境修订和精确引用清单的一次性 capability；数据库仅保存 SHA-256 摘要及无秘密值元数据。
- Runner Manager 在已验证的宿主 tmpfs 中暂存明文，以只读方式挂载到容器 `/run/secrets`，执行结束后强制清理。
- 测试计划支持租户/项目范围、乐观并发、草稿/启用/归档状态机、不可变修订，以及脚本与环境修订固定引用。
- 启用计划按数据行原子展开 Execution Batch、Run 与不可变 RunSpec；计划执行幂等键按租户和项目隔离，Runner 任务由可信 RunSpec 物化且只携带秘密引用。
- pytest 与 Playwright 脚本可通过 `runner.load_parameters()` 读取当前数据行的最终公共参数；公开环境变量注入子进程，参数文件位于容器 tmpfs 且执行后清理。
- 后端测试及前端 TypeScript 生产构建。

未配置 `DATABASE_URL` 时 API 使用内存仓储；配置后执行域、质量资产域、脚本域、环境域和测试计划域使用对应 SQL 仓储。数据库表只通过 Alembic 迁移创建，API 启动不会自动建表。需求、用例、脚本、环境和测试计划列表必须传入 `project_id`，归档资产不可再修改。事件同时保存每次 Attempt 内的 `seq` 和跨事件单调递增的持久化 `cursor`；Runs API 提供 Attempt/Event 列表及支持 `after`、`Last-Event-ID` 断点续传的 SSE。`AUTO_DISPATCH` 默认关闭，以保持创建 Run 后处于 `CREATED` 的现有 API 契约；启用后，新执行批次中的每个 Run 仅在首次物化时投递，失败投递收敛为 `INFRA_ERROR`，不会在幂等重放时隐式重试。

秘密写入与受信任解析需要同时配置 `SECRET_ENCRYPTION_KEY`（Base64 编码的 32 字节 KEK）和 `SECRET_ENCRYPTION_KEY_ID`。缺失密钥时公开值的增删改查及秘密脱敏读取继续可用，秘密写入/解析以通用 503 失败关闭。`TaskEnvelope` 只携带固定环境修订与类型化秘密引用；秘密明文不会进入 Run 参数、Celery、Docker 标准输入、命令行、环境变量、label、日志、API 响应或产物。

执行含秘密的任务前，Runner 节点必须预先将 `SECRET_STAGING_ROOT` 挂载为 `tmpfs`。`LinuxTmpfsVerifier` 会在每次暂存前校验挂载类型；目录缺失、为符号链接或落在磁盘文件系统时均失败关闭。秘密文件按 `/run/secrets/{environment_variable|common_parameter}/{name}` 提供给脚本，脚本可使用 `runner.secret_path()` 定位但不得输出其内容。当前实现由受信任的 node-local Runner Manager 在宿主侧一次性兑换 capability；独立 Secret Broker 服务、mTLS 工作负载证书和远程领取将在部署身份体系完成后启用。

## 本地运行

后端：

    py -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    .\.venv\Scripts\python.exe -m uvicorn services.api.main:app --reload

复制 `.env.example` 并按环境注入变量。启用 SQL 仓储前先执行迁移：

    .\.venv\Scripts\python.exe -m alembic upgrade head

首次打开 `http://localhost:5173/` 时，登录页会引导创建首位管理员；后续使用该账户登录。生产 HTTPS 部署必须设置 `SESSION_COOKIE_SECURE=true`，`AUTH_REQUIRED` 应保持为 `true`。

启动 Celery Worker：

    .\.venv\Scripts\python.exe -m celery -A workers.celery_app:celery_app worker --loglevel=INFO

Windows 本地调试可在 Worker 命令末尾添加 `--pool=solo`。当 `AUTO_DISPATCH=true` 时，新 Run 会进入 `QUEUED` 并投递 `smarttest.prepare_run`；Worker 将其推进至 `DISPATCHING`。生产部署由独立、可访问本机 Docker API 的 Runner Manager 消费 `TaskEnvelope`，并使用 `DockerExecutor` 持久化完整生命周期；`InProcessRunnerExecutor` 仅用于测试和本地开发。Worker 与 Celery Beat 必须使用同一套已迁移的 `DATABASE_URL`，Beat 按 `ATTEMPT_REAPER_INTERVAL_SECONDS` 周期回收失联 Attempt。

启动失联回收调度器（另一个终端）：

    .\.venv\Scripts\python.exe -m celery -A workers.celery_app:celery_app beat --loglevel=INFO

构建隔离 Runner 镜像：

    docker build -f runner/Dockerfile -t smarttest-runner:local .

无需 Celery 的最小本地闭环可设置 `AUTO_DISPATCH=true` 和 `LOCAL_RUNNER_ENABLED=true` 后直接启动 API。此模式使用数据库状态将 Run 推进至 `DISPATCHING`，API lifespan 内的本地 Runner 轮询执行并在完成后按测试计划的 `max_parallel` 自动补位；默认使用 `smarttest-runner:local` 镜像、`none` 网络、`.sources` 源码目录和 `.runner-work` 临时工作目录。只有需要访问被测网络时才应显式设置 `RUNNER_NETWORK=bridge`：

    $env:AUTO_DISPATCH="true"
    $env:LOCAL_RUNNER_ENABLED="true"
    .\.venv\Scripts\python.exe -m uvicorn services.api.main:app --reload

前端（另一个终端）：

    npm.cmd install --prefix apps/web
    npm.cmd run dev --prefix apps/web

Vite 默认代理 `/api` 与 `/health` 到 `http://127.0.0.1:8000`，可通过 `VITE_API_TARGET` 修改。

### 导入自动化项目

“自动化脚本”支持三种源码来源：直接粘贴单个 Python 文件、上传完整项目 ZIP、导入公开 HTTPS Git 仓库。ZIP 根目录应直接包含入口文件及依赖模块，入口使用 POSIX 相对路径（例如 `tests/test_login.py`）。ZIP 压缩包最大 10 MiB、解压后最大 50 MiB、最多 2000 个文件；路径穿越、符号链接、加密包和大小写冲突路径会被拒绝。

Git 导入支持 GitHub、GitLab、Bitbucket 和 Gitee，可指定分支、标签或提交 SHA。只允许公开 HTTPS 仓库，不接受账号密码、查询参数、重定向或内网地址。导入后会生成不可变的内容摘要，后续仓库更新不会改变已创建的脚本修订和运行快照。

项目中的本地 Python 模块可直接按原目录导入。Runner 镜像默认提供 pytest、Playwright 及 Chromium；其他第三方依赖需预先加入 Runner 镜像，平台不会执行项目内安装脚本。

### Windows 本机执行

pytest 与 Playwright 脚本可在表单中选择“Windows 本机执行”。该模式需要显式启用，并配置允许访问的项目根目录（多个目录使用 Windows 路径分隔符 `;`）：

    $env:HOST_EXECUTION_ENABLED="true"
    $env:HOST_PROJECT_ROOTS="D:\automation-projects"

表单中的项目目录必须位于白名单内，Python 解释器必须位于该项目目录中，例如 `.venv\Scripts\python.exe`。入口仍填写项目内相对路径。HTTP 引擎、密钥引用和白名单外路径不支持本机执行；同一项目目录的任务会串行运行，避免桌面浏览器操作冲突。

脚本中的可变入参应通过 `runner.load_parameters()` 读取，不要写死在函数调用中。例如先读取 `parameters = load_parameters()`，再将 `parameters["shopId"]`、`parameters["shopAccount"]` 等值传给业务方法。前端“测试计划”选择脚本后可填写每个脚本的执行入参 JSON；计划可通过“编辑入参”继续修改，每次保存生成新修订，历史 RunSpec 不受影响。

## 验证

    .\.venv\Scripts\python.exe -m pytest tests -q
    npm.cmd run build --prefix apps/web

## 推荐实施方式

首期采用“模块化单体控制面 + 独立 Runner 执行面”，先完成业务闭环和执行可靠性；达到规模阈值后，再按执行编排、实时事件、报告等边界拆分服务。