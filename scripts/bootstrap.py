#!/usr/bin/env python3
"""账号资源准备。只读发现、明确选择、写前确认、按账号记录恢复。

不读取原仓库凭证；不重置业务 Key；SDK 错误仅输出错误码和 request ID。
"""
import contextlib
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid
import urllib.request

import onboarding as ui

# Official FC network documentation, checked 2026-10-29. Unknown regions fail
# closed until this compatibility list is refreshed, rather than guessing a zone.
FC_ZONES = {
    "cn-hangzhou": "h i j k f g", "cn-shanghai": "m l n b e g f",
    "cn-beijing": "i h k j l c e g f", "cn-shenzhen": "e d f",
    "cn-qingdao": "c", "cn-zhangjiakou": "b c a", "cn-huhehaote": "a b",
    "cn-chengdu": "a b", "cn-hongkong": "d c b",
    "ap-southeast-1": "a c b", "ap-southeast-3": "a",
    "ap-southeast-5": "a b", "ap-northeast-1": "c b a",
    "eu-west-1": "a", "eu-central-1": "a b", "us-west-1": "a b", "us-east-1": "b a",
}


def confirm(message, word="yes"):
    print("\n准备执行：" + message)
    while input("输入 " + word + " 批准；Ctrl+C 退出：").strip() != word:
        print("尚未批准，未执行此操作。请按提示输入；Ctrl+C 可退出。")


def choose(title, rows, label, allow_new=True):
    print("\n" + title)
    for i, row in enumerate(rows, 1):
        print(f"  {i}. 复用 {label(row)}")
    if allow_new:
        print("  n. 新建专用资源")
    while True:
        answer = input("选择编号" + ("或 n" if allow_new else "") + "：").strip()
        if answer == "n" and allow_new:
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(rows):
            return rows[int(answer) - 1]
        print("请选择列出的项目；Ctrl+C 可退出。")


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class CloudError(RuntimeError):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class Cloud:
    def __init__(self, credentials, region):
        self.credentials, self.region = credentials, region

    def config(self, endpoint):
        from alibabacloud_tea_openapi.models import Config
        return Config(access_key_id=self.credentials["ALIBABA_CLOUD_ACCESS_KEY_ID"],
                      access_key_secret=self.credentials["ALIBABA_CLOUD_ACCESS_KEY_SECRET"],
                      security_token=self.credentials.get("ALIBABA_CLOUD_SECURITY_TOKEN") or None,
                      endpoint=endpoint, region_id=self.region,
                      connect_timeout=10000, read_timeout=30000)

    @staticmethod
    def error(action, exc):
        code = str(getattr(exc, "code", None) or type(exc).__name__)
        data = getattr(exc, "data", None) or {}
        request_id = data.get("RequestId", data.get("requestId", "unknown")) if isinstance(data, dict) else "unknown"
        return CloudError(f"{action} 失败：{code}（RequestId={request_id}）。未将失败当作资源不存在。"
                          "请检查权限、开通、余额、配额和地域；完成后重跑。服务入口：https://console.aliyun.com/sandbox/", code)

    def rpc(self, service, action, **query):
        from alibabacloud_tea_openapi.client import Client
        from alibabacloud_tea_openapi.models import Params, OpenApiRequest
        from alibabacloud_tea_openapi.utils import Utils
        from alibabacloud_tea_util.models import RuntimeOptions
        version, endpoint = {
            "cr": ("2018-12-01", f"cr.{self.region}.aliyuncs.com"),
            "vpc": ("2016-04-28", f"vpc.{self.region}.aliyuncs.com"),
            "ecs": ("2014-05-26", f"ecs.{self.region}.aliyuncs.com"),
            "sts": ("2015-04-01", "sts.aliyuncs.com"),
            "resourcemanager": ("2020-03-31", "resourcemanager.aliyuncs.com"),
            "bssopenapi": ("2017-12-14", "business.aliyuncs.com"),
        }[service]
        try:
            result = Client(self.config(endpoint)).call_api(
                Params(action=action, version=version, protocol="HTTPS", pathname="/", method="POST",
                       auth_type="AK", style="RPC", req_body_type="formData", body_type="json"),
                OpenApiRequest(query=Utils.query(query)), RuntimeOptions(autoretry=False))["body"]
            if result.get("IsSuccess") is False or result.get("Success") is False or result.get("success") is False:
                code = result.get('Code', result.get('code'))
                raise CloudError(f"{action}: {code}; RequestId={result.get('RequestId')}", code)
            return result
        except CloudError:
            raise
        except Exception as exc:
            raise self.error(action, exc) from None

    def sandbox(self, action, body=None, path_id=None, **fields):
        from alibabacloud_sandbox20260820.client import Client
        from alibabacloud_sandbox20260820 import models
        request_type = getattr(models, action + "Request")
        request = request_type.from_map(request_type(), {"body": body, **fields})
        method = re.sub(r"(?<!^)(?=[A-Z])", "_", action).lower()
        try:
            args = (path_id, request) if path_id else (request,)
            response = getattr(Client(self.config(f"sandbox.{self.region}.aliyuncs.com")), method)(*args)
            result = response.body.to_map() if response.body is not None else {}
            if result.get("code") not in {None, "Success", "success", "200", "OK"}:
                raise CloudError(f"{action}: {result.get('code')}; RequestId={result.get('requestId')}", result.get("code"))
            return result
        except CloudError:
            raise
        except Exception as exc:
            raise self.error(action, exc) from None

    def pages(self, service, action, collection, **query):
        rows = []
        seen = set()
        # VPC discovery needs a smaller page size; use it for both requests
        # and the end-of-list check when TotalCount is omitted.
        page_size = 50 if service == "vpc" else 100
        for page in range(1, 10001):
            if service == "sandbox":
                result = self.sandbox(action, pageNumber=page, pageSize=page_size, **query)
            else:
                result = self.rpc(service, action, PageNumber=page, PageSize=page_size, **query) if service in {"vpc", "ecs"} else self.rpc(service, action, PageNo=page, PageSize=page_size, **query)
            values = result
            for key in collection.split("."):
                values = values.get(key, {})
            if not isinstance(values, list):
                raise CloudError(action + " 返回结构不符合预期，停止选择。")
            fingerprint = json.dumps(values, sort_keys=True)
            if values and fingerprint in seen:
                raise CloudError(action + " 重复返回同一页，未使用不完整列表。")
            seen.add(fingerprint)
            rows.extend(values)
            total = result.get("TotalCount", result.get("total"))
            if total is not None:
                if len(rows) >= int(total):
                    return rows
                if not values:
                    raise CloudError(action + " 列表提前结束，未使用不完整列表。")
            elif len(values) < page_size:
                return rows
        raise CloudError("分页超过限制，未使用不完整列表。")


