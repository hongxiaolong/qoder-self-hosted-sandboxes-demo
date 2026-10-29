import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request

from qoder_self_hosted_sandboxes_demo.demos.report_analyst.execution import execution_snapshot
from qoder_self_hosted_sandboxes_demo.demos.report_analyst.service import create_server


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / 'worker.jsonl'
        (self.root / 'identity.json').write_text(json.dumps(dict(qoder_session_url='https://qoder.com/cloud/sessions/sess-test',browser_sandbox_id='browser-test',analysis_sandbox_id='analysis-test')))

    def event(self, kind, tool, key='python', **extra):
        with self.log.open('a') as out:
            out.write(json.dumps(dict(kind=kind,tool=tool,tool_use_id=key,session_id='sess-test',time=1,**extra))+'\n')

    def test_real_started_code_and_finished_stdout_are_joined(self):
        self.event('tool_start','run_python',input={'code':"print('actual')"})
        event = execution_snapshot(self.root,self.log)['events'][0]
        self.assertEqual(event['state'],'running')
        self.assertEqual(event['code'],"print('actual')")
        self.event('tool_finished','run_python',output='exit_code=0\nactual',is_error=False,elapsed_ms=12)
        event = execution_snapshot(self.root,self.log)['events'][0]
        self.assertEqual(event['output'],'exit_code=0\nactual')
        self.assertEqual(event['state'],'completed')

    def test_browser_execution_and_transfer_are_distinguished(self):
        self.event('tool_start','download_report',input={'month':'2026-10','region':'华东','token':'hidden'})
        self.event('tool_finished','download_report',output=json.dumps({'rows':100,'bytes':3360,'browser_path':'/browser/data.csv','analysis_path':'/workspace/input.csv','token':'hidden'}))
        data = execution_snapshot(self.root,self.log)
        self.assertEqual(data['events'][0]['role'],'browser')
        self.assertIn('100',data['events'][0]['output_summary'])
        self.assertNotIn('hidden',json.dumps(data))

    def test_other_sessions_generic_tools_partial_lines_and_secrets_not_exposed(self):
        self.event('tool_start','Bash',input={'command':'secret-command'})
        self.event('tool_start','run_python',input={'code':'API_KEY="private-key"\nprint(1)'})
        self.event('tool_finished','run_python',output='Bearer private-token https://host/upload?_state=private-state',is_error=True)
        with self.log.open('a') as out:
            out.write(json.dumps(dict(kind='tool_start',tool='run_python',tool_use_id='other',session_id='another-session',input={'code':'other-session'}))+'\nnull\n{"partial":')
        data=execution_snapshot(self.root,self.log)
        text=json.dumps(data)
        for secret in ['secret-command','private-key','private-token','private-state','other-session']:
            self.assertNotIn(secret,text)
        self.assertEqual(data['events'][0]['state'],'error')

    def test_http_endpoint_and_missing_log_are_honest(self):
        server=create_server(self.root,0,'127.0.0.1',execution_log=self.log)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(thread.join,3);self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/api/execution') as response:
            self.assertEqual(json.load(response)['events'],[])
        self.event('tool_start','append_record',input={'amount':6666,'category':'现场演示'})
        self.event('tool_finished','append_record',output=json.dumps({'before_rows':100,'after_rows':101,'record':{'amount':6666}}))
        with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/api/execution') as response:
            event=json.load(response)['events'][0]
            self.assertIn('100 → 101',event['output_summary'])
            self.assertIn('不含表头',event['output_summary'])
