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


class InfrastructureError(RuntimeError):
    """No answer verdict is available; retain any stages already observed."""
    def __init__(self, message, progress=None):
        super().__init__(message)
        self.progress = dict(progress or {})
        self.tier = None


class OsgClient:
    def __init__(self, retries=2):
        self.process = None
        self.responses = queue.Queue()
        self.progress = {}
        self.threads = []
        self.retries = retries
        atexit.register(self.close)

    def close(self):
        process = self.process
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            # Killing the entire group closes inherited pipes too. Join readers
            # before releasing ownership or removing their scratch directories.
            for thread in self.threads:
                thread.join()
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
            for directory in (ROOT / "tools/osg-validator/.runs").glob(f"answer-{process.pid}-*"):
                shutil.rmtree(directory)
            self.process = None
            self.threads = []

    def _start(self):
        self.close()
        self.responses = queue.Queue()
        self.progress = {}
        self.process = subprocess.Popen(
            ["node", "--max-old-space-size=1024", str(ROOT / "tools/osg-validator/worker.mjs")],
            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1, start_new_session=True,
            # Do not pass model/SAP credentials to compiler or generated code.
            env={"PATH": os.environ["PATH"]},
        )
        process, responses, progress = self.process, self.responses, self.progress

        def read_results():
            for line in process.stdout:
                try:
                    responses.put(json.loads(line))
                except ValueError:
                    responses.put({"infrastructure_error": "Invalid validator JSON response"})
            responses.put({"infrastructure_error": "Validator worker terminated (startup or compiler failure)"})

        def read_progress():
            for line in process.stderr:
                try:
                    event = json.loads(line)
                    if isinstance(event.get("progress"), dict):
                        progress.update(event["progress"])
                except (ValueError, AttributeError):
                    pass

        self.threads = [threading.Thread(target=read_results, daemon=True),
                        threading.Thread(target=read_progress, daemon=True)]
        for thread in self.threads:
            thread.start()

    def _validate_once(self, name, source, tests):
        start = time.monotonic()
        try:
            if self.process is None or self.process.poll() is not None:
                self._start()
            self.progress.clear()
            self.process.stdin.write(json.dumps({"name": name, "source": source, "tests": tests}) + "\n")
            self.process.stdin.flush()
            while True:
                try:
                    result = self.responses.get(timeout=0.1)
                    if not isinstance(result, dict):
                        raise InfrastructureError("Invalid validator response")
                    if result.get("infrastructure_error"):
                        raise InfrastructureError(result["infrastructure_error"])
                    stage = result.get("stage")
                    if stage not in (None, "syntax", "activation", "unit_syntax", "unit") or not all(
                        result.get(key) in (True, False, None) for key in ("syntax", "activation", "unit_syntax", "unit")
                    ) or (stage is None and result.get("unit") is not True) or (
                        stage is not None and result.get(stage) is not False
                    ):
                        raise InfrastructureError("Invalid validator verdict protocol")
                    result["elapsed_seconds"] = round(time.monotonic() - start, 4)
                    return result
                except queue.Empty:
                    if time.monotonic() - start >= 90:
                        raise InfrastructureError("Validator exceeded 90 s wall timeout")
                    try:
                        status = Path(f"/proc/{self.process.pid}/status").read_text()
                        rss = next(int(line.split()[1]) for line in status.splitlines() if line.startswith("VmRSS:"))
                        if rss > 1536 * 1024:
                            raise InfrastructureError("Compiler exceeded 1536 MiB resident memory cap")
                    except (OSError, StopIteration):
                        pass
        except (OSError, ValueError, InfrastructureError) as exc:
            self.close()
            raise InfrastructureError(str(exc), self.progress) from exc

    def validate(self, name, source, tests):
        known = {}
        for attempt in range(self.retries + 1):
            try:
                return self._validate_once(name, source, tests)
            except InfrastructureError as exc:
                known.update({k: v for k, v in exc.progress.items() if v is not None})
                if attempt == self.retries:
                    raise InfrastructureError(
                        f"OSG infrastructure failure after {attempt + 1} attempts: {exc}", known
                    ) from exc


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
    try:
        result = client.validate(contract["class_name"], source, (ROOT / contract["unittest_file"]).read_text())
    except InfrastructureError as exc:
        for key, field in (("syntax", "sap_syntax_pass"), ("activation", "sap_activation_pass"),
                           ("unit_syntax", "unit_syntax_pass"), ("unit", "unit_pass")):
            tier[field] = exc.progress.get(key)
        tier.update(error_stage="infra", error_message=str(exc))
        exc.tier = tier
        raise
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
        try:
            tier, feedback = evaluate(client, prompt_file, chat[-1]["content"], canonicalize)
        except InfrastructureError as exc:
            from chat_state import INFRA_FEEDBACK_PREFIX
            tier, feedback = exc.tier, f"{INFRA_FEEDBACK_PREFIX}: {exc}"
        chat.append({"role": "user", "content": feedback})
        tiers[i][f"round_{round_number}"] = tier
    return chats, tiers


def selected_client():
    validator = os.environ.get("ABAP_VALIDATOR", "sap")
    if validator not in ("sap", "osg"):
        raise ValueError("ABAP_VALIDATOR must be sap or osg")
    return OsgClient() if validator == "osg" else None
