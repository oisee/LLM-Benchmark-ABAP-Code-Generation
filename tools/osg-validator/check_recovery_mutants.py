#!/usr/bin/env python3
"""Run isolated recovery/fingerprint mutants, each after a passing control.

Requires the installed local Node dependencies and pinned library. No model,
SAP, or network calls are made. Logs stay in the system temporary directory.
"""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys

root=Path(__file__).resolve().parents[2]
python=sys.executable
results=[]
with tempfile.TemporaryDirectory(prefix='osg-recovery-mutants-') as tmp:
    clone=Path(tmp)
    for directory in ('src','dataset','tools/osg-validator'):
        shutil.copytree(root/directory,clone/directory,ignore=shutil.ignore_patterns('node_modules','.cache','.runs','__pycache__'))
    (clone/'data').mkdir()
    (clone/'tools/osg-validator/.cache').mkdir()
    (clone/'tools/osg-validator/.cache/open-abap-core').symlink_to(root/'tools/osg-validator/.cache/open-abap-core',target_is_directory=True)
    (clone/'tools/osg-validator/node_modules').symlink_to(root/'tools/osg-validator/node_modules',target_is_directory=True)
    validator=clone/'src/osg_validator.py'
    runner=clone/'src/osg_run.py'
    audit=clone/'tools/osg-validator/audit.py'
    feedback=clone/'tools/osg-validator/feedback.mjs'
    engine=clone/'tools/osg-validator/engine.mjs'
    originals={p:p.read_text() for p in (validator,runner,audit,feedback,engine)}
    def probe(name,path,mutated,test):
        command=[python,'-m','unittest','discover','-s','tools/osg-validator','-p','test_recovery.py','-k',test,'-v']
        control=subprocess.run(command,cwd=clone,capture_output=True,text=True,timeout=60)
        assert control.returncode==0, control.stdout+control.stderr
        path.write_text(mutated)
        try:
            result=subprocess.run(command,cwd=clone,capture_output=True,text=True,timeout=60)
            detected=result.returncode!=0 and ('FAIL:' in result.stderr or 'ERR_ASSERTION' in result.stderr)
            results.append({'mutant':name,'test':test,'control_passed':True,'detected':detected})
            (Path(tempfile.gettempdir())/('osg-recovery-mutant-'+name+'.log')).write_text(result.stdout+result.stderr)
        finally:path.write_text(originals[path])
    probe('lost-worker-ownership',validator,originals[validator].replace('        self.close()\n        self.responses', '        # cleanup deliberately removed\n        self.responses').replace('            self.close()\n            raise InfrastructureError', '            # cleanup deliberately removed\n            raise InfrastructureError'),'test_compiler_crash_cleans_runtime_before_replacement')
    probe('no-retries',validator,originals[validator].replace('range(self.retries + 1)','range(1)').replace('if attempt == self.retries:','if attempt == 0:'),'test_startup_failure_retries_bounded_without_verdict')
    probe('fabricated-eof-verdict',validator,originals[validator].replace('{"infrastructure_error": "Validator worker terminated (startup or compiler failure)"}', '{"stage":"unit","unit":False}'),'test_persistent_crash_retains_known_stages')
    probe('discard-progress',validator,originals[validator].replace('known.update({k: v for k, v in exc.progress.items() if v is not None})','known.clear()'),'test_persistent_crash_retains_known_stages')
    probe('consume-round',runner,originals[runner].replace("                            raise\n", "                            tier, feedback = exc.tier, 'repair compiler'\n"),'test_runner_checkpoints_without_feedback_or_next_request')
    probe('wrong-infra-marker',validator,originals[validator].replace('f"{INFRA_FEEDBACK_PREFIX}: {exc}"','f"[INFRA]: {exc}"'),'test_benchmark_seam_uses_infra_retry_state')
    probe('old-verdict-key',audit,subprocess.check_output(['git','show','8bbe068:tools/osg-validator/audit.py'],cwd=root,text=True),'test_verdict_fingerprint_covers_all_inputs')
    probe('scrub-values',feedback,subprocess.check_output(['git','show','8bbe068:tools/osg-validator/feedback.mjs'],cwd=root,text=True),'test_value_paths_and_truncation')
    # Test the compiled-key assertions without running unrelated engine checks.
    compiled_command=['node','--test','--test-name-pattern=compiled cache','tools/osg-validator/test.mjs']
    control=subprocess.run(compiled_command,cwd=clone,capture_output=True,text=True,timeout=60)
    assert control.returncode==0, control.stdout+control.stderr
    engine.write_text(originals[engine].replace("return createHash('sha256').update(JSON.stringify([library,ddl,lock,canonicalizer,", "return createHash('sha256').update(JSON.stringify([library,[], '', '',"))
    result=subprocess.run(compiled_command,cwd=clone,capture_output=True,text=True,timeout=60)
    results.append({'mutant':'ignore-ddl-and-lock','test':'compiled cache changes for canonicalization, every DDL input and dependency lock','control_passed':True,'detected':result.returncode!=0 and 'AssertionError' in result.stdout+result.stderr})
    (Path(tempfile.gettempdir())/'osg-recovery-mutant-compiled-key.log').write_text(result.stdout+result.stderr)
print(json.dumps(results,indent=2))
(root/'tools/osg-validator/recovery-mutation-summary.json').write_text(json.dumps({'checks':results,'all_detected':all(r['detected'] for r in results)},indent=2)+'\n')
assert all(r['detected'] for r in results)
