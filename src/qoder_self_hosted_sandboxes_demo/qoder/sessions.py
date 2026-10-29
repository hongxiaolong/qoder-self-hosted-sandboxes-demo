# SPDX-License-Identifier: Apache-2.0
"""Sessions：绑定 Agent 与 Environment；停止工作并按归属回收 Session 关联资源。"""

from .client import APIError


def create(client, agent, environment, run_id, title):
    return client.request(
        "POST",
        "/sessions",
        {
            "agent": {"id": agent["id"], "type": "agent", "version": agent["version"]},
            "environment_id": environment["id"],
            "title": title,
            "metadata": {"demo_run": run_id},
        },
    )


def cleanup_owned(client, state, persist):
    errors = []
    if state.get("work_id") and not state.get("work_stopped"):
        try:
            client.request(
                "POST",
                f"/environments/{state['environment_id']}/work/{state['work_id']}/stop",
                {"force": True},
            )
            state["work_stopped"] = True
            persist(state)
        except APIError as exc:
            if exc.status == 404:
                state["work_stopped"] = True
                persist(state)
            else:
                errors.append(str(exc))
        except Exception as exc:
            errors.append(str(exc))
    for key, plural, method, suffix in [
        ("session_id", "sessions", "POST", "/archive"),
        ("agent_id", "agents", "POST", "/archive"),
        ("environment_id", "environments", "POST", "/archive"),
        ("skill_id", "skills", "DELETE", ""),
    ]:
        if state.get(key) and not state.get(key + "_cleaned"):
            try:
                item = client.request("GET", f"/{plural}/{state[key]}")
                if item.get("metadata", {}).get("demo_run") != state["run_id"]:
                    raise ValueError("资源归属不匹配")
                client.request(method, f"/{plural}/{state[key]}" + suffix)
                state[key + "_cleaned"] = True
                persist(state)
            except APIError as exc:
                if exc.status == 404:
                    state[key + "_cleaned"] = True
                    persist(state)
                else:
                    errors.append(str(exc))
            except Exception as exc:
                errors.append(str(exc))
    return errors
