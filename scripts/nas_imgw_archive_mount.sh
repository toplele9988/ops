#!/bin/bash
# 给 imgw 账号在 NAS 上开一个可写的留存目录（聊天附件、原始证据）。
# 做法与现有 /mnt/nas229/edge/wechat/media_archive/videos 的挂载单元完全相同：
# CIFS 挂载 + 属主映射为 imgw(995:985) + 文件 0600 / 目录 0700 + noexec,nosuid,nodev + 同一份凭据文件。
# 只新增一个 systemd 挂载单元，不改现有单元、不改网络。需要 root：  sudo bash scripts/nas_imgw_archive_mount.sh
# 回滚：  sudo systemctl stop 'mnt-nas229-edge-imgw\x2darchive.mount' && sudo rm '/etc/systemd/system/mnt-nas229-edge-imgw\x2darchive.mount' && sudo systemctl daemon-reload
set -euo pipefail
SRC=/etc/systemd/system/mnt-nas229-edge-wechat-media_archive-videos.mount
WHERE=/mnt/nas229/edge/imgw-archive
UNIT="$(systemd-escape -p --suffix=mount "$WHERE")"
DEST="/etc/systemd/system/$UNIT"
[ "$(id -u)" = 0 ] || { echo "请用 sudo 运行"; exit 1; }
[ -f "$SRC" ] || { echo "找不到现有挂载单元 $SRC"; exit 1; }
[ -d "$WHERE" ] || { echo "目录 $WHERE 不存在，请先创建"; exit 1; }
[ ! -e "$DEST" ] && [ ! -L "$DEST" ] || { echo "$DEST 已存在，不覆盖"; exit 1; }
(set -C; sed -e 's#^Description=.*#Description=imgw archive (chat attachments and raw evidence) on NAS229 edge#' \
    -e 's#^What=.*#What=//192.168.100.229/edge/imgw-archive#' \
    -e "s#^Where=.*#Where=$WHERE#" "$SRC" > "$DEST")
chmod 644 "$DEST"
systemctl daemon-reload
systemctl start "$UNIT"
mountpoint "$WHERE"
# 以 imgw 身份验证：创建、写入、读回校验、原子改名、清理
sudo -u imgw sh -es -- "$WHERE" <<'VERIFY_ARCHIVE'
D=$1
umask 077
if [ -e "$D/.imgw-archive-id" ] || [ -L "$D/.imgw-archive-id" ]; then
  [ -f "$D/.imgw-archive-id" ] && [ ! -L "$D/.imgw-archive-id" ] || { echo "哨兵不是普通文件，请管理员核对"; exit 1; }
else
  (set -C; printf '%s\n' 'imgw-archive v1' > "$D/.imgw-archive-id")
fi
probe=$(mktemp -d "$D/.imgw-check.XXXXXXXX")
trap 'rm -f -- "$probe/data.tmp" "$probe/data.bin"; rmdir -- "$probe"' EXIT
head -c 1048576 /dev/urandom > "$probe/data.tmp"
a=$(sha256sum "$probe/data.tmp" | cut -d" " -f1)
mv -- "$probe/data.tmp" "$probe/data.bin"
b=$(sha256sum "$probe/data.bin" | cut -d" " -f1)
[ "$a" = "$b" ] && echo "imgw 写入/读回校验/原子改名：通过"
VERIFY_ARCHIVE
echo "完成。开机自动挂载需要再执行：  sudo systemctl enable '$UNIT'"
