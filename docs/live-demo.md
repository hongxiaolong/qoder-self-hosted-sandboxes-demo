# Qoder Self-hosted Sandboxes Demo：报表分析 Agent

## 1. 安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[demo,build]"
```

Python 使用 3.11+。镜像构建使用已启动的 Docker 和 Buildx。

## 2. 配置连接

```bash
cp -n config/.env.example config/.env
cp -n config/.env.cloud.example config/.env.cloud
cp -n config/registry.example.toml config/registry.toml
chmod 600 config/.env config/.env.cloud
```

填写 `config/.env`：

```dotenv
QODER_ACCESS_TOKEN=<Qoder 国际站 PAT>
E2B_API_KEY=<阿里云 Sandbox Team API Key>
E2B_API_URL=https://api.cn-hangzhou.sandbox.aliyuncs.com
E2B_DOMAIN=cn-hangzhou.sandbox.aliyuncs.com
```

Qoder PAT 使用国际站账号创建；本例调用 `https://api.qoder.com/api/v1/cloud`。阿里云 Key 在 [杭州 Agent Sandbox 控制台](https://console.aliyun.com/sandbox/cn-hangzhou/overview) 对应 Team 的 API Keys 中创建。阿里云 Key 与 E2B Cloud Key 不可混用。

填写 `config/registry.toml`：

```toml
repository="<实例名>-registry.cn-hangzhou.cr.aliyuncs.com/sandboxhub/qoder-self-hosted-sandboxes-demo"
instance_id=""
vpc_id=""
vswitch_id=""
security_group_id=""
region="cn-hangzhou"
team_id="<与 E2B_API_KEY 相同的 Team ID>"
```

这里 `sandboxhub` 是命名空间，`qoder-self-hosted-sandboxes-demo` 是本项目的私有镜像仓库，`analysis-<源码哈希>` 是镜像 tag。先在 ACR 创建对应命名空间和私有仓库，或替换为自己已有的路径。

在 ACR 控制台打开该实例的 **访问控制 → 专有网络**，添加本次使用的 VPC 绑定，等绑定状态为 **RUNNING** 后再构建模板。自动推导会读取已有绑定，不会替你创建 VPC 绑定；公网 Docker 登录成功不能证明平台已具备 VPC 拉取路径。

这里的 VPC 参数用于 ACR 镜像拉取，不配置运行实例的 VPC 访问。`team_id` 必须属于当前账号并与 `E2B_API_KEY` 对应；Team 删除后重新创建，即使名称仍为 default，也要更新 ID。

已有 Docker 登录直接复用，无需填写 `config/.env.registry`。若希望脚本自动登录，先执行 `cp -n config/.env.registry.example config/.env.registry`，再填写 `ACR_USERNAME`（ACR 登录用户名）和 `ACR_PASSWORD`（镜像仓库登录密码或临时 Token，不是阿里云 AK 或控制台登录密码）。脚本只读取 `config/.env.registry`，不会读取 `.example`；通过标准输入传递密码。仓库需要允许推送 Linux AMD64 镜像。

`config/.env.cloud` 仅用于通过阿里云原生 OpenAPI 创建自定义模板：

```dotenv
ALIBABA_CLOUD_ACCESS_KEY_ID=<阿里云 AK ID>
ALIBABA_CLOUD_ACCESS_KEY_SECRET=<阿里云 AK Secret>
ALIBABA_CLOUD_SECURITY_TOKEN=
```

使用临时凭证时填写 Security Token。运行现成模板不需要把 AK 注入 Worker。账号需拥有对应 Team 的模板创建权限及 ACR 拉取条件。优先按镜像地址自动推导 ACR 实例和网络；如平台返回拉取网络错误，再填写实例 ID、VPC、vSwitch 与安全组。

```bash
qoder-self-hosted-sandboxes-demo doctor
```

成功输出 `doctor_pass` 表示 Qoder 模型列表可访问，不打印凭证；Sandbox 接入由第 5 节单独验证。

## 3. 构建两个模板

```bash
qoder-self-hosted-sandboxes-demo templates browser
qoder-self-hosted-sandboxes-demo templates analysis
```

镜像、规格、启动命令、就绪检查统一在 `config/templates.toml` 配置。第一条使用官方 Browser 镜像，配置浏览器启动命令与 `/health` 就绪检查。第二条构建自定义分析镜像、复用 Docker 登录推送 ACR，再从 VPC 镜像地址创建二代模板并等待 `ready`。

模板 ID 写入 `var/state/templates.json`。在控制台「沙箱模板」可查到两个模板；分析模板的镜像地址应为自己的 ACR 仓库。两个构建命令每次都会创建新模板，已有就绪模板时无需重复构建。构建是环境准备操作，不计入下面 10 分钟演示。

镜像固化的运行依赖：E2B SDK、Playwright 客户端、pandas、Plotly、openpyxl；Chromium 在 Browser 模板内，分析镜像不重复安装浏览器。代码或 Dockerfile 修改后重新构建分析模板。

