"""Provisioning transactions and real official SDK wire contracts, no cloud access."""
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bootstrap as b
from qoder_self_hosted_sandboxes_demo.sandbox import templates as template_builder


class Account:
    region = "cn-hangzhou"
    def __init__(self, uid="100"):
        self.uid = uid
        self.teams, self.keys, self.instances = [], [], []
        self.switches, self.vpcs, self.groups, self.bindings = [], [], [], []
        self.namespaces, self.repos, self.writes = [], [], []

    def pages(self, service, action, collection, **params):
        return {"ListTeams": self.teams, "ListApiKeys": self.keys, "ListInstance": self.instances,
                "DescribeVSwitches": self.switches, "DescribeVpcs": self.vpcs,
                "DescribeSecurityGroups": self.groups, "ListNamespace": self.namespaces,
                "ListRepository": self.repos}[action]

    def sandbox(self, action, body=None, **params):
        self.writes.append(action)
        if action == "CreateTeam":
            row = {"teamID": "team-1", **body}
            self.teams.append(row)
            return {"team": row}
        if action == "CreateApiKey":
            row = {"apiKeyID": "key-1", **body, "status": "active"}
            self.keys.append(row)
            return {"apiKey": {**row, "apiKeyValue": "e2b_TEST_ONLY_SECRET"}}
        if action.startswith("Delete"):
            return {}
        raise AssertionError(action)

    def rpc(self, service, action, **params):
        if action == "GetCallerIdentity": return {"AccountId": self.uid, "Arn": "test"}
        if action == "GetDefaultResourceGroup": return {"ResourceGroup": {"Id": "rg-test"}}
        if action == "GetInstanceVpcEndpoint": return {"Enable": True, "LinkedVpcs": self.bindings}
        if action == "DescribeZones": return {"Zones": {"Zone": [{"ZoneId": "cn-hangzhou-i"}]}}
        if action in {"DescribeVpcAttribute", "DescribeVSwitchAttributes"}: return {"Status": "Available"}
        if action == "DescribeSecurityGroupAttribute": return {"Permissions": {"Permission": []}}
        if action == "GetInstanceEndpoint": return {"Enable": True, "AclEnable": False, "Domains": [{"Domain": "test-registry.cn-hangzhou.cr.aliyuncs.com"}]}
        self.writes.append(action)
        if action == "CreateInstance":
            self.instances.append({"InstanceId": "cri-test", "InstanceName": params["Parameter.2.Value"], "InstanceStatus": "RUNNING", "InstanceSpecification": "Enterprise_Basic"})
            return {"Data": {"InstanceId": "cri-test", "OrderId": "order-test"}}
        if action == "CreateVpc":
            self.vpcs.append({"VpcId": "vpc-test", "CidrBlock": params["CidrBlock"]})
            return {"VpcId": "vpc-test"}
        if action == "CreateVSwitch":
            self.switches.append({"VpcId": params["VpcId"], "VSwitchId": "vsw-test", "ZoneId": params["ZoneId"], "Status": "Available", "AvailableIpAddressCount": 200, "CidrBlock": params["CidrBlock"]})
            return {"VSwitchId": "vsw-test"}
        if action == "CreateSecurityGroup":
            self.groups.append({"SecurityGroupId": "sg-test", "VpcId": params["VpcId"]})
            return {"SecurityGroupId": "sg-test"}
        if action == "CreateInstanceVpcEndpointLinkedVpc":
            self.bindings.append({"VpcId": params["VpcId"], "Status": "RUNNING"})
            return {}
        if action == "CreateNamespace":
            self.namespaces.append({"NamespaceName": params["NamespaceName"]})
            return {}
        if action == "CreateRepository":
            self.repos.append({"RepoId": "repo-test", "RepoNamespaceName": params["RepoNamespaceName"], "RepoName": params["RepoName"], "RepoType": "PRIVATE"})
            return {"RepoId": "repo-test"}
        if action.startswith("Delete"): return {}
        raise AssertionError(action)


