#!/usr/bin/env bash
# ============================================================
#  Yorozuya 服务器一键部署（Ubuntu / Debian，需要 root）
# ------------------------------------------------------------
#  用法（在项目根目录执行）：
#      bash deploy/install.sh
#  或者把域名直接给它，全程不用回答提问：
#      DOMAIN=yorozuya.example.com bash deploy/install.sh
#
#  它会做五件事：
#    ① 没装 Docker 就装（官方脚本 + compose 插件）
#    ② 按你的域名生成 deploy/.env（随机数据库密码，权限 600）
#    ③ docker compose up -d --build（app + MySQL + Caddy）
#    ④ 等后端真的起来（探 /api/agent/status），失败就把日志尾巴打出来
#    ⑤ 建你的第一个账号（默认关着注册，所以在这一步建）
#
#  可以重复跑：已存在的 .env 不会被覆盖，只做「补齐 + 重启」。
# ============================================================
set -euo pipefail

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m[ok]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------- 0. 环境自检 ----------------
[ "$(id -u)" = "0" ] || die "请用 root 跑：sudo bash deploy/install.sh"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
[ -f "$ROOT/app/server.py" ] || die "没找到 $ROOT/app/server.py —— 请把整个项目传上服务器，脚本在 <项目根>/deploy/ 里跑"
[ -f "$HERE/docker-compose.yml" ] || die "没找到 $HERE/docker-compose.yml"

ensure_curl() { command -v curl >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq curl; }; }
ensure_curl

# ---------------- 1. Docker ----------------
if ! command -v docker >/dev/null 2>&1; then
  say "安装 Docker（官方脚本，约 1 分钟）"
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker >/dev/null 2>&1 || true
fi
docker compose version >/dev/null 2>&1 || die "缺 docker compose 插件；试：apt-get install -y docker-compose-v2"
ok "Docker：$(docker --version | cut -d, -f1) / $(docker compose version --short 2>/dev/null || echo v2)"

# ---------------- 2. 站点地址 ----------------
DOMAIN="${DOMAIN:-}"
if [ -z "$DOMAIN" ]; then
  printf '\n你的域名（直接回车 = 先只用 IP + http 跑通，仅测试用）: '
  read -r DOMAIN || true
fi
IP="$(curl -fsS --max-time 6 https://ifconfig.me 2>/dev/null | tr -d '\r\n' || true)"
[ -n "$IP" ] || IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "$IP" ] || IP="服务器IP"

if [ -n "$DOMAIN" ]; then
  SITE_ADDRESS="$DOMAIN"
  PUBLIC_URL="https://$DOMAIN"
  VISIT="$PUBLIC_URL"
else
  SITE_ADDRESS=":80"
  PUBLIC_URL="http://$IP"
  VISIT="$PUBLIC_URL"
  DOMAIN="$IP"
  warn "没填域名：走 $VISIT（明文 HTTP，登录 token 会明文传输，测完请换域名）"
fi

# ---------------- 3. .env ----------------
if [ -f "$HERE/.env" ]; then
  say ".env 已存在，沿用（想重来就删掉它再跑）"
else
  say "生成随机数据库密码并写 $HERE/.env"
  gen() { openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c 28; }
  R1="$(gen)"; R2="$(gen)"
  umask 077
  cat > "$HERE/.env" <<EOF
DOMAIN=$DOMAIN
SITE_ADDRESS=$SITE_ADDRESS
PUBLIC_URL=$PUBLIC_URL
MYSQL_ROOT_PASSWORD=$R1
MYSQL_PASSWORD=$R2
EOF
  chmod 600 "$HERE/.env"
  ok ".env 已生成（数据库密码是随机的，已记在里面）"
fi
# 每次跑都把站点地址同步成刚算出来的（换域名/改 IP 时不用手改）
sed -i "s|^SITE_ADDRESS=.*|SITE_ADDRESS=$SITE_ADDRESS|; s|^PUBLIC_URL=.*|PUBLIC_URL=$PUBLIC_URL|; s|^DOMAIN=.*|DOMAIN=$DOMAIN|" "$HERE/.env"

# ---------------- 4. 放行端口（只在 ufw 开着时动它）----------------
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi '^Status: active'; then
  say "ufw 是开着的，放行 80/443"
  ufw allow 80/tcp >/dev/null || true
  ufw allow 443/tcp >/dev/null || true
fi

# ---------------- 5. 起服务 ----------------
say "构建并启动（首次 1~3 分钟：拉镜像 + 建库建表）"
cd "$HERE"
docker compose up -d --build

say "等后端就绪（探 127.0.0.1:8902）"
READY=0
for i in $(seq 1 60); do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8902/api/agent/status || true)"
  # 401 = 服务在（只是没带 token），这就是我们要的
  if [ "$code" = "401" ] || [ "$code" = "200" ]; then READY=1; break; fi
  sleep 2
done
if [ "$READY" != "1" ]; then
  warn "等了 2 分钟还没就绪，下面是 app 日志的最后 40 行："
  docker compose logs --tail 40 app || true
  die "先把上面的报错解决掉，再重新跑一次本脚本"
fi
ok "后端已就绪"
docker compose ps

# ---------------- 6. 建第一个账号 ----------------
say "建你的账号（用户名 2-20 位中英文数字下划线；密码 6-64 位）"
printf '用户名（直接回车 = 跳过，稍后按最后打印的命令自己建）: '
read -r USERNAME || true
if [ -n "$USERNAME" ]; then
  printf '密码: '
  read -r PASSWORD || true
  if [ -z "$PASSWORD" ]; then
    warn "密码空着，跳过建号"
  else
    RESP="$(curl -sS -X POST http://127.0.0.1:8902/api/auth/register \
              -H 'Content-Type: application/json' \
              -d "{\"username\":\"$USERNAME\",\"password\":\"$PASSWORD\"}" || true)"
    case "$RESP" in
      *token*) ok "账号已建好：$USERNAME" ;;
      *)       warn "建号返回：$RESP（用户名被占用/密码太短之类，按提示改了重跑本脚本即可）" ;;
    esac
  fi
fi

# ---------------- 7. 收尾提示 ----------------
cat <<EOF

=====================================================================
 部署完成
---------------------------------------------------------------------
 访问：   $VISIT
 登录：   用刚建的账号（用户名 $USERNAME 或你自己建的那个）
 必做①    登录后进「设置」填你的模型接口（apiUrl / apiKey / 模型名），
          不填就是演示模式（本地语料回复，不调模型）
 必做②    确认注册/访客已关闭（应当都返回 403）：
          curl -s -o /dev/null -w '%{http_code}\n' -X POST $VISIT/api/auth/register \\
               -H 'Content-Type: application/json' -d '{"username":"x","password":"123456"}'
 没建号   curl -s -X POST $VISIT/api/auth/register \\
             -H 'Content-Type: application/json' \\
             -d '{"username":"yorozuya","password":"你的密码"}'
---------------------------------------------------------------------
 常用命令（都在 $HERE 下跑）：
   docker compose logs -f app     看后端日志
   docker compose restart app     重启后端
   docker compose down / up -d    停 / 起
 备份数据库：
   docker compose exec -T db sh -c 'mysqldump -uroot -p"\$MYSQL_ROOT_PASSWORD" zhiban' > zhiban-\$(date +%F).sql
=====================================================================
EOF
