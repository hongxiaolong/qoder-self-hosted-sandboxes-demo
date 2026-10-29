# SPDX-License-Identifier: Apache-2.0
"""在 Worker 所在的 Sandbox 执行工具。

模块定位：实现演示需要的 Bash、Read、Write、Edit；命令与文件操作均发生在远端。
主要逻辑：固定工作目录、清理子进程凭证、限制输出，超时后终止命令进程组。
支撑能力：让 Qoder 的模型通过真实工具调用读取项目、修改代码和运行测试。
    路径检查是演示约束；Bash 的真正隔离边界是阿里云 Sandbox。
"""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import tempfile

MAX_OUTPUT = 24000


def clean_env():
    # Allowlist rather than inheriting the Worker's credential-bearing environment.
    result = {
        k: v
        for k, v in os.environ.items()
        if k in {"PATH", "LANG", "LC_ALL", "HOME", "USER", "TMPDIR", "TZ"}
    }
    result.update(PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1")
    return result


class Tools:
    def __init__(self, workdir):
        self.root = Path(workdir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, raw):
        p = Path(raw)
        p = (self.root / p).resolve() if not p.is_absolute() else p.resolve()
        if not p.is_relative_to(self.root):
            raise ValueError("File tools accept paths inside the workspace only")
        return p

    def execute(self, name, args):
        method = getattr(self, "tool_" + name.lower(), None)
        if method is None:
            return f"Unsupported tool: {name}", True
        try:
            return method(args)
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}", True

    def tool_bash(self, args):
        command = args.get("command")
        if not isinstance(command, str) or not command:
            raise ValueError("Bash requires command")
        if args.get("run_in_background"):
            raise ValueError("This executor only executes foreground commands")
        # Qoder's Bash timeout parameter uses milliseconds.
        seconds = min(max(float(args.get("timeout", 60000)) / 1000, 1), 90)
        with tempfile.TemporaryFile() as out:
            proc = subprocess.Popen(
                ["/bin/bash", "-lc", command],
                cwd=self.root,
                env=clean_env(),
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                code = proc.wait(timeout=seconds)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                code = 124
            out.seek(0)
            text = out.read(MAX_OUTPUT + 1).decode(errors="replace")
        return f"exit_code={code}\n{text[:MAX_OUTPUT]}", code != 0

    def tool_read(self, args):
        path = self.path(args["file_path"])
        offset = max(1, int(args.get("offset", 1))) - 1
        limit = min(2000, max(1, int(args.get("limit", 2000))))
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("File read limit is 1 MiB")
        lines = path.read_text().splitlines()
        return "\n".join(
            f"{i + 1}: {s}" for i, s in enumerate(lines) if offset <= i < offset + limit
        )[:MAX_OUTPUT], False

    def tool_write(self, args):
        path = self.path(args["file_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args["content"], encoding="utf-8")
        return f"Wrote {path.relative_to(self.root)}", False

    def tool_edit(self, args):
        path = self.path(args["file_path"])
        old, new = args["old_string"], args["new_string"]
        text = path.read_text()
        if not old or old not in text:
            raise ValueError("old_string must be non-empty and present in file")
        if not args.get("replace_all") and text.count(old) != 1:
            raise ValueError("old_string is ambiguous; provide more context")
        path.write_text(text.replace(old, new, -1 if args.get("replace_all") else 1))
        return f"Edited {path.relative_to(self.root)}", False
