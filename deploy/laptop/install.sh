#!/usr/bin/env bash
# Optional: run JobPilot on the spare Xubuntu laptop instead of (or alongside) GitHub Actions.
# A MongoDB run-lock in scheduler.py guarantees the two never run at the same time.
#
#   git clone <repo> ~/jobpilot && cd ~/jobpilot && cp /path/to/.env .env
#   bash deploy/laptop/install.sh
#
# Installs a systemd *user* timer (every 30 min, survives reboots, no root needed after linger).
set -euo pipefail
cd "$(dirname "$0")/../.."
REPO="$(pwd)"

command -v python3 >/dev/null || { echo "python3 missing: sudo apt install python3 python3-venv"; exit 1; }
[ -f .env ] || { echo "Copy your .env into $REPO first"; exit 1; }

python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/jobpilot.service <<EOF
[Unit]
Description=JobPilot AI pipeline run
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$REPO
Environment=TZ=Asia/Kolkata
ExecStartPre=-/usr/bin/git -C $REPO pull --ff-only
ExecStart=$REPO/.venv/bin/python -c "from scheduler import run_pipeline; run_pipeline()"
TimeoutStartSec=1500
EOF

cat > ~/.config/systemd/user/jobpilot.timer <<EOF
[Unit]
Description=Run JobPilot AI every 30 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=30min
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now jobpilot.timer
sudo loginctl enable-linger "$USER" || echo "Run 'sudo loginctl enable-linger $USER' so it runs without being logged in"

echo
echo "Installed. Useful commands:"
echo "  systemctl --user list-timers jobpilot.timer      # next run"
echo "  journalctl --user -u jobpilot.service -f         # live logs"
echo "  systemctl --user start jobpilot.service          # run now"
echo
echo "Laptop power tips: set lid-close action to 'do nothing' (Xfce Power Manager) and disable"
echo "suspend on AC so it keeps running 24/7."
echo
echo "Optional local LLM fallback (unlimited, slower): curl -fsSL https://ollama.com/install.sh | sh"
echo "  ollama pull qwen2.5:3b   then add OLLAMA_URL=http://localhost:11434 to .env"
