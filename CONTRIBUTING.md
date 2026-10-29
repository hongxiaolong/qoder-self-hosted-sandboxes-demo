# Contributing

使用 Python 3.11+。安装 `python -m pip install -e ".[demo,build]"` 后运行：

```bash
python -m unittest discover -s tests -v
```

涉及 Worker 协议的改动应覆盖确认、心跳、工具结果回传、失败重试和中断行为。涉及数据处理的改动应使用真实临时文件验算，不能只验证 mock 返回值。

云端联调使用独立资源并运行三轮验收。只有 `analysis_mode=custom_image` 的完整验证，才能作为自定义镜像流程通过的证据。源码变更后重新构建镜像与模板，不在实例中临时替换代码来宣称镜像通过。

不要提交凭证、账号资源配置或原始会话记录。文档中的成功判定应与验收代码一致。

通用 Worker、Qoder API 不得反向依赖 `demos/`。增加工具通过 `tool_provider` 注入，不在 Worker 中硬编码某个演示。人工配置放 `config/`，生成文件放 `var/`。
