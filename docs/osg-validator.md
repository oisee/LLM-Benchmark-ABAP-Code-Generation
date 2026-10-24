# OSG-js validator

OSG is an additional validator for this benchmark. SAP remains the default.
It uses the public, pinned open-abap-core library and published abaplint packages;
it requires neither an SAP connection nor SAP credentials.

## Install and verify

From the repository root, install the normal Python dependencies (`uv sync`),
then use Node 22 or newer:

```sh
npm ci --prefix tools/osg-validator
npm run bootstrap --prefix tools/osg-validator
npm test --prefix tools/osg-validator
uv run python tools/osg-validator/test_integration.py
uv run python tools/osg-validator/check_feedback_mutants.py
uv run python tools/osg-validator/audit.py gate
uv run python tools/osg-validator/audit.py calibrate
uv run python tools/osg-validator/audit.py subset
uv run python tools/osg-validator/throughput.py
```

The mutation check temporarily changes adapter files and restores them in `finally`;
run it separately from other local tests. It detects eight deliberate feedback,
configuration, dry-run and timeout regressions; the compact results are in
`feedback-mutation-summary.json`.

The integration tests use only a loopback Python HTTP server. It returns a
canonical solution and then a broken solution through the existing direct OpenAI
generator. Other checks exercise syntax/type errors, wrong tables/fields, failed
assertions, fresh databases, an infinite loop, a memory allocation mutant, and
preservation of earlier rounds. No real model benchmark was run for this change.

`audit.py gate` exits nonzero when more than 10% of canonicals fail, a
syntax-break/unknown-type mutant survives, the overall kill rate is below 90%,
or any counted mutant fails at a different stage than expected. Four surviving
mutants for tasks 21 and 34 are recorded as dataset weaknesses: their tests cannot
observe the mutated behavior. Six more (40, 43, 69, 147, 160 off_by_one; 153
flipped_comparison) were read by hand and are equivalent mutants: no input can
tell them from the original (the reason for each is in `EQUIVALENT` in
`audit.py`). Both groups are excluded from the kill-rate denominator; a listed
mutant that starts failing is counted again, and any other survivor is counted
and keeps the expected-stage gate red. Baseline failures are
listed in `tools/osg-validator/excluded.json`; they are never hidden to make the
gate pass. Only passing canonicals provide meaningful mutation baselines.

Audits use three compiler workers by default (`--workers` changes this). Full
answer replays and a source-keyed SQLite cache remain under ignored `osg-results/`.
Use `compare_replays.py BEFORE_REPLAY AFTER_REPLAY --output SUMMARY` for an
exact nullable verdict comparison, including failed-stage fields and stable
SHA-256 digests. It exits nonzero on a difference or mismatched answer identifiers.

The tracked JSON files contain compact summaries and at most 20 examples per
stage and disagreement direction, plus one example per false-accept cause.
The cache key covers engine source, compatibility contents, the library pin, Python adapter, task,
answer and unit-test source; audit-only reporting
changes can reuse verdicts. Remove the cache to measure a completely fresh run.

## Select the backend

```sh
uv run python src/abap_test.py --model MODEL --mode resume --validator osg
ABAP_VALIDATOR=osg uv run python src/parallel_runner.py --model MODEL --workers 3
ABAP_VALIDATOR=osg uv run python src/main.py --model MODEL
```

The last command runs generation as well as validation: use it only when a model
run is intended. `ABAP_VALIDATOR=sap` restores the default. For comparisons, use a
separate model/output name so OSG results do not overwrite SAP results.

The OSG adapter calls the benchmark's own `extract_contract` and
`canonicalize_code`. Stored assistant messages enter validation exactly as in
the SAP pipeline; the direct generator already calls `remove_code_blocks`.
The feedback uses the existing templates, Python list/dict representation, ADT
URI shapes, success text, and `sap_syntax_pass`, `sap_activation_pass`,
`unit_pass` fields. `validator`, `osg_stage`, `unit_syntax_pass`, `test_count` and
`elapsed_seconds` add detail without requiring changes to statistics scripts.
`unit_syntax` maps to the existing `unit` error stage. Previous OSG rounds are
preserved when resuming. The fixed tests are used throughout; the optional
noncanonical OSG mode renames the class, whereas SAP's legacy mode can also adapt
the method call. Use canonicalization for comparable runs.

