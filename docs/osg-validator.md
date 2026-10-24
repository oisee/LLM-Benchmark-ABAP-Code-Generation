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
uv run python tools/osg-validator/audit.py gate
uv run python tools/osg-validator/audit.py calibrate
uv run python tools/osg-validator/audit.py subset
uv run python tools/osg-validator/throughput.py
```

The integration tests use only a loopback Python HTTP server. It returns a
canonical solution and then a broken solution through the existing direct OpenAI
generator. Other checks exercise syntax/type errors, wrong tables/fields, failed
assertions, fresh databases, an infinite loop, a memory allocation mutant, and
preservation of earlier rounds. No real model benchmark was run for this change.

`audit.py gate` exits nonzero when more than 10% of canonicals fail, a
syntax-break/unknown-type mutant survives, the overall kill rate is below 90%,
or any mutant fails at a different stage than expected. Baseline failures are
listed in `tools/osg-validator/excluded.json`; they are never hidden to make the
gate pass. Only passing canonicals provide meaningful mutation baselines.

Audits use three compiler workers by default (`--workers` changes this). Full
answer replays and a source-keyed SQLite cache remain under ignored `osg-results/`.
The tracked JSON files contain compact summaries and at most 20 examples per
stage and disagreement direction. The cache key covers engine source, the library pin, Python adapter, task,
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
  --subset tools/osg-validator/subset.json --repetitions 10 --rounds 6
```

This command performs model calls when explicitly invoked. It uses the existing
`ask_openai_direct`, system prompt, extraction, R0–R5 feedback and conversation/
tier formats. It refuses to overwrite an existing output. Validation and normal
analysis scripts can read the resulting files unchanged. An API key is passed
only to the model client; neither compiler nor generated code inherits model or
SAP credentials. The default runner key is a dummy for local test servers.

The subset parses the BOM-marked, semicolon-separated CSV with Python `csv`,
including quoted multiline fields. It excludes every red canonical, assigns a
multilabel task to its first category in the stored priority order, sorts and
shuffles candidates with seed 20261024, and never duplicates a task. The priority
maximizes available slots capped at ten per category, with deterministic CSV-order
ties. `shortfalls` records categories that cannot supply ten tasks. The source
CSV has only four date/time tasks even before exclusions; a full 60-task subset
is impossible without changing the dataset or labels.

## Toolchain, stages and isolation

Pins: `@abaplint/core` 2.120.55, `@abaplint/transpiler` 2.13.89,
`@abaplint/runtime` 2.13.89 and `@abaplint/database-sqlite` 2.13.83.
The bootstrap fetches `https://github.com/open-abap/open-abap-core.git` at
`8b397be863e805182c32e4bbb8bedbd291619f58`, never a floating branch.
The package lock tracks transitive dependencies. The bootstrap also prepares the
immutable compiled library before parallel workers start.

Each Node compiler keeps a warm abaplint registry containing library dependencies
and the 13 converted tables. Only the current answer's class and test include
are installed; they are removed before the next answer. Immutable library output
is compiled once and cached. Syntax uses `Version.Cloud`, error namespace `.`,
and rules `parser_error`, `check_syntax`, `unknown_types`, `implement_methods`,
`begin_end_names`. Class syntax is checked first, then class transpilation, test
syntax, test transpilation and ABAP Unit execution. The final activation verdict
also becomes false if test transpilation fails. Class creation/source setting are
local registry operations rather than ADT operations.

All generated answer code executes in a fresh Node child, with a new ABAP runtime
and in-memory SQLite database. Compilation/parsing stays in the supervised warm
worker and never evaluates generated code. The parent enforces a 30-second total
answer deadline, kills the process group on timeout, and records a unit failure
with a timeout message. Linux resident-memory watchdogs cap the compiler at
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
the reverse. Cause labels are automated heuristics from the first OSG error, not
independent proof of the underlying cause. Unclassified runtime/semantic cases
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

Canonical gate: **150/180 pass; 30/180 excluded (16.67%)**, exceeding the 10% limit. Mutants: **626/636 killed (98.43%)**. All syntax-break and unknown-type mutants fail in syntax. 10 behavioral mutants survive, so the stronger expected-stage check also remains red. The CLI correctly exits **1**.

| Kind | Killed / total | Expected stage hits |
|---|---:|---:|
| syntax_break | 150/150 | 150/150 (syntax) |
| unknown_type | 150/150 | 150/150 (syntax) |
| flipped_comparison | 121/124 | 121/124 (unit) |
| result_not_assigned | 148/150 | 148/150 (unit) |
| off_by_one | 49/54 | 49/54 (unit) |
| wrong_table | 5/5 | 5/5 (syntax) |
| wrong_field | 3/3 | 3/3 (syntax) |

Survivors: 21.txt (flipped_comparison), 21.txt (result_not_assigned), 34.txt (flipped_comparison), 34.txt (result_not_assigned), 40.txt (off_by_one), 43.txt (off_by_one), 69.txt (off_by_one), 147.txt (off_by_one), 153.txt (flipped_comparison), 160.txt (off_by_one). Task 21 checks values only while iterating over returned rows and omits a cardinality assertion; task 34 contains no assertion at all. Both accept an empty result. Other survivors may be equivalent mutations or insufficient test coverage; they are reported without changing the tests.

Calibration: **4,817 recorded answers, 2,648 unique task/source pairs, 1,800 conversations**. The table uses cumulative stage acceptance; null/unreached is not accepted.

| Stage | Agreement | FALSE REJECT | FALSE ACCEPT |
|---|---:|---:|---:|
| syntax | 90.907% | 132 | 306 |
| activation | 91.385% | 139 | 276 |
| unit_syntax | 84.866% | 252 | 477 |
| unit | 90.637% | 215 | 236 |

