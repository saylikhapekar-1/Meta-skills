#!/usr/bin/env python3
"""
Deterministic pre-runtime validation of an agent_spec.

No model calls. No heuristics. Seven static checks that either pass or fail.

Usage:
    python validate_spec.py agent_spec.json --registry infra_tools.json \
        [--discovery discovery_report.json] [--json]

Exit code 0 = all checks passed, spec may be promoted to status "validated".
Exit code 1 = at least one check failed. Do not run this spec.
"""

import argparse
import json
import re
import sys
from typing import Any


class Result:
    def __init__(self, number: int, name: str):
        self.number = number
        self.name = name
        self.failures: list[str] = []
        self.notes: list[str] = []

    def fail(self, msg: str) -> None:
        self.failures.append(msg)

    def note(self, msg: str) -> None:
        self.notes.append(msg)

    @property
    def passed(self) -> bool:
        return not self.failures


IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
LITERALS = {"true", "false", "null", "and", "or", "not", "in", "is"}


def predicate_identifiers(pred: str) -> set[str]:
    """Pull candidate field names out of a predicate string.

    Quoted string literals are stripped first — 'tier_1' on the right-hand side
    of a comparison is a value, not a field, and treating it as one produces a
    false failure on check 5.
    """
    found = set()
    for token in IDENT.findall(QUOTED.sub(" ", pred or "")):
        if token.lower() in LITERALS:
            continue
        if token.replace(".", "").isdigit():
            continue
        found.add(token.split(".")[0])
    return found


# ---------------------------------------------------------------- check 1

def check_tool_binding(spec: dict, registry: dict) -> Result:
    """Every referenced tool exists in the registry with matching arg names."""
    r = Result(1, "Tool binding")
    reg_tools = {t["id"]: t for t in registry.get("tools", [])}
    if not reg_tools:
        r.fail("tool registry is empty — cannot verify any binding")
        return r

    for tool in spec.get("tools", []):
        tid = tool.get("id")
        if tid not in reg_tools:
            r.fail(f"spec declares tool '{tid}' which is not in the registry (hallucinated tool)")
            continue
        spec_args = set((tool.get("args") or {}).keys())
        reg_args = set((reg_tools[tid].get("args") or {}).keys())
        if spec_args - reg_args:
            r.fail(f"tool '{tid}' declares unknown args: {sorted(spec_args - reg_args)}")
        if reg_tools[tid].get("kind") and tool.get("kind") != reg_tools[tid]["kind"]:
            r.fail(
                f"tool '{tid}' kind mismatch: spec says '{tool.get('kind')}', "
                f"registry says '{reg_tools[tid]['kind']}'"
            )

    declared = {t.get("id") for t in spec.get("tools", [])}
    nodes = spec.get("nodes", {})
    referenced = {s.get("tool") for s in nodes.get("investigate", {}).get("steps", [])}
    referenced |= {a.get("tool") for a in nodes.get("act", {}).get("actions", [])}
    referenced.discard(None)

    for tid in sorted(referenced - declared):
        r.fail(f"node references tool '{tid}' that the spec never declares")
    return r


# ---------------------------------------------------------------- check 2

def check_evidence_closure(spec: dict) -> Result:
    """Every decide input is produced by some investigate step; no cycles."""
    r = Result(2, "Evidence closure")
    nodes = spec.get("nodes", {})
    steps = nodes.get("investigate", {}).get("steps", [])

    produced: dict[str, str] = {}
    for s in steps:
        for field in s.get("produces", []):
            if field in produced:
                r.fail(f"evidence field '{field}' produced by both '{produced[field]}' and '{s.get('id')}'")
            produced[field] = s.get("id")

    incident_fields = set((nodes.get("incident", {}).get("input_schema") or {}).keys())

    for field in nodes.get("decide", {}).get("inputs", []):
        if field not in produced and field not in incident_fields:
            r.fail(f"decide node consumes '{field}' which no investigate step produces")

    # dependency cycle detection over $field references in step args
    graph: dict[str, set[str]] = {}
    for s in steps:
        sid = s.get("id")
        deps = set()
        for val in (s.get("args") or {}).values():
            if isinstance(val, str) and val.startswith("$"):
                ref = val[1:].split(".")[0]
                if ref in produced:
                    deps.add(produced[ref])
        graph[sid] = deps - {sid}

    state: dict[str, int] = {}

    def walk(node: str, path: list[str]) -> None:
        if state.get(node) == 1:
            r.fail(f"cyclic evidence dependency: {' -> '.join(path + [node])}")
            return
        if state.get(node) == 2:
            return
        state[node] = 1
        for dep in graph.get(node, ()):
            walk(dep, path + [node])
        state[node] = 2

    for sid in graph:
        walk(sid, [])
    return r