## Endpoint and subset runner

The existing direct generator already accepts a constructed OpenAI client and a
model name. Its two CLI call sites now explicitly honor `OPENAI_BASE_URL`.
For an arbitrary model identifier and the generated subset, use the small runner:

```sh
uv run python src/osg_run.py --base-url "$OPENAI_BASE_URL" --model "$MODEL" \
  --api-key "$OPENAI_API_KEY" --output-name osg-experiment \
  --subset tools/osg-validator/subset.json --repetitions 1 --rounds 6 \
  --temperature 0.2 --max-tokens 5000
```

This command performs model calls when explicitly invoked. It uses the existing
`ask_openai_direct`, system prompt, extraction, R0–R5 feedback and conversation/
tier formats. It refuses to overwrite an existing output. Validation and normal
analysis scripts can read the resulting files unchanged. An API key is passed
only to the model client; neither compiler nor generated code inherits model or
SAP credentials. The default runner key is a dummy for local test servers.

Defaults are the checked-in **46-task subset, one repetition, R0 through R5,
temperature 0.2, max_tokens 5000, and one request at a time**. A passing answer
stops that conversation early. Temperature is explicitly sent on every request,
including model names containing `gpt-5`; this runner targets an explicitly
configured compatible endpoint. The general provider's existing GPT parameter
handling is unchanged. Both sampling parameters are overridable. For a reasoning
endpoint with a shared output/reasoning budget, use e.g. `--max-tokens 15000`.
The runner sends `max_tokens`, so the endpoint must support that parameter and
the requested temperature; it does not silently substitute server defaults.

Add `--dry-run` to print the full task list, rounds, model, parameters, concurrency
and maximum request count (276 by default), without starting either client or
writing output. The run writes `data/NAME_manifest.json` with the same plan,
`NAME.json` with conversations, `NAME_tiers.json` with verdicts, and
`NAME_result.json` with per-task, per-repetition, per-round generation metadata.
The result records `finish_reason`, `truncated` (`finish_reason == "length"`),
full endpoint usage and a separate nullable `reasoning_tokens` copied from
`completion_tokens_details.reasoning_tokens`. Null means not supplied, not zero.
A truncated response is still validated and receives the normal feedback round.
No API key is saved in the manifest.

The subset parses the BOM-marked, semicolon-separated CSV with Python `csv`,
including quoted multiline fields. It excludes every red canonical, assigns a
multilabel task to its first category in the stored priority order, sorts and
shuffles candidates with seed 20261024, and never duplicates a task. The priority
maximizes available slots capped at ten per category, with deterministic CSV-order
ties. `shortfalls` records categories that cannot supply ten tasks. Those categories take
all eligible tasks and print the shortfall as information; this is not a gate failure. The source
CSV has only four date/time tasks even before exclusions; a full 60-task subset
is impossible without changing the dataset or labels.

## Feedback fidelity and corpus evidence

Run `python tools/osg-validator/corpus_shapes.py` to regenerate
`corpus-shapes.json`. It scans all recorded user feedback, parses Python literals
after replacing only the unstable Element repr, and reports every key/stack shape,
kind, title, assertion-detail family and entries-per-message histogram. All 4,817
feedback messages parse: 1,415 syntax failures, 1,433 test-syntax failures, 454
unit failures and 1,515 successes. There are **zero activation-failure messages**;
activation parity cannot be measured from this corpus.

| SAP entry shape / frequency | OSG source and mapping |
|---|---|
| Syntax `uri,type,short_text,line,offset`: 1,515 E + 45 W; `uri,type,short_text`: 456 E | abaplint issue message, source include, one-based row and zero-based column offset; omit internal rule key and file name |
| Test syntax `uri,type,short_text,line,offset`: 7,515 E | Same mapping, correct class or test-include ADT URI |
| `failedAssertion`, critical: 499 | Observe the original assertion call and thrown error, render MSG or method/assertion label, comparison detail and ABAP test/program identity |
| `exception`, critical: 11 | ABAP exception class and `get_text`; table-expression index retained by a delegating adapter to reproduce missing-row text |
| `runtimeAbortion`, fatal: 180 | Runtime message as a bracketed detail, no JS stack/object dump |
| `warning`, tolerable: 54; `abortion`, tolerable: 374 | At 60 seconds, one duration warning followed by an abortion entry per unfinished method |
| Activation: no recorded entries | Preserve SAP wrapper; `activate_class` uses exception text, so OSG supplies a sanitized transpiler message string, not syntax dictionaries |

