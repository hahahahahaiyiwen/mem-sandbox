# Workspace Showcase Live Evaluation

- Schema: `1`
- Outcome: `failed`
- Provider: `azure_openai`
- Model/deployment: `gpt-5.4-mini-global`
- Scenario: `document-review`
- Fixture version: `1`
- Started (UTC): `2026-09-13T18:30:59.998894+00:00`
- Approval reference: `https://github.com/hahahahahaiyiwen/mem-sandbox/issues/76#issuecomment-5655176089`
- Verification: `0/4 matched`
- Unsupported interactions: `0`
- Repair interactions: `0`

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
| 1 | document-review-editor | succeeded |
| 2 | document-review-reviewer | succeeded |

## Verification by check

| Check | Artifacts | Matched |
|---|---:|---:|
| output_correctness | 1 | 0 |
| unrelated_content_preservation | 3 | 0 |
| evidence_accuracy | 1 | 0 |

## Artifact verification

| Path | Check kinds | Match state | Stable code |
|---|---|---|---|
| /workspace/source/release-brief.txt | unrelated_content_preservation | unmatched | verification_incomplete |
| /workspace/review/instructions.md | unrelated_content_preservation | unmatched | verification_incomplete |
| /workspace/drafts/release-notes.md | output_correctness, unrelated_content_preservation | unmatched | verification_incomplete |
| /workspace/review/findings.md | evidence_accuracy | unmatched | verification_incomplete |

## Tool interactions

| Sequence | Stage | Tool | Outcome | Stable code | Unsupported | Repair of | Duration ns |
|---:|---|---|---|---|---|---:|---:|
| 1 | document-review-editor | read_file | succeeded | none | no | none | 2602400 |
| 2 | document-review-editor | read_file | succeeded | none | no | none | 1383400 |
| 3 | document-review-editor | read_file | succeeded | none | no | none | 2388700 |
| 4 | document-review-editor | apply_patch | failed | invalid_patch | no | none | 1499100 |
| 5 | document-review-editor | apply_patch | failed | invalid_patch | no | none | 545300 |
| 6 | document-review-editor | execute | failed | session_policy_denied | no | none | 841600 |
| 7 | document-review-editor | apply_patch | failed | invalid_patch | no | none | 939500 |
| 8 | document-review-reviewer | read_file | succeeded | none | no | none | 1387800 |
| 9 | document-review-reviewer | read_file | succeeded | none | no | none | 1460000 |
| 10 | document-review-reviewer | read_file | succeeded | none | no | none | 2517400 |
| 11 | document-review-reviewer | write_file | succeeded | none | no | none | 4172600 |
| 12 | document-review-reviewer | read_file | succeeded | none | no | none | 784500 |

## Timing by category

| Category | Samples | Total ns |
|---|---:|---:|
| workspace_seed | 1 | 9682600 |
| workspace_read | 7 | 12524200 |
| workspace_mutation | 4 | 7156500 |
| workspace_execute | 1 | 841600 |
| snapshot_persist | 0 | 0 |
| snapshot_restore | 0 | 0 |
| host_verification | 1 | 2214600 |
| cleanup | 4 | 7766500 |
| provider_model | 10 | 33720170900 |
| end_to_end | 1 | 34239156700 |

## Usage

- Model calls: `10`
- Token completeness: `complete`
- Input tokens: `53722`
- Output tokens: `1534`
- Total tokens: `55256`
- Cost completeness: `unavailable`
- Provider-reported cost: `unavailable`

## Failure attribution

- Failure attribution: `host_verification`
- Exception type: `ScenarioVerificationError`
- Stage: `none`
- Stable code: `scenario_verification_failed`

## Limitations

- This result is non-deterministic.
- This result is non-gating.
- This result is provider- and machine-specific.
- This result is not directly comparable across uncontrolled runs.