# ---------------------------------------------------------------- check 3

def check_write_gating(spec: dict, registry: dict) -> Result:
    """No writes during investigate; irreversible actions sit behind approval."""
    r = Result(3, "Write gating")
    kinds = {t["id"]: t.get("kind") for t in registry.get("tools", [])}
    kinds.update({t["id"]: t.get("kind") for t in spec.get("tools", []) if t.get("kind")})

    nodes = spec.get("nodes", {})
    for s in nodes.get("investigate", {}).get("steps", []):
        kind = kinds.get(s.get("tool"))
        if kind in ("write", "execute"):
            r.fail(f"investigate step '{s.get('id')}' calls {kind} tool '{s.get('tool')}' — investigate must be read-only")

    approval = nodes.get("human_approval", {})
    gated_tiers = set((approval.get("triggers") or {}).get("risk_tiers", []))
    if not approval:
        r.fail("no human_approval node defined")

    for a in nodes.get("act", {}).get("actions", []):
        if a.get("reversible") is False and a.get("risk_tier") not in gated_tiers:
            r.fail(
                f"action '{a.get('id')}' is irreversible but its tier '{a.get('risk_tier')}' "
                f"is not gated by human_approval"
            )
        if a.get("reversible") is False and a.get("rollback") not in (None, "", "none"):
            r.note(f"action '{a.get('id')}' marked irreversible but declares a rollback — check which is true")
    return r


# ---------------------------------------------------------------- check 4

def check_risk_tier_coverage(spec: dict) -> Result:
    """Every action has exactly one defined tier; every tier has a route."""
    r = Result(4, "Risk tier coverage")
    tiers = (spec.get("risk_policy") or {}).get("tiers") or {}
    if not tiers:
        r.fail("risk_policy.tiers is empty")
        return r

    for name, cfg in tiers.items():
        if not cfg.get("route"):
            r.fail(f"risk tier '{name}' has no defined route")
        if cfg.get("route") == "autonomous_if_confidence_above" and cfg.get("threshold") is None:
            r.fail(f"risk tier '{name}' routes on confidence but sets no threshold")

    for a in spec.get("nodes", {}).get("act", {}).get("actions", []):
        tier = a.get("risk_tier")
        if tier is None:
            r.fail(f"action '{a.get('id')}' has no risk_tier")
        elif tier not in tiers:
            r.fail(f"action '{a.get('id')}' uses undefined risk tier '{tier}'")
    return r


# ---------------------------------------------------------------- check 5

def check_constraint_enforceability(spec: dict, discovery: dict | None) -> Result:
    """Hard constraints land on action preconditions over producible fields."""
    r = Result(5, "Constraint enforceability")
    nodes = spec.get("nodes", {})
    actions = {a.get("id"): a for a in nodes.get("act", {}).get("actions", [])}

    produced = set()
    for s in nodes.get("investigate", {}).get("steps", []):
        produced.update(s.get("produces", []))
    produced.update((nodes.get("incident", {}).get("input_schema") or {}).keys())

    spec_constraints = spec.get("constraints", [])
    hard = [c for c in spec_constraints if c.get("type") == "hard"]

    for c in hard:
        cid = c.get("id")
        if not c.get("predicate"):
            r.fail(f"hard constraint '{cid}' has no predicate — prose is not enforceable")
            continue
        if not c.get("blocks_actions"):
            r.fail(f"hard constraint '{cid}' blocks no actions")
        for aid in c.get("blocks_actions", []):
            action = actions.get(aid)
            if action is None:
                r.fail(f"constraint '{cid}' blocks unknown action '{aid}'")
                continue
            if c["predicate"] not in (action.get("preconditions") or []):
                r.fail(f"constraint '{cid}' predicate is not a precondition on action '{aid}'")
        for field in predicate_identifiers(c["predicate"]):
            if field not in produced:
                r.fail(f"constraint '{cid}' predicate references '{field}' which nothing produces")
        if not c.get("on_unevaluable"):
            r.fail(f"hard constraint '{cid}' has no on_unevaluable behaviour")

    if discovery:
        spec_ids = {c.get("id") for c in spec_constraints}
        ack = set(spec.get("acknowledged_unenforceable", []))
        for c in discovery.get("constraints", []):
            if c.get("type") == "hard" and c.get("id") not in spec_ids and c.get("id") not in ack:
                r.fail(f"hard constraint '{c.get('id')}' from discovery is silently dropped from the spec")
        for item in discovery.get("unenforceable_constraints", []):
            key = item if isinstance(item, str) else item.get("id")
            if key not in ack:
                r.fail(f"unenforceable constraint '{key}' must be explicitly acknowledged, not omitted")
    return r