All five unit kinds use keys in this exact order:
`title,kind,severity,stack,details`. Assertion and exception `stack` is the stable
string `'<Element stack>'`; SAP's `<Element 'stack' at 0x...>` is a Python-client
artifact, not useful stack content. Runtime/timeout stacks remain `[]`. No JS
stack frames, dependency object fields or internal paths reach feedback.

Unit execution catches each failing test method and continues, returning one
entry per failure. Test methods are ordered by name, matching the corpus; setup
and teardown retain the transpiler lifecycle. Runtime failures during global
initialization/setup can only yield a run-level abort. Timeout also lists methods
that could not start, as the recorded SAP output does. Syntax diagnostics are
returned individually, ordered by source include, line and offset, without an
invented error cap. All 2,392 corpus messages with positions are in source order;
syntax lists range from 1–20 entries, test syntax from 1–13, unit lists from 1–13.
OSG returns all actual diagnostics rather than forcing those observed maxima.

ABAP display preserves character content/NUMC zeroes, renders an all-blank value
as empty, uses X/empty booleans, numeric grouping dots, decimal commas and trailing
minus, and table/type summaries and row differences rather than JavaScript repr.
Comparison details use scalar `Different values`, long-string differing-position,
and table empty/missing/reordered-row templates found in the corpus. The pinned
assertion implementation still decides failure; observers only format errors.

Remaining differences: abaplint/transpiler **wording remains their own**, and
abaplint can report a different number of errors. The configured rules emit E
entries; SAP warning semantics are not emulated. Activation is a transpiler
exception, without SAP's full activation transport/HTTP text (no corpus evidence).
The exact SAP DDIC/class-pool qualified type names, byte lengths of all composite
and Unicode types, packed/floating numeric locale edge cases, string-diagnostic
selection threshold and table duplicate/type-mismatch comparison details are not
fully exposed by this runtime. Their display is a deterministic approximation;
we do not invent a SAP type-error verdict. Generic ABAP exceptions use available
runtime text (class plus a stable fallback if no meaningful text exists); only the
recorded missing-row exception has an exactly reconstructed message. Runtime-gap
messages remain OSG's. Parent wall/memory guards are additional infrastructure
limits, reported as runtime abortions. These limits still constrain feedback
comparability despite matching the recorded outer shapes.

## Toolchain, stages and isolation

Pins: `@abaplint/core` 2.120.55, `@abaplint/transpiler` 2.13.89,
`@abaplint/runtime` 2.13.89 and `@abaplint/database-sqlite` 2.13.83.
The bootstrap fetches `https://github.com/open-abap/open-abap-core.git` at
`8b397be863e805182c32e4bbb8bedbd291619f58`, never a floating branch.
The package lock tracks transitive dependencies. The bootstrap also prepares the
immutable compiled library before parallel workers start.

Each Node compiler keeps a warm abaplint registry containing library dependencies
the adapter compatibility objects loaded after that library, and the 13 converted tables. Only the current answer's class and test include
are installed; they are removed before the next answer. Immutable library output
is compiled once and cached. Syntax uses `Version.Cloud`, error namespace `.`,
and rules `parser_error`, `check_syntax`, `unknown_types`, `implement_methods`,
`begin_end_names`. Class syntax is checked first, then class transpilation, test
syntax, test transpilation and ABAP Unit execution. The final activation verdict
also becomes false if test transpilation fails. Class creation/source setting are
local registry operations rather than ADT operations.

