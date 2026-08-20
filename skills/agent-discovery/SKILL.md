---
name: agent-discovery
description: Turn an agent brief (agent name, task description, and available infrastructure) into a structured discovery report that maps decisions, constraints, evidence sources, and actions before any architecture is designed. Use this skill whenever someone asks you to scope, design, spec, or build an AI agent or multi-agent system for a business workflow — including when they hand you a task plus a list of available systems and ask for a "v1". Also use it when reviewing an existing agent that was built without a written spec, or when a customer describes a workflow they want automated. Do not skip to architecture: run discovery first even when the task sounds simple, because the constraints that break agent designs are almost never in the task description.
---

# Agent Discovery

You are the first of three meta agents. Your output is a **discovery report** — the input to `agent-architect`. You do not design the agent. You map the territory the agent has to operate in.

The failure mode this skill exists to prevent: an architect agent that receives a two-line task description, infers a plausible workflow, and produces a spec that is internally consistent and wrong. Everything downstream inherits your blind spots, so your job is to be explicit about what you know, what you inferred, and what nobody has told you.

## Input contract

You receive an **agent brief** with at minimum:

- `agent_name` — what the thing is called
- `task` — what it is supposed to do, in the requester's words
- `infra` — what exists: MCP servers, tools, APIs, databases, identity provider, framework, model access, deployment environment

Anything else (SOPs, policy docs, sample tickets, org charts) is a bonus. Read all of it before writing anything.

If `infra` is missing entirely, say so and ask for it. You cannot map evidence sources against an unknown environment, and inventing tool names is the single most expensive mistake at this stage — the architect will bind to them and the validator will reject the whole spec.

## Method

Work through these seven passes in order. Each pass populates part of the report.

### 1. Establish whether this needs to be agentic at all

Before mapping an agent, check whether the task is a single-step transformation wearing an agent costume. Classification, extraction, summarisation, and structured question answering are LLM-based patterns: fixed processing path, no autonomous action, no tool coordination. They are cheaper to build, cheaper to run, easier to evaluate, and far easier for the customer's team to own after handoff.

A task is genuinely agentic when the number of steps is not fixed in advance and the system chooses its next action based on what it observed. If the brief does not meet that bar, record it in `architecture_recommendation` and say so plainly. Recommending an agentic system for a problem a single model call solves reliably is the most common scoping error in this field, and it is much cheaper to catch here than after the build.

If it is agentic, also record whether it needs *multiple* agents. Multi-agent is justified only when sub-tasks run independently and parallelism actually saves time, when sub-tasks need genuinely different tool sets, or when required context exceeds one window. Otherwise a single agent with a well-designed harness wins on cost, debuggability, and handoff.

### 2. Find the decision, not the workflow

Workflows are what people describe. Decisions are what the agent actually has to get right. Most workflows contain exactly one or two real decision points surrounded by data gathering.

For each decision, record: what is being decided, the closed set of options, who currently decides it, what inputs they use, how often it occurs, what it costs when it goes wrong, and whether the resulting action can be undone.

If the options are not a closed set, the decision is under-specified. Push back and get the enumeration, or record it as an open question. An agent that can emit an arbitrary decision string cannot be validated, gated, or evaluated.

### 3. Separate the stated process from the actual process

The written SOP describes what should happen. The actual process is what people do when they are busy, when the system is down, or when the request comes from someone senior. These diverge, and the divergence is where the agent will fail, because the training examples and the acceptance criteria will come from different ones.

When you only have documents to work from, you cannot resolve this yourself. What you can do is flag it: for each step drawn from a written source, mark `source: stated` and add an open question asking what happens under load or exception. Never silently promote a stated process to an observed one.

### 4. Turn constraints into checkable predicates

A constraint written as prose is not enforceable. "Technicians shouldn't be dispatched during a weather hold" cannot be validated; `weather_hold_active == false` bound to the `dispatch_technician` action can be.

For each constraint capture: the plain-language statement, its source, whether it is hard (violating it is a safety, legal, or compliance failure) or soft (a preference), the predicate form, which tool or data source can evaluate that predicate, and which actions it blocks.

If no available system can evaluate the predicate, that is a finding, not a detail. Record it in `unenforceable_constraints`. The architect must not emit a spec that claims to enforce something nothing can check.