# ---------------------------------------------------------------- check 6

def check_reasoning_contract(spec: dict) -> Result:
    """Decide node emits a fully specified structured summary over a closed set."""
    r = Result(6, "Reasoning contract completeness")
    decide = spec.get("nodes", {}).get("decide", {})
    schema = decide.get("output_schema") or {}

    required_fields = [
        "chosen_option",
        "supporting_evidence",
        "constraints_checked",
        "confidence",
        "rejected_alternatives",
    ]
    for f in required_fields:
        if f not in schema:
            r.fail(f"reasoning contract is missing required field '{f}'")

    options = decide.get("options")
    if not options or not isinstance(options, list):
        r.fail("decide node has no closed set of options")
    elif len(set(options)) != len(options):
        r.fail("decide node options contain duplicates")

    for name, defn in schema.items():
        if not isinstance(defn, dict) or not defn.get("type"):
            r.fail(f"reasoning contract field '{name}' has no declared type")

    chosen = schema.get("chosen_option") or {}
    if chosen.get("type") not in (None, "enum"):
        r.fail("chosen_option must be an enum over the declared options, not free text")

    if decide.get("confidence_threshold_for_autonomy") is None:
        r.fail("decide node sets no confidence threshold for autonomous action")
    return r


# ---------------------------------------------------------------- check 7

def check_termination_and_budget(spec: dict, registry: dict) -> Result:
    """Ceilings exist, each has a fallback, no fallback performs an ungated write."""
    r = Result(7, "Termination and budget")
    b = spec.get("budgets") or {}
    pairs = [
        ("max_investigate_steps", "on_step_limit"),
        ("max_tokens_per_session", "on_token_limit"),
        ("wall_clock_timeout_s", "on_timeout"),
    ]
    for ceiling, fallback in pairs:
        if b.get(ceiling) is None:
            r.fail(f"budget '{ceiling}' is not set")
        if not b.get(fallback):
            r.fail(f"budget '{ceiling}' has no fallback behaviour ('{fallback}')")

    kinds = {t["id"]: t.get("kind") for t in registry.get("tools", [])}
    actions = {a.get("id"): a for a in spec.get("nodes", {}).get("act", {}).get("actions", [])}
    gated = set(((spec.get("nodes", {}).get("human_approval") or {}).get("triggers") or {}).get("risk_tiers", []))

    fallbacks = [b.get(f) for _, f in pairs]
    fallbacks.append((spec.get("nodes", {}).get("human_approval") or {}).get("on_timeout"))
    for fb in fallbacks:
        if fb in actions:
            a = actions[fb]
            if kinds.get(a.get("tool")) in ("write", "execute") and a.get("risk_tier") not in gated:
                r.fail(f"fallback '{fb}' performs an ungated {kinds.get(a.get('tool'))} action")
    return r


# ---------------------------------------------------------------- driver

def run(spec: dict, registry: dict, discovery: dict | None) -> list[Result]:
    return [
        check_tool_binding(spec, registry),
        check_evidence_closure(spec),
        check_write_gating(spec, registry),
        check_risk_tier_coverage(spec),
        check_constraint_enforceability(spec, discovery),
        check_reasoning_contract(spec),
        check_termination_and_budget(spec, registry),
    ]


def load(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("spec")
    p.add_argument("--registry", required=True, help="tool registry JSON: {\"tools\": [...]}")
    p.add_argument("--discovery", default=None)
    p.add_argument("--json", action="store_true", help="machine-readable output")
    args = p.parse_args()

    spec = load(args.spec)
    registry = load(args.registry)
    discovery = load(args.discovery) if args.discovery else None

    results = run(spec, registry, discovery)
    ok = all(r.passed for r in results)

    if args.json:
        print(json.dumps({
            "spec": spec.get("agent_name"),
            "passed": ok,
            "checks": [
                {"number": r.number, "name": r.name, "passed": r.passed,
                 "failures": r.failures, "notes": r.notes}
                for r in results
            ],
        }, indent=2))
    else:
        print(f"\nagent_spec validation — {spec.get('agent_name', '(unnamed)')}\n")
        for r in results:
            status = "PASS" if r.passed else "FAIL"
            print(f"  [{status}] {r.number}. {r.name}")
            for f in r.failures:
                print(f"         ✗ {f}")
            for n in r.notes:
                print(f"         · {n}")
        print()
        if ok:
            print("All seven checks passed. Spec may be promoted to status 'validated'.\n")
        else:
            failed = sum(1 for r in results if not r.passed)
            print(f"{failed} check(s) failed. Do not run this spec.\n")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
