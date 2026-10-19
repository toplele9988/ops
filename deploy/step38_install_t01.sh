#!/bin/bash
# step38：TASK-01 频闪灯画廊 + CPQ 报价抽屉 + 对客导出 + Sol 函件 安装。失败自动回滚。用法：sudo bash step38_install_t01.sh
set -uo pipefail
[[ $EUID -eq 0 ]] || { echo 'run with sudo' >&2; exit 1; }
REL=/home/cz/swarm/release-t01; W=/opt/imgw/wechat; C=$W/console; TS=$(date +%Y%m%d%H%M%S)
ENVF=/etc/imgw/console-cpq.env; DROP=/etc/systemd/system/imgw-console.service.d/cpq.conf
OUT=$REL/step38_result.txt
exec > >(tee "$OUT") 2>&1
echo "=== 1. 校验"
( cd "$REL" && md5sum -c --quiet release.md5 ) || { echo 'ABORT: 发布包被改动'; exit 2; }
( cd "$W" && md5sum -c --quiet "$REL/orig.md5" ) || { echo 'ABORT: 生产 app.py / base3.html 已变动'; exit 2; }
for need in "$W/tests/test_console.py" /var/lib/imgw/video_knowledge_auto/equipment/catalog.sqlite3 /var/lib/imgw/media/fengwu; do
  [[ -e "$need" ]] || { echo "ABORT: 缺少 $need"; exit 2; }; done
/opt/imgw/venv/bin/python -c "import weasyprint, pypdf, PIL, yaml" || { echo 'ABORT: 依赖缺失'; exit 2; }

echo "=== 2. 备份"
cp -p "$C/app.py" "$C/app.py.bak-$TS"; cp -p "$C/templates/v3/base3.html" "$C/templates/v3/base3.html.bak-$TS"
HAD_ENV=0; [[ -e "$ENVF" ]] && HAD_ENV=1
NEWDIRS=("$C/cpq" "$C/strobe" "$C/security" "$C/routes" "$C/templates/strobe" "$C/templates/quote" "$C/static/strobe" "$C/static/js" "$C/static/fonts" "$W/config" "$W/data")
NEWFILES=("$W/scripts/sol_letter.py" "$W/scripts/cpq_seed.py" "$W/tests/test_console_cpq_json.py")
rollback() {
  echo "!!! $1 —— 回滚"
  cp -p "$C/app.py.bak-$TS" "$C/app.py"; cp -p "$C/templates/v3/base3.html.bak-$TS" "$C/templates/v3/base3.html"
  rm -rf "${NEWDIRS[@]}" "${NEWFILES[@]}"
  rm -f "$DROP"; rmdir /etc/systemd/system/imgw-console.service.d 2>/dev/null
  [[ $HAD_ENV -eq 0 ]] && rm -f "$ENVF"
  systemctl daemon-reload; systemctl restart imgw-console; exit 3
}