## 4. 创建 Qoder 与 Sandbox 资源

```bash
qoder-self-hosted-sandboxes-demo setup
qoder-self-hosted-sandboxes-demo status
```

自定义实例先核对镜像中的运行代码与网页，检查服务是否已在运行；若未就绪直接报错，不在现场补启动。

输出 `setup_ready`、Qoder Session 地址、两个不同的 Sandbox ID 和创建耗时。默认实例存活 3600 秒，可在 `config/app.toml` 调整，受账号限制约束。

在 Qoder 的 Agents、Environments、Skills、Sessions 页面查看本次资源。进入输出的 Session 地址发任务；无需在 Environment 的 Setup script 中输入任务。

在阿里云杭州控制台对应 Team 的「沙箱实例」查看这两个 ID：Browser 实例使用 Browser 模板，Analysis 实例使用 ACR 自定义模板。

另开终端：

```bash
source .venv/bin/activate
qoder-self-hosted-sandboxes-demo preview
```

打开 [演示入口](http://127.0.0.1:8765)。左侧为云端 Chromium 画面和文件传递证据，右侧为分析看板。点击「打开 Qoder 发任务」进入本次 Session。

## 5. 查看 Agent 与执行环境（0:00–1:00）

在 Qoder 的 Agents 页面打开本次报表分析 Agent，查看系统指令、工具和 `report-analysis` Skill。进入本次 Session，核对选择的 Agent 与 Self-hosted Environment。

在阿里云控制台对应 Team 的「沙箱实例」查看两个 ID：Browser 实例负责操作浏览器、下载报表；Analysis 实例负责执行分析代码、更新看板和生成文件。

打开本机预览，左侧显示浏览器执行过程，右侧显示分析看板。后续任务都从同一 Qoder Session 发起。

## 6. 下载报表并分析（1:00–4:00）

在 Qoder Session 点击 **Send Message**，输入以下内容并点击 **Send**：

```text
请使用 report-analysis Skill，从数据门户筛选 2026 年 10 月、华东地区的数据：真实操作浏览器、翻页并下载 CSV，传到分析 Sandbox。用你生成的 Python 按分类、地区和日期统计金额，发布交互看板并导出 CSV、Excel、JSON 和离线报告。报告实际行数、总金额和两个 Sandbox ID。
```

提交后在终端执行（只观察并验收，不重复发消息）：

```bash
qoder-self-hosted-sandboxes-demo observe --phase collect
```

页面与结果：

1. 左侧 Chromium 选中月份与地区、翻到下一页、下载 CSV。
2. 文件证据显示 Browser 路径、Analysis 路径和相同的 SHA-256。
3. 右侧出现分类柱状图、每日趋势、数据行数和金额合计；鼠标悬停查看数据点。
4. 点击 Excel 下载真实文件，Data 页查看原始记录，Categories 页查看分类汇总。

默认合成数据应为 **100 行、45,392.18**；分类金额为计算 16,883.41、存储 15,835.75、网络 6,546.47、数据库 6,126.55。

成功判定：`verification` 中检查项全部为 `true`，Qoder 任务结束；本地 `var/results/<run_id>/collect-verification.json` 保存独立核算结果。

## 7. 连续追问（4:00–6:00）

在同一 Qoder Session 发送：

```text
沿用已下载的数据，找出金额最高的分类并排除，重新计算分类、地区和日期汇总，发布新看板并更新导出文件。不要重新下载，不修改 input.csv。说明排除了哪个分类、剩余行数和总金额。
```

```bash
qoder-self-hosted-sandboxes-demo observe --phase revise
```

成功判定：排除「计算」，剩余 **65 行、28,508.77**；看板版本增加、两个实例 ID 不变、`input.csv` 哈希不变。此轮执行的是新的分析代码，复用已下载文件，不重复采集。

## 8. 现场追加（6:00–8:30）

在同一 Session 发送：

```text
恢复展示所有分类。在当前 input.csv 追加一条记录：日期 2026-10-29，分类“现场演示”，地区“华东”，金额 6666。仅追加一次。重新生成并执行分析代码，发布看板并更新所有导出文件。报告与最初完整分析相比，行数与总金额的变化。
```

```bash
qoder-self-hosted-sandboxes-demo observe --phase append
```

成功判定：**101 行、52,058.18**；柱状图新增「现场演示」6,666；输入哈希改变，原始数据源哈希不变。Excel 中也应有这条新增记录。全部验收成功后输出 `LIVE_PASS`。

需要自由选择追加金额时，可修改消息；内置 `append` 验收固定检查 6,666，应同步调整验收条件。不要把不同金额的正确结果当作内置验收失败后强行跳过。

## 9. 核对执行证据（8:30–10:00）

```bash
qoder-self-hosted-sandboxes-demo status
qoder-self-hosted-sandboxes-demo verify --phase append
```

在控制台实例终端核对：

Browser 实例：

先在 Analysis 实例读取 `/workspace/evidence/transfer.json` 的 `browser_path`，再到 Browser 实例用 `ls -l <browser_path>` 查看实际下载文件。根据 Browser 镜像的下载行为，文件可能位于 `/tmp/report-analysis-<run_id>/` 或 `/home/user/browsertool/downloads/`。

Analysis 实例：

```bash
ls -l /workspace/input.csv /workspace/analysis.py /workspace/artifacts
cat /workspace/evidence/transfer.json
cat /workspace/evidence/append.json
```

下载离线 HTML 和 Excel，核对看板版本、分类和金额。看板中的实例 ID、传递证据中的实例 ID、控制台中的实例 ID 应一致。

## 10. 命令行完整复现

不在 Qoder 页面发消息时，用以下命令提交同样三轮任务：

```bash
qoder-self-hosted-sandboxes-demo task --phase collect
qoder-self-hosted-sandboxes-demo task --phase revise
qoder-self-hosted-sandboxes-demo task --phase append
```

`task` 与 `observe` 二选一，避免重复提交。`task` 会发送任务；`observe` 仅读取控制台刚发的一条新用户消息。等上一轮验收通过再发下一轮。

三轮原始提示词统一保存在 `config/demo.toml` 的 `[scenarios]`，可直接修改。以下从零创建并连续运行：

```bash
qoder-self-hosted-sandboxes-demo all --keep
```

`--keep` 保留本次资源便于在控制台核对；省略时，进入任务阶段后会在流程结束时清理。若 `setup` 中途失败，仍需按下一节排障并回收已创建的资源。已有未回收资源时拒绝重复 `setup`。

## 11. 排障与回收

- **Unauthorized / Invalid API key**：先核对 `config/.env` 中的三个 E2B 变量；配置文件中的非空值优先于终端环境变量。阿里云 Key 连接阿里云 Endpoint，E2B Cloud Key 连接 E2B Cloud。不要把 Key 打印到演示屏幕。
- **镜像推送连接中断**：检查本机 Docker 的代理与网络，恢复后重试构建；无需修改 Sandbox 的 VPC 或 Team 参数。
- **自定义模板拉取失败**：核对杭州 ACR 企业版仓库、镜像 tag、同账号拉取权限与可用网络。按实际错误补齐 `instance_id` 或 VPC 字段。
- **模板代码不一致**：重新运行分析模板构建；正式流程拒绝在旧镜像中临时覆盖源码来完成验证。
- **采集失败**：运行 `qoder-self-hosted-sandboxes-demo logs`，核对 Browser `/health`、CDP、数据页代理和实际下载目录。工具已兼容官方下载扩展目录。
- **页面被下载**：通过本机 `preview` 入口打开；云端网关可能给 HTML 添加附件响应头。本例代理保留鉴权并修正页面响应头。
- **预览端口占用**：先在旧预览终端按 `Ctrl+C`；也可修改 `config/demo.toml` 的 `preview_port` 后重试。`cleanup` 不会关闭本机预览进程。
- **任务超时**：运行 `qoder-self-hosted-sandboxes-demo resume` 接续读取已有任务，不再次提交追加记录。用 `logs` 查看 Worker；不要并行启动第二个 Worker。
- **setup 中途失败**：先 `status` 查看已创建资源。仅在尚未记录任何 Qoder Skill、Agent、Environment、Session，且两个 Sandbox 服务已恢复时使用 `resume-setup`；若已记录任一 Qoder 资源，先 `cleanup`，再 `setup`。程序会拒绝覆盖已有 Qoder 资源记录。
- **仅先验证工具链**：`qoder-self-hosted-sandboxes-demo setup --official-analysis` 显式使用官方分析模板，验证记录标记为 `official-development`，不代表 ACR 自定义镜像通过。
- **实例到期**：下载结果后重新 `cleanup`、`setup`；本例没有持久卷，不承诺到期后的文件或进程恢复。

```bash
qoder-self-hosted-sandboxes-demo cleanup
```

该命令仅清理本次运行记录拥有的资源：停止工作、释放双实例、归档 Qoder Session/Agent/Environment、删除本次 Skill。ACR 镜像和两个模板保留复用，需要时在对应控制台按具体 ID 删除。

## 参考资料

- [Agent Sandbox 实操——E2B SDK 接入](https://alidocs.dingtalk.com/i/nodes/KGZLxjv9VGkoG9YwHqLDNyjPV6EDybno)：参考其依赖、代码、命令和成功判定结构。
- [Agent 平台体验指引](https://alidocs.dingtalk.com/i/nodes/93NwLYZXWyxXroNzC07KqzkP8kyEqBQm)：参考其架构、界面操作与执行证据组织方式；本例未复现其中其他平台功能。
- [通过 SDK 使用 Agent Sandbox](https://help.aliyun.com/zh/agent-sandbox/using-the-cloud-sandbox-via-the-sdk)
- [使用 Browser Sandbox](https://help.aliyun.com/zh/agent-sandbox/use-browser-use-sandbox)
- [通过代码构建自定义模板](https://help.aliyun.com/zh/agent-sandbox/build-and-manage-custom-templates-with-code)
