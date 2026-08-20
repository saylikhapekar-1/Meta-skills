# agent_spec schema

The declarative blueprint that populates the fixed workflow graph. Every field below is read either by the validator (`scripts/validate_spec.py`) or by the runtime that instantiates the nodes.

## Contents

- Top level
- `tools`
- `nodes.incident`
- `nodes.investigate`
- `nodes.decide`
- `nodes.human_approval`
- `nodes.act`
- `risk_policy`
- `constraints`
- `budgets`
- `observability`
- Full example

## Top level

```json
{
  "spec_version": "1.0",
  "agent_name": "",
  "status": "draft | validated",
  "derived_from": "discovery_report.json",
  "generated_at": "",
  "open_questions_outstanding": [],
  "tools": [],
  "nodes": {},
  "risk_policy": {},
  "constraints": [],
  "budgets": {},
  "observability": {}
}
```

`status` may only be `validated` when all seven checks pass. The runtime refuses to load a spec that is not `validated`.

## tools

Every tool the spec may reference, bound to the registry supplied in `infra`.

```json
{
  "id": "check_weather_hold",
  "server": "park-ops-mcp",
  "kind": "read | write | execute",
  "args": {"park_zone": "string"},
  "returns": {"weather_hold_active": "boolean", "since": "timestamp"},
  "reversible": true,
  "timeout_ms": 3000
}
```

`kind` drives check 3. `args` and `returns` drive checks 1 and 2. A tool absent from the registry is a hard failure, not a warning.

## nodes.incident

```json
{
  "input_schema": {"incident_id": "string", "zone": "string", "reported_by": "string", "description": "string"},
  "required_fields": ["incident_id", "zone", "description"],
  "on_malformed": "reject_with_reason"
}
```

## nodes.investigate

Ordered or parallel read-only steps. Each declares the evidence fields it produces.

```json
{
  "mode": "sequential | parallel",
  "steps": [
    {
      "id": "s1",
      "tool": "check_weather_hold",
      "args": {"park_zone": "$incident.zone"},
      "produces": ["weather_hold_active"],
      "on_failure": "retry_then_gap | skip_with_gap | abort",
      "max_retries": 2
    }
  ],
  "evidence_gaps_visible_to_decide": true
}
```

`produces` names must be unique across steps. A field a step produces is what a decide-node input may reference — that link is check 2.

## nodes.decide

The structured reasoning contract. This replaces hidden chain-of-thought.

```json
{
  "options": ["dispatch_technician", "close_as_no_action", "escalate_to_supervisor"],
  "output_schema": {
    "chosen_option": {"type": "enum", "values": "$options", "required": true},
    "supporting_evidence": {"type": "array[evidence_ref]", "required": true},
    "constraints_checked": {"type": "array[constraint_eval]", "required": true},
    "confidence": {"type": "float", "range": [0, 1], "required": true},
    "rejected_alternatives": {"type": "array[{option, reason}]", "required": true}
  },
  "inputs": ["weather_hold_active", "technician_available", "safety_policy_tier"],
  "confidence_threshold_for_autonomy": 0.85
}
```

`options` must be a closed set. `inputs` must all appear in some investigate step's `produces`.

## nodes.human_approval

```json
{
  "triggers": {
    "risk_tiers": ["high"],
    "below_confidence": 0.85,
    "on_evidence_gap": true,
    "on_constraint_unevaluable": true
  },
  "reviewer": {"role": "shift_supervisor", "channel": "ops_queue"},
  "payload": ["incident", "reasoning_summary", "evidence", "confidence", "trigger_reason"],
  "while_waiting": "hold_open | default_output | route_to_manual",
  "timeout_minutes": 15,
  "on_timeout": "route_to_manual",
  "decision_logged_as": {"reviewer_id": "", "verdict": "approve|reject|modify", "reason": "", "latency_ms": 0}
}
```

`decision_logged_as` is what makes override rates queryable later — it is how you find out the agent is wrong in a way nobody reported.

## nodes.act

```json
{
  "actions": [
    {
      "id": "dispatch_technician",
      "tool": "create_dispatch",
      "risk_tier": "high",
      "reversible": false,
      "preconditions": ["weather_hold_active == false", "technician_available == true"],
      "rollback": null,
      "on_failure": "escalate"
    }
  ]
}
```

`preconditions` are the enforcement point for constraints — check 5 verifies each hard constraint lands here. `rollback: null` on an irreversible action is fine and honest; what is not fine is an irreversible action without an approval gate.

## risk_policy

```json
{
  "tiers": {
    "low": {"route": "autonomous"},
    "medium": {"route": "autonomous_if_confidence_above", "threshold": 0.9},
    "high": {"route": "approval_required"},
    "forbidden": {"route": "never"}
  }
}
```

Check 4 verifies every action maps to exactly one tier and every tier has a route.

## constraints

Carried forward from discovery, now bound.

```json
{
  "id": "c1",
  "statement": "No technician dispatch during an active weather hold",
  "type": "hard",
  "predicate": "weather_hold_active == false",
  "evaluated_by": "check_weather_hold",
  "blocks_actions": ["dispatch_technician"],
  "on_unevaluable": "escalate_to_human"
}
```

`on_unevaluable` matters more than it looks. When the tool that evaluates a hard constraint is down, the safe default is escalation, never assumption.

## budgets

```json
{
  "max_investigate_steps": 8,
  "max_tokens_per_session": 60000,
  "wall_clock_timeout_s": 120,
  "on_step_limit": "escalate_to_human",
  "on_token_limit": "escalate_to_human",
  "on_timeout": "escalate_to_human"
}
```

Check 7 verifies all three ceilings exist, each has a fallback, and no fallback performs an ungated write.

## observability

```json
{
  "sink": "sqlite",
  "tables": {
    "sessions": ["session_id", "agent_name", "spec_version", "started_at", "ended_at", "outcome", "total_tokens", "total_latency_ms"],
    "steps": ["session_id", "step_index", "node", "tool", "args_json", "result_status", "latency_ms", "tokens"],
    "decisions": ["session_id", "chosen_option", "confidence", "supporting_evidence_json", "constraints_checked_json", "rejected_alternatives_json"],
    "approvals": ["session_id", "reviewer_id", "verdict", "reason", "latency_ms"],
    "errors": ["session_id", "step_index", "error_type", "message", "retry_count"]
  }
}
```

Structured at write time is the whole point: override rates, constraint-block frequency, per-tool latency, and confidence calibration all become single SQL queries rather than log parsing.

## Note on what the reasoning contract does and does not give you

The decide node produces a *stated rationale*, not a trace of the computation. It is strong evidence for audit, compliance, and ops review, and it makes human override analysis possible. It is weaker evidence for diagnosing *why* a model got something wrong, because a model's account of its own decision is not guaranteed to be faithful to the process that produced it. Use it for the first purpose confidently; treat the second with more caution and lean on the step-level trace instead.
