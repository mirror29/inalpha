#!/usr/bin/env bash
# 拉取公有领域 Rider–Waite–Smith 塔罗牌图(78 张)到 public/tarot/。
#
# 源:Wikimedia Commons(Pamela Coleman Smith 1909,已入公有领域)。
# 走 upload.wikimedia.org 整图直链(路径含 md5 哈希),下载后用 macOS `sips`
# 缩到 420px 高、存为干净命名 <key>.jpg,整图即删——只把 ~45KB/张的缩略图入库。
#
# 命名(与前端 TarotCards.tsx 的 cardKey 一致):
#   大牌 major-00.jpg .. major-21.jpg
#   小牌 wands-01.jpg .. pentacles-14.jpg(01=Ace .. 11=Page 12=Knight 13=Queen 14=King)
#
# 用法:
#   bash apps/dashboard/scripts/fetch-tarot.sh           # 下载缺失的
#   bash apps/dashboard/scripts/fetch-tarot.sh --verify  # 只校验 URL 可达(不下载)
#   bash apps/dashboard/scripts/fetch-tarot.sh --force   # 重下全部
set -uo pipefail

VERIFY=0; FORCE=0
for a in "$@"; do
  [ "$a" = "--verify" ] && VERIFY=1
  [ "$a" = "--force" ] && FORCE=1
done

DIR="$(cd "$(dirname "$0")/.." && pwd)/public/tarot"
mkdir -p "$DIR"
UA="InalphaTarotFetch/1.0 (https://inalpha.dev)"
HEIGHT=420

# key -> Wikimedia 文件名
declare -a MAP=(
  "major-00:RWS_Tarot_00_Fool.jpg"
  "major-01:RWS_Tarot_01_Magician.jpg"
  "major-02:RWS_Tarot_02_High_Priestess.jpg"
  "major-03:RWS_Tarot_03_Empress.jpg"
  "major-04:RWS_Tarot_04_Emperor.jpg"
  "major-05:RWS_Tarot_05_Hierophant.jpg"
  "major-06:RWS_Tarot_06_Lovers.jpg"
  "major-07:RWS_Tarot_07_Chariot.jpg"
  "major-08:RWS_Tarot_08_Strength.jpg"
  "major-09:RWS_Tarot_09_Hermit.jpg"
  "major-10:RWS_Tarot_10_Wheel_of_Fortune.jpg"
  "major-11:RWS_Tarot_11_Justice.jpg"
  "major-12:RWS_Tarot_12_Hanged_Man.jpg"
  "major-13:RWS_Tarot_13_Death.jpg"
  "major-14:RWS_Tarot_14_Temperance.jpg"
  "major-15:RWS_Tarot_15_Devil.jpg"
  "major-16:RWS_Tarot_16_Tower.jpg"
  "major-17:RWS_Tarot_17_Star.jpg"
  "major-18:RWS_Tarot_18_Moon.jpg"
  "major-19:RWS_Tarot_19_Sun.jpg"
  "major-20:RWS_Tarot_20_Judgement.jpg"
  "major-21:RWS_Tarot_21_World.jpg"
)
# 小牌:后端 arcana -> Wikimedia 花色前缀
declare -a SUITS=("wands:Wands" "cups:Cups" "swords:Swords" "pentacles:Pents")
for s in "${SUITS[@]}"; do
  arc="${s%%:*}"; pre="${s##*:}"
  for n in $(seq 1 14); do
    nn=$(printf "%02d" "$n")
    MAP+=("${arc}-${nn}:${pre}${nn}.jpg")
  done
done

fail=0; got=0; skip=0
for entry in "${MAP[@]}"; do
  key="${entry%%:*}"; file="${entry##*:}"
  out="$DIR/$key.jpg"
  if [ "$FORCE" = "0" ] && [ "$VERIFY" = "0" ] && [ -f "$out" ]; then
    skip=$((skip+1)); continue
  fi
  h=$(printf '%s' "$file" | md5 -q)
  url="https://upload.wikimedia.org/wikipedia/commons/${h:0:1}/${h:0:2}/$file"
  if [ "$VERIFY" = "1" ]; then
    code=$(curl -sS -A "$UA" -o /dev/null -w "%{http_code}" --max-time 30 -I "$url" 2>/dev/null)
    if [ "$code" != "200" ]; then echo "✗ $key  HTTP $code  $file"; fail=$((fail+1)); fi
    continue
  fi
  tmp=$(mktemp /tmp/tarot.XXXXXX)
  code=$(curl -sS -A "$UA" -o "$tmp" -w "%{http_code}" --max-time 60 "$url" 2>/dev/null)
  if [ "$code" != "200" ]; then echo "✗ $key  HTTP $code  $file"; rm -f "$tmp"; fail=$((fail+1)); continue
  fi
  if sips -Z "$HEIGHT" "$tmp" --out "$out" >/dev/null 2>&1; then
    got=$((got+1)); echo "✓ $key  ($(stat -f%z "$out") B)"
  else
    echo "✗ $key  sips 失败"; fail=$((fail+1))
  fi
  rm -f "$tmp"
done

echo "---"
if [ "$VERIFY" = "1" ]; then
  echo "校验完成:失败 $fail / ${#MAP[@]}"
else
  echo "下载 $got,跳过 $skip,失败 $fail / ${#MAP[@]}"
fi
[ "$fail" = "0" ]
