#!/bin/bash
# 只在指定目录生成候选文件，不安装、不启动服务；安装命令交总指挥审查后由老板执行。
set -euo pipefail
umask 077

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "用法：bash deploy/whatsapp/wamedia_service.sh 绝对输出目录 [已安装源码目录]" >&2
  exit 2
fi
OUT=$1
CODE_ROOT=${2:-/opt/imgw/wechat}
for value in "$OUT" "$CODE_ROOT"; do
  if [[ ! "$value" =~ ^/[A-Za-z0-9_./-]+$ ]] || [[ "/$value/" == *"/../"* ]]; then
    echo "目录须为绝对路径，只能包含英文、数字、点、下划线、短横线和斜杠，不能包含上级目录" >&2
    exit 2
  fi
done
for name in imgw-wamedia.service 70-wamedia.conf; do
  if [ -e "$OUT/$name" ] || [ -L "$OUT/$name" ]; then
    echo "候选文件已存在，请换一个输出目录" >&2
    exit 1
  fi
done

MNT=/mnt/nas229/edge/imgw-archive
ROOT=$MNT/whatsapp
MOUNT_UNIT=$(systemd-escape --path --suffix=mount "$MNT")
mkdir -p "$OUT"
cat > "$OUT/imgw-wamedia.service" <<EOF
[Unit]
Description=WhatsApp 附件归档入控制台
After=$MOUNT_UNIT
Wants=$MOUNT_UNIT

[Service]
Type=simple
User=imgw
Group=imgw
UMask=0077
WorkingDirectory=$CODE_ROOT
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PYTHONPATH=$CODE_ROOT
Environment=IMGW_WA_MEDIA_DB=/var/lib/imgw/ops-app/whatsapp-media.sqlite3
Environment=IMGW_WA_MEDIA_QUEUE=/var/lib/imgw/ops-app/whatsapp-media-queue.sqlite3
Environment=IMGW_WA_MEDIA_BUFFER=/var/lib/imgw/ops-app/whatsapp-media-buffer
Environment=IMGW_WA_BRIDGE_MEDIA=/var/lib/imgw/whatsapp/media
Environment=IMGW_WA_MEDIA_ROOT=$ROOT
Environment=IMGW_WA_MEDIA_MOUNT=$MNT
ExecStart=/opt/imgw/venv/bin/python -m console.wamedia.ingest --bridge-db /var/lib/imgw/whatsapp/messages.sqlite3 --bridge-media /var/lib/imgw/whatsapp/media
Restart=on-failure
RestartSec=10
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadOnlyPaths=-/var/lib/imgw/whatsapp
ReadWritePaths=/var/lib/imgw/ops-app
ReadWritePaths=-$ROOT

[Install]
WantedBy=multi-user.target
EOF

cat > "$OUT/70-wamedia.conf" <<EOF
[Unit]
After=$MOUNT_UNIT
Wants=$MOUNT_UNIT

[Service]
Environment=IMGW_WA_MEDIA_DB=/var/lib/imgw/ops-app/whatsapp-media.sqlite3
Environment=IMGW_WA_MEDIA_QUEUE=/var/lib/imgw/ops-app/whatsapp-media-queue.sqlite3
Environment=IMGW_WA_MEDIA_BUFFER=/var/lib/imgw/ops-app/whatsapp-media-buffer
Environment=IMGW_WA_MEDIA_ROOT=$ROOT
Environment=IMGW_WA_MEDIA_MOUNT=$MNT
ReadWritePaths=/var/lib/imgw/ops-app
EOF
chmod 644 "$OUT/imgw-wamedia.service" "$OUT/70-wamedia.conf"

cat <<EOF
候选文件已生成；尚未安装或启动服务。
先按 docs/wamedia.md 备份源码、独立库和服务配置，核对 NAS 真实挂载、0700 媒体目录及哨兵。
确认源码已安装到 $CODE_ROOT，且原有控制台服务确为 imgw-console 和 imgw-console-lan 后，由老板执行：
sudo install -m 0644 '$OUT/imgw-wamedia.service' /etc/systemd/system/imgw-wamedia.service
sudo install -d /etc/systemd/system/imgw-console.service.d /etc/systemd/system/imgw-console-lan.service.d
sudo install -m 0644 '$OUT/70-wamedia.conf' /etc/systemd/system/imgw-console.service.d/70-wamedia.conf
sudo install -m 0644 '$OUT/70-wamedia.conf' /etc/systemd/system/imgw-console-lan.service.d/70-wamedia.conf
sudo systemctl daemon-reload
完成总指挥统一演练和路由核对后，再启动 imgw-wamedia 并重启两个控制台。
EOF