echo "=== 3. 安装文件"
inst_tree() { # src dst
  ( cd "$1" && find . -type d ) | while read -r d; do install -d -o root -g root -m 0755 "$2/$d"; done
  ( cd "$1" && find . -type f ) | while read -r f; do install -o root -g root -m 0644 "$1/$f" "$2/$f"; done
}
for d in cpq strobe security routes templates/strobe templates/quote static/strobe static/js static/fonts; do inst_tree "$REL/console/$d" "$C/$d"; done
install -o root -g root -m 0644 "$REL/console/app.py" "$C/app.py"
install -o root -g root -m 0644 "$REL/console/templates/v3/base3.html" "$C/templates/v3/base3.html"
install -d -o root -g imgw -m 0750 "$W/config" "$W/data" "$W/data/cpq"
install -o root -g imgw -m 0640 "$REL/config/cpq.yaml" "$W/config/cpq.yaml"
install -o root -g imgw -m 0640 "$REL/config/company_profile.yaml" "$W/config/company_profile.yaml"
for f in "$REL"/data/cpq/*.yaml; do install -o root -g imgw -m 0640 "$f" "$W/data/cpq/$(basename "$f")"; done
install -o root -g imgw -m 0750 "$REL/scripts/sol_letter.py" "$W/scripts/sol_letter.py"
install -o root -g imgw -m 0750 "$REL/scripts/cpq_seed.py" "$W/scripts/cpq_seed.py"
install -o root -g root -m 0644 "$REL/tests/test_console_cpq_json.py" "$W/tests/test_console_cpq_json.py"
# 包目录需可被 imgw 读取
chmod -R a+rX "$C/cpq" "$C/strobe" "$C/security" "$C/routes"

echo "=== 4. 运行环境（密钥仅写入 $ENVF，不显示）"
install -d -o imgw -g imgw -m 0700 /var/lib/imgw/console /var/lib/imgw/console/media-cache
if [[ ! -e "$ENVF" ]]; then
  umask 077
  SECRET=$(/opt/imgw/venv/bin/python -c 'import secrets; print(secrets.token_hex(32))')
  cat > "$ENVF" <<EOF
MEDIA_UID_SECRET=$SECRET
XL_DB_PATH=/var/lib/imgw/video_knowledge_auto/equipment/catalog.sqlite3
MEDIA_ROOT=/var/lib/imgw/media
MEDIA_CACHE=/var/lib/imgw/console/media-cache
CPQ_WORK_DB=/var/lib/imgw/console/cpq_work.sqlite3
SOL_LETTER_CMD=/opt/imgw/venv/bin/python /opt/imgw/wechat/scripts/sol_letter.py
EOF
  unset SECRET
fi
chown root:imgw "$ENVF"; chmod 0640 "$ENVF"
install -d -m 0755 /etc/systemd/system/imgw-console.service.d
cat > "$DROP" <<'EOF'
[Service]
EnvironmentFile=/etc/imgw/console-cpq.env
# 函件走 Sol(codex 只读沙箱)，在 NoNewPrivileges 下无法启动；其余加固保持不变（与 imgw-inquiry-radar 一致）。
NoNewPrivileges=false
EOF
systemctl daemon-reload

echo "=== 5. 工作库与种子（占位模板；真实运价/汇率/配件成本待业主填写）"
runas() { systemd-run --wait --pipe --quiet --collect -p User=imgw -p Group=imgw -p WorkingDirectory=$W -p Environment=PYTHONPATH=$W -p Environment=PYTHONDONTWRITEBYTECODE=1 -p EnvironmentFile=$ENVF "$@"; }
seedout=$(runas /opt/imgw/venv/bin/python scripts/cpq_seed.py 2>&1); seedrc=$?
echo "$seedout" | tail -6 | sed 's/^/  /'
[[ $seedrc -eq 0 ]] || rollback '种子失败'

echo "=== 6. 测试（生产同构）"
res=$(cd "$W" && runas /opt/imgw/venv/bin/python -m pytest -p no:cacheprovider -q tests/test_console_cpq_json.py tests/test_console_v3.py tests/test_console_v3_B.py tests/test_console_v3_C.py tests/test_console_media.py 2>&1 | tail -1)
echo "  $res"; echo "$res" | grep -q " passed" && ! echo "$res" | grep -qE "failed|error" || rollback '测试未通过'
runas /opt/imgw/venv/bin/python -c "
import importlib; web=importlib.import_module('console.app')
paths=[getattr(r,'path','') for r in web.routes]
assert '/strobes' in paths and paths.index('/media/a/{asset_uid}') < paths.index('/media/{folder}/{name}'), 'cpq routes missing or misordered'
print('  routes ok:', len(paths))" || rollback 'CPQ 路由未挂载'

echo "=== 7. 重启与探测"
systemctl restart imgw-console; sleep 5
[[ "$(systemctl is-active imgw-console)" == active ]] || rollback 'imgw-console 未运行'
journalctl -u imgw-console --since "-1min" --no-pager 2>/dev/null | grep -q cpq_routes_unavailable && rollback '日志显示 CPQ 路由未挂载'
for p in /healthz / /v3/ /v3/library /strobes /api/strobes /api/cpq/context /static/js/cpq/entry.js /static/strobe/strobe.css /media/a/a_0000000000000000 /media/fengwu/FW-SEG-800.png; do
  echo "  GET $p: $(curl -s -o /dev/null -w '%{http_code}' -m 8 http://127.0.0.1:8790$p)"; done
echo "  POST /api/cpq/calculate (无会话): $(curl -s -o /dev/null -w '%{http_code}' -m 8 -X POST -H 'content-type: application/json' -d '{}' http://127.0.0.1:8790/api/cpq/calculate)"
h=$(curl -s -o /dev/null -w '%{http_code}' -m 8 http://127.0.0.1:8790/healthz); [[ "$h" == 200 ]] || rollback '/healthz 异常'
s=$(curl -s -o /dev/null -w '%{http_code}' -m 8 http://127.0.0.1:8790/strobes); [[ "$s" == 302 ]] || rollback '/strobes 未登录应为 302'

echo "=== 8. Sol 沙箱自检（与控制台相同的沙箱属性；失败仅警告，函件功能将返回 LETTER_FAILED）"
SAMPLE='{"dto":{"quote_no":"Q000000-000","buyer":{"contact":"Test"},"incoterm":"DDP","named_place":"Hamburg","lines":[{"sku":"XL-STB-001","qty":2,"unit_price":100.0,"amount":200.0}],"totals":{"total_amount":200.0}},"tone":"formal","purpose":"first_quote","extra_notes":""}'
echo "$SAMPLE" | systemd-run --wait --pipe --quiet --collect -p User=imgw -p Group=imgw -p WorkingDirectory=$W -p Environment=PYTHONPATH=$W -p NoNewPrivileges=false -p PrivateTmp=true -p ProtectSystem=strict -p ProtectHome=true -p ReadWritePaths=/var/lib/imgw -p EnvironmentFile=$ENVF /opt/imgw/venv/bin/python $W/scripts/sol_letter.py 2>&1 | head -c 600 | sed 's/^/  /' || echo '  WARN: Sol 自检失败'
echo
echo "完成。备份后缀 .bak-$TS。回滚：把 app.py.bak-$TS、templates/v3/base3.html.bak-$TS 覆盖回去，删除 $DROP 后 systemctl daemon-reload && systemctl restart imgw-console。"
