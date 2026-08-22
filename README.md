# 小学课程智能排课系统

一个轻量的单学校、单管理员 B/S 排课应用。后端使用 FastAPI，前端不需要构建工具；本地使用 JSON，Render 部署时可使用 Upstash Redis 云端持久化。

## 已实现功能

- 按 2025.8 课程标准自动控制各年级周课时：一、二年级 30 节；三至六年级 35 节。
- 固定班会、阅读连堂、趣味课堂，并避开英语、语文、数学教研时间。
- 每天上午前两节只排语数英，一、二年级只排语数；三至六年级上午第三节优先排语数英。
- 同一教师同一课时只允许在一个班任课。
- 默认每个年级 6 个班，可按实际情况修改。
- 支持数学、英语教师跨兄弟班以及音体美等小课教师跨多个班任课，自动避开教师时间冲突。
- 体育、艺术等每周多节的副科会优先分散到不同日期；无法完全满足时随生成结果提示。
- 小课教师 6—12 个班、每周至少 12 节等要求作为生成后警告，不阻止课表生成；大量提示默认折叠。
- 配置学校名称、班级数量、教师及任课关系。
- 教师名单支持按模板批量导入 `.xlsx`，可填写姓名、期望最低周课时和多个可任科目；同名已有教师会更新，文件内重名会自动编号区分。
- 任课配置按“年级 → 班级”筛选，并可通过姓名搜索教师；跨班级修改会暂存，最后一次性保存所有已修改班级。
- 班级课表按“年级 → 班级”两级选择查看，也可切换查看教师课表。
- 支持正式 A4 横向打印班级课表和教师课表；班级打印只显示科目，教师课表会同时显示班级与科目。
- 班级课表可按全部年级或指定年级生成多页 PDF，适合一次性批量打印。
- 教师课表可按全部或年级批量导出 ZIP（每位教师一份独立 PDF），也可按姓名搜索并下载单名教师 PDF。
- 所有 PDF 均为正式 A4 横向、纯白底、加粗表头的简洁样式。
- 本地 JSON 与 Upstash 云端存储双模式自动切换，无需 SQL。

## 运行

项目约定使用 Conda 环境 `classassign`。

```powershell
conda activate classassign
pip install -r requirements.txt
Copy-Item .env.example .env
# 编辑 .env，填写管理员密码和至少32位的 CLASSASSIGN_SECRET
python run.py
```

也可以在 Windows 中双击 `启动排课系统.bat`。

启动后访问：<http://127.0.0.1:8765>

当前工作目录已经包含一个仅限本机、不会提交到 Git 的 `.env`，可继续使用原本的本地登录信息。新环境需要自行创建 `.env`。

账号、密码和 Cookie 签名密钥不再写在源代码中。

## 配置项

配置可写入本机 `.env`，或由 Render 等部署平台注入环境变量：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CLASSASSIGN_USERNAME` | 无 | 管理员账号 |
| `CLASSASSIGN_PASSWORD` | 无 | 管理员密码，至少8位 |
| `CLASSASSIGN_SECRET` | 无 | 登录 Cookie 签名密钥，至少32位 |
| `CLASSASSIGN_HOST` | `127.0.0.1` | 监听地址；服务器可设为 `0.0.0.0` |
| `CLASSASSIGN_PORT` | `8765` | 服务端口 |
| `CLASSASSIGN_DATA_DIR` | 项目下的 `data` | JSON 数据保存目录 |
| `CLASSASSIGN_SECURE_COOKIE` | `0` | 使用 HTTPS 时设为 `1` |
| `UPSTASH_REDIS_REST_URL` | 空 | Upstash Redis 的 REST URL；与 Token 同时配置后启用云存储 |
| `UPSTASH_REDIS_REST_TOKEN` | 空 | Upstash Redis 的 Standard REST Token |
| `CLASSASSIGN_STATE_KEY` | `classassign:state:v1` | 云端保存整套应用状态所用的 Redis Key |

PowerShell 示例：

```powershell
$env:CLASSASSIGN_USERNAME = "admin"
$env:CLASSASSIGN_PASSWORD = "请替换为强密码"
$env:CLASSASSIGN_SECRET = "请替换为至少32位随机长字符串"
$env:CLASSASSIGN_HOST = "0.0.0.0"
$env:CLASSASSIGN_PORT = "8765"
python run.py
```

## 数据与备份

未配置 Upstash 时，系统会自动创建 `data/app_state.json`。停止服务后复制该文件即可备份；还原时覆盖同名文件即可。

同时配置 `UPSTASH_REDIS_REST_URL` 和 `UPSTASH_REDIS_REST_TOKEN` 后，系统会把完整状态以一个 JSON 值保存在 Upstash。若云端 Key 尚不存在，系统会自动初始化；一旦启用云端模式，连接失败会明确报错，不会静默改存到 Render 临时磁盘。

“排课规则”页面可以下载不含已生成课表的配置 JSON；“生成课表”页面可以导出班级或教师 PDF。

## Render 免费部署

仓库根目录已经提供 `render.yaml`，不需要 Docker：

1. 在 Upstash 创建一个 Redis 数据库，复制 REST URL 和 **Standard** REST Token。
2. 将代码推送到 GitHub。确认 `.env` 与 `data/app_state.json` 没有被提交。
3. 在 Render 选择 **New → Blueprint**，连接此仓库；Render 会读取 `render.yaml`。
4. 首次创建时填写 `CLASSASSIGN_PASSWORD`、`UPSTASH_REDIS_REST_URL`、`UPSTASH_REDIS_REST_TOKEN`。`CLASSASSIGN_SECRET` 由 Render 自动生成。
5. 创建服务并等待健康检查通过。以后推送仓库即可自动更新，账号密码与云存储 Token 不会出现在 GitHub 中。

Render 使用 HTTPS，因此配置已将安全 Cookie 打开。应用定位为单管理员，默认以一个 Uvicorn 进程运行；Upstash 的单 Key 写入适合当前规模。

## 普通服务器部署

安装 `requirements.txt` 后，可以直接运行 `python run.py`，或使用 Uvicorn 监听服务器端口。公网使用时请配置 HTTPS，并将 `CLASSASSIGN_SECURE_COOKIE` 设为 `1`。