All generated answer code executes in a fresh Node child, with a new ABAP runtime
and in-memory SQLite database. Compilation/parsing stays in the supervised warm
worker and never evaluates generated code. The runtime child has a 60-second
limit for the entire ABAP Unit run, matching the recorded SAP duration (not 60
seconds per method). The Python parent retains a separate 90-second total-answer
guard, including worker startup/compilation, and kills the process group on expiry. Linux resident-memory watchdogs cap the compiler at
1536 MiB and each runtime child at 384 MiB; V8 heap limits are 1024 and 256 MiB,
respectively. A V8 heap exhaustion can appear as `SIGABRT`. Limits are per process,
not a security sandbox against hostile JavaScript. Runtime children inherit only
`PATH`. Scratch directories are removed after success, failure, or parent timeout.

## Tables and MANDT

`ddl.mjs` tokenizes the dataset's flat DDL, preserving field order, keys, nullability,
CHAR/NUMC/INT4/DATS/CURR/CUKY types and currency reference annotations in abapGit
DD02V/DD03P XML. It rejects unsupported types/grammar rather than guessing.
All 13 tables are included in the warm registry and SQLite schema.

These tables have no MANDT. DD02V therefore leaves CLIDEP empty; no synthetic
client column is added. The pinned runtime's Open SQL executes against the emitted
schema without adding an implicit MANDT predicate. Its `sy-mandt` defaults to
`123`, which does not partition these client-independent tables. A focused test
inserts, aggregates and deletes rows in ZSTOCK and repeats in another fresh
answer. It passes without a MANDT column. Missing SQL aggregate fields currently
reach SQLite and fail in the unit stage; a wrong table is rejected in syntax.

Some ERP canonicals refer to tables absent from the provided 13 DDL files. Those
are excluded with the exact name/error, rather than inventing additional schemas.

## Interpretation of calibration

The replay covers every stored answer in all ten repetitions and every recorded
round, including excluded tasks. Cumulative agreement treats an unreached/null
stage as not accepted, as in tier-based scoring. `both_observed` statistics expose
how many comparisons actually reached the stage on both systems.

Only 317 of the 4,817 answers have a matching round in the recorded tiers file.
For those rounds saved fields take precedence. Missing rounds are reconstructed
from the recorded SAP feedback templates; the JSON includes oracle-source counts
and a separate recorded-tiers-only comparison. Original tiers do not store unit
syntax separately, so that stage is reconstructed from feedback. The recorded
feedback corpus has only syntax failure, unit-syntax failure, unit failure and
success templates, avoiding ambiguous activation-error reconstruction here.

A FALSE REJECT means SAP accepted the stage while OSG did not; FALSE ACCEPT means
the reverse. False-reject cause labels are heuristics from the first OSG error. False-accept
categories use the earliest recorded SAP feedback and remain visible even when
OSG passes a later stage. They are diagnostic classifications rather than
independent proof of a compiler defect. Unclassified runtime/semantic cases
need manual review. At most twenty excerpts per direction/stage aid that review.

The headline is the benchmark's repetition-level unit pass rate: R0 and any
success in R0–R5, using all 1,800 conversations as denominator. Later replayed
rounds were generated after **SAP feedback**, not OSG feedback. This is a
per-answer comparison, **not a comparison of feedback trajectories**.

Feedback templates match, but actual diagnostics differ from SAP. Cloud syntax
rules approximate SAP restrictions; they do not certify SAP-released APIs or
identical compiler/runtime behavior. SQLite is not SAP's database, and the pinned
library is incomplete. Do not put these numbers on the SAP leaderboard as if
they were the same execution environment.

## Measured results and gaps

See the checked-in `gate-summary.json`, `excluded.json`, `calibration-summary.json`
and `subset.json` under `tools/osg-validator/` for machine-readable details.

<!-- OSG_RESULTS -->

Feedback-format revalidation: the complete fresh gate is green (162/180
canonicals, 673/673 counted mutants, all expected-stage checks). All **4,817**
recorded answers (2,648 unique task/source pairs) were replayed with the 60-second
unit limit. `verdict-diff-summary.json` proves **zero per-answer differences**
against the preceding adapter replay, comparing all four nullable stage verdicts
and both failure-stage fields. Before/after verdict hashes match. Calibration
wall time was 486.045 seconds; gate wall time was 146.570 seconds. Feedback
wording can change heuristic diagnostic buckets without changing any verdict.


