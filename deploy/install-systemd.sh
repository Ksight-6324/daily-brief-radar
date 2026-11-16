#!/usr/bin/env bash
# Install Daily Brief Radar as systemd services on a Debian or Ubuntu style server.
# Run as root from the repository, which must already be checked out at /opt/brief/app:
#   git clone <your fork> /opt/brief/app && sudo /opt/brief/app/deploy/install-systemd.sh
set -euo pipefail

APP=/opt/brief/app
[ "$(id -u)" -eq 0 ] || { echo "Run as root." >&2; exit 1; }
[ -f "$APP/requirements.txt" ] || { echo "Check the repository out at $APP first." >&2; exit 1; }

id -u brief > /dev/null 2>&1 || useradd --system --home-dir /opt/brief --shell /usr/sbin/nologin brief
install -d -o brief -g brief -m 700 /var/lib/brief /var/backups/brief
install -d -o root -g brief -m 750 /etc/brief

python3 -m venv /opt/brief/venv
/opt/brief/venv/bin/pip install --quiet --upgrade pip
/opt/brief/venv/bin/pip install --quiet -r "$APP/requirements.txt"
chown -R brief:brief /opt/brief/app /opt/brief/venv

if [ ! -f /etc/brief/brief.env ]; then
  install -o root -g brief -m 640 "$APP/deploy/brief.env.example" /etc/brief/brief.env
  echo "Created /etc/brief/brief.env. Fill it in before starting the services."
  NEEDS_ENV=1
fi

cp "$APP"/deploy/systemd/*.service "$APP"/deploy/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload

if [ "${NEEDS_ENV:-0}" = "1" ]; then
  echo "Next: edit /etc/brief/brief.env, then run:"
  echo "  sudo systemctl enable --now brief-api brief-worker brief-scheduler brief-maintenance.timer brief-check.timer"
  echo "The database is created and upgraded automatically when the first service starts."
else
  systemctl enable --now brief-api brief-worker brief-scheduler brief-maintenance.timer brief-check.timer
  echo "Services started. Check them with: systemctl status 'brief-*'"
fi
