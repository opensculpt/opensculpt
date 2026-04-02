# Community Evolution Contributions

This directory contains evolved code and learnings shared by OpenSculpt instances worldwide.

When an instance evolves a useful tool, strategy, or resolution, it can share it back here via GitHub PR. Other instances pull these contributions and sandbox-validate them before use.

## Structure

```
community/
├── contributions/    # Metadata JSON per instance (strategies, scores, archive)
│   └── {instance_id}.json
├── evolved/          # Evolved Python code per instance
│   └── {instance_id}/
│       └── *.py
└── README.md
```

## How It Works

1. Your OpenSculpt instance evolves locally (demands → solver → tools/strategies)
2. You set a GitHub token in Settings and enable auto-share
3. Your instance forks `opensculpt/opensculpt`, commits learnings, opens a PR
4. Maintainers review and merge
5. Other instances pull merged contributions on next boot
6. All community code is **sandbox-validated** (static analysis + subprocess isolation) before loading

## Reciprocity

- **Contributors** (GitHub token + auto-share enabled): get real-time access to all contributions
- **Non-contributors**: get weekly bundled contributions (7-day delay)

## Safety

All community code passes through two validation gates:
1. **Static analysis** — blocks dangerous imports (`os`, `subprocess`, `socket`, etc.)
2. **Subprocess sandbox** — executes in an isolated process with timeout

Code that fails either gate is rejected and logged.