Canonical exclusions fell from **30/180 (16.67%) to 18/180 (10%)**. The canonical
threshold now passes. The six behavioral survivors were then reviewed by hand
and are equivalent mutants (see above); with them listed the gate is **green**:
162/180 canonicals, 673/673 counted mutants killed at the expected stage.

| Stage | Agreement before → after | FALSE REJECT before → after | FALSE ACCEPT before → after |
|---|---:|---:|---:|
| syntax | 90.907% → 90.637% | 132 → 129 | 306 → 322 |
| activation | 91.385% → 91.385% | 139 → 139 | 276 → 276 |
| unit_syntax | 84.866% → 87.710% | 252 → 96 | 477 → 496 |
| unit | 90.637% → 93.004% | 215 → 85 | 236 → 252 |

| Oracle/backend | R0 unit pass | Best R0–R5 unit pass |
|---|---:|---:|
| SAP (unchanged) | 665/1800 (36.944%) | 1515/1800 (84.167%) |
| OSG before | 672/1800 (37.333%) | 1344/1800 (74.667%) |
| OSG after | 732/1800 (40.667%) | 1474/1800 (81.889%) |

**R0 now exceeds SAP by 3.723 percentage points.** Unit false accepts rise from
236 to 252 while false rejects fall from 215 to 85. The remaining over-rating is
as material as the residual under-rating; OSG is not a substitute SAP oracle.

All 4,817 recorded answers (2,648 unique task/source pairs; 1,800 conversations)
were replayed, including excluded tasks. Saved tiers cover 317 answers; 4,500
use recorded feedback reconstruction. Unreached/null stages count as not accepted.
Later rounds followed SAP feedback: this is a per-answer comparison, not a replay
of model trajectories driven by OSG feedback. No new model or SAP execution was run.

Counted mutants: **673/679 killed (99.116%)**, versus 626/636 (98.428%) before. Four unobservable survivors for tasks 21 and 34 are now outside the denominator and listed as dataset weaknesses. All syntax-break and unknown-type mutants die in syntax. Remaining survivors: 40.txt (off_by_one), 43.txt (off_by_one), 69.txt (off_by_one), 147.txt (off_by_one), 153.txt (flipped_comparison), 160.txt (off_by_one). All six were later shown equivalent by reading the canonicals and are listed in `EQUIVALENT`.

Subset: **46 unique tasks**, versus 45 before. Take all eligible tasks in short categories: Date & Time 1/10 and Database 5/10; every other category has 10. Shortfalls are printed and informational.


## Adapter compatibility inventory

The adapter supplies clean-room objects in `tools/osg-validator/compat/` after
open-abap-core. The frozen library and all canonical/test sources are unchanged.
`compat-inventory.json` records exact task/answer references and occurrence
counts; regenerate the prior-calibration scan with
`python tools/osg-validator/inventory.py PATH_TO_PRIOR_REPLAY_JSON`.

| Object | Supplied contract | Corpus need |
|---|---|---|
| CL_AUNIT_ASSERT | ASSERT_EQUALS delegates ACT, EXP, MSG, TOL, QUIT and LEVEL to CL_ABAP_UNIT_ASSERT; also supports inheritance | Tests 6, 7, 10, 12, 16–18, 27–31. Only ASSERT_EQUALS is used, including inherited calls in task 7. No other classic methods were found in tests or recorded answers. |
| CHAR50 | DDIC CHAR, length 50 | ERP 008 canonical |
| CHAR100 | DDIC CHAR, length 100 | ERP 008 canonical |
| DATE_COMPUTE_DAY | DATE (DATS) to DAY (CHAR1), Monday=1 through Sunday=7, for valid Gregorian dates | ERP 007 canonical and recorded answers |
| QF05_RANDOM_INTEGER | RAN_INT_MIN / RAN_INT_MAX to inclusive random integer RAN_INT, for MIN < MAX | Task 53 unit tests. Equal/reversed bounds fail closed by assertion: their precise SAP exception semantics are outside the documented subset. |
| ZOSG_COMPAT | Local function group and two include scaffolds housing the preceding function modules | Adapter implementation container, no additional SAP API |