class Bootstrap:
    IDENTITY_FIELDS = {
        "team": ("teamID",), "key": ("apiKeyID",), "acr": ("InstanceId",),
        "vpc": ("VpcId",), "switch": ("VSwitchId",), "sg": ("SecurityGroupId",),
        "repository": ("InstanceId", "RepoId"),
        "namespace": ("InstanceId", "NamespaceName"),
        "binding": ("InstanceId", "VpcId"), "acl": ("InstanceId", "Entry"),
        "public-endpoint": ("InstanceId",),
    }

    def __init__(self, root, cloud):
        self.root, self.cloud = Path(root), cloud
        who = cloud.rpc("sts", "GetCallerIdentity")
        self.account = str(who["AccountId"])
        self.path = self.root / "var/state/bootstrap.json"
        self.state = ui.read_json(self.path, {})
        identity = {"account": self.account, "region": cloud.region}
        if self.state and any(self.state.get(k) != v for k, v in identity.items()):
            raise CloudError("准备记录属于其他账号或地域；请在独立目录运行，或先回收原账号资源。")
        self.state = self.state or {**identity, "resources": {}, "operations": {}, "id": uuid.uuid4().hex[:10]}
        if self.state.get("uninstalled"):
            atomic_json(self.path.with_name("bootstrap-" + self.state["id"] + "-uninstalled.json"), self.state)
            self.state = {**identity, "resources": {}, "operations": {}, "id": uuid.uuid4().hex[:10]}
        self.name = "qsd-" + self.state["id"]
        self.save()
        print(f"  ✓ 阿里云身份已验证 · 账号 {self.account} · {cloud.region}")

    def save(self):
        atomic_json(self.path, self.state)

    def record(self, kind, value, owned=False):
        old = self.state["resources"].get(kind, {})
        keys = self.IDENTITY_FIELDS.get(kind, ())
        same = bool(keys) and all(value.get(k) and value.get(k) == old.get("value", {}).get(k) for k in keys)
        if old and not same:
            self.state.setdefault("history", []).append({"kind": kind, **old})
        recovered = False
        for name, operation in self.state["operations"].items():
            operation_name = operation.get("name", name)
            if operation_name == "acr-purchase":
                operation_name = "acr"
            if operation.get("status") != "done" or operation_name != kind:
                continue
            result = operation.get("result", {})
            result = result.get("team", result) if isinstance(result, dict) else {}
            if kind == "acr":
                result = result.get("Data", result.get("data", result))
                if "instanceId" in result and "InstanceId" not in result:
                    result = {**result, "InstanceId": result["instanceId"]}
            created = {**operation.get("scope", {}), **result}
            if keys and all(value.get(k) and value.get(k) == created.get(k) for k in keys):
                recovered = True
                break
        self.state["resources"][kind] = {"value": value, "owned": owned or recovered or (same and old.get("owned", False))}
        self.save()
        return value

    @staticmethod
    def operation_key(name, scope=None):
        if not scope:
            return name
        suffix = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()[:20]
        return name + ":" + suffix

    def has_operation(self, name, scope=None):
        return self.operation_key(name, scope) in self.state["operations"]

    def mutate(self, name, description, fn, idempotent=False, *, scope=None):
        key = self.operation_key(name, scope)
        operation = self.state["operations"].get(key)
        legacy = self.state["operations"].get(name, {}) if scope else {}
        if not operation and legacy.get("status") == "pending":
            raise CloudError(f"旧版 {name} 操作结果未知且未记录目标资源，请先核对后再续跑。")
        if operation and operation["status"] == "done":
            return operation["result"]
        if operation and operation["status"] == "pending" and not idempotent:
            raise CloudError(f"{name} 上次结果未知。请先核对云端资源；不能盲目重复创建。记录：{self.path}")
        confirm(description)
        token = operation["token"] if operation else str(uuid.uuid4())
        self.state["operations"][key] = {"token": token, "status": "pending", "description": description, "name": name, "scope": scope or {}}
        self.save()
        try:
            result = fn(token)
        except CloudError as exc:
            # An explicit permission/input rejection is retryable after fixing
            # it; a network timeout or server failure has an unknown outcome.
            if any(x in str(exc.code).lower() for x in ("forbidden", "noprivilege", "accessdenied", "invalidparameter", "unauthorized")):
                self.state["operations"][key]["status"] = "rejected"
                self.save()
            raise
        # Key plaintext is saved in the credential file inside fn, never here.
        self.state["operations"][key].update(status="done", result=result)
        self.save()
        return result

    def missing(self, name, scope=None):
        """Only after an authoritative list proves an old completed resource absent."""
        name = self.operation_key(name, scope)
        old = self.state["operations"].get(name)
        if old and old["status"] == "done":
            self.state.setdefault("completed_operations", []).append({name: old})
            del self.state["operations"][name]
            self.save()

    def wait(self, description, probe, seconds=600):
        end = time.monotonic() + seconds
        started = time.monotonic()
        last_notice = -30
        while time.monotonic() < end:
            value = probe()
            if value:
                return value
            elapsed = int(time.monotonic() - started)
            if elapsed - last_notice >= 30:
                print("  · " + description + f" · 已等待 {elapsed}s，自动核对中", flush=True)
                last_notice = elapsed
            time.sleep(5)
        raise CloudError(description + "超时；保留记录，重跑继续核对。")

    def team(self):
        rows = self.cloud.pages("sandbox", "ListTeams", "teams")
        previous = self.state["resources"].get("team", {}).get("value", {}).get("teamID")
        match = next((r for r in rows if r["teamID"] == previous or r.get("teamName") == self.name), None)
        if not match:
            match = choose("选择 Sandbox Team（生产业务请使用独立 Team）", rows,
                           lambda r: f"{r.get('teamName')} / {r['teamID']} / {r.get('plan')}")
        if match:
            return self.record("team", match, match.get("teamName") == self.name and "team" in self.state["operations"])
        self.missing("team")
        groups = self.cloud.rpc("resourcemanager", "GetDefaultResourceGroup")["ResourceGroup"]
        result = self.mutate("team", f"在资源组 {groups['Id']} 新建 {self.name} Team；使用 Pro 计划，Sandbox 按使用量计费。",
                             lambda _: self.cloud.sandbox("CreateTeam", body={"teamName": self.name, "resourceGroupID": groups["Id"], "plan": "pro", "description": "Qoder Sandbox demo"}))
        return self.record("team", result["team"], True)

    def key(self, team):
        connection, _, _ = ui.load_local(self.root)
        old = self.state["resources"].get("key", {}).get("value")
        keys = self.cloud.pages("sandbox", "ListApiKeys", "apiKeys", teamID=team["teamID"])
        # Only a key created and bound by this bootstrap is automatically reused.
        if old and old.get("fingerprint") == hashlib.sha256(connection.get("E2B_API_KEY", "").encode()).hexdigest() and any(k["apiKeyID"] == old["apiKeyID"] and k.get("status", "active").lower() in {"active", "enabled", "normal"} for k in keys):
            return
        key_name = self.name + "-key"
        orphan = next((k for k in keys if k.get("apiKeyName") == key_name), None)
        if orphan:
            self.mutate("orphan-key-" + orphan["apiKeyID"], "本项目专用 Key 明文丢失或失效；删除该专用 Key 后重新创建，不重置其他 Key。",
                        lambda _: self.cloud.sandbox("DeleteApiKey", path_id=orphan["apiKeyID"]))
            self.state["operations"].pop("key-" + team["teamID"], None)
            self.save()
        else:
            self.missing("key-" + team["teamID"])
        def create(_):
            key = self.cloud.sandbox("CreateApiKey", body={"teamID": team["teamID"], "apiKeyName": key_name})["apiKey"]
            ui.write_env_values(self.root / "config/.env", {"E2B_API_KEY": key["apiKeyValue"]})
            return {"apiKeyID": key["apiKeyID"], "teamID": team["teamID"], "fingerprint": hashlib.sha256(key["apiKeyValue"].encode()).hexdigest()}
        result = self.mutate("key-" + team["teamID"], "在所选 Team 创建 Demo 专用 API Key，明文仅保存至本机受限配置文件。", create)
        self.record("key", result, True)

    def acr(self):
        rows = self.cloud.pages("cr", "ListInstance", "Instances")
        rows = [r for r in rows if r.get("InstanceStatus") == "RUNNING" and r.get("InstanceSpecification") in {"Enterprise_Basic", "Enterprise_Standard", "Enterprise_Advanced"}]
        previous = self.state["resources"].get("acr", {}).get("value", {}).get("InstanceId")
        match = next((r for r in rows if r["InstanceId"] == previous or r.get("InstanceName") == self.name), None)
        if not match:
            match = choose("选择同地域 ACR 企业版实例（经济版不适用）", rows,
                           lambda r: f"{r['InstanceName']} / {r['InstanceId']}")
        if match:
            return self.record("acr", match)
        print("购买会自动支付：ACR Basic / 1个月 / 手动续费 / 默认OSS存储。")
        ui.open_config_url("https://cr.console.aliyun.com/")
        confirm("请先在官网核对所选地域 Basic 1个月的实时价格及服务协议；同意按该价格自动支付（不自动续费）。", "purchase")
        query = {"ProductCode": "acr", "ProductType": "acr_ee_public_cn", "SubscriptionType": "Subscription", "Period": 1, "RenewalStatus": "ManualRenewal"}
        for i, (code, value) in enumerate([("InstanceType", "Basic"), ("InstanceName", self.name), ("Region", self.cloud.region), ("DefaultOssBucket", "true")], 1):
            query[f"Parameter.{i}.Code"], query[f"Parameter.{i}.Value"] = code, value
        result = self.mutate("acr-purchase", "自动下单并支付上述 ACR 实例；OSS及后续使用可能另有费用。",
                             lambda token: self.cloud.rpc("bssopenapi", "CreateInstance", ClientToken=token, **query), True)
        data = result.get("Data", result.get("data", {}))
        instance_id = data.get("InstanceId", data.get("instanceId"))
        if not instance_id:
            raise CloudError("订单已记录但未返回实例 ID；请核对订单，禁止重复购买。")
        self.record("acr", {"InstanceId": instance_id, "InstanceName": self.name, "order": data}, True)
        return self.wait("等待 ACR 实例就绪", lambda: next((r for r in self.cloud.pages("cr", "ListInstance", "Instances") if r["InstanceId"] == instance_id and r["InstanceStatus"] == "RUNNING"), None))

    def network(self, instance):
        region = self.cloud.region
        suffixes = FC_ZONES.get(region)
        if not suffixes:
            raise CloudError("尚未核验该地域 FC 可用区。请更新 scripts/bootstrap.py 的官方可用区清单后重跑。")
        zones = {region + ("-" if region.startswith("cn-") else "") + z for z in suffixes.split()}
        switches = self.cloud.pages("vpc", "DescribeVSwitches", "VSwitches.VSwitch", RegionId=region)
        private_ranges = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
        vpcs_in_region = self.cloud.pages("vpc", "DescribeVpcs", "Vpcs.Vpc", RegionId=region)
        vpcs_in_region = [v for v in vpcs_in_region if any(ipaddress.ip_network(v["CidrBlock"]).subnet_of(n) for n in private_ranges)]
        valid_vpcs = {v["VpcId"] for v in vpcs_in_region}
        switches = [s for s in switches if s["VpcId"] in valid_vpcs and s["ZoneId"] in zones and int(s.get("AvailableIpAddressCount", 0)) >= 8 and s.get("Status") == "Available"]
        endpoint = self.cloud.rpc("cr", "GetInstanceVpcEndpoint", InstanceId=instance, ModuleName="Registry")
        linked = [v for v in endpoint.get("LinkedVpcs", []) if v.get("Status") == "RUNNING"]
        # Recommend networks already attached to the selected registry first.
        switches.sort(key=lambda s: s["VpcId"] not in {v["VpcId"] for v in linked})
        prev = self.state["resources"].get("switch", {}).get("value", {}).get("VSwitchId")
        switch = next((s for s in switches if s["VSwitchId"] == prev), None)
        if not switch:
            switch = choose("选择镜像拉取网络；优先显示已绑定 ACR 的网络", switches,
                            lambda s: f"{s.get('VSwitchName')} / {s['VpcId']} / {s['VSwitchId']} / {s['ZoneId']}")
        if switch:
            self.record("switch", switch)
            self.record("vpc", {"VpcId": switch["VpcId"]})
        else:
            vpcs = [v for v in vpcs_in_region if ipaddress.ip_network(v["CidrBlock"]).prefixlen <= 24]
            vpc = choose("在已有 VPC 新建交换机，或新建独立 VPC", vpcs, lambda v: f"{v.get('VpcName')} / {v['VpcId']} / {v['CidrBlock']}")
            if not vpc:
                old_vpc = self.state["operations"].get("vpc", {}).get("result", {}).get("VpcId")
                if old_vpc and not any(v["VpcId"] == old_vpc for v in vpcs_in_region):
                    self.missing("vpc")
                vpc = self.mutate("vpc", "新建独立 Demo VPC，网段 192.168.0.0/16，不配置互联。",
                                  lambda t: self.cloud.rpc("vpc", "CreateVpc", RegionId=region, VpcName=self.name, CidrBlock="192.168.0.0/16", ClientToken=t), True)
                self.record("vpc", {"VpcId": vpc["VpcId"]}, True)
                vpc = {**vpc, "CidrBlock": "192.168.0.0/16"}
            else:
                self.record("vpc", {"VpcId": vpc["VpcId"]})
            existing = self.cloud.pages("vpc", "DescribeVSwitches", "VSwitches.VSwitch", RegionId=region, VpcId=vpc["VpcId"])
            used = [ipaddress.ip_network(s["CidrBlock"]) for s in existing]
            cidr = next((str(n) for n in ipaddress.ip_network(vpc["CidrBlock"]).subnets(new_prefix=24) if not any(n.overlaps(u) for u in used)), None)
            if not cidr:
                raise CloudError("该 VPC 无可用 /24 网段；请重跑选择其他 VPC。")
            zone_rows = [{"id": z["ZoneId"]} for z in self.cloud.rpc("ecs", "DescribeZones", RegionId=region)["Zones"]["Zone"] if z["ZoneId"] in zones]
            if not zone_rows:
                raise CloudError("当前地域没有已核验的可用区交集。")
            zone = zone_rows[0]["id"]
            self.wait("等待 VPC 就绪", lambda: self.cloud.rpc("vpc", "DescribeVpcAttribute", RegionId=region, VpcId=vpc["VpcId"]).get("Status") == "Available")
            created = self.mutate("switch", f"在 VPC {vpc['VpcId']} 新建交换机 {cidr} / {zone}。",
                                  lambda t: self.cloud.rpc("vpc", "CreateVSwitch", RegionId=region, VpcId=vpc["VpcId"], CidrBlock=cidr, ZoneId=zone, VSwitchName=self.name, ClientToken=t), True,
                                  scope={"VpcId": vpc["VpcId"], "CidrBlock": cidr, "ZoneId": zone})
            switch = {"VSwitchId": created["VSwitchId"], "VpcId": vpc["VpcId"]}
            self.record("switch", switch, True)
        self.wait("等待交换机就绪", lambda: self.cloud.rpc("vpc", "DescribeVSwitchAttributes", RegionId=region, VSwitchId=switch["VSwitchId"]).get("Status") == "Available")
        groups = self.cloud.pages("ecs", "DescribeSecurityGroups", "SecurityGroups.SecurityGroup", RegionId=region, VpcId=switch["VpcId"])
        compatible = []
        for group in groups:
            if group.get("ServiceManaged", False) or group.get("SecurityGroupType", "normal") != "normal":
                continue
            rules = self.cloud.rpc("ecs", "DescribeSecurityGroupAttribute", RegionId=region, SecurityGroupId=group["SecurityGroupId"], Direction="egress").get("Permissions", {}).get("Permission", [])
            if egress_allows_https(rules):
                compatible.append(group)
        groups = compatible
        old = self.state["resources"].get("sg", {}).get("value", {}).get("SecurityGroupId")
        sg = next((g for g in groups if g["SecurityGroupId"] == old), None)
        if not sg:
            sg = choose("选择安全组；已有组若规则不兼容会停止，不自动放宽规则", groups, lambda g: f"{g.get('SecurityGroupName')} / {g['SecurityGroupId']}")
        if not sg:
            self.missing("sg", {"VpcId": switch["VpcId"]})
            sg = self.mutate("sg", "新建 Demo 普通安全组（默认出方向允许，未新增入方向放行）。",
                             lambda t: self.cloud.rpc("ecs", "CreateSecurityGroup", RegionId=region, VpcId=switch["VpcId"], SecurityGroupName=self.name, SecurityGroupType="normal", ClientToken=t), True,
                             scope={"VpcId": switch["VpcId"]})
            self.record("sg", sg, True)
        else:
            self.record("sg", sg)
        # Fail closed on restricted existing groups: no modification of production rules.
        rules = self.cloud.rpc("ecs", "DescribeSecurityGroupAttribute", RegionId=region, SecurityGroupId=sg["SecurityGroupId"], Direction="egress").get("Permissions", {}).get("Permission", [])
        if not egress_allows_https(rules):
            raise CloudError("所选安全组有自定义出方向规则，无法静态证明拉取可达。请重跑选择新建专用安全组。")
        binding_name = "binding-" + instance + "-" + switch["VpcId"]
        if not any(v["VpcId"] == switch["VpcId"] for v in endpoint.get("LinkedVpcs", [])):
            self.missing(binding_name)
            self.mutate(binding_name, f"为 ACR {instance} 添加 VPC {switch['VpcId']} 绑定，允许创建 PrivateZone 服务关联角色和 DNS 记录（需对应权限/配额）。",
                        lambda _: self.cloud.rpc("cr", "CreateInstanceVpcEndpointLinkedVpc", InstanceId=instance, VpcId=switch["VpcId"], VswitchId=switch["VSwitchId"], ModuleName="Registry", EnableCreateDNSRecordInPvzt=True))
            self.record("binding", {"InstanceId": instance, "VpcId": switch["VpcId"]}, True)
        elif binding_name in self.state["operations"]:
            self.record("binding", {"InstanceId": instance, "VpcId": switch["VpcId"]}, True)
        self.wait("等待 ACR VPC 绑定生效", lambda: any(v["VpcId"] == switch["VpcId"] and v["Status"] == "RUNNING" for v in self.cloud.rpc("cr", "GetInstanceVpcEndpoint", InstanceId=instance, ModuleName="Registry").get("LinkedVpcs", [])))
        return dict(vpc_id=switch["VpcId"], vswitch_id=switch["VSwitchId"], security_group_id=sg["SecurityGroupId"])

    def repository(self, instance):
        # Registry returns real domains; never invent an account-specific hostname.
        endpoint = self.cloud.rpc("cr", "GetInstanceEndpoint", InstanceId=instance, EndpointType="internet", ModuleName="Registry")
        domains = endpoint.get("Domains", [])
        if not domains:
            raise CloudError("ACR 未返回公网域名，无法从本机推送；检查实例状态后重跑。")
        host = domains[0] if isinstance(domains[0], str) else domains[0]["Domain"]
        namespace = self.name
        repo_name = "qoder-self-hosted-sandboxes-demo"
        repos = self.cloud.pages("cr", "ListRepository", "Repositories", InstanceId=instance)
        candidates = [r for r in repos if r.get("RepoType") == "PRIVATE"]
        old = self.state["resources"].get("repository", {}).get("value", {})
        repo = next((r for r in candidates if r.get("RepoId") == old.get("RepoId") or (r.get("RepoNamespaceName") == namespace and r.get("RepoName") == repo_name)), None)
        if not repo:
            self.missing("repository", {"InstanceId": instance, "NamespaceName": namespace, "RepoName": repo_name})
            repo = choose("选择私有仓库（构建将推送独立源码版本 tag）", candidates, lambda r: f"{r['RepoNamespaceName']}/{r['RepoName']}")
        if not repo:
            namespace_scope = {"InstanceId": instance, "NamespaceName": namespace}
            namespaces = self.cloud.pages("cr", "ListNamespace", "Namespaces", InstanceId=instance)
            if not any(n["NamespaceName"] == namespace for n in namespaces):
                self.missing("namespace", namespace_scope)
                self.mutate("namespace", f"在 ACR {instance} 新建专用命名空间 {namespace}（默认私有）。",
                            lambda _: self.cloud.rpc("cr", "CreateNamespace", InstanceId=instance, NamespaceName=namespace, AutoCreateRepo=False, DefaultRepoType="PRIVATE"), scope=namespace_scope)
                self.record("namespace", {"InstanceId": instance, "NamespaceName": namespace}, True)
            elif self.has_operation("namespace", namespace_scope):
                self.record("namespace", {"InstanceId": instance, "NamespaceName": namespace}, True)
            result = self.mutate("repository", f"新建私有仓库 {namespace}/{repo_name}。", lambda _: self.cloud.rpc("cr", "CreateRepository", InstanceId=instance, RepoNamespaceName=namespace, RepoName=repo_name, RepoType="PRIVATE", Summary="Qoder sandbox demo"), scope={"InstanceId": instance, "NamespaceName": namespace, "RepoName": repo_name})
            repo = {"RepoId": result["RepoId"], "RepoNamespaceName": namespace, "RepoName": repo_name, "InstanceId": instance}
            self.record("repository", repo, True)
        else:
            self.record("repository", {**repo, "InstanceId": instance})
        if not endpoint.get("Enable"):
            self.public_acl(instance, endpoint, force=True)
            self.missing("public-endpoint", {"InstanceId": instance})
            self.mutate("public-endpoint", "开启所选 ACR 的公网 Registry 入口（供本机构建推送）；将检查并保留现有访问策略。",
                        lambda _: self.cloud.rpc("cr", "UpdateInstanceEndpointStatus", InstanceId=instance, EndpointType="internet", ModuleName="Registry", Enable=True), scope={"InstanceId": instance})
            self.record("public-endpoint", {"InstanceId": instance}, True)
        elif endpoint.get("AclEnable"):
            self.public_acl(instance, endpoint)
        return host + "/" + repo["RepoNamespaceName"] + "/" + repo["RepoName"]

    def uninstall(self):
        if ui.local_run_state(self.root) in {"recorded", "partial"}:
            raise CloudError("先运行 Demo cleanup 回收 Qoder 和 Sandbox，再卸载准备资源。")
        print("将删除本项目创建的模板、Key、仓库、命名空间、绑定及网络；复用资源保留。")
        print("已购买 ACR 实例及默认 OSS 存储保留，不自动退款；共享服务角色保留。")
        confirm("执行上述卸载；云端存在其他依赖时会停止，已完成的删除可续跑。", "uninstall")
        from alibabacloud_sandbox20260820 import models
        from alibabacloud_sandbox20260820.client import Client
        templates = ui.read_json(self.root / "var/state/templates.json", {})
        team = self.state["resources"].get("team", {}).get("value", {})
        history = templates.get("created_templates", [])
        # Only explicit create receipts carry ownership. A bare configured ID
        # must never become owned merely because it appears in templates.json.
        known_teams = {team.get("teamID")} | {
            record.get("value", {}).get("teamID")
            for record in self.state.get("history", []) if record.get("kind") == "team"
        }
        unknown = []
        for record in history:
            if record.get("deleted"):
                continue
            tid = record.get("template_id")
            if (not record.get("created_by_demo") or not tid
                    or record.get("region") != self.cloud.region
                    or not record.get("team_id") or record["team_id"] not in known_teams):
                unknown.append(str(tid or "未记录 ID"))
                continue
            client = Client(self.cloud.config(f"sandbox.{self.cloud.region}.aliyuncs.com"))
            try:
                client.delete_template(tid, models.DeleteTemplateRequest(team_id=record["team_id"]))
            except Exception as exc:
                if not ui.not_found(exc):
                    raise self.cloud.error("DeleteTemplate", exc) from None
            record["deleted"] = True
            for kind in ("analysis", "browser"):
                if templates.get(kind + "_template") == tid:
                    templates.pop(kind + "_template")
            atomic_json(self.root / "var/state/templates.json", templates)
        for kind in ("analysis", "browser"):
            tid = templates.get(kind + "_template")
            if tid:
                unknown.append(str(tid))
        if unknown:
            raise CloudError("模板归属记录不完整，已保留：" + ", ".join(dict.fromkeys(unknown))
                             + "；请人工核对并处理后重试卸载，尚未删除仓库、网络或 Team。")
        resources = [{"kind": k, **v} for k, v in self.state["resources"].items()] + self.state.get("history", [])
        order = ["key", "repository", "namespace", "public-endpoint", "acl", "binding", "sg", "switch", "vpc", "team"]
        for kind in order:
            for record in resources:
                if record["kind"] != kind or not record.get("owned") or record.get("deleted"):
                    continue
                v = record["value"]
                signature = kind + ":" + json.dumps(v, sort_keys=True)
                if signature in self.state.get("deleted", []):
                    continue
                params = {
                    "public-endpoint": ("cr", "UpdateInstanceEndpointStatus", {"InstanceId": v.get("InstanceId"), "EndpointType": "internet", "ModuleName": "Registry", "Enable": False}),
                    "repository": ("cr", "DeleteRepository", {"InstanceId": v.get("InstanceId"), "RepoId": v.get("RepoId")}),
                    "namespace": ("cr", "DeleteNamespace", {"InstanceId": v.get("InstanceId"), "NamespaceName": v.get("NamespaceName")}),
                    "acl": ("cr", "DeleteInstanceEndpointAclPolicy", {"InstanceId": v.get("InstanceId"), "EndpointType": "internet", "ModuleName": "Registry", "Entry": v.get("Entry")}),
                    "binding": ("cr", "DeleteInstanceVpcEndpointLinkedVpc", {"InstanceId": v.get("InstanceId"), "VpcId": v.get("VpcId"), "ModuleName": "Registry"}),
                    "sg": ("ecs", "DeleteSecurityGroup", {"RegionId": self.cloud.region, "SecurityGroupId": v.get("SecurityGroupId")}),
                    "switch": ("vpc", "DeleteVSwitch", {"RegionId": self.cloud.region, "VSwitchId": v.get("VSwitchId")}),
                    "vpc": ("vpc", "DeleteVpc", {"RegionId": self.cloud.region, "VpcId": v.get("VpcId")}),
                }
                try:
                    if kind in {"team", "key"}:
                        self.cloud.sandbox("DeleteTeam" if kind == "team" else "DeleteApiKey", path_id=v["teamID"] if kind == "team" else v["apiKeyID"])
                    else:
                        service, action, query = params[kind]
                        self.cloud.rpc(service, action, **query)
                except CloudError as exc:
                    code = str(exc.code or "").lower()
                    if code not in {"notfound", "notexist", "resourcenotfound", "resourcenotexist"} and not code.endswith((".notfound", ".notexist")):
                        raise
                self.state.setdefault("deleted", []).append(signature)
                self.save()
                print("✓ 已删除本项目资源：" + kind)
        self.state["uninstalled"] = True
        self.save()
        for kind in ("acr",):
            r = self.state["resources"].get(kind, {})
            if r.get("owned"):
                print("保留并需核对：" + kind + " " + str(r["value"].get("InstanceId")) + "；如不再需要，请在 ACR 控制台关闭入口/按订单规则退订，默认OSS存储需单独处理。")

    def public_acl(self, instance, endpoint, force=False):
        print("查询本机公网出口 IPv4（仅访问 api.ipify.org，不发送凭证）。")
        with urllib.request.urlopen("https://api.ipify.org", timeout=10) as response:
            address = ipaddress.IPv4Address(response.read(64).decode().strip())
        entries = endpoint.get("AclEntries", [])
        if any(address in ipaddress.ip_network(e["Entry"]) for e in entries):
            return
        # Adding a first ACL to a currently unrestricted shared endpoint could
        # interrupt existing clients. Never do that implicitly.
        if not force and not endpoint.get("AclEnable"):
            return
        cidr = str(address) + "/32"
        scope = {"InstanceId": instance, "Entry": cidr}
        self.missing("acl", scope)
        self.mutate("acl", f"为 ACR 新增本机出口 {cidr} 白名单，保留原有条目；代理/Docker出口不同时需使用同一出口。",
                    lambda _: self.cloud.rpc("cr", "CreateInstanceEndpointAclPolicy", InstanceId=instance, EndpointType="internet", ModuleName="Registry", Entry=cidr, Comment=self.name), scope=scope)
        self.record("acl", {"InstanceId": instance, "Entry": cidr}, True)

    def login(self, instance, repository):
        registry_login(self.cloud, instance, repository)


