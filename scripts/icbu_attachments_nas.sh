#!/bin/bash
# 把阿里附件的存放位置切到 NAS（/mnt/nas229/edge/imgw-archive）。
# 只新增三个 systemd drop-in（接收服务 + 两个控制台服务），不改任何现有单元文件、不改网络、不重启服务。
# 重启由总指挥在核对后执行。需要 root：  sudo bash scripts/icbu_attachments_nas.sh
#
# 作用：
#   icbu-inbound   ：After/Wants 挂载（软依赖：NAS 掉线只让附件暂时不可用，文字入库不受影响）；
#                    ReadWritePaths=- 允许写 NAS 附件目录；Environment 指定附件根目录。
#                    附件模块自身每次操作检查哨兵文件 .imgw-archive-id 并固定挂载身份，挂载丢失时返回 503，绝不回落写本机。
#   imgw-console(-lan)：只读展示附件，只需要相同的 Environment 和软依赖。
# 附件登记库（SQLite）仍留在本机 /var/lib/imgw/icbu/attachments.sqlite3。
# 回滚：  sudo rm /etc/systemd/system/{icbu-inbound,imgw-console,imgw-console-lan}.service.d/60-nas-archive.conf
#         sudo systemctl daemon-reload   （然后重启三个服务；附件目录里已有的文件保留，不要删除）
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "请用 sudo 运行"; exit 1; }
MNT=/mnt/nas229/edge/imgw-archive
ROOT=$MNT/icbu/attachments
mountpoint -q "$MNT" || { echo "$MNT 没有挂载，先确认 NAS 挂载"; exit 1; }
[ -f "$MNT/.imgw-archive-id" ] || { echo "缺少哨兵文件 $MNT/.imgw-archive-id"; exit 1; }
sudo -u imgw mkdir -p "$ROOT"
sudo -u imgw sh -c "chmod 700 '$MNT/icbu' '$ROOT'" 2>/dev/null || true
sudo -u imgw test -w "$ROOT" || { echo "imgw 不能写 $ROOT"; exit 1; }

write() {  # unit, writable(0/1)
  local unit="$1" rw="$2" dir="/etc/systemd/system/$1.service.d" f="/etc/systemd/system/$1.service.d/60-nas-archive.conf"
  [ ! -e "$f" ] || { echo "$f 已存在，不覆盖"; exit 1; }
  mkdir -p "$dir"
  {
    echo "# NAS attachments: soft dependency. A NAS outage must only make attachments unavailable."
    echo "[Unit]"
    echo "After=$(systemd-escape -p --suffix=mount "$MNT")"
    echo "Wants=$(systemd-escape -p --suffix=mount "$MNT")"
    echo "[Service]"
    echo "Environment=ICBU_ATTACHMENTS_ROOT=$ROOT"
    [ "$rw" != 1 ] || echo "ReadWritePaths=-$MNT"
  } > "$f"
  chmod 644 "$f"
  echo "已写入 $f"
}
write icbu-inbound 1
write imgw-console 0
write imgw-console-lan 0
systemctl daemon-reload
for u in icbu-inbound imgw-console imgw-console-lan; do
  printf '%s: ' "$u"; systemctl show "$u.service" -p DropInPaths --value | tr ' ' '\n' | grep -c 60-nas-archive | sed 's/$/ 个 drop-in 已生效(待重启)/'
done
echo "完成。服务尚未重启，附件仍按旧位置工作；通知总指挥继续。"
