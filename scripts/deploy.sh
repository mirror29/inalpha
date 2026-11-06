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
HOST_ENV_FILE="infra/.env.prod"
# Compose 内的 env_file 相对 compose 文件解析，因此容器侧仍使用文件名。
export ENV_FILE=.env.prod
DC=(docker compose --profile tunnel -f "$COMPOSE_FILE" --env-file "$HOST_ENV_FILE")

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

[ -f "$HOST_ENV_FILE" ] || {
  echo "缺 $HOST_ENV_FILE —— 从 infra/.env.prod.example 复制并填值" >&2
  exit 1
}

if ! grep -qE '^CLOUDFLARE_TUNNEL_TOKEN=.+$' "$HOST_ENV_FILE"; then
  echo "缺 CLOUDFLARE_TUNNEL_TOKEN ——生产 tunnel profile 无法启动" >&2
  exit 1
fi

command -v python3 >/dev/null
command -v flock >/dev/null
mkdir -p "$ROOT/.cache"
exec 9>"$ROOT/.cache/deploy.lock"
flock -n 9 || { echo "已有部署正在运行" >&2; exit 1; }

if [ "$do_git_pull" -eq 1 ]; then
  echo "==> git pull --ff-only"
  git pull --ff-only
fi

protected_images="$(mktemp)"
trap 'rm -f "$protected_images"' EXIT
container_ids="$(docker ps -aq)"
if [ -n "$container_ids" ]; then
  docker inspect --format '{{.Image}}' $container_ids > "$protected_images"
fi
image_refs="$("${DC[@]}" config --images)"
cleanup_args=()
while IFS= read -r ref; do
  repository="${ref%@*}"
  if [[ "${repository##*/}" == *:* ]]; then
    repository="${repository%:*}"
  fi
  case "${repository##*/}" in
    inalpha-data|inalpha-paper|inalpha-research|inalpha-factor|inalpha-evolver|inalpha-mastra|inalpha-dashboard)
      cleanup_args+=(--repository "$repository") ;;
  esac
done <<< "$image_refs"

if [ "$mode" = "image" ]; then
  echo "==> 拉取镜像(GHCR)"
  "${DC[@]}" pull
  echo "==> 起栈(migrate 先跑 alembic upgrade head,再起各服务)"
  "${DC[@]}" up -d --wait --wait-timeout 300
else
  echo "==> 就地构建并起栈(--build;小内存机注意 OOM)"
  "${DC[@]}" up -d --build --wait --wait-timeout 300
fi

echo "==> 当前状态"
"${DC[@]}" ps

if [ "${#cleanup_args[@]}" -gt 0 ]; then
  echo "==> 清理旧应用镜像（保留最近两个版本、部署前版本及容器引用）"
  if ! python3 scripts/cleanup-images.py "${cleanup_args[@]}" \
    --protect-file "$protected_images" --apply; then
    echo "警告：服务已部署，但镜像清理失败；请检查后重试。" >&2
  fi
fi

echo "==> 完成。排障:'${DC[*]} logs -f <service>';健康看上方 ps 的 STATUS。"
