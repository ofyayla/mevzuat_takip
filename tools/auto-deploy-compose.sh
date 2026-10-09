#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

POLL_INTERVAL="${AUTO_UPDATE_INTERVAL:-30}"
UPSTREAM_REF="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)"
DEPLOYMENT_PENDING=1

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

if ! [[ "$POLL_INTERVAL" =~ ^[1-9][0-9]*$ ]]; then
  printf 'AUTO_UPDATE_INTERVAL pozitif bir tam sayı olmalı.\n' >&2
  exit 2
fi

if [[ -z "$UPSTREAM_REF" ]]; then
  printf 'Bu branch için Git upstream tanımlı değil; önce upstream ayarlayın.\n' >&2
  exit 2
fi

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  printf 'Docker Compose v2 bulunamadı.\n' >&2
  exit 2
fi

sync_remote() {
  if ! git fetch --quiet; then
    log 'Git fetch başarısız; sonraki kontrolde yeniden denenecek.'
    return 1
  fi

  local current_commit target_commit
  current_commit="$(git rev-parse HEAD)"
  if ! target_commit="$(git rev-parse --verify "${UPSTREAM_REF}^{commit}" 2>/dev/null)"; then
    log "Upstream commit bulunamadı: $UPSTREAM_REF"
    return 1
  fi

  if [[ "$current_commit" != "$target_commit" ]]; then
    if git merge-base --is-ancestor "$current_commit" "$target_commit"; then
      if [[ -n "$(git status --porcelain)" ]]; then
        log 'Yerel değişiklikler var; Git güncellemesi güvenlik için uygulanmadı.'
        return 1
      fi

      if ! git merge --ff-only --quiet "$UPSTREAM_REF"; then
        log 'Fast-forward uygulanamadı; uygulama güncellenmedi.'
        return 1
      fi

      log "Yeni commit alındı ($(git rev-parse --short HEAD)); Compose imajları güncelleniyor."
      DEPLOYMENT_PENDING=1
    elif ! git merge-base --is-ancestor "$target_commit" "$current_commit"; then
      log 'Yerel ve uzak branch ayrışmış; otomatik birleştirme yapılmadı.'
      return 1
    fi
  fi
}

apply_compose() {
  if docker compose up -d --build; then
    DEPLOYMENT_PENDING=0
  else
    log 'Compose güncellemesi başarısız; sonraki kontrolde yeniden denenecek.'
    return 1
  fi
}

ensure_api_running() {
  local api_running running_services
  running_services="$(docker compose ps --status running --services 2>/dev/null || true)"
  api_running=0
  if printf '%s\n' "$running_services" | grep -Fxq 'api'; then
    api_running=1
  fi

  if [[ "$DEPLOYMENT_PENDING" == '1' || "$api_running" == '0' ]]; then
    if [[ "$api_running" == '0' ]]; then
      log 'API çalışmıyor; Compose uygulaması başlatılıyor.'
    fi
    apply_compose
  fi
}

log "Otomatik güncelleme başladı; izlenen upstream: $UPSTREAM_REF (aralık: ${POLL_INTERVAL}s)."
trap 'log "Otomatik güncelleme durduruldu."; exit 0' INT TERM

while true; do
  sync_remote || true
  ensure_api_running || true
  sleep "$POLL_INTERVAL"
done