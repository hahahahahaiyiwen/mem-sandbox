# MemSandbox Benchmark Report

- Schema: `2`
- Tier: `scalability`
- Commit: `7a07a0b13514477d5640dbc85c0f0fc596d9d233`
- Source tree: `4c24f9a0321a32bf2f7083d5c5a325e0720f6aee7d7370a24f5e2f947a6aa688`
- Working tree dirty: `true`
- Started: `2026-09-09T20:13:19.180016+00:00`
- Python: `3.12.12`
- Platform: `Windows-11-10.0.22631-SP0`
- CPU: `Intel64 Family 6 Model 106 Stepping 6, GenuineIntel`
- Logical CPUs: `16`
- Runner: `windows-development-workstation`
- Power: `uncontrolled-development-workstation`
- Sampling: `2` warm-ups, `5` measured samples

| Case | Driver | Profile | Operation files | Operation KiB | Snapshot KiB | Max retained KiB | Max peak KiB | Samples | Median ms | Failures |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| workspace_seed | memory_workspace | active_project | 128 | 512.0 |  |  |  | 5 | 246.661 | 0 |
| workspace_read_hot | memory_workspace | active_project | 1 | 4.0 |  |  |  | 5 | 0.036 | 0 |
| workspace_overwrite_hot | memory_workspace | active_project | 1 | 4.0 |  |  |  | 5 | 3.437 | 0 |
| workspace_copy_directory | memory_workspace | active_project | 8 | 32.0 |  |  |  | 5 | 5.500 | 0 |
| workspace_snapshot_encode | memory_workspace | active_project | 128 | 512.0 | 703.9 |  |  | 5 | 16.662 | 0 |
| workspace_retained_memory | memory_workspace | active_project | 128 | 512.0 |  | 535.5 | 612.5 | 5 | 1261.992 | 0 |
| workspace_seed | memory_workspace | quota_edge | 256 | 8192.0 |  |  |  | 5 | 2409.097 | 0 |
| workspace_read_hot | memory_workspace | quota_edge | 1 | 32.0 |  |  |  | 5 | 0.071 | 0 |
| workspace_overwrite_hot | memory_workspace | quota_edge | 1 | 32.0 |  |  |  | 5 | 17.004 | 0 |
| workspace_copy_directory | memory_workspace | quota_edge | 8 | 256.0 |  |  |  | 5 | 16.282 | 0 |
| workspace_snapshot_encode | memory_workspace | quota_edge | 256 | 8192.0 | 10964.5 |  |  | 5 | 289.893 | 0 |
| workspace_retained_memory | memory_workspace | quota_edge | 256 | 8192.0 |  | 8237.4 | 8388.8 | 5 | 6625.813 | 0 |
| workspace_seed | memory_workspace | content_heavy | 128 | 8192.0 |  |  |  | 5 | 835.726 | 0 |
| workspace_read_hot | memory_workspace | content_heavy | 1 | 64.0 |  |  |  | 5 | 0.107 | 0 |
| workspace_overwrite_hot | memory_workspace | content_heavy | 1 | 64.0 |  |  |  | 5 | 13.017 | 0 |
| workspace_copy_directory | memory_workspace | content_heavy | 8 | 512.0 |  |  |  | 5 | 12.313 | 0 |
| workspace_snapshot_encode | memory_workspace | content_heavy | 128 | 8192.0 | 10943.9 |  |  | 5 | 366.948 | 0 |
| workspace_retained_memory | memory_workspace | content_heavy | 128 | 8192.0 |  | 8215.5 | 8292.5 | 5 | 1645.715 | 0 |
| workspace_seed | memory_workspace | node_heavy | 512 | 512.0 |  |  |  | 5 | 3859.589 | 0 |
| workspace_read_hot | memory_workspace | node_heavy | 1 | 1.0 |  |  |  | 5 | 0.036 | 0 |
| workspace_overwrite_hot | memory_workspace | node_heavy | 1 | 1.0 |  |  |  | 5 | 16.912 | 0 |
| workspace_copy_directory | memory_workspace | node_heavy | 8 | 8.0 |  |  |  | 5 | 19.033 | 0 |
| workspace_snapshot_encode | memory_workspace | node_heavy | 512 | 512.0 | 766.8 |  |  | 5 | 26.963 | 0 |
| workspace_retained_memory | memory_workspace | node_heavy | 512 | 512.0 |  | 601.1 | 895.7 | 5 | 20959.804 | 0 |
