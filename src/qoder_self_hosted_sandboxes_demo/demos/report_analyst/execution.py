"""Audience-facing projections of real Worker events, never the raw log."""
import json
from pathlib import Path
import re


LABELS = {
    "download_report": ("browser", "筛选并下载报表"),
    "run_python": ("analysis", "运行 Python 分析"),
    "append_record": ("analysis", "追加现场记录"),
    "publish_dashboard": ("analysis", "核算并发布看板"),
    "export_artifacts": ("analysis", "生成交付文件"),
}


def public_text(value, limit=8000):
    text = str(value)
    text = re.sub(r'(https?://[^\s"?]+)\?[^\s"]+', r'\1?[redacted]', text)
    text = re.sub(r'(?i)(bearer\s+)[\w.\-]+', r'\1[redacted]', text)
    text = re.sub(r'''(?im)((?:[\w]*(?:token|password|secret|api_key|access_key)[\w]*)["']?\s*[:=]\s*)[^\n,}]+''', r'\1[redacted]', text)
    return text[:limit] + ("\n…内容已截断" if len(text) > limit else "")


def execution_snapshot(root, log_path):
    root = Path(root)
    identity = json.loads((root / "identity.json").read_text())
    calls = {}
    path = Path(log_path)
    if path.exists():
        with path.open("rb") as stream:
            size = stream.seek(0, 2)
            stream.seek(max(0, size - 2 * 1024 * 1024))
            if size > 2 * 1024 * 1024:
                stream.readline()
            for line in stream:
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    continue  # A concurrent write may not have completed yet.
                if not isinstance(event, dict):
                    continue
                tool = event.get("tool")
                key = event.get("tool_use_id")
                if tool not in LABELS or not key:
                    continue
                if event.get("session_id") != str(identity.get("qoder_session_url", "")).rsplit("/", 1)[-1]:
                    continue
                if event.get("kind") == "tool_start":
                    role, title = LABELS[tool]
                    args = event.get("input") or {}
                    if not isinstance(args, dict):
                        continue
                    inputs = {
                        "download_report": {k: args.get(k) for k in ["month", "region"]},
                        "run_python": {"file": "/workspace/input.csv", "script": "/workspace/analysis.py"},
                        "append_record": {k: args.get(k) for k in ["date", "category", "region", "amount"]},
                        "publish_dashboard": {"file": args.get("analysis_file", "analysis.json")},
                        "export_artifacts": {"file": "artifacts/dashboard.json"},
                    }[tool]
                    calls[key] = dict(id=key, tool=tool, role=role, title=title,
                                      started_at=event.get("time"), state="running",
                                      input=public_text(json.dumps(inputs, ensure_ascii=False), 1500),
                                      code=public_text(args.get("code", "")) if tool == "run_python" else "",
                                      output="等待工具返回真实结果")
                    calls[key]["input_summary"] = (
                        f"{args.get('month', '2026-10')} · {args.get('region', '华东')}" if tool == "download_report"
                        else "analysis.py（工作目录含 input.csv）" if tool == "run_python"
                        else "input.csv + 1 条用户指定记录" if tool == "append_record"
                        else "analysis.json + input.csv" if tool == "publish_dashboard"
                        else "已核算的看板结果"
                    )
                elif event.get("kind") == "tool_finished" and key in calls:
                    output = event.get("output", "")
                    # Only project the business fields of structured tool results.
                    if tool != "run_python" and not event.get("is_error"):
                        try:
                            data = json.loads(output)
                            if tool == "download_report":
                                output = json.dumps({k: data.get(k) for k in ["rows", "bytes", "browser_path", "analysis_path", "source_sha256", "target_sha256"]}, ensure_ascii=False)
                            elif tool == "export_artifacts":
                                output = json.dumps([{k: f.get(k) for k in ["name", "bytes"]} for f in data], ensure_ascii=False)
                            else:
                                output = json.dumps({k: data[k] for k in ["revision", "row_count", "total_amount", "before_rows", "after_rows", "record"] if k in data}, ensure_ascii=False)
                        except (ValueError, TypeError, KeyError, AttributeError):
                            output = "工具已完成，结果格式无法摘要，请查看终端验收。"
                    calls[key].update(state="error" if event.get("is_error") else "completed",
                                      finished_at=event.get("time"), elapsed_ms=event.get("elapsed_ms"),
                                      output=public_text(output, 4000))
                    calls[key]["output_summary"] = "执行失败，展开查看原因" if event.get("is_error") else "已返回真实结果，展开查看输出"
                    if tool == "run_python" and str(output).startswith("exit_code=0"):
                        calls[key]["output_summary"] = "退出码 0 · 代码执行成功，展开查看标准输出"
                    if not event.get("is_error") and tool != "run_python":
                        try:
                            result = json.loads(output)
                            summary = {
                                "download_report": lambda: f"{result['rows']} 条数据已下载并传入分析沙箱",
                                "append_record": lambda: f"{result['before_rows']} → {result['after_rows']} 条数据（不含表头）",
                                "publish_dashboard": lambda: f"v{result['revision']} · {result['row_count']} 条 · 合计 {result['total_amount']:,.2f}",
                                "export_artifacts": lambda: " / ".join(f['name'] for f in result),
                            }[tool]()
                            calls[key]["output_summary"] = public_text(summary, 300)
                        except (ValueError, TypeError, KeyError):
                            pass
    events = list(calls.values())
    return {"identity": identity, "events": events[-24:], "recorded": bool(events)}
