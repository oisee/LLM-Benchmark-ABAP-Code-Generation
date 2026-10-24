#!/usr/bin/env python3
"""Check deliberate adapter regressions in place, restoring every file in finally.

Run with no other local validator tests active. Bulk logs stay ignored.
"""
import json
from pathlib import Path
import subprocess
import sys
root=Path(__file__).resolve().parents[2]
output=root/'osg-results/feedback-mutations'
output.mkdir(parents=True,exist_ok=True)
python=sys.executable
node=['node','--test','--test-name-pattern=SAP feedback','tools/osg-validator/test.mjs']
def py(name):return [python,'tools/osg-validator/test_integration.py','IntegrationTests.'+name]
cases=[
 ('unit_kind','tools/osg-validator/feedback.mjs','return {title,kind,severity,stack,details};',"return {title,kind:'E',severity,stack,details};",node),
 ('syntax_location','tools/osg-validator/engine.mjs','line:i.getStart().getRow(),','',node),
 ('blank_value','tools/osg-validator/feedback.mjs',"? '' : safeText(value)","? ' ' : safeText(value)",node),
 ('temperature','src/osg_run.py',"'temperature': temperature}","'temperature': 0.9}",py('test_fake_openai_canonical_then_broken')),
 ('default_budget','src/osg_run.py','default=5000)','default=8192)',py('test_cli_defaults_are_comparable')),
 ('dry_run','src/osg_run.py',"if getattr(args, 'dry_run', False):","if False:",py('test_dry_run_has_no_clients_or_writes')),
 ('activation_shape','src/osg_validator.py','error:\\n{activation_text}','error:\\n{errors}',py('test_activation_uses_sap_exception_text_contract')),
 ('duration','tools/osg-validator/engine.mjs','},60000);','},30000);',py('test_timeout_is_unit_failure_and_worker_recovers')),
]
results=[]
for name,file,old,new,command in cases:
 p=root/file;original=p.read_text()
 assert old in original,(name,'mutation missing')
 try:
  p.write_text(original.replace(old,new,1))
  result=subprocess.run(command,cwd=root,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
  (output/(name+'.log')).write_text(result.stdout)
  results.append({'mutation':name,'detected':result.returncode!=0})
  print(name,result.returncode,flush=True)
 finally:p.write_text(original)
(root/'tools/osg-validator/feedback-mutation-summary.json').write_text(json.dumps(results,indent=2)+'\n')
if not all(r['detected'] for r in results):sys.exit(1)
