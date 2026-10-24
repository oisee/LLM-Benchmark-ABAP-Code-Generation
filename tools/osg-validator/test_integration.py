"""Local-only integration tests. This file never contacts a model service."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
import sys

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / 'src'))
from osg_validator import OsgClient, evaluate


class IntegrationTests(unittest.TestCase):
    def test_fake_openai_canonical_then_broken(self):
        from osg_run import run
        source = (ROOT / 'dataset/abap_canonical_solution/z_humaneval_000.abap').read_text()
        responses = [source, source.replace('ENDMETHOD.', 'invalid ??? . ENDMETHOD.', 1)]
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.assert_path = self.path
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path, body))
                payload = {'id':'fake', 'object':'chat.completion','created':0,'model':'fake-model',
                           'choices':[{'index':0,'message':{'role':'assistant','content':'```abap\n'+responses.pop(0)+'\n```'},'finish_reason':'stop'}]}
                data=json.dumps(payload).encode()
                self.send_response(200);self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)

            def log_message(self, *args):
                pass

        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        name='osg-local-test-'+str(os.getpid())
        files=[ROOT/'data'/f'{name}.json',ROOT/'data'/f'{name}_tiers.json']
        try:
            with tempfile.NamedTemporaryFile(mode='w',suffix='.json') as subset:
                json.dump({'tasks':['0.txt']},subset);subset.flush()
                args=SimpleNamespace(subset=subset.name,output_name=name,model='fake-model',
                    base_url=f'http://127.0.0.1:{server.server_port}/v1',api_key='fake',max_tokens=4096,repetitions=2,rounds=1)
                asyncio.run(run(args))
            tiers=json.loads(files[1].read_text())['0.txt']
            chats=json.loads(files[0].read_text())['0.txt']
            self.assertTrue(tiers[0]['round_0']['unit_pass'])
            self.assertEqual(tiers[1]['round_0']['error_stage'],'syntax')
            self.assertEqual(chats[0][-1]['content'],'The unit tests were successful.')
            self.assertTrue(chats[1][-1]['content'].startswith('The syntax check failed with the following errors:\n[{'))
            self.assertEqual(len(requests),2)
            self.assertTrue(all(path=='/v1/chat/completions' and body['model']=='fake-model' for path,body in requests))
            self.assertTrue(all('```' not in chat[2]['content'] for chat in chats))
        finally:
            server.shutdown();server.server_close();thread.join()
            for path in files:
                path.unlink(missing_ok=True)

    def test_timeout_is_unit_failure_and_worker_recovers(self):
        source=(ROOT/'dataset/abap_canonical_solution/z_humaneval_000.abap').read_text()
        # A valid ABAP infinite loop must be terminated by the parent watchdog.
        mutant=source.replace('ENDMETHOD.','DO. ENDDO. ENDMETHOD.',1)
        # Insert at entry, since the canonical algorithm may return early.
        import re
        mutant=re.sub(r'(METHOD has_close_elements\.)',r'\1\nDO. ENDDO.',source,flags=re.I)
        client=OsgClient()
        try:
            tier,feedback=evaluate(client,'0.txt',mutant)
            self.assertEqual(tier['error_stage'],'unit')
            self.assertFalse(tier['unit_pass'])
            self.assertIn('timeout',feedback)
            self.assertTrue(evaluate(client,'0.txt',source)[0]['unit_pass'])
        finally:
            client.close()

    def test_historical_rounds_are_preserved(self):
        from osg_validator import run_single_prompt
        from unittest.mock import patch
        source=(ROOT/'dataset/abap_canonical_solution/z_humaneval_000.abap').read_text()
        chat=[{'role':'assistant','content':source},{'role':'user','content':'Earlier feedback'}, {'role':'assistant','content':source}]
        client=OsgClient()
        try:
            with patch('abap_interaction.load_tiers',return_value={'0.txt':[{'round_0':{'unit_pass':False}}]}):
                _,tiers=run_single_prompt(client,'0.txt',[chat],True,'fake-model')
            self.assertFalse(tiers[0]['round_0']['unit_pass'])
            self.assertTrue(tiers[0]['round_1']['unit_pass'])
        finally:
            client.close()

if __name__=='__main__':unittest.main()