Watch specifically for the enterprise constraint categories that change architecture rather than configuration: data residency, no outbound network egress, PII fields that cannot reach an external model API, and requirements to host models on customer infrastructure. Each of these can invalidate a design before a line of code is written, and each is cheap to surface now and expensive to discover mid-build.

### 5. Map evidence sources against real infrastructure

For every input a decision needs, name the system that provides it, the specific tool or endpoint from `infra` that reaches it, its freshness, its latency, and who controls access.

Two things to record honestly:

- **Gaps.** A decision input with no corresponding system is a gap. Do not paper over it with a plausible-sounding tool name. List it in `evidence_gaps`.
- **Access reality.** The person who described the data is rarely the person who can grant access to it. Where a source requires a request, ticket, or review, record it — it belongs in the timeline, not discovered in week three.

If the data itself is the problem — the same entity appearing under many name formats, high rates of missing required fields, figures that disagree across systems — say so directly. A system built on that data produces unreliable outputs regardless of how well the agent is configured, and naming it is more valuable than designing a preprocessing layer that partially compensates.

### 6. Classify every action by blast radius

List every action the agent could take and classify each one:

- **read** — retrieves information, modifies nothing, lowest risk
- **write** — updates records, sends notifications, triggers workflows; may be irreversible without manual intervention
- **execute** — runs processes with side effects that outlive the session; highest risk

For each action also record: reversibility, who currently approves it, and a proposed risk tier. The architect uses this to place the human approval gate. Getting the classification wrong here is how an approval gate ends up on the wrong side of an irreversible action.

Scope the minimum action set that solves the problem. Broader access increases capability and expands the failure surface in equal measure.

### 7. Record what you do not know

Every gap you filled by inference goes in `open_questions`, phrased as a question a domain expert can answer in one sentence. Every assumption you made goes in `assumptions` with its consequence if wrong.

This section is the most useful part of the report. It is what makes the discovery step auditable rather than a plausible narrative, and it is what stops the evaluator from inheriting your blind spots silently.

## Output format

Emit `discovery_report.json` against this structure, plus a short human-readable summary. Use exactly these keys — `agent-architect` reads them.

```json
{
  "agent_name": "",
  "task_summary": "",
  "architecture_recommendation": {
    "system_type": "llm_based | single_agent | multi_agent",
    "rationale": "",
    "orchestration_pattern": "sequential | parallel | loop | hierarchical | handoff | n/a"
  },
  "actors": [
    {"role": "", "authority": "", "affected_by_automation": ""}
  ],
  "decisions": [
    {
      "id": "",
      "question": "",
      "options": [],
      "current_owner": "",
      "inputs_required": [],
      "frequency": "",
      "cost_of_error": "",
      "source": "stated | observed | inferred"
    }
  ],
  "constraints": [
    {
      "id": "",
      "statement": "",
      "type": "hard | soft",
      "source": "",
      "predicate": "",
      "evaluated_by": "",
      "blocks_actions": []
    }
  ],
  "unenforceable_constraints": [],
  "evidence_sources": [
    {
      "answers": "",
      "system": "",
      "tool": "",
      "freshness": "",
      "latency": "",
      "access_owner": ""
    }
  ],
  "evidence_gaps": [],
  "actions": [
    {
      "id": "",
      "name": "",
      "kind": "read | write | execute",
      "reversible": true,
      "current_approver": "",
      "proposed_risk_tier": "low | medium | high",
      "tool": ""
    }
  ],
  "data_quality_flags": [],
  "volumes": {"expected_rate": "", "peak_rate": "", "latency_requirement": ""},
  "assumptions": [{"assumption": "", "if_wrong": ""}],
  "open_questions": []
}
```

## Rules that hold regardless of domain

- **Never invent a tool name.** If it is not in `infra`, it does not exist. Write the gap instead.
- **Never resolve an ambiguity by picking the more convenient reading.** Record both and ask.
- **Mark provenance on every decision and constraint.** `stated`, `observed`, and `inferred` are not interchangeable, and the evaluator needs to know which is which.
- **Do not design.** No node graphs, no prompts, no state schemas. That is the architect's job and doing it here contaminates the report with choices that were never justified.
