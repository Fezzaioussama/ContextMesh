# graphify
- **graphify** (`.claude/skills/graphify/SKILL.md`) - any input to knowledge graph. Trigger: `/graphify`
When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

# Agents (`.claude/agents/`)
- **challenger**: discuss and challenge an idea before building it; ends with an implementation brief.
- **implementer**: writes the code under strict limits (500-line files, 40-line function bodies, complexity 4, no duplication, docstrings).
- **reviewer**: independent read-only review; returns PASS / FAIL / BLOCKED.

When the user asks to use the agents for a change: pass the challenger's brief (or the user's acceptance criteria) to the implementer, then send the implementer's handoff to the reviewer. Send FAIL findings back to the implementer and review again, at most 3 rounds; then report the verdict and any open limit exceptions to the user. `python3 .claude/scripts/check_limits.py` measures the limits.
