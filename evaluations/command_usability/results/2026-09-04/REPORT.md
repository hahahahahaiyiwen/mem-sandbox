# Command Usability Evaluation Results

The versioned corpus was executed through public `SandboxSession` tools. Model/provider measurements are observational and are not CI gates.

| Model | Sample | Completed | First parser/registry acceptance | Tool calls | Repair turns | Input tokens | Output tokens | Provider latency (ms) | Tool output bytes | Truncated tasks |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| gpt-5.6-sol | 1 | 12/12 | 11/12 | 15 | 1 | 86975 | 1421 | 22299 | 456 | 0 |
| gpt-5.6-sol | 2 | 12/12 | 12/12 | 13 | 0 | 64309 | 1025 | 25975 | 364 | 0 |
| claude-sonnet-5 | 1 | 12/12 | 12/12 | 14 | 0 | 103788 | 1772 | 16258 | 477 | 0 |
| claude-sonnet-5 | 2 | 12/12 | 11/12 | 16 | 1 | 285049 | 2871 | 36459 | 488 | 0 |
| gemini-3.7-flash | 1 | 12/12 | 12/12 | 16 | 0 | 113897 | 869 | 13923 | 488 | 0 |
| gemini-3.7-flash | 2 | 12/12 | 12/12 | 17 | 0 | 90463 | 961 | 12214 | 621 | 0 |

## Remaining gaps

- Every sampled task completed within the configured turn budget.

Provider token and latency totals are recorded per multi-task model session. Task-level metrics cover sandbox calls, repair turns, output size, and truncation. See `results.json` and each sample transcript for raw evidence.
