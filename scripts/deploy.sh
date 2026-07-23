#!/usr/bin/env bash
#
# Inalpha 生产部署入口(VPS 手动)—— 见 docs/miro/decisions/0058
#
# 默认走「拉 GHCR 预构建镜像」路径:CI(build-images.yml)已 build 好,
# VPS 只 pull 不 build,绕开小内存机现场 build OOM。
#
# 在 VPS 仓库根目录运行:
#   bash scripts/deploy.sh            # git pull + compose pull + up -d(推荐)
#   bash scripts/deploy.sh --no-pull  # 跳过 git pull(已 checkout 指定版本时)
#   bash scripts/deploy.sh --build    # 就地 build 而非拉镜像(应急;4G 机器慎用,可能 OOM)
#
# 前置:
#   - infra/.env.prod 已填好(含 IMAGE_PREFIX / IMAGE_TAG 指向 GHCR)
#   - 若 GHCR 包为 private:先 `docker login ghcr.io`(PAT 需 read:packages 权限)
#
set -euo pipefail

# 仓库根 = 本脚本上一级目录
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE_FILE="infra/docker-compose.prod.yml"
ENV_FILE="infra/.env.prod"
export ENV_FILE=.env.prod
DC=(docker compose --profile tunnel -f "$COMPOSE_FILE" --env-file "$ENV_FILE")

do_git_pull=1
mode="image" # image | build

for arg in "$@"; do
  case "$arg" in
    --no-pull) do_git_pull=0 ;;
    --build)   mode="build" ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "未知参数: $arg(用 --help 看用法)" >&2; exit 2 ;;
  esac
done

[ -f "$ENV_FILE" ] || {
  echo "缺 $ENV_FILE —— 从 infra/.env.prod.example 复制并填值" >&2
  exit 1
}

if ! grep -qE '^CLOUDFLARE_TUNNEL_TOKEN=.+$' "$ENV_FILE"; then
  echo "缺 CLOUDFLARE_TUNNEL_TOKEN ——生产 tunnel profile 无法启动" >&2
  exit 1
fi

if [ "$do_git_pull" -eq 1 ]; then
  echo "==> git pull --ff-only"
  git pull --ff-only
fi

if [ "$mode" = "image" ]; then
  echo "==> 拉取镜像(GHCR)"
  "${DC[@]}" pull
  echo "==> 起栈(migrate 先跑 alembic upgrade head,再起各服务)"
  "${DC[@]}" up -d
else
  echo "==> 就地构建并起栈(--build;小内存机注意 OOM)"
  "${DC[@]}" up -d --build
fi

echo "==> 当前状态"
"${DC[@]}" ps

echo "==> 完成。排障:'${DC[*]} logs -f <service>';健康看上方 ps 的 STATUS。"