def authorize(prompt):
    if "选择编号" in prompt: return "n"
    if "输入 purchase" in prompt: return "purchase"
    return "yes"


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sink = contextlib.redirect_stdout(io.StringIO())
        self.sink.__enter__()
        self.addCleanup(self.sink.__exit__, None, None, None)

    def prepare(self, cloud):
        instance = b.Bootstrap(self.root, cloud)
        with patch("builtins.input", side_effect=authorize), patch.object(b.ui, "open_config_url"):
            team = instance.team()
            instance.key(team)
            acr = instance.acr()
            network = instance.network(acr["InstanceId"])
            repository = instance.repository(acr["InstanceId"])
        self.assertEqual(network["vpc_id"], "vpc-test")
        self.assertIn("/qoder-self-hosted-sandboxes-demo", repository)
        return instance

    def test_blank_account_transaction_restarts_without_duplicate_purchase_or_resources(self):
        cloud = Account()
        instance = self.prepare(cloud)
        writes = list(cloud.writes)
        self.prepare(cloud)
        self.assertEqual(writes, cloud.writes)
        self.assertEqual(writes.count("CreateInstance"), 1)
        text = instance.path.read_text()
        self.assertNotIn("e2b_TEST_ONLY_SECRET", text)
        self.assertEqual((self.root / "config/.env").stat().st_mode & 0o777, 0o600)

    def test_other_account_cannot_inherit_resources(self):
        b.Bootstrap(self.root, Account())
        with self.assertRaises(b.CloudError): b.Bootstrap(self.root, Account("200"))

    def test_old_account_reuses_existing_resources_without_creating_namespace(self):
        cloud = Account()
        cloud.teams = [{"teamID": "existing", "teamName": "test"}]
        cloud.instances = [{"InstanceId": "cri-test", "InstanceName": "existing", "InstanceStatus": "RUNNING", "InstanceSpecification": "Enterprise_Basic"}]
        cloud.vpcs = [{"VpcId": "vpc-test", "CidrBlock": "10.0.0.0/16"}]
        cloud.switches = [{"VpcId": "vpc-test", "VSwitchId": "vsw-test", "ZoneId": "cn-hangzhou-i", "Status": "Available", "AvailableIpAddressCount": 100}]
        cloud.bindings = [{"VpcId": "vpc-test", "Status": "RUNNING"}]
        cloud.groups = [{"SecurityGroupId": "sg-test"}]
        cloud.repos = [{"RepoId": "old-repo", "RepoName": "existing", "RepoNamespaceName": "existing", "RepoType": "PRIVATE"}]
        instance = b.Bootstrap(self.root, cloud)
        with patch("builtins.input", side_effect=lambda prompt: "1" if "选择编号" in prompt else "yes"):
            team = instance.team()
            instance.key(team)
            acr = instance.acr()
            instance.network(acr["InstanceId"])
            instance.repository(acr["InstanceId"])
        self.assertEqual(cloud.writes, ["CreateApiKey"])
        self.assertFalse(instance.state["resources"]["repository"]["owned"])

    def test_permission_failure_does_not_turn_into_create(self):
        cloud = Account()
        instance = b.Bootstrap(self.root, cloud)
        with patch.object(cloud, "pages", side_effect=b.CloudError("Forbidden", "Forbidden")), self.assertRaises(b.CloudError):
            instance.team()
        self.assertEqual(cloud.writes, [])

    def test_explicit_rejection_can_retry_after_authorization_is_fixed(self):
        instance = b.Bootstrap(self.root, Account())
        with patch("builtins.input", return_value="yes"):
            with self.assertRaises(b.CloudError):
                instance.mutate("x", "test", lambda _: (_ for _ in ()).throw(b.CloudError("no access", "Forbidden")))
            self.assertEqual(instance.mutate("x", "test", lambda _: {"id": "ok"}), {"id": "ok"})

    def test_uninstall_deletes_owned_resources_but_retains_paid_acr(self):
        cloud = Account()
        instance = self.prepare(cloud)
        cloud.writes.clear()
        with patch("builtins.input", return_value="uninstall"):
            instance.uninstall()
        self.assertIn("DeleteRepository", cloud.writes)
        self.assertIn("DeleteTeam", cloud.writes)
        self.assertNotIn("DeleteInstance", cloud.writes)
        self.assertNotIn("RefundInstance", cloud.writes)
        fresh = b.Bootstrap(self.root, cloud)
        self.assertNotEqual(instance.state["id"], fresh.state["id"])

    def test_template_rebuild_preserves_creation_receipts_and_legacy_ids(self):
        saved = {"browser_template": "legacy-browser"}
        scope = {"team_id": "team-1", "region": "cn-hangzhou"}
        template_builder.record_created(saved, "browser", "new-browser", scope)
        saved["browser_template"] = "new-browser"
        template_builder.record_created(saved, "browser", "next-browser", scope)
        records = saved["created_templates"]
        self.assertEqual([r["template_id"] for r in records], ["legacy-browser", "new-browser", "next-browser"])
        self.assertNotIn("created_by_demo", records[0])
        self.assertEqual(records[1]["team_id"], "team-1")
        self.assertEqual(records[2]["region"], "cn-hangzhou")
        with patch.object(template_builder, "STATE_DIR", self.root / "var/state"):
            template_builder.store(saved)
            self.assertEqual(template_builder.state(), saved)

    def test_uninstall_cleans_all_recorded_templates_and_accepts_not_found(self):
        cloud = Account()
        instance = b.Bootstrap(self.root, cloud)
        instance.record("team", {"teamID": "team-1"})
        saved = {}
        scope = {"team_id": "team-1", "region": cloud.region}
        for identity in ("old-analysis", "new-analysis"):
            template_builder.record_created(saved, "analysis", identity, scope)
            saved["analysis_template"] = identity
        b.atomic_json(self.root / "var/state/templates.json", saved)
        missing = RuntimeError("already removed")
        missing.status_code = 404
        with patch("alibabacloud_sandbox20260820.client.Client") as client, patch.object(cloud, "config", return_value=None, create=True), patch("builtins.input", return_value="uninstall"):
            client.return_value.delete_template.side_effect = [missing, None]
            instance.uninstall()
        self.assertEqual([call.args[0] for call in client.return_value.delete_template.call_args_list], ["old-analysis", "new-analysis"])
        stored = b.ui.read_json(self.root / "var/state/templates.json")
        self.assertNotIn("analysis_template", stored)
        self.assertTrue(all(record["deleted"] for record in stored["created_templates"]))
        self.assertTrue(instance.state["uninstalled"])

    def test_uninstall_retains_unclaimed_or_wrong_scope_templates_and_dependencies(self):
        cloud = Account()
        instance = b.Bootstrap(self.root, cloud)
        instance.record("team", {"teamID": "team-1"}, True)
        instance.record("repository", {"InstanceId": "cri-1", "RepoId": "repo-1"}, True)
        for history in ([], [{"template_id": "unknown", "created_by_demo": True, "team_id": "team-1", "region": "other-region"}], [{"template_id": "unknown", "created_by_demo": True, "team_id": "other-team", "region": cloud.region}]):
            with self.subTest(history=history):
                b.atomic_json(self.root / "var/state/templates.json", {"analysis_template": "unknown", "created_templates": history})
                with patch("alibabacloud_sandbox20260820.client.Client") as client, patch("builtins.input", return_value="uninstall"), self.assertRaisesRegex(b.CloudError, "模板归属记录不完整"):
                    instance.uninstall()
                client.assert_not_called()
                self.assertEqual(cloud.writes, [])
                self.assertNotIn("uninstalled", instance.state)

    def test_unknown_write_is_never_automatically_replayed(self):
        instance = b.Bootstrap(self.root, Account())
        with patch("builtins.input", return_value="yes"):
            with self.assertRaises(TimeoutError):
                instance.mutate("create", "create test", lambda _: (_ for _ in ()).throw(TimeoutError()))
            with self.assertRaises(b.CloudError):
                instance.mutate("create", "create test", lambda _: self.fail("replayed"))

    def test_idempotent_purchase_timeout_reuses_same_token(self):
        instance = b.Bootstrap(self.root, Account())
        tokens = []
        def purchase(token):
            tokens.append(token)
            if len(tokens) == 1: raise TimeoutError()
            return {"id": "same"}
        with patch("builtins.input", return_value="yes"):
            with self.assertRaises(TimeoutError): instance.mutate("buy", "test purchase", purchase, True)
            instance.mutate("buy", "test purchase", purchase, True)
        self.assertEqual(tokens[0], tokens[1])

    def test_decline_creates_neither_operation_nor_resource(self):
        instance = b.Bootstrap(self.root, Account())
        with patch("builtins.input", side_effect=["", "no", "YES", KeyboardInterrupt]), self.assertRaises(KeyboardInterrupt):
            instance.mutate("x", "test", lambda _: self.fail("executed"))
        self.assertEqual(instance.state["operations"], {})

    def test_only_explicit_approval_runs_mutation_after_empty_and_invalid_input(self):
        instance = b.Bootstrap(self.root, Account())
        writes = []
        def answer(_prompt):
            self.assertEqual(writes, [])
            return next(answers)
        answers = iter(["", "no", "y", "yes"])
        with patch("builtins.input", side_effect=answer):
            instance.mutate("x", "test", lambda _: writes.append("executed") or {})
        self.assertEqual(writes, ["executed"])

    def test_purchase_and_uninstall_require_their_exact_approval_word(self):
        for word in ("purchase", "uninstall"):
            with self.subTest(word=word), patch("builtins.input", side_effect=["", "yes", "typo", word]) as read:
                b.confirm("test", word)
                self.assertEqual(read.call_count, 4)

    def test_reused_resource_does_not_inherit_ownership_from_another_id(self):
        instance = b.Bootstrap(self.root, Account())
        instance.record("switch", {"VSwitchId": "one", "VpcId": "same"}, True)
        instance.record("switch", {"VSwitchId": "two", "VpcId": "same"})
        self.assertFalse(instance.state["resources"]["switch"]["owned"])
        self.assertTrue(instance.state["history"][0]["owned"])

    def test_restrictive_groups_are_not_silently_modified(self):
        self.assertFalse(b.egress_allows_https([{"Policy": "Drop"}]))
        self.assertFalse(b.egress_allows_https([{"Policy": "Accept", "IpProtocol": "tcp", "PortRange": "80/80", "DestCidrIp": "0.0.0.0/0"}]))
        self.assertTrue(b.egress_allows_https([{"Policy": "Accept", "IpProtocol": "tcp", "PortRange": "443/443", "DestCidrIp": "0.0.0.0/0"}]))

    def test_same_namespace_in_another_registry_does_not_inherit_ownership(self):
        instance = b.Bootstrap(self.root, Account())
        instance.record("namespace", {"InstanceId": "cri-a", "NamespaceName": "demo"}, True)
        instance.record("namespace", {"InstanceId": "cri-b", "NamespaceName": "demo"})
        self.assertFalse(instance.state["resources"]["namespace"]["owned"])
        self.assertEqual(instance.state["history"][0]["value"]["InstanceId"], "cri-a")

    def test_each_registry_gets_its_own_acl_and_deleted_acl_can_be_recreated(self):
        cloud = Account()
        instance = b.Bootstrap(self.root, cloud)
        calls = []
        def rpc(service, action, **query):
            self.assertEqual(action, "CreateInstanceEndpointAclPolicy")
            calls.append(query)
            return {}
        endpoint = {"AclEnable": True, "AclEntries": []}
        with patch.object(cloud, "rpc", side_effect=rpc), patch("builtins.input", return_value="yes"), patch.object(b.urllib.request, "urlopen", side_effect=lambda *_args, **_kwargs: io.BytesIO(b"198.51.100.10")):
            instance.public_acl("cri-a", endpoint)
            instance.public_acl("cri-b", endpoint)
            # The authoritative endpoint list says our first ACL was removed.
            instance.public_acl("cri-a", endpoint)
        self.assertEqual([q["InstanceId"] for q in calls], ["cri-a", "cri-b", "cri-a"])

    def test_network_writes_are_scoped_and_creation_ownership_survives_interruption(self):
        instance = b.Bootstrap(self.root, Account())
        with patch("builtins.input", return_value="yes"):
            first = instance.mutate("sg", "create in A", lambda _: {"SecurityGroupId": "sg-a"}, True, scope={"VpcId": "vpc-a"})
            second = instance.mutate("sg", "create in B", lambda _: {"SecurityGroupId": "sg-b"}, True, scope={"VpcId": "vpc-b"})
        self.assertNotEqual(first, second)
        # Process stopped after the create result was saved but before record().
        recovered = b.Bootstrap(self.root, Account())
        recovered.record("sg", {"SecurityGroupId": "sg-a"})
        self.assertTrue(recovered.state["resources"]["sg"]["owned"])

    def test_uninstall_resumes_when_cloud_resource_is_already_deleted(self):
        cloud = Account()
        instance = b.Bootstrap(self.root, cloud)
        instance.record("key", {"apiKeyID": "removed-key", "teamID": "t"}, True)
        with patch.object(cloud, "sandbox", side_effect=b.CloudError("gone", "NotFound")), patch("builtins.input", return_value="uninstall"):
            instance.uninstall()
        self.assertTrue(instance.state["uninstalled"])
        self.assertEqual(len(instance.state["deleted"]), 1)

    def test_uninstall_does_not_hide_permission_or_dependency_failures(self):
        for code in ("Forbidden", "ResourceInUse", "SystemError"):
            cloud = Account()
            instance = b.Bootstrap(self.root, cloud)
            instance.record("key", {"apiKeyID": "retained-key", "teamID": "t"}, True)
            with patch.object(cloud, "sandbox", side_effect=b.CloudError("failed", code)), patch("builtins.input", return_value="uninstall"), self.assertRaises(b.CloudError):
                instance.uninstall()
            self.assertNotIn("uninstalled", instance.state)
            self.assertEqual(instance.state.get("deleted", []), [])

    def test_pagination_uses_total_when_server_returns_smaller_pages(self):
        cloud = b.Cloud({}, "cn-hangzhou")
        pages = [{"teams": [{"id": i} for i in ids], "total": 5} for ids in ([1, 2], [3, 4], [5])]
        with patch.object(cloud, "sandbox", side_effect=pages):
            rows = cloud.pages("sandbox", "ListTeams", "teams")
        self.assertEqual([r["id"] for r in rows], [1, 2, 3, 4, 5])

    def test_pagination_rejects_repeated_or_incomplete_pages(self):
        cloud = b.Cloud({}, "cn-hangzhou")
        for second in ([{"id": 1}], []):
            with patch.object(cloud, "sandbox", side_effect=[{"teams": [{"id": 1}], "total": 2}, {"teams": second, "total": 2}]), self.assertRaises(b.CloudError):
                cloud.pages("sandbox", "ListTeams", "teams")

    def test_complete_legacy_configuration_can_keep_its_working_key(self):
        connection = {"QODER_ACCESS_TOKEN": "fake-qoder", "E2B_API_KEY": "fake-existing-key", "E2B_API_URL": "https://api.cn-hangzhou.sandbox.aliyuncs.com", "E2B_DOMAIN": "cn-hangzhou.sandbox.aliyuncs.com"}
        credentials = {"ALIBABA_CLOUD_ACCESS_KEY_ID": "fake-ak", "ALIBABA_CLOUD_ACCESS_KEY_SECRET": "fake-sk"}
        registry = {"repository": "test.cn-hangzhou.cr.aliyuncs.com/old/repo", "instance_id": "cri-old", "team_id": "team-old", "region": "cn-hangzhou"}
        b.ui.write_env_values(self.root / "config/.env", connection)
        b.ui.write_env_values(self.root / "config/.env.cloud", credentials)
        b.ui.write_toml_values(self.root / "config/registry.toml", registry)
        (self.root / "config/app.toml").write_text('[qoder]\nbase_url="https://qoder.invalid"\n')
        original = (self.root / "config/.env").read_bytes()
        with patch("builtins.input", return_value="reuse"), patch("e2b.Sandbox.list") as listing, patch("qoder_self_hosted_sandboxes_demo.qoder.client.API.request") as qoder, patch.object(b.ui, "template_team_check", return_value="team-old") as team_check, patch.object(b, "Cloud", side_effect=AssertionError("must not create resources")):
            b.run(self.root)
        team_check.assert_called_once_with(self.root)
        listing.return_value.next_items.assert_called_once()
        qoder.assert_called_once_with("GET", "/models")
        self.assertEqual((self.root / "config/.env").read_bytes(), original)
        self.assertFalse((self.root / "var/state/bootstrap.json").exists())

    def test_registry_temporary_token_only_goes_through_stdin(self):
        cloud = Account()
        with patch.object(cloud, "rpc", return_value={"TempUsername": "test-user", "AuthorizationToken": "FAKE_ONLY_TOKEN"}), patch.object(b.subprocess, "run", return_value=type("Result", (), {"returncode": 0})()) as process:
            b.registry_login(cloud, "cri-test", "registry.invalid/namespace/repo")
        self.assertNotIn("FAKE_ONLY_TOKEN", process.call_args.args[0])
        self.assertEqual(process.call_args.kwargs["input"], "FAKE_ONLY_TOKEN\n")

    def test_legacy_docker_login_can_be_kept_without_token_management_permission(self):
        b.ui.write_env_values(self.root / "config/.env.cloud", {"ALIBABA_CLOUD_ACCESS_KEY_ID": "fake-ak", "ALIBABA_CLOUD_ACCESS_KEY_SECRET": "fake-sk"})
        b.ui.write_toml_values(self.root / "config/registry.toml", {"region": "cn-hangzhou", "repository": "registry.invalid/namespace/repo", "instance_id": "cri-old"})
        with patch.object(b.Cloud, "rpc", side_effect=b.CloudError("GetAuthorizationToken denied", "Forbidden")), patch("builtins.input", return_value=""), patch.object(b.subprocess, "run") as process:
            b.ui.docker_login(self.root)
        process.assert_not_called()

    def test_prepaid_purchase_ownership_is_recovered_from_nested_result(self):
        for result in ({"Data": {"InstanceId": "cri-paid"}}, {"data": {"instanceId": "cri-paid"}}):
            instance = b.Bootstrap(self.root, Account())
            instance.state["operations"]["acr-purchase"] = {"status": "done", "result": result}
            instance.save()
            restored = b.Bootstrap(self.root, Account())
            restored.record("acr", {"InstanceId": "cri-paid"})
            self.assertTrue(restored.state["resources"]["acr"]["owned"])

    def test_vpc_discovery_uses_supported_page_size_and_reads_all_pages(self):
        received = []
        include_total = [True]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                action = self.headers["x-acs-action"]
                query = parse_qs(urlsplit(self.path).query)
                page, size = int(query["PageNumber"][0]), int(query["PageSize"][0])
                received.append((action, page, size))
                code = 200
                if size > 50:
                    code, body = 400, {"Code": "InvalidParameter", "Message": 'The specified parameter "PageSize" is not valid.'}
                else:
                    rows = [{"id": n} for n in range((page - 1) * size, min(page * size, 75))]
                    outer, inner = {"DescribeVSwitches": ("VSwitches", "VSwitch"), "DescribeVpcs": ("Vpcs", "Vpc")}[action]
                    body = {outer: {inner: rows}}
                    if include_total[0]: body["TotalCount"] = 75
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        from alibabacloud_tea_openapi.models import Config
        cloud = b.Cloud({"ALIBABA_CLOUD_ACCESS_KEY_ID": "test", "ALIBABA_CLOUD_ACCESS_KEY_SECRET": "test"}, "cn-hangzhou")
        with patch.object(cloud, "config", return_value=Config(access_key_id="test", access_key_secret="test", protocol="HTTP", endpoint=f"127.0.0.1:{server.server_port}")):
            for total in [True, False]:
                include_total[0] = total
                for action, collection in [("DescribeVSwitches", "VSwitches.VSwitch"), ("DescribeVpcs", "Vpcs.Vpc")]:
                    with self.subTest(action=action, total=total):
                        received.clear()
                        rows = cloud.pages("vpc", action, collection, RegionId="cn-hangzhou")
                        self.assertEqual(rows, [{"id": n} for n in range(75)])
                        self.assertEqual(received, [(action, 1, 50), (action, 2, 50)])

    def test_sdk_requests_use_actual_serialization_and_signature(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                received.append((self.headers.get("x-acs-action"), self.path, self.rfile.read(int(self.headers.get("Content-Length", 0))), self.headers.get("Authorization")))
                action = received[-1][0]
                value = {"ListTeams": {"teams": [], "total": 0}, "CreateTeam": {"team": {"teamID": "t"}}, "CreateApiKey": {"apiKey": {"apiKeyID": "k", "apiKeyValue": "test"}}, "ListInstance": {"IsSuccess": True, "Instances": []}}[action]
                raw = json.dumps(value).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            do_GET = do_POST
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        from alibabacloud_tea_openapi.models import Config
        cloud = b.Cloud({"ALIBABA_CLOUD_ACCESS_KEY_ID": "test", "ALIBABA_CLOUD_ACCESS_KEY_SECRET": "test"}, "cn-hangzhou")
        with patch.object(cloud, "config", return_value=Config(access_key_id="test", access_key_secret="test", protocol="HTTP", endpoint=f"127.0.0.1:{server.server_port}")):
            self.assertEqual(cloud.pages("sandbox", "ListTeams", "teams"), [])
            cloud.sandbox("CreateTeam", body={"teamName": "demo", "resourceGroupID": "rg-test", "plan": "pro"})
            cloud.sandbox("CreateApiKey", body={"teamID": "t", "apiKeyName": "demo"})
            cloud.rpc("cr", "ListInstance", PageNo=1, PageSize=100)
        self.assertTrue(all(row[3] for row in received))
        self.assertEqual(json.loads(received[1][2])["teamName"], "demo")
        self.assertEqual(json.loads(received[2][2])["teamID"], "t")
        self.assertEqual(parse_qs(urlsplit(received[3][1]).query)["PageSize"], ["100"])
