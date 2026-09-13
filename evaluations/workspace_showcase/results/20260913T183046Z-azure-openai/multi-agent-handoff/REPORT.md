# Workspace Showcase Live Evaluation

- Schema: `1`
- Outcome: `succeeded`
- Provider: `azure_openai`
- Model/deployment: `gpt-5.4-mini-global`
- Scenario: `multi-agent-handoff`
- Fixture version: `1`
- Started (UTC): `2026-09-13T18:31:49.403118+00:00`
- Approval reference: `https://github.com/hahahahahaiyiwen/mem-sandbox/issues/76#issuecomment-5655176089`
- Verification: `3/3 matched`
- Unsupported interactions: `0`
- Repair interactions: `1`

## Versions

- mem-sandbox: `0.2.0`
- mem-sandbox-openai-agents: `0.1.0`
- openai-agents: `0.22.0`
- openai: `3.8.0`
- Python: `3.12.12`
- OS: `Windows-11-10.0.22631-SP0`
- Architecture: `AMD64`

## Stage outcomes

| Sequence | Stage | Outcome |
|---:|---|---|
| 1 | multi-agent-plan | succeeded |
| 2 | multi-agent-implement | succeeded |
| 3 | multi-agent-review | succeeded |

## Verification by check

| Check | Artifacts | Matched |
|---|---:|---:|
| output_correctness | 2 | 2 |
| unrelated_content_preservation | 0 | 0 |
| evidence_accuracy | 1 | 1 |

## Artifact verification

| Path | Check kinds | Match state | Stable code |
|---|---|---|---|
| /workspace/app.conf | output_correctness | matched | none |
| /workspace/handoff/plan.txt | output_correctness | matched | none |
| /workspace/handoff/review.txt | evidence_accuracy | matched | none |

## Tool interactions

| Sequence | Stage | Tool | Outcome | Stable code | Unsupported | Repair of | Duration ns |
|---:|---|---|---|---|---|---:|---:|
| 1 | multi-agent-plan | read_file | succeeded | none | no | none | 790800 |
| 2 | multi-agent-plan | write_file | succeeded | none | no | none | 1143300 |
| 3 | multi-agent-implement | read_file | succeeded | none | no | none | 1147300 |
| 4 | multi-agent-implement | read_file | succeeded | none | no | none | 1327800 |
| 5 | multi-agent-implement | apply_patch | failed | invalid_patch | no | none | 1192000 |
| 6 | multi-agent-implement | apply_patch | failed | invalid_patch | no | none | 815000 |
| 7 | multi-agent-implement | apply_patch | succeeded | none | no | 6 | 858200 |
| 8 | multi-agent-review | read_file | succeeded | none | no | none | 1125100 |
| 9 | multi-agent-review | read_file | succeeded | none | no | none | 1285300 |
| 10 | multi-agent-review | write_file | succeeded | none | no | none | 945300 |

## Timing by category

| Category | Samples | Total ns |
|---|---:|---:|
| workspace_seed | 1 | 1926200 |
| workspace_read | 5 | 5676300 |
| workspace_mutation | 5 | 4953800 |
| workspace_execute | 0 | 0 |
| snapshot_persist | 0 | 0 |
| snapshot_restore | 0 | 0 |
| host_verification | 2 | 1564000 |
| cleanup | 4 | 4005600 |
| provider_model | 11 | 17750388100 |
| end_to_end | 1 | 18010502700 |

## Usage

- Model calls: `11`
- Token completeness: `complete`
- Input tokens: `50204`
- Output tokens: `828`
- Total tokens: `51032`
- Cost completeness: `unavailable`
- Provider-reported cost: `unavailable`

## Failure attribution

- Failure attribution: `none`

## Limitations

- This result is non-deterministic.
- This result is non-gating.
- This result is provider- and machine-specific.
- This result is not directly comparable across uncontrolled runs.
