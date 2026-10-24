"""OSG-js backend. No SAP connection or credentials are needed.

The compiler stays warm; generated answers run in fresh, memory-limited Node
children. A parent watchdog covers the entire per-answer pipeline.
"""
import atexit
import json
import os
from pathlib import Path
import queue
import signal
import shutil
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[1]


class OsgClient:
    def __init__(self):
        self.process = None
        self.responses = queue.Queue()
        self.progress = {}
        self.threads = []
        atexit.register(self.close)

    def close(self):
        if self.process is not None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait()
            for thread in self.threads:
                thread.join(timeout=2)
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                stream.close()
            for directory in (ROOT / "tools/osg-validator/.runs").glob(f"answer-{self.process.pid}-*"):
                shutil.rmtree(directory, ignore_errors=True)
            self.process = None

    def _start(self):
        self.responses = queue.Queue()
        self.process = subprocess.Popen(
            ["node", "--max-old-space-size=1024", str(ROOT / "tools/osg-validator/worker.mjs")],
            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1, start_new_session=True,
            # Do not pass model/SAP credentials to compiler or generated code.
            env={"PATH": os.environ["PATH"]},
        )
        process, responses = self.process, self.responses

        def read_results():
            for line in process.stdout:
                try:
                    responses.put(json.loads(line))
                except ValueError:
                    continue
            responses.put({"stage": "unit", "unit": False,
                           "errors": ["Validator worker terminated (memory cap or initialization failure)"]})

        def read_progress():
            for line in process.stderr:
                try:
                    event = json.loads(line)
                    if "progress" in event:
                        self.progress = event["progress"]
                except ValueError:
                    pass

        self.threads = [threading.Thread(target=read_results, daemon=True),
                        threading.Thread(target=read_progress, daemon=True)]
        for thread in self.threads:
            thread.start()

    def validate(self, name, source, tests):
        if self.process is None or self.process.poll() is not None:
            self._start()
        self.progress = {}
        start = time.monotonic()
        self.process.stdin.write(json.dumps({"name": name, "source": source, "tests": tests}) + "\n")
        self.process.stdin.flush()
        while True:
            try:
                result = self.responses.get(timeout=0.1)
                break
            except queue.Empty:
                reason = None
                if time.monotonic() - start >= 90:
                    reason = "Validator exceeded 90 s wall timeout"
                try:
                    status = Path(f"/proc/{self.process.pid}/status").read_text()
                    rss = next(int(line.split()[1]) for line in status.splitlines() if line.startswith("VmRSS:"))
                    if rss > 1536 * 1024:
                        reason = "Validator exceeded 1536 MiB resident memory cap"
                except (OSError, StopIteration):
                    pass
                if reason:
                    self.close()
                    result = {**self.progress, "stage": "unit", "unit": False, "errors": [reason]}
                    break
        result["elapsed_seconds"] = round(time.monotonic() - start, 4)
        return result


def evaluate(client, prompt_file, source, canonicalize=True):
    # Reuse the benchmark's extraction/canonicalization contract exactly.
    from abap_interaction import canonicalize_code, create_tier_result, extract_contract
    contract = extract_contract(prompt_file)
    tier = create_tier_result()
    tier.update(validator="osg", unit_syntax_pass=None)
    if canonicalize:
        source, report = canonicalize_code(source, contract)
        tier["canonicalization_report"] = report
        if report["warnings"]:
            tier.update(error_stage="canonicalization", error_message="; ".join(report["warnings"]))
            return tier, (f"Code structure issue: {tier['error_message']}. "
                          "Please provide a valid ABAP class with a public static method.")
    else:
        # The OSG path uses fixed tests. Legacy dynamic renaming is SAP-only.
        import re
        match = re.search(r"CLASS\s+(\w+)\s+DEFINITION", source, re.I)
        if match:
            source = re.sub(rf"\b{re.escape(match[1])}\b", contract["class_name"], source, flags=re.I)
    result = client.validate(contract["class_name"], source, (ROOT / contract["unittest_file"]).read_text())
    tier.update(sap_syntax_pass=result.get("syntax"), sap_activation_pass=result.get("activation"),
                unit_syntax_pass=result.get("unit_syntax"), unit_pass=result.get("unit"),
                osg_stage=result.get("stage"), elapsed_seconds=result.get("elapsed_seconds"),
                test_count=result.get("test_count", 0))
    stage = result.get("stage")
    if stage is None:
        return tier, "The unit tests were successful."
    uri = f"/sap/bc/adt/oo/classes/{contract['class_name']}"
    if stage == "unit_syntax":
        uri += "/includes/testclasses"
    errors = []
    for diagnostic in result.get("errors", []):
        if isinstance(diagnostic, dict):
            item_uri = f"/sap/bc/adt/oo/classes/{contract['class_name']}"
            if diagnostic.get("test_include"):
                item_uri += "/includes/testclasses"
            errors.append({"uri": item_uri, **{key: diagnostic[key] for key in
                           ("type", "short_text", "line", "offset") if key in diagnostic}})
        else:
            errors.append({"uri": uri, "type": "E", "short_text": diagnostic})
    if stage == "unit":
        errors = result.get("unit_feedback") or [{
            "title": "Failed due to a runtime abortion", "kind": "runtimeAbortion",
            "severity": "fatal", "stack": [],
            "details": ["[" + str(message) + "]" for message in result.get("errors", [])],
        }]
    activation_text = "\n".join(str(e.get('short_text', '')) if isinstance(e, dict) else str(e)
                                for e in result.get('errors', []))
    messages = {
        "syntax": f"The syntax check failed with the following errors:\n{errors}",
        "activation": f"The activation failed with the following error:\n{activation_text}",
        "unit_syntax": f"The unittest syntax check failed with the following errors:\n{errors}\nTry to fix the class, not the unit test.",
        "unit": f"The unit test failed with the following errors:\n{errors}\n Try to fix the class, not the unit test.",
    }
    feedback = messages[stage]
    tier.update(error_stage="unit" if stage == "unit_syntax" else stage, error_message=feedback)
    return tier, feedback


def run_single_prompt(client, prompt_file, chats, canonicalize, model_name):
    from abap_interaction import get_round_number, load_tiers
    tiers = load_tiers(model_name).get(prompt_file, []) if model_name else []
    tiers += [{} for _ in range(len(chats) - len(tiers))]
    for i, chat in enumerate(chats):
        if not chat or chat[-1]["role"] != "assistant":
            continue
        round_number = get_round_number(chat)
        tier, feedback = evaluate(client, prompt_file, chat[-1]["content"], canonicalize)
        chat.append({"role": "user", "content": feedback})
        tiers[i][f"round_{round_number}"] = tier
    return chats, tiers


def selected_client():
    validator = os.environ.get("ABAP_VALIDATOR", "sap")
    if validator not in ("sap", "osg"):
        raise ValueError("ABAP_VALIDATOR must be sap or osg")
    return OsgClient() if validator == "osg" else None
