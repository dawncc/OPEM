#!/usr/bin/env bash
set -uo pipefail

MEMORY_URL="${MEMORY_URL:-http://127.0.0.1:18000}"
MEMORY_SITE="${MEMORY_SITE:-$HOME/.codex-memory-test/site}"
RESULT="${RESULT:-/tmp/codex-memory-remote-result.txt}"
LOG="${LOG:-/tmp/codex-memory-remote-run.log}"

codex exec \
  --dangerously-bypass-approvals-and-sandbox \
  --skip-git-repo-check \
  --ephemeral \
  -C "${CODEX_TEST_CWD:-$HOME/work}" \
  -c "mcp_servers.codex_memory.command='python3'" \
  -c "mcp_servers.codex_memory.args=['-m','memory_mcp.main','--server','${MEMORY_URL}']" \
  -c "mcp_servers.codex_memory.env={PYTHONPATH='${MEMORY_SITE}'}" \
  -o "$RESULT" \
  - >"$LOG" 2>&1 <<'PROMPT'
Use the codex_memory MCP tools in this exact order:
1. Call memory_health.
2. Call memory_chat_submit with project "codex-rl-cc-9-test", session_id "rl-cc-9-tool-archive-003", and these chronological messages:
   - sequence 0, role user, content "Verify successful and failed tool archival from RL-CC-9."
   - sequence 1, role assistant, content "I will submit both tool outcomes through Memory MCP."
   - sequence 2, role tool, content "REMOTE_TOOL_SUCCESS_MARKER_20260711: pytest 4 passed in 0.31s", metadata {"tool_name":"pytest","status":"completed","exit_code":0,"command":"pytest -q"}
   - sequence 3, role assistant, content "The success is complete; now archive a representative failure."
   - sequence 4, role tool, content "REMOTE_TOOL_FAILURE_MARKER_20260711\nFileNotFoundError: /tmp/rl-cc-9-missing.txt", metadata {"tool_name":"python","status":"failed","exit_code":1,"command":"python verify_file.py"}
3. Use a shell command to wait 3 seconds for the Memory worker.
4. Call memory_recall with query "REMOTE_TOOL_SUCCESS_MARKER_20260711 pytest 4 passed" and project "codex-rl-cc-9-test".
5. Call memory_recall with query "REMOTE_TOOL_FAILURE_MARKER_20260711 FileNotFoundError" and project "codex-rl-cc-9-test".
Report exact MCP tool statuses, whether each marker was recalled, and the source session. Do not edit files.
PROMPT
code=$?
echo "CODEX_EXIT=$code"
cat "$RESULT" 2>/dev/null || true
echo "---LOG_TAIL---"
tail -80 "$LOG" 2>/dev/null || true
exit "$code"
