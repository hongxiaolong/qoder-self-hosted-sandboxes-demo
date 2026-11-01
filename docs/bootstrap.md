# 账号资源自动准备

在仓库根目录运行 `./start.sh`。本版本保留自定义分析镜像构建、两个 Sandbox 和三轮任务，没有切换为开发环境替代自定义模板。

## 客户需要提供什么

- 阿里云 AK/SK 和 region；已有环境变量可以回车采用。STS 临时凭证还需配套 Token。
- Qoder 国际站 PAT：独立授权，阿里云凭证不能替代。输入 `open` 打开官方获取入口。
- 选择现有资源或新建，并批准实际变更；无需手填 Team ID、Sandbox Key、ACR 域名、实例 ID 和网络 ID。

已有完整旧配置且尚无自动准备账本时，可以输入 `reuse` 沿用原 Team、Key、仓库与网络，或输入 `discover` 重新发现。复用会先验证 Qoder 和 Sandbox 列表访问，不创建新 Key、不认领旧资源的删除权；Key 的 Team 关联仍由客户原配置指定，不能从访问成功推导出来。ACR 临时登录不可用时，可明确选择已有 Docker 登录或仓库专用凭证。

Python 3.11+ 和可运行的 Docker/Buildx 仍是本机构建条件。Python 包安装自动执行，系统软件安装、账号实名、控制台服务协议确认及无权自行完成的 RAM 授权仍由用户/管理员处理。

## 实际执行顺序

1. 用独立 Qoder 凭证查询模型；用 STS GetCallerIdentity 核实阿里云账号，检查历史记录的账号和地域。
2. 完整分页查询 Team，显示名称、ID、计划；选择复用或在默认资源组新建 Pro Team。不会升级已有 Team。
3. 复用本向导保存、指纹匹配且管理面状态正常的专用 Key；没有可用明文则批准创建专用 Key。本项目同名 Key 明文丢失可批准删除重建，不重置其他业务 Key。管理面状态不等于数据面已通过，后续模板/实例调用会继续验证。
4. 查询同地域运行中的 ACR 企业版 Basic/Standard/Advanced。客户选择复用或新购 Basic 1个月、手动续费。购买前展示条款和控制台实时价格入口，需输入 `purchase`，随后确认自动支付。当前未实现自动报价和最高金额约束，未核对价格不要批准；只对中国站产品类型实现自动购买。
5. 以所选 ACR 绑定为优先级查询交换机，要求 FC 支持的可用区、可用状态、至少8个空闲地址；可复用现有 VPC 中的交换机、在现有 VPC 新建交换机，或新建独立 VPC。新交换机自动选择不重叠的 /24 网段。
6. 查询同 VPC 非服务托管的普通安全组。已有规则可确认允许 TCP/443 时可复用，否则选择新建，不修改旧规则。ACR 缺少绑定时，经批准创建 VPC 绑定，允许平台创建 PrivateZone 角色/记录，等待 RUNNING。
7. 选择私有仓库或新建专用命名空间、仓库。从接口获取真实域名。公网入口关闭时，先经批准添加本机公网出口 /32 白名单，再开启入口；已有受限入口仅追加需要的 /32，保留原条目。使用 api.ipify.org 查询出口，不发送凭证。
8. 写入本机 0600 配置；通过 GetAuthorizationToken 获取临时仓库凭证，在 Docker 就绪后自动登录，继续模板构建和三轮验收。Browser 镜像地域随 region 调整，但不承诺所有地域/镜像版本均已实测。

写前确认需要输入指定词（通常为 `yes`，购买为 `purchase`）；回车、`no` 或输错会继续提示，未批准不执行。`Ctrl+C` 才退出。通过 `./start.sh` 运行时，查询或创建报错会暂停在终端，保留错误与资源记录，不自动重放操作；修正后按 `Ctrl+C` 退出再重跑。

查询或创建失败不视为“账号没有资源”。错误保留接口名、错误码、RequestId，不输出请求体/密钥。错误提示提供服务入口；完成开通、授权或余额处理后重新运行即可保留已完成进度。

## 恢复和回收

`var/state/bootstrap.json` 记录账号、地域、项目标识、资源归属、写操作意图、幂等 Token、完成状态；Key 明文只在 `config/.env`，不写资源账本。重跑自动匹配本项目资源。购买和支持幂等的网络创建使用相同 Token 恢复；没有幂等保障且结果未知的请求会先通过列表核对，不能安全确认时停止并要求核对该操作。明确的权限/参数拒绝可在修正后重试。禁止同时运行两个准备向导。

操作记录按目标 ACR、VPC 等参数隔离，同名命名空间和相同白名单地址不会跨实例继承归属。创建结果已保存、资源归属尚未保存时，可根据已完成操作里的精确资源 ID 恢复。卸载遇到明确的 NotFound 按已删除续跑；权限、资源占用和服务错误仍保留失败状态。

- `qoder-self-hosted-sandboxes-demo cleanup`：回收运行实例和 Qoder 资源，保留模板及基础设施。
- `.venv/bin/python scripts/bootstrap.py --uninstall`：先要求运行资源已回收，再删除本目录模板及本向导拥有的 Key、仓库、命名空间、白名单、绑定和网络，最后删除本项目 Team。复用资源保留，云端存在依赖会停止。
- 自动开启的公网入口先关闭，再删除自己的白名单，避免移除最后一个条目后扩大暴露范围。
- 预付费 ACR、默认 OSS 存储、共享服务角色保留并提示；退订退款没有接入，本命令不能承诺停止全部基础设施费用。

不要删除资源账本或跨账号拷贝凭证后继续运行。脚本不允许将其他账号/地域覆盖进已有记录。同目录卸载后再次准备仍会重新查询云端；外部删除的资源遇到不确定记录应先核对，禁止手工篡改归属。

## 验证范围

新增测试使用内存云服务覆盖空白账号创建与重复运行、拒绝授权、账号切换、未知写请求禁止重放、购买超时幂等和归属保护；使用真正安装的官方 SDK 与本机 HTTP 服务验证 Team/Key 请求的签名、序列化和 RPC 分页参数。原有业务、网页、下载、Worker 和向导测试一并回归。

本次合入版本未使用真实凭证，未创建云资源或真实购买。空白账号首次开通、ACR 购买、服务角色授权、PrivateZone、不同地域容量和端到端 LIVE_PASS 仍需在实际账号验收；本地测试不能证明这些外部条件已满足。

## 官方接口依据

- [Sandbox 原生 SDK](https://help.aliyun.com/zh/agent-sandbox/alibaba-cloud-sdk-api-description-1)
- [Team 创建及开通前提](https://help.aliyun.com/zh/agent-sandbox/create-team)
- [Key 明文与管理边界](https://help.aliyun.com/zh/agent-sandbox/api-key-management)
- [ACR 创建并支付](https://help.aliyun.com/zh/acr/developer-reference/createinstance-1)
- [ACR VPC 绑定](https://help.aliyun.com/zh/acr/developer-reference/api-cr-2018-12-01-createinstancevpcendpointlinkedvpc)
- [临时登录凭证](https://help.aliyun.com/zh/acr/developer-reference/api-cr-2018-12-01-getauthorizationtoken)
- [FC 支持的可用区](https://help.aliyun.com/zh/functioncompute/configure-network-settings)，清单核对日期 2026-10-29。