def registry_login(cloud, instance, repository):
    token = cloud.rpc("cr", "GetAuthorizationToken", InstanceId=instance)
    result = subprocess.run(["docker", "login", repository.split("/")[0], "--username", token["TempUsername"], "--password-stdin"], input=token["AuthorizationToken"] + "\n", capture_output=True, text=True)
    if result.returncode:
        raise CloudError("Docker 临时登录失败；请检查公网白名单和本机网络。凭证未输出。")
    print("✓ 已通过 AK 获取 ACR 临时凭证并登录。")


def reuse_existing_configuration(root):
    """Explicitly retain a complete pre-bootstrap configuration, without adoption."""
    if (Path(root) / "var/state/bootstrap.json").exists():
        return False
    connection, credentials, registry = ui.load_local(root)
    try:
        ui.validate_config(connection, credentials, registry)
    except ValueError:
        return False
    print("发现完整旧配置。复用会沿用已配置的 Team、Key、仓库和网络，不取得其删除权。")
    while True:
        choice = input("输入 reuse 复用旧配置；输入 discover 重新查询并选择资源：").strip()
        if choice == "discover":
            return False
        if choice == "reuse":
            break
    from e2b import Sandbox
    from qoder_self_hosted_sandboxes_demo.qoder.client import API
    import tomllib
    ui.template_team_check(root)
    app = tomllib.loads((Path(root) / "config/app.toml").read_text())
    API(app["qoder"]["base_url"], connection["QODER_ACCESS_TOKEN"]).request("GET", "/models")
    Sandbox.list(limit=1, api_key=connection["E2B_API_KEY"], api_url=connection["E2B_API_URL"], domain=connection["E2B_DOMAIN"], request_timeout=15).next_items()
    print("✓ Qoder 和原 Sandbox Key 可访问，Team 已核对；继续复用已有资源。")
    return True


