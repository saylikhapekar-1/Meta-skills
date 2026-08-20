# Meta Agent Skills

Three skills that turn an agent brief into a validated, testable v1 agent design.

```
agent_brief (name + task + infra)
        │
        ▼
  agent-discovery  ──▶  discovery_report.json
        │
        ▼
  agent-architect  ──▶  agent_spec.json  ──▶  validate_spec.py (7 checks)
        │                                              │
        │                                        must pass before runtime
        ▼
  agent-evaluator  ──▶  evals.json + coverage report
```

The generated spec then populates the fixed production workflow:

```
incident → investigate → decide → human_approval → act
```

## Layout

The three skills live under `.claude/skills/`, so Claude Code picks them up automatically in this project:

```
.claude/skills/
├── agent-discovery/
│   └── SKILL.md
├── agent-architect/
│   ├── SKILL.md
│   ├── references/
│   │   ├── agent_spec_schema.md      # the schema a spec is written against
│   │   └── agent_spec_good.json      # worked example (park-incident-ops)
│   └── scripts/
│       └── validate_spec.py          # the seven checks
└── agent-evaluator/
    └── SKILL.md
```

Paths matter here: `agent-architect/SKILL.md` refers to `references/agent_spec_schema.md` and `scripts/validate_spec.py` relative to its own directory, so the two subfolders are not cosmetic.

## The MCP / Skills boundary

These skills contain **no company facts**. That is the whole design.

- **Skills** = how the agent should think. Methodology. Portable across every domain and every client.
- **MCP** = what is true about this specific company. Safety policies, technician rosters, escalation routes, SOPs, entity schemas.

The boundary erodes in one predictable way: examples. A skill that illustrates a point with "check the weather hold before dispatching a technician" has quietly absorbed a theme-park fact. That is fine as illustration and harmful as instruction. Keep examples clearly marked as illustrative, and keep the *rule* domain-neutral.

Safety policies are the genuinely awkward case. The content of a policy is company-specific and belongs in MCP. "Always evaluate the governing policy before proposing a high-risk action" is methodology and belongs in a skill. Splitting them correctly is what lets you carry these three skills to the next engagement unchanged.

## Input contract

The pipeline expects a brief:

```json
{
  "agent_name": "",
  "task": "",
  "infra": {
    "mcp_servers": [{"name": "", "tools": [{"id": "", "kind": "read|write|execute", "args": {}, "returns": {}}]}],
    "data_stores": [],
    "identity_provider": "",
    "framework": "",
    "model_access": [],
    "deployment": {"environment": "", "egress": "", "data_residency": "", "pii_handling": ""}
  },
  "supporting_docs": []
}
```

The `infra.mcp_servers[].tools` list doubles as the **tool registry** the validator checks against. If it is missing, check 1 cannot run and nothing downstream is trustworthy — a spec that binds to invented tools is exactly what these checks exist to catch.

## Running the validator

```bash
python .claude/skills/agent-architect/scripts/validate_spec.py agent_spec.json \
    --registry infra_tools.json \
    --discovery discovery_report.json
```

Exit code 0 means all seven checks passed and `status` may be promoted to `validated`. Non-zero means do not run the spec.

The `--discovery` flag is optional but strongly recommended: without it, check 5 cannot detect a hard constraint that discovery found and the architect silently dropped, which is a failure mode no other check catches.

## What each check defends against

| # | Check | Prevents |
|---|-------|----------|
| 1 | Tool binding | Hallucinated tools, drifted signatures |
| 2 | Evidence closure | Decisions made on fields nothing produces |
| 3 | Write gating | Writes during investigation; ungated irreversible actions |
| 4 | Risk tier coverage | Actions with no approval policy |
| 5 | Constraint enforceability | Safety rules that live only in a prompt |
| 6 | Reasoning contract | Free-text decisions that cannot be queried or gated |
| 7 | Termination and budget | Unbounded loops; fallbacks that write |

