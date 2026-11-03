#!/bin/bash
# Stores the Alibaba.com Open Platform app credentials for the console (read only by root and group imgw).
# Run it yourself in a terminal on 221; the values are typed with echo off and never go through chat.
set -eu
target=/etc/imgw/alibaba-open.json
if [ -e "$target" ]; then
  read -r -p "$target 已存在，要覆盖吗？(yes/no) " ans
  [ "$ans" = "yes" ] || { echo "已取消，没有改动。"; exit 0; }
  sudo cp -a "$target" "$target.bak-$(date +%Y%m%d%H%M%S)"
fi
read -r -p "App Key（一串数字，可以明文看）: " key
read -r -s -p "App Secret（输入时屏幕不显示）: " secret; echo
[ -n "$key" ] && [ -n "$secret" ] || { echo "不能为空，没有保存。"; exit 1; }
tmp=$(mktemp); chmod 600 "$tmp"
python3 - "$key" "$secret" > "$tmp" <<'PY'
import json, sys
print(json.dumps({"schema": "alibaba-open-v1", "app_key": sys.argv[1], "app_secret": sys.argv[2]}, ensure_ascii=False))
PY
sudo install -o root -g imgw -m 0640 "$tmp" "$target"
rm -f "$tmp"
echo "已保存到 $target（root:imgw，0640）。只保存了 App Key 和 App Secret，没有显示出来。"
