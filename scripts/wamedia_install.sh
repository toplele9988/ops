#!/bin/bash
# 安装 WhatsApp 附件归档服务（imgw-wamedia）：需要 root。 sudo bash scripts/wamedia_install.sh
# 先决条件：源码已在 /opt/imgw/wechat（总指挥已部署）；候选单元已由 deploy/whatsapp/wamedia_service.sh 生成到 private_out/wamedia-units。
# 回滚： sudo systemctl disable --now imgw-wamedia; sudo rm /etc/systemd/system/imgw-wamedia.service /etc/systemd/system/imgw-console{,-lan}.service.d/70-wamedia.conf; sudo systemctl daemon-reload
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "请用 sudo 运行"; exit 1; }
SRC=/srv/imgw-dev/ops/private_out/wamedia-units
for f in imgw-wamedia.service 70-wamedia.conf; do [ -f "$SRC/$f" ] || { echo "缺少 $SRC/$f"; exit 1; }; done
[ ! -e /etc/systemd/system/imgw-wamedia.service ] || { echo "imgw-wamedia.service 已存在，不覆盖"; exit 1; }
mountpoint -q /mnt/nas229/edge/imgw-archive || { echo "NAS 归档目录没有挂载"; exit 1; }
sudo -u imgw test -d /mnt/nas229/edge/imgw-archive/whatsapp || { echo "NAS 上缺少 whatsapp 目录"; exit 1; }
install -m 0644 "$SRC/imgw-wamedia.service" /etc/systemd/system/imgw-wamedia.service
install -d /etc/systemd/system/imgw-console.service.d /etc/systemd/system/imgw-console-lan.service.d
for u in imgw-console imgw-console-lan; do
  [ ! -e /etc/systemd/system/$u.service.d/70-wamedia.conf ] || { echo "$u 的 70-wamedia.conf 已存在，不覆盖"; exit 1; }
  install -m 0644 "$SRC/70-wamedia.conf" /etc/systemd/system/$u.service.d/70-wamedia.conf
done
systemctl daemon-reload
systemctl enable --now imgw-wamedia
systemctl restart imgw-console imgw-console-lan
sleep 5
systemctl is-active imgw-wamedia imgw-console imgw-console-lan
echo "完成。imgw-wamedia 已启动并开机自启。"
