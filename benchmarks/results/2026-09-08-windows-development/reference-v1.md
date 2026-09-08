# MemSandbox Benchmark Report

- Schema: `1`
- Tier: `reference`
- Commit: `5e09afe4e382a9163092c31c68bf46bb13ccb1f2`
- Python: `3.12.12`
- Platform: `Windows-11-10.0.22631-SP0`

| Case | Driver | Profile | Samples | Median ms | Failures |
|---|---|---|---:|---:|---:|
| core_create_ready | direct | empty | 5 | 0.325 | 0 |
| create_first_operation | direct | empty | 5 | 0.651 | 0 |
| memory | direct | empty | 5 | 4.192 | 0 |
| seeded_create_ready | direct | small_project | 5 | 6.133 | 0 |
| create_first_operation | direct | small_project | 5 | 6.693 | 0 |
| snapshot_create | direct | small_project | 5 | 3.014 | 0 |
| resume_ready | direct | small_project | 5 | 3.220 | 0 |
| resume_first_operation | direct | small_project | 5 | 5.148 | 0 |
| memory | direct | small_project | 5 | 22.212 | 0 |
| burst_create | direct | empty | 5 | 1.012 | 0 |
| stateful_scenario | direct | empty | 5 | 23.332 | 0 |
| cold_process_ready | direct | empty | 2 | 11318.459 | 0 |
| adapter_create_ready | openai_sandbox | empty | 5 | 0.948 | 0 |
| create_first_operation | openai_sandbox | empty | 5 | 1.732 | 0 |
| memory | openai_sandbox | empty | 5 | 14.185 | 0 |
| seeded_create_ready | openai_sandbox | small_project | 5 | 11.883 | 0 |
| create_first_operation | openai_sandbox | small_project | 5 | 16.699 | 0 |
| snapshot_create | openai_sandbox | small_project | 5 | 6.284 | 0 |
| resume_ready | openai_sandbox | small_project | 5 | 19.972 | 0 |
| resume_first_operation | openai_sandbox | small_project | 5 | 21.337 | 0 |
| memory | openai_sandbox | small_project | 5 | 47.782 | 0 |
| burst_create | openai_sandbox | empty | 5 | 4.623 | 0 |
| stateful_scenario | openai_sandbox | empty | 5 | 51.248 | 0 |
| cold_process_ready | openai_sandbox | empty | 2 | 7816.167 | 0 |
| adapter_create_ready | openai_capability | empty | 5 | 4.693 | 0 |
| create_first_operation | openai_capability | empty | 5 | 7.146 | 0 |
| memory | openai_capability | empty | 5 | 75.369 | 0 |
| seeded_create_ready | openai_capability | small_project | 5 | 17.512 | 0 |
| create_first_operation | openai_capability | small_project | 5 | 17.415 | 0 |
| snapshot_create | openai_capability | small_project | 5 | 10.462 | 0 |
| resume_ready | openai_capability | small_project | 5 | 27.870 | 0 |
| resume_first_operation | openai_capability | small_project | 5 | 31.695 | 0 |
| memory | openai_capability | small_project | 5 | 68.382 | 0 |
| burst_create | openai_capability | empty | 5 | 16.314 | 0 |
| stateful_scenario | openai_capability | empty | 5 | 63.423 | 0 |
| cold_process_ready | openai_capability | empty | 2 | 9554.839 | 0 |
| backend_adapter_overhead | openai_sandbox | empty | 5 | 0.992 | 0 |
| capability_overhead | openai_capability | empty | 5 | 3.744 | 0 |