@contextlib.contextmanager
def locked(root):
    import fcntl
    path = Path(root) / "var/state/bootstrap.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CloudError("另一个准备向导正在运行，请等待其完成。") from None
        yield


def run(root):
    root = Path(root)
    if ui.local_run_state(root) in {"recorded", "partial"}:
        raise CloudError("存在未回收运行，请先继续或回收旧运行后再准备账号。")
    if reuse_existing_configuration(root):
        return
    connection, credentials, registry = ui.load_local(root)
    ui.config_section("2.1 · 验证两个账号入口", "阿里云：AK/SK + region；Qoder：独立 PAT。输入内容可见，已有值可回车保留。")
    for key, label in [("ALIBABA_CLOUD_ACCESS_KEY_ID", "阿里云 AK ID"), ("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "阿里云 AK Secret")]:
        credentials[key] = ui.prompt_value(label, credentials.get(key) or os.environ.get(key), secret=True, field=key)
    key = "ALIBABA_CLOUD_SECURITY_TOKEN"
    credentials[key] = ui.prompt_value("STS Token（长期 AK 留空）", credentials.get(key) or os.environ.get(key), secret=True, optional=True, field=key)
    region = ui.prompt_value("阿里云地域", registry.get("region") or "cn-hangzhou", field="region")
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+", region):
        raise CloudError("地域格式不正确。")
    connection["QODER_ACCESS_TOKEN"] = ui.prompt_value("Qoder PAT（独立授权；输入 open 查看获取入口）", connection.get("QODER_ACCESS_TOKEN") or os.environ.get("QODER_ACCESS_TOKEN"), secret=True, field="QODER_ACCESS_TOKEN")
    # Read-only Qoder check happens before any Alibaba Cloud mutation.
    from qoder_self_hosted_sandboxes_demo.qoder.client import API
    import tomllib
    app = tomllib.loads((root / "config/app.toml").read_text())
    API(app["qoder"]["base_url"], connection["QODER_ACCESS_TOKEN"]).request("GET", "/models")
    print("  ✓ Qoder 授权可用；接下来查询阿里云资源。")
    with locked(root):
        b = Bootstrap(root, Cloud(credentials, region))
        ui.write_env_values(root / "config/.env.cloud", credentials)
        ui.write_env_values(root / "config/.env", {"QODER_ACCESS_TOKEN": connection["QODER_ACCESS_TOKEN"]})
        ui.write_toml_values(root / "config/registry.toml", {"region": region})
        ui.config_section("2.2 · 连接 Sandbox Team", "Team 是实例的归属空间；专用 Key 用来实际调用 Sandbox。优先选择已有 Team。")
        team = b.team()
        b.key(team)
        print("  ✓ Team 与调用 Key 已准备。后续构建/创建会验证数据面权限。")
        ui.config_section("2.3 · 选择镜像仓库实例", "ACR 保存稍后构建的分析镜像。先复用；没有合适实例时，再单独确认购买。")
        acr = b.acr()
        print("  ✓ ACR 实例：" + acr["InstanceId"])
        ui.config_section("2.4 · 接通镜像拉取网络", "选择 VPC、交换机和安全组，让云端模板能从私有 ACR 拉取镜像。")
        network = b.network(acr["InstanceId"])
        print("  ✓ 镜像拉取网络已核对。")
        ui.config_section("2.5 · 确定镜像存放位置", "选择私有仓库并核对本机推送入口；需要新增绑定或白名单时会明确询问。")
        repository = b.repository(acr["InstanceId"])
        ui.write_toml_values(root / "config/registry.toml", {"region": region, "team_id": team["teamID"], "instance_id": acr["InstanceId"], "repository": repository, **network})
        ui.write_env_values(root / "config/.env", {"E2B_API_URL": f"https://api.{region}.sandbox.aliyuncs.com", "E2B_DOMAIN": f"{region}.sandbox.aliyuncs.com"})
        print("\n  ✓ 基础连接已保存")
        print("    地域   " + region)
        print("    Team   " + team["teamID"])
        print("    仓库   " + repository)
        print("    配置   config/（汇总不显示密钥，文件权限 0600）")
        print("  下一步：自动登录 ACR，准备网页采集与 Python 分析两套模板。")


def uninstall(root):
    if not (Path(root) / "var/state/bootstrap.json").exists():
        raise CloudError("没有本项目自动准备账本，不能认领旧配置的资源；请用 Demo cleanup 回收本轮实例。")
    _, credentials, registry = ui.load_local(root)
    with locked(root):
        Bootstrap(root, Cloud(credentials, registry["region"])).uninstall()


def egress_allows_https(rules):
    if not rules:
        return True  # Only called for normal (not enterprise) security groups.
    if any(r.get("Policy", "Accept").lower() != "accept" for r in rules):
        return False
    for rule in rules:
        ports = rule.get("PortRange", "-1/-1").split("/")
        if rule.get("DestCidrIp") == "0.0.0.0/0" and rule.get("IpProtocol", "").lower() in {"all", "tcp"}:
            if ports == ["-1", "-1"] or (len(ports) == 2 and int(ports[0]) <= 443 <= int(ports[1])):
                return True
    return False


if __name__ == "__main__":
    try:
        if "--help" in sys.argv:
            print("python scripts/bootstrap.py：自动准备；--uninstall：卸载本项目创建的准备资源（先运行 Demo cleanup）。")
            sys.exit(0)
        if not sys.stdin.isatty():
            raise CloudError("请在交互式终端运行；批量输入不能绕过客户选择与授权。")
        if sys.argv[1:] == ["--uninstall"]:
            uninstall(ui.ROOT)
        elif len(sys.argv) == 1:
            run(ui.ROOT)
        else:
            raise CloudError("未知参数；使用 --help 查看说明。")
    except KeyboardInterrupt:
        print("\n已停止；保留准备记录，重跑不会盲目重复创建。", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        # SDK errors are already reduced to code/request ID. Other exceptions
        # may contain request bodies, so do not print arbitrary exception text.
        if type(exc).__name__ == "APIError" and hasattr(exc, "status"):
            message = f"Qoder HTTP {exc.status}：请核对独立 PAT、账号权限与额度。入口：https://qoder.com/account/integrations"
        else:
            message = str(exc) if isinstance(exc, CloudError) else "准备未完成（" + type(exc).__name__ + "）；凭证未输出，配置和记录保留。"
        print(message, file=sys.stderr)
        sys.exit(1)
