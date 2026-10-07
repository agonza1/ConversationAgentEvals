# Enforced evaluation designs

Save and publish a reviewed Evaluation design to create immutable runnable cases.
Each case carries its programmatic checks and required evidence into evaluation,
run analysis, ASSERT manifests, and vCon analysis exports/replay. Built-in cases
without a published design are unchanged. Standalone ASSERT YAML export does not
execute these CAE-specific checks.

Enter one explicit check expression per line:

| Expression | Meaning |
| --- | --- |
| `transcript_present` | Nonempty recorded transcript |
| `action_trace_present` | At least one parseable action event |
| `final_state_present` | Nonempty final-state object |
| `final_state_complete` | Boolean `complete` is exactly true; false fails, missing is insufficient |
| `tool_succeeded:lookup_account` | At least one successful invocation of that exact tool; retry success satisfies it |
| `transcript_contains:verified` | Case-insensitive literal substring in any transcript line, including caller lines; not agent-only or semantic verification |

Evidence requirements accept `transcript`, `action_trace`, `final_state`, `vcon`
and `final_state_or_action_trace`. Space-separated legacy names such as
`conversation transcript`, `action trace`, and `final state` are aliases.
Presence checks do not validate an entire artifact schema or prove its truth.
Tool-success checks require an explicit successful terminal status; `observed`,
pending, and unknown events do not establish success.
Natural-language conditionals and arbitrary code are never inferred or executed.
Unsupported entries are warned about during design preview and explicitly block
verification with an insufficient-evidence result at runtime.

All declared checks are mandatory gates regardless of their displayed severity.
Explicit check failures yield fail/0. Missing evidence or unsupported checks yield
needs-review/n/a. Failed evaluations retain a failed lifecycle in run history and
are counted separately from needs-review outcomes in suite summaries.
An explicit focused forbidden-action hit still retains score 0.
Passing checks do not increase the focused policy score or automatically verify
natural-language behaviors. A successful ID-linked tool call is not semantic proof;
the focused behavior remains insufficient until semantic review. Other behaviors
are not evaluated by this case. Results include normalized action-event positions,
transcript line positions, or final-state paths; missing artifacts have no invented
citations. Applied semantic pass proposals are rejected while mandatory design
gates are blocked. Original automatic results and review provenance stay intact.

Validation uses isolated fixtures, not real purchases/payments. Actual local voice
readiness still depends on ASR/TTS services and provider credentials; fixture-based
voice evidence does not certify those external dependencies.