| Oracle/backend | R0 unit pass | Best R0–R5 unit pass |
|---|---:|---:|
| SAP | 665/1800 (36.944%) | 1515/1800 (84.167%) |
| OSG | 672/1800 (37.333%) | 1344/1800 (74.667%) |

SAP sources: 317 matching tier entries and 4,500 feedback reconstructions. Recorded-tiers-only comparisons: syntax 276/317 agreement (14 false rejects, 27 false accepts); activation 142/157 (15, 0); unit 79/84 (5, 0). Unit syntax is not separately stored in original tiers. These denominators include only observed saved fields.

False-reject cause counts (automated heuristics):

- syntax: DB difference: 104, missing library class/method/type or unresolved name: 28
- activation: DB difference: 104, missing library class/method/type or unresolved name: 34, runtime or semantic difference (manual review needed): 1
- unit_syntax: DB difference: 72, missing library class/method/type or unresolved name: 180
- unit: DB difference: 45, missing library class/method/type or unresolved name: 136, runtime or semantic difference (manual review needed): 34

All 25 timeout answers (16 unique sources) were rechecked without competing audits/tests; their stages and unit verdicts were unchanged. None of these timeout answers was SAP-unit-accepted. Later rounds came after SAP feedback, so these are per-answer results, not OSG-driven trajectories.

Uncached warm throughput: **1.3742 answers/s** with one worker, 30 fresh runtime/database executions across five regular and five ERP tasks, all passing. Median 0.7188 s; maximum 0.8113 s; first answer including compiler startup 3.8086 s. Immutable compiled library cache was present; verdict cache was not used. Cached gate/replay timings are not uncached throughput measurements.

Subset: **45 unique eligible tasks**, with these unavoidable shortfalls under first-label assignment:

- Date & Time Operations: 1/10
- String Handling: 10/10
- Database Operations (ABAP): 5/10
- List & Array Operations: 10/10
- Logical Checks: 10/10
- Mathematical Calculations: 9/10

The dataset contains only four date/time-labelled tasks before exclusions, and only one survives the canonical gate. Thus ten per category cannot be supplied honestly.

Pinned-toolchain/dataset gaps are listed below; no frozen toolchain, library or canonical solution was changed.

| Task | Failed stage | Observed reason |
|---|---|---|
| 6.txt | unit_syntax | CL_AUNIT_ASSERT missing from the pinned library. |
| 7.txt | unit_syntax | CL_AUNIT_ASSERT missing from the pinned library. |
| 10.txt | unit_syntax | CL_AUNIT_ASSERT missing from the pinned library. |
| 12.txt | unit_syntax | CL_AUNIT_ASSERT missing from the pinned library. |
| 16.txt | unit_syntax | CL_AUNIT_ASSERT missing from the pinned library. |
| 17.txt | unit_syntax | CL_AUNIT_ASSERT missing from the pinned library. |
| 18.txt | unit_syntax | CL_AUNIT_ASSERT missing from the pinned library. |
| 20.txt | unit_syntax | NUMBERS parameter type rejected as incompatible in unit-test syntax. |
| 27.txt | unit_syntax | CL_AUNIT_ASSERT missing from the pinned library. |
| 28.txt | unit_syntax | CL_AUNIT_ASSERT missing from the pinned library. |
| 29.txt | unit_syntax | CL_AUNIT_ASSERT missing from the pinned library. |
| 30.txt | unit_syntax | CL_AUNIT_ASSERT missing from the pinned library. |
| 31.txt | unit_syntax | CL_AUNIT_ASSERT missing from the pinned library. |
| 32.txt | unit | test_case_1 assert_true fails in the numerical root-finding test; cause needs semantic review. |
| 53.txt | unit_syntax | QF05_RANDOM_INTEGER function module missing/not released. |
| 71.txt | unit | Runtime round() raises: todo, handle decimals. |
| 73.txt | unit | test_case_1 returns 0; expected 4 (arithmetic DO count behavior needs review). |
| 132.txt | unit | test_case_1 returns false; expected true; reverse STEP -1 loop behavior needs review. |
| 162.txt | syntax | CALCULATE_HASH_FOR_CHAR missing/not released; HASH160 type missing. |
| erp_001.txt | unit | SQLite result retains CHAR padding: employee name has trailing spaces; expected unpadded string. |
| erp_005.txt | syntax | Canonical references ZFS_DATA, absent from the supplied 13 tables (DDL supplies ZFNET_DATA). |
| erp_006.txt | syntax | Strict SELECT syntax requires @ escaping for MAT_ID at lines 28 and 39. |
| erp_007.txt | syntax | DATE_COMPUTE_DAY function module missing/not released. |
| erp_008.txt | syntax | CHAR50 and CHAR100 DDIC types missing from the pinned library. |
| erp_009.txt | activation | Test transpilation cannot infer # at test line 42 (TypeNameOrInfer). |
| erp_011.txt | syntax | Configured parser rejects SELECT at canonical line 37. |
| erp_012.txt | syntax | SCARR and SBOOK tables/types absent from supplied schemas/library. |
| erp_013.txt | syntax | SFLIGHT table/types absent from supplied schemas/library. |
| erp_014.txt | syntax | SBOOK absent; IV_CARRID, IV_CONNID and IV_FLDATE also fail strict @ escaping. |
| erp_015.txt | syntax | SFLIGHT and SPFLI tables/types absent from supplied schemas/library. |

Additional observed gaps: unknown SQL aggregate fields reach SQLite rather than syntax rejection; Cloud/released-API coverage differs from SAP; arithmetic DO counts, reverse loops and numerical assertions require semantic review; some unit tests do not reject empty results.
