#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

POLL_INTERVAL="${AUTO_UPDATE_INTERVAL:-30}"
UPSTREAM_REF="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)"
START_SCRIPT="$REPO_ROOT/jupyterhub/start.sh"
DEPLOYMENT_PENDING=0

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

if [[ ! -f "$START_SCRIPT" ]]; then
  printf 'Uygulama başlatma scripti bulunamadı: %s\n' "$START_SCRIPT" >&2
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

      log "Yeni commit alındı ($(git rev-parse --short HEAD)); uygulama süreçleri güncellenecek."
      DEPLOYMENT_PENDING=1
    elif ! git merge-base --is-ancestor "$target_commit" "$current_commit"; then
      log 'Yerel ve uzak branch ayrışmış; otomatik birleştirme yapılmadı.'
      return 1
    fi
  fi
}

apply_lifecycle_command() {
  local command=$1
  if bash "$START_SCRIPT" "$command"; then
    DEPLOYMENT_PENDING=0
  else
    log "jupyterhub/start.sh $command başarısız; sonraki kontrolde yeniden denenecek."
    return 1
  fi
}

application_is_ready() {
  local status_output
  if ! status_output="$(bash "$START_SCRIPT" status 2>&1)"; then
    log 'Uygulama durumu alınamadı; başlatma/güncelleme denenecek.'
    return 1
  fi

  if grep -Fq 'readyz: yanıt yok' <<< "$status_output"; then
    return 1
  fi

  if grep -Fq 'DURUYOR' <<< "$status_output"; then
    return 1
  fi

  return 0
}

ensure_application_running() {
  local app_running=0
  if application_is_ready; then
    app_running=1
  fi

  if [[ "$DEPLOYMENT_PENDING" == '1' ]]; then
    log 'Yeni Git commiti için jupyterhub/start.sh update çalıştırılıyor.'
    apply_lifecycle_command update
  elif [[ "$app_running" == '0' ]]; then
    log 'Uygulama hazır değil; jupyterhub/start.sh start çalıştırılıyor.'
    apply_lifecycle_command start
  fi
}

log "Otomatik güncelleme başladı; izlenen upstream: $UPSTREAM_REF (aralık: ${POLL_INTERVAL}s)."
trap 'log "Otomatik güncelleme durduruldu."; exit 0' INT TERM

while true; do
  sync_remote || true
  ensure_application_running || true
  sleep "$POLL_INTERVAL"
done
