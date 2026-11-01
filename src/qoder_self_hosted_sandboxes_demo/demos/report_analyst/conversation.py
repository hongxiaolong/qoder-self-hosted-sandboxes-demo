"""Terminal follow-up chat with persistent, explicit idempotent retries."""
from contextlib import contextmanager
import fcntl
import json
import os
import tempfile
import time
import uuid

from .live_session import ChatError, LiveSession
from .shared import api, bind_trace, results


def observer(config, values, state):
    client = api(config, values)
    client.timeout = 5
    return LiveSession(client, state["session_id"], results(state) / "live-session.sqlite3")


@contextmanager
def pending_record(state):
    path = results(state) / "pending-message.json"
    with path.with_suffix(".lock").open("a") as lock:
        os.fchmod(lock.fileno(), 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ChatError("另一个终端正在提交消息，请稍后重试。") from None
        try:
            try:
                if path.exists():
                    path.chmod(0o600)
                    pending = json.loads(path.read_text())
                else:
                    pending = None
            except (OSError, ValueError) as exc:
                raise ChatError("无法读取本地待确认消息，请检查文件或权限。", 500) from exc
            if pending is not None:
                if (not isinstance(pending, dict)
                        or pending.get("session_id") != state["session_id"]
                        or not isinstance(pending.get("text"), str)):
                    raise ChatError("待确认消息记录不属于当前会话或已损坏，请检查本地记录。")
                try:
                    uuid.UUID(pending["request_id"])
                except (KeyError, ValueError, TypeError, AttributeError):
                    raise ChatError("待确认消息编号已损坏，请检查本地记录。") from None
            yield path, pending
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def save_pending(path, pending):
    # Create with private permissions before writing; replace only after flush.
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(pending, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def submit(live, state, text=None, *, retry=False):
    with pending_record(state) as (path, pending):
        if retry:
            if not pending:
                raise ChatError("没有待确认消息，无需重试。", 400)
        else:
            if pending:
                raise ChatError("上一条提交尚未确认。输入 /retry 沿用原编号重试，或 /discard 放弃本地重试记录。")
            if not isinstance(text, str) or not text.strip() or len(text) > 16000:
                raise ChatError("请输入 1–16000 字的消息。", 400)
            pending = dict(session_id=state["session_id"], request_id=str(uuid.uuid4()), text=text.strip())
            try:
                save_pending(path, pending)
            except OSError as exc:
                raise ChatError("消息未提交：无法保存本地重试记录，请检查磁盘或权限。", 500) from exc
        receipt = live.send(pending["text"], pending["request_id"], source="terminal")
        path.unlink()
        return receipt


def chat(config, values, state, message=None, *, retry=False):
    if retry and message is not None:
        raise ValueError("重试时不能同时提供新消息。")
    if state.get("cleaned") or time.time() >= state["created_at"] + config["sandbox"]["timeout_seconds"]:
        raise ValueError("本轮执行环境已结束，请重新创建。")
    if not all(state.get(p + "_verified") for p in ("collect", "revise", "append")):
        raise ValueError("先完成三轮引导任务及验收，再继续自由对话。")
    live = observer(config, values, state)
    bind_trace(live.client, state, values, mutations_only=True)
    once = message is not None or retry
    print("\n自由对话 · 复用当前 Qoder Session 与两个 Sandbox", flush=True)
    print("输入 /retry 重试待确认提交，/discard 放弃本地重试记录，/back 返回收尾。", flush=True)
    while True:
        try:
            text = "/retry" if retry else message if once else input("\n你 › ").strip()
            if not once and text == "/back":
                return
            if not text:
                if once:
                    raise ChatError("请输入 1–16000 字的消息。", 400)
                continue
            if not once and text == "/discard":
                with pending_record(state) as (path, pending):
                    if pending:
                        path.unlink()
                print("已放弃本地重试记录；该操作不会撤回已到达 Qoder 的消息。", flush=True)
                continue
            if time.time() >= state["created_at"] + config["sandbox"]["timeout_seconds"]:
                raise ChatError("本轮执行环境已到期，请返回收尾并回收资源。", 410)
            receipt = submit(live, state, text, retry=retry or (not once and text == "/retry"))
        except (EOFError, KeyboardInterrupt):
            if once:
                raise RuntimeError("提交被中断；请使用 chat --retry 确认原消息。") from None
            return
        except Exception as exc:
            if isinstance(exc, ChatError):
                print(str(exc), flush=True)
            else:
                print("提交未确认，已保留原消息与编号。输入 /retry 或使用 chat --retry 安全重试。", flush=True)
            if once:
                if isinstance(exc, ChatError):
                    raise
                raise RuntimeError("提交未确认；请使用 chat --retry 沿用原消息编号重试。") from exc
            continue
        print("  已发送，等待 Qoder 执行。", flush=True)
        remaining = state["created_at"] + config["sandbox"]["timeout_seconds"] - time.time()
        deadline = time.monotonic() + min(config["demo"]["turn_timeout_seconds"], max(0, remaining))
        seen = set()
        try:
            while time.monotonic() < deadline:
                snapshot = live.snapshot()
                turn = next((t for t in snapshot["turns"] if t["id"] == receipt["id"]), None)
                for tool in snapshot["tools"]:
                    if tool["message_id"] == receipt["id"] and tool["id"] not in seen:
                        print("  ↳ Qoder 请求工具：" + tool["tool"], flush=True)
                        seen.add(tool["id"])
                if turn and turn["state"] not in {"running", "waiting_tools"}:
                    for reply in turn["replies"]:
                        print("\nQoder › " + reply["text"], flush=True)
                    print("\n  本轮状态：" + {"completed": "已完成", "stopped": "已停止", "interrupted": "已中断"}.get(turn["state"], turn["state"]), flush=True)
                    break
                if snapshot["terminal"]:
                    print("会话已结束，请回收并重新创建资源。", flush=True)
                    return
                time.sleep(1.5)
            else:
                print("等待已超时，任务可能仍在执行；请检查当前 Qoder 会话，避免重复提交。", flush=True)
        except (EOFError, KeyboardInterrupt):
            if once:
                raise RuntimeError("消息已提交，进度观察已中断；请检查当前 Qoder 会话。") from None
            return
        except Exception as exc:
            if once:
                raise RuntimeError("消息已提交，但观察执行进度失败；请检查当前 Qoder 会话。") from exc
            print("消息已提交，暂时无法观察进度；请检查当前 Qoder 会话。", flush=True)
        if once:
            return
