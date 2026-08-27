#!/usr/bin/env bash
# =========================================================
# Datei:      stacks/ubuntu/setup_ubuntu.sh
# Zweck:      Bare-Metal-Setup des Ubuntu-Cores (Blueprint v1.2.0)
# Host:       Ubuntu 24.04 · 192.168.178.50 (Always-on)
# Aufruf:     sudo bash setup_ubuntu.sh  (als Admin)
# =========================================================
set -euo pipefail

CORE_IP="${CORE_IP:-192.168.178.50}"
WIN_IP="${WIN_IP:-192.168.178.60}"
INSTALL_DIR=/srv/alpha
REPO_SRC="${1:-.}"   # Quell-Repo-Pfad (Default: Verzeichnis dieses Skripts)

echo "========================================================="
echo "  PROJEKT:ALPHA — UBUNTU CORE SETUP (Blueprint v1.2.0)"
echo "========================================================="

# 0. Privilegien
if [[ $EUID -ne 0 ]]; then
  echo "  [FEHLER] Bitte mit sudo ausführen: sudo bash $0" >&2
  exit 1
fi

# 1. Basis-Pakete
echo "[1/6] Pakete installieren (redis-server, nfs-kernel-server, python venv)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq redis-server nfs-kernel-server python3-venv python3-pip git > /dev/null
echo "  [OK] redis-server, NFS-Server, python3-venv"

# 2. Dienstbenutzer
echo "[2/6] Benutzer 'alpha' anlegen..."
id -u alpha &>/dev/null || useradd --system --create-home --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin alpha
mkdir -p "$INSTALL_DIR/data" "$INSTALL_DIR/etc"
chown -R alpha:alpha "$INSTALL_DIR"
echo "  [OK] /srv/alpha (Daten: /srv/alpha/data)"

# 3. Repo-Dateien
echo "[3/6] Projekt-Dateien nach $INSTALL_DIR kopieren..."
cp -r "$REPO_SRC"/app "$REPO_SRC"/bin "$INSTALL_DIR"/
cp "$REPO_SRC"/setup_alpha.py "$REPO_SRC"/requirements.txt "$INSTALL_DIR"/ 2>/dev/null || true
chown -R alpha:alpha "$INSTALL_DIR"
chmod +x "$INSTALL_DIR"/bin/m8-ctl
echo "  [OK] app/ + bin/ kopiert"

# 4. Python-Venv
echo "[4/6] Python-Venv bauen (.venv)..."
sudo -u alpha python3 -m venv "$INSTALL_DIR/.venv"
sudo -u alpha "$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade pip
sudo -u alpha "$INSTALL_DIR/.venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"
echo "  [OK] .venv mit fastapi/uvicorn/redis/duckdb/pyarrow"

# 5. .env
if [[ ! -f "$INSTALL_DIR/.env" ]]; then
  cp "$REPO_SRC/.env.example" "$INSTALL_DIR/.env" 2>/dev/null || true
  chown alpha:alpha "$INSTALL_DIR/.env"
  echo "  [OK] .env aus .env.example erstellt"
fi

# 6. Redis-Konfiguration (AOF)
cat > "$INSTALL_DIR/etc/redis.conf" <<'REDIS'
bind 127.0.0.1 192.168.178.50
port 6379
protected-mode yes
appendonly yes
appendfsync everysec
appendfilename "appendonly.aof"
dir /var/lib/redis
maxmemory 512mb
maxmemory-policy noeviction
logfile /var/log/redis/alpha.log
daemonize no
REDIS
mkdir -p /var/lib/redis /var/log/redis
chown -R redis:redis /var/lib/redis /var/log/redis

# 7. NFS-Export removed in Sigma (Ubuntu-only; no Windows portal)
echo "[5/6] NFS-Export übersprungen (Projekt:Sigma — Ubuntu-only)."

# 8. systemd-Units
echo "[6/6] systemd-Units aktivieren..."
cp "$REPO_SRC/stacks/ubuntu/systemd/alpha-redis.service" /etc/systemd/system/
cp "$REPO_SRC/stacks/ubuntu/systemd/alpha-core.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now alpha-redis
systemctl enable --now alpha-core
sleep 3
systemctl --no-pager --lines=3 status alpha-core || true

echo ""
echo "========================================================="
echo "  SIGMA UBUNTU CORE BEREIT ($CORE_IP)"
echo "  API:       http://$CORE_IP:8000/api/dashboard/init"
echo "  CLI:       sudo -u alpha /srv/alpha/bin/m8-ctl states"
echo "  Redis:     :6379 (AOF /var/lib/redis)"
echo "  TV MCP:    SIGMA_TV_MCP_URL (CSV seam)"
echo "  Logs:      journalctl -u alpha-core -f"
echo "========================================================="
