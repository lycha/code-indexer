#!/bin/bash

input=$(cat)
tool_name=$(echo "$input" | jq -r '.tool_name')

# Only log Bash commands
if [ "$tool_name" != "Bash" ]; then
  exit 0
fi

command=$(echo "$input" | jq -r '.tool_input.command')
timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
session_id=$(echo "$input" | jq -r '.session_id')

# Log to file
log_file="$HOME/.factory/command-log.jsonl"

# Create log entry
log_entry=$(jq -n \
  --arg ts "$timestamp" \
  --arg sid "$session_id" \
  --arg cmd "$command" \
  '{timestamp: $ts, session_id: $sid, command: $cmd}')

echo "$log_entry" >> "$log_file"

exit 0
