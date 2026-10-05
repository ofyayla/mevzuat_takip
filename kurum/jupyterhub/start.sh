#!/usr/bin/env bash
# Mevzuat Takip — JupyterHub (atgdevtmirpr01) üzerinde iki servisi yerel süreç olarak çalıştırır.
#   mevzuat-crawler : mevzuat-crawler serve                 (kaynak tarama → MongoDB)
#   mevzuat-core    : gunicorn (portal + API), celery worker, celery beat  (MongoDB → PostgreSQL, YZ)
#
# Önkoşul (bir kez): kurum/export.py çıktısı, iki ayrı venv, her app/ dizininde .env ve `alembic upgrade head`.
#
#   kurum/jupyterhub/start.sh [start|stop|restart|status|logs <süreç>]     # varsayılan: start
#
# Yollar ortam değişkenleriyle ezilebilir (varsayılanlar aşağıda).
set -euo pipefail

KURUM_DIR=${KURUM_DIR:-$HOME/mevzuat-kurum}
CORE_VENV=${CORE_VENV:-$HOME/venv-core}
CRAWLER_VENV=${CRAWLER_VENV:-$HOME/venv-crawler}
LOG_DIR=${LOG_DIR:-$HOME/mevzuat-logs}
API_BIND=${API_BIND:-127.0.0.1:5000}
WORKER_CONCURRENCY=${WORKER_CONCURRENCY:-2}
# Kurum CA'ları ile birleşik sertifika paketi (LLM / iç HTTPS); dosya yoksa kullanılmaz
CA_BUNDLE_FILE=${CA_BUNDLE_FILE:-$HOME/certs/bundle.pem}

CORE_APP=$KURUM_DIR/mevzuat-core/app
CRAWLER_APP=$KURUM_DIR/mevzuat-crawler/app
PROCS=(crawler api worker beat)

mkdir -p "$LOG_DIR"
if [[ -f $CA_BUNDLE_FILE ]]; then
  export SSL_CERT_FILE=$CA_BUNDLE_FILE REQUESTS_CA_BUNDLE=$CA_BUNDLE_FILE
fi

running() {   # süreç adı → çalışıyorsa 0
  local pidfile=$LOG_DIR/$1.pid
  [[ -f $pidfile ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null
}

launch() {    # ad, çalışma dizini, komut...
  local name=$1 dir=$2; shift 2
  if running "$name"; then
    echo "  $name zaten çalışıyor (pid $(cat "$LOG_DIR/$name.pid"))"
    return
  fi
  (cd "$dir" && nohup "$@" >>"$LOG_DIR/$name.log" 2>&1 & echo $! >"$LOG_DIR/$name.pid")
  sleep 2
  if running "$name"; then
    echo "  $name başladı (pid $(cat "$LOG_DIR/$name.pid"), log: $LOG_DIR/$name.log)"
  else
    echo "  $name BAŞLAMADI — son log satırları:" >&2
    tail -n 20 "$LOG_DIR/$name.log" >&2
    return 1
  fi
}

check_prereqs() {
  local ok=0
  for f in "$CORE_APP/.env" "$CRAWLER_APP/.env" "$CORE_VENV/bin/gunicorn" "$CORE_VENV/bin/celery" \
           "$CRAWLER_VENV/bin/mevzuat-crawler"; do
    [[ -e $f ]] || { echo "Eksik: $f" >&2; ok=1; }
  done
  return $ok
}

start() {
  check_prereqs
  echo "mevzuat-crawler:"
  launch crawler "$CRAWLER_APP" "$CRAWLER_VENV/bin/mevzuat-crawler" serve
  echo "mevzuat-core:"
  launch api "$CORE_APP" "$CORE_VENV/bin/gunicorn" app.api.main:app -c ../gunicorn_config.py -b "$API_BIND"
  launch worker "$CORE_APP" "$CORE_VENV/bin/celery" -A app.worker worker \
    -Q ingest,process,ai,monitor,default -c "$WORKER_CONCURRENCY" -l INFO
  launch beat "$CORE_APP" "$CORE_VENV/bin/celery" -A app.worker beat -l INFO \
    -s "$LOG_DIR/celerybeat-schedule" --pidfile=
  sleep 3
  if curl -fsS "http://$API_BIND/readyz" >/dev/null 2>&1; then
    echo "API hazır: http://$API_BIND/readyz"
  else
    echo "API henüz yanıt vermiyor; kontrol: $0 logs api" >&2
  fi
}

stop() {
  for name in "${PROCS[@]}"; do
    if running "$name"; then
      local pid; pid=$(cat "$LOG_DIR/$name.pid")
      kill "$pid"
      for _ in {1..30}; do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
      kill -0 "$pid" 2>/dev/null && kill -9 "$pid"
      echo "  $name durdu"
    else
      echo "  $name çalışmıyor"
    fi
    rm -f "$LOG_DIR/$name.pid"
  done
}

status() {
  for name in "${PROCS[@]}"; do
    if running "$name"; then
      printf '  %-8s çalışıyor (pid %s)\n' "$name" "$(cat "$LOG_DIR/$name.pid")"
    else
      printf '  %-8s DURUYOR\n' "$name"
    fi
  done
  curl -fsS "http://$API_BIND/readyz" 2>/dev/null && echo || echo "  readyz: yanıt yok"
}

case ${1:-start} in
  start)   start ;;
  stop)    stop ;;
  restart) stop; start ;;
  status)  status ;;
  logs)    tail -n 100 -f "$LOG_DIR/${2:?süreç adı: ${PROCS[*]}}.log" ;;
  *)       echo "Kullanım: $0 [start|stop|restart|status|logs <${PROCS[*]}>]" >&2; exit 2 ;;
esac
