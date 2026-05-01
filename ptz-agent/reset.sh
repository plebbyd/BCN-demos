#!/bin/bash
# Reset the MSA scratchpad to a clean initial state.
# Usage: bash reset.sh

cat > scratchpads/active.yaml << 'EOF'
goals:
- Monitor node health and sensor status
- Execute scheduled and injected tasks
- Report anomalies and maintain clean state
current_task: Read all sensors and log initial system state
pending_actions:
- Check system health and note any issues
- Signal done with a summary
completed_tasks: []
notes: ""
last_updated: null
EOF

echo "Scratchpad reset to default state."
echo ""
echo "Quick start:"
echo "  python -m msa.agent --once              # run one cycle"
echo "  python -m msa.agent --status            # check state"
echo "  python -m msa.agent --task 'say hello'  # inject a task"
echo "  python -m msa.agent --sensors           # list sensors"
