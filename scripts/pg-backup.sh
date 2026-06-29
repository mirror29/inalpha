#!/usr/bin/env bash
# Inalpha Postgres 每日备份:pg_dump→gzip→本地留14份 + 上传R2(留15天),独立目录防 down -v
set -euo pipefail
DIR="$HOME/pg-backups"; mkdir -p "$DIR"
TS=$(date +%Y%m%d-%H%M%S); OUT="$DIR/inalpha-$TS.sql.gz"
if ! sudo docker ps --format '{{.Names}}' | grep -q '^inalpha-postgres$'; then
  echo "$(date '+%F %T') ERROR: inalpha-postgres 没在跑,跳过"; exit 1; fi
sudo docker exec inalpha-postgres sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges' \
  | gzip > "$OUT.tmp"
mv "$OUT.tmp" "$OUT"
ls -1t "$DIR"/inalpha-*.sql.gz 2>/dev/null | tail -n +15 | xargs -r rm -f
if rclone copy "$OUT" r2:inalpha-backups/ 2>/dev/null; then
  rclone delete --min-age 15d r2:inalpha-backups/ 2>/dev/null || true; R2="R2 OK"
else R2="R2 上传失败(本地已留)"; fi
echo "$(date '+%F %T') 本地 OK: $(basename "$OUT") ($(du -h "$OUT" | cut -f1)) | $R2 | 本地存 $(ls -1 "$DIR"/inalpha-*.sql.gz | wc -l) 份"