The weekday shim uses Gregorian ordinal arithmetic instead of the runtime's date
epoch conversion. The random shim uses the platform's uniform integer generator;
its sequence is deliberately not promised to reproduce SAP's PRNG. These legacy
entry points are compatibility for this corpus, **not evidence that SAP Cloud
releases them**. The five API/type objects are individually exercised in
`test.mjs`: each fails in unit syntax with `createEngine({compat:false})`, passes
with compatibility, and a changed expected value fails in unit execution.
Task 7 additionally exercises inherited ASSERT_EQUALS.

Public signature/behavior references (no SAP implementation source was used):

- [SAP assertion method semantics](https://help.sap.com/docs/SAP_NETWEAVER_AS_ABAP_752/ba879a6e2ea04d9bb94c7ccd7cdac446/49268dc67b6716b4e10000000a42189d.html) and [SAP assertion parameter documentation](https://learning.sap.com/courses/deepening-your-abap-programming-knowledge/implementing-code-tests-with-abap-unit_b23c7a00-c2e8-406d-8969-b00db3f1fd87).
- [Classic assertion usage on SAP Community](https://community.sap.com/t5/application-development-and-automation-blog-posts/abap-unit-test-initial-few-steps-before-the-sprint/ba-p/13407782).
- [SAP calendar function documentation](https://help.sap.com/docs/SAP_NETWEAVER_701/6d9532e26c4b10148b87b4b12a95ece6/48dfbc1aab14280fe10000000a42189c.html) and [weekday numbering](https://help.sap.com/docs/SUPPORT_CONTENT/abap/3353524296.html).
- [Random parameter usage on SAP Community](https://community.sap.com/t5/application-development-and-automation-blog-posts/a-comprehensive-guide-to-generating-random-numbers-in-sap/ba-p/13558581). This documents ordinary ranges, not a complete edge-case contract; unsupported ranges fail closed.
- [SAP API documentation using CHAR50 and CHAR100](https://help.sap.com/docs/SAP_TRANSPORTATION_RESOURCE_PLANNING/38b8eafc22cf44cfbe2e5eed5631b1b2/ee4dcb504ba94c9a99e059478e501e9e.html).

The scan also found HASH160 in task 162. CALCULATE_HASH_FOR_CHAR and HASH160 remain
unsupplied together: the located SAP-hosted examples do not establish the full
ALG/default, character encoding, fixed output and exception contract. Task 162
stays excluded rather than installing an inferred alias and a possibly incorrect
hash implementation. The [historical SAP Community discussion](https://community.sap.com/t5/technology-q-a/md5-functionality/qaq-p/917032)
illustrates why encoding and distinct legacy hashing interfaces matter.

Among prior false rejects, CL_AUNIT_ASSERT occurs in 146 unit-syntax and 120 unit
comparisons, QF05_RANDOM_INTEGER in 10 of each, and DATE_COMPUTE_DAY in three
syntax/activation comparisons. Counts overlap across cumulative stages. Other
frequent names are local TY_/TT_ flight result types (21, 20 and 11 early-stage
comparisons for different task families), whose unresolved types follow missing
flight schemas. They are not released SAP API names to stub. DAY_IN_WEEK appears
once; a complete Cloud-released legacy function contract was not established, so
it is not added. No additional frequent missing, documented Cloud-released API
was identified. Existing CHAR1/2/20/40/80 types already come from the pinned core.

## SQL language-version decision

Keep `Version.Cloud` and its strict host-variable escaping. SAP documents that
[ABAP for Cloud Development uses the strictest SQL syntax check](https://help.sap.com/docs/abap-cloud/abap-keywords/abap-language-versions).
[SAP's SQL host-variable documentation](https://help.sap.com/doc/abapdocu_latest_index_htm/latest/en-US/abapinsert_source.htm)
requires `@` in strict modes. There were no recorded SAP-syntax-accepted answers
with an unescaped SELECT in ERP 006, 011 or 014. No canonical SAP execution trace
was supplied that proves otherwise. Thus there is no evidence to justify weakening
the parser to Standard ABAP. ERP 006 remains excluded for its unescaped variables;
ERP 014 additionally requires the absent flight model. ERP 011's current first
failure is a SELECT parser error, not proof of an escaping mismatch; it also
references the absent flight model. No flight tables were added.

## OSG runtime gaps

Run `node tools/osg-validator/gaps.mjs` to reproduce the pinned failures. The
runner exits zero only when every diagnostic reaches its recorded failing stage;
these are intentionally failing ABAP programs, not passing canonical solutions.
Each source has a matching tiny ABAP Unit include; compact actual errors are in
`gaps-summary.json`.

| Gap | Minimal ABAP source under tools/osg-validator/gaps | Observed stage / impact |
|---|---|---|
| round with decimal places | z_gap_round.clas.abap | unit: `round(), todo, handle decimals`; task 71 |
| Arithmetic DO count | z_gap_do_count.clas.abap | unit: `DO length / 2 TIMES` runs zero times instead of four; tasks 73 and newly exposed task 10 |
| Reverse STEP -1 | z_gap_reverse_step.clas.abap | unit: returns zero rows instead of reversed three; task 132 |
| SQLite CHAR into STRING table | z_gap_char_padding.clas.abap | unit: `Ada` retains CHAR80 trailing spaces; ERP 001 |
| INSERT FROM TABLE VALUE # inference | z_gap_infer.clas.abap | activation: TypeNameOrInfer; ERP 009 and newly exposed ERP 007 tests |
| Unknown aggregate field | z_gap_aggregate.clas.abap | unit: unknown column reaches SQLite instead of syntax rejection |
| SQL UPPER predicate | z_gap_sql_upper.clas.abap | activation: SQL Condition todo2; newly exposed ERP 008 |

Task 20 remains a parameter-type compatibility exclusion, and task 32 remains an
unresolved numerical canonical/test discrepancy. These have not been proved to
be missing library APIs. Their original canonical/test pairs remain the evidence;
no compatibility object masks them.

## False accepts: observed causes and examples

False accepts can over-rate models and remain a material limitation. Categories
come from the earliest SAP rejection and propagate to later cumulative stages;
they are not four independent errors. A fixture discrepancy is still counted as
a false accept under the recorded oracle. Warning rejection reflects benchmark
handling, not proof that SAP rejected valid syntax. No release-whitelist parity
is claimed. The before and after compact summaries supply one representative feedback excerpt per
cause, plus up to 20 sequential examples per stage/direction.

| Cause (before → after) | syntax | activation | unit_syntax | unit |
|---|---:|---:|---:|---:|
| Runtime or assertion difference | 0 → 0 | 0 → 0 | 0 → 0 | 7 → 23 |
| SAP declaration or activation check | 4 → 4 | 4 → 4 | 4 → 4 | 0 → 0 |
| SAP fixture missing supplied dataset DDIC object | 267 → 283 | 237 → 237 | 295 → 314 | 104 → 104 |
| SAP name-resolution check | 4 → 4 | 4 → 4 | 4 → 4 | 3 → 3 |
| SAP type or conversion check | 2 → 2 | 2 → 2 | 164 → 164 | 114 → 114 |
| SAP warnings treated as rejection by benchmark | 29 → 29 | 29 → 29 | 10 → 10 | 8 → 8 |

Representative recorded answers (see summary for the actual feedback):

- `erp_000.txt:0:R0`: SAP fixture missing supplied dataset DDIC object.
- `116.txt:4:R0`: SAP warnings treated as rejection by benchmark.
- `116.txt:8:R0`: SAP type or conversion check.
- `erp_004.txt:7:R5`: SAP name-resolution check.
- `erp_008.txt:5:R4`: SAP declaration or activation check.
- `17.txt:1:R0`: Runtime or assertion difference.

False-reject cause counts with the current feedback (automated first-error heuristics;
wording changes can move buckets):

- syntax: DB difference: 104, missing library class/method/type or unresolved name: 25.
- activation: DB difference: 104, missing library class/method/type or unresolved name: 34, runtime or semantic difference (manual review needed): 1.
- unit_syntax: DB difference: 72, missing library class/method/type or unresolved name: 24.
- unit: DB difference: 45, missing library class/method/type or unresolved name: 6, runtime or semantic difference (manual review needed): 34.
