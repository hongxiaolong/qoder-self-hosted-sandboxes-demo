# SPDX-License-Identifier: Apache-2.0
"""演示运行记录与角色到 Sandbox 实例的映射。"""

import json
from qoder_self_hosted_sandboxes_demo.settings import RESULTS_DIR
from qoder_self_hosted_sandboxes_demo.qoder.client import API
from qoder_self_hosted_sandboxes_demo.sandbox.client import connect_sandbox


def api(config, values):
    return API(config["qoder"]["base_url"], values["QODER_ACCESS_TOKEN"])


def connect(state, values, key="analysis_sandbox_id"):
    return connect_sandbox(state[key], values)


def emit(kind, **data):
    print(
        json.dumps({"kind": kind, **data}, ensure_ascii=False, default=str), flush=True
    )


def results(state):
    p = RESULTS_DIR / state["run_id"]
    p.mkdir(parents=True, exist_ok=True)
    return p


def worker_logs(box):
    return [
        json.loads(x)
        for x in box.files.read("/tmp/demo-worker/worker.jsonl").splitlines()
        if x.strip()
    ]
