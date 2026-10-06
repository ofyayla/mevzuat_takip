#!/usr/bin/env bash
# Mevzuat Takip — JupyterHub (atgdevtmirpr01) üzerinde iki servisi yerel süreç olarak kurar ve çalıştırır.
#   mevzuat-crawler : mevzuat-crawler serve                 (kaynak tarama → MongoDB)
#   mevzuat-core    : gunicorn (portal + API), celery worker, celery beat  (MongoDB → PostgreSQL, YZ)
#
#   kurum/jupyterhub/start.sh [komut]
#     start    (varsayılan) kurulum adımları (eksik olanlar) + süreçleri başlat
#     setup    yalnızca kurulum: venv'ler, kurum/export.py, .env, PostgreSQL kullanıcı/DB, alembic, units-load
#     update   durdur → export'u yeniden üret (git pull sonrası) → setup → başlat
#     check    Mongo/Redis/Postgres erişimi, LLM testi (llm-check), crawler kontrolü
#     stop | restart | status | logs <crawler|api|worker|beat|setup>
#
# Ayarlar ve parolalar tek dosyada: ~/.mevzuat/mevzuat.env (ilk çalıştırmada şablonu oluşturulur).
# Her app/.env bu dosyadan üretilir; app/.env elle düzenlenmez (her setup'ta yeniden yazılır).
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
CONF_DIR=${CONF_DIR:-$HOME/.mevzuat}
CONF=$CONF_DIR/mevzuat.env
KURUM_DIR=${KURUM_DIR:-$HOME/mevzuat-kurum}
CORE_VENV=${CORE_VENV:-$HOME/venv-core}
CRAWLER_VENV=${CRAWLER_VENV:-$HOME/venv-crawler}
LOG_DIR=${LOG_DIR:-$HOME/mevzuat-logs}
CERT_DIR=${CERT_DIR:-$HOME/certs}
PY_ENV=${PY_ENV:-$CONF_DIR/python312}       # sistemde Python ≥ 3.11 yoksa conda ile buraya kurulur
CA_CERT_URLS=${CA_CERT_URLS:-"https://nexus.alb.albarakatech.com/repository/atg-raw-file/alb_ca/albaraka-root-ca-2042.crt https://nexus.alb.albarakatech.com/repository/atg-raw-file/alb_ca/albaraka-sub-ca-2041.crt"}
export PIP_DISABLE_PIP_VERSION_CHECK=1

CORE_APP=$KURUM_DIR/mevzuat-core/app
CRAWLER_APP=$KURUM_DIR/mevzuat-crawler/app
PROCS=(crawler api worker beat)
SETSID=$(command -v setsid || true)
SETUP_LOG=$LOG_DIR/setup.log

mkdir -p "$LOG_DIR"
[[ ${DEBUG:-} == 1 ]] && set -x    # DEBUG=1 kurum/jupyterhub/start.sh → her komutu göster

say()  { echo "▸ $(date +%H:%M:%S) $*"; }
die()  { echo "HATA: $*" >&2; exit 1; }
quiet() {     # uzun çıktılı komutları setup.log'a yazar; hata olursa son satırları gösterir
  if ! "$@" >>"$SETUP_LOG" 2>&1; then
    echo "HATA: $* — son log satırları ($SETUP_LOG):" >&2
    tail -n 25 "$SETUP_LOG" >&2
    exit 1
  fi
}

# ---------------------------------------------------------------- ayarlar
write_conf_template() {
  mkdir -p "$CONF_DIR" && chmod 700 "$CONF_DIR"
  cat >"$CONF" <<'EOF'
# Mevzuat Takip — JupyterHub ayarları (kurum/jupyterhub/start.sh okur). Değerleri tek tırnak içinde yazın.
# Parolalar yalnızca bu dosyada durur (chmod 600); repoya eklenmez.

# PostgreSQL (127.0.0.1:5433). Yönetici hesabı yalnızca uygulama kullanıcısı/DB yoksa oluşturmak için kullanılır.
PG_HOST='127.0.0.1'
PG_PORT='5433'
PG_ADMIN_USER='postgres'
PG_ADMIN_PASSWORD=''
MEVZUAT_PG_USER='mevzuat'
MEVZUAT_PG_PASSWORD=''
MEVZUAT_PG_DB='mevzuat'

# Redis (127.0.0.1:6380) — Celery kuyruğu
REDIS_HOST='127.0.0.1'
REDIS_PORT='6380'
REDIS_PASSWORD=''
REDIS_DB='0'

# MongoDB (127.0.0.1:27017) — crawler yazar, core okur. Kimlik doğrulama varsa:
# mongodb://kullanici:parola@127.0.0.1:27017/?authSource=admin
MONGO_URL='mongodb://127.0.0.1:27017'
MONGO_DB='mevzuat_crawl'

# LLM (vLLM, OpenAI uyumlu)
LLM_BASE_URL='https://qwen36-35b-a3b-nvfp4.llm.alb.albarakatech.com/v1'
LLM_MODEL='qwen36-35b-a3b-nvfp4'
LLM_API_KEY='EMPTY'
LLM_MAX_CONCURRENCY='2'
# 'false': LLM sunucusunun TLS sertifikası doğrulanmaz (kurum CA'sı pakete eklenene kadar; YALNIZCA geliştirme)
LLM_VERIFY_TLS='false'

# Crawler internete proxy ile çıkıyorsa (ör. http://proxy.kurum.local:8080)
HTTPS_PROXY=''

# Portal + API (yalnızca localhost; tarayıcıdan: ssh -L 5000:127.0.0.1:5000 <kullanıcı>@atgdevtmirpr01)
API_BIND='127.0.0.1:5000'
WORKER_CONCURRENCY='2'
EOF
  chmod 600 "$CONF"
}

load_conf() {
  if [[ ! -f $CONF ]]; then
    write_conf_template
    die "Ayar dosyası oluşturuldu: $CONF — parolaları (MEVZUAT_PG_PASSWORD, REDIS_PASSWORD, gerekirse PG_ADMIN_PASSWORD) doldurup tekrar çalıştırın."
  fi
  # shellcheck source=/dev/null
  . "$CONF"
  API_BIND=${API_BIND:-127.0.0.1:5000}
  WORKER_CONCURRENCY=${WORKER_CONCURRENCY:-2}
}

require_secrets() {
  [[ -n ${MEVZUAT_PG_PASSWORD:-} ]] || die "MEVZUAT_PG_PASSWORD boş ($CONF)"
}

ca_env() {    # kurum CA'ları + certifi birleşik paketi varsa Python'a tanıt (LLM / iç HTTPS)
  if [[ -f $CERT_DIR/bundle.pem ]]; then
    export SSL_CERT_FILE=$CERT_DIR/bundle.pem REQUESTS_CA_BUNDLE=$CERT_DIR/bundle.pem
  fi
}

# ---------------------------------------------------------------- kurulum adımları
pick_python() {   # Python ≥ 3.11 bulur; yoksa conda/mamba ile $PY_ENV'e 3.12 kurar (stdout: python yolu)
  local p conda=""
  for p in ${PYTHON:-} "$PY_ENV/bin/python" python3.13 python3.12 python3.11 python3 \
           "$HOME"/.conda/envs/*/bin/python /opt/conda/envs/*/bin/python /opt/tljh/user/envs/*/bin/python; do
    if command -v "$p" >/dev/null 2>&1 &&
       "$p" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
      echo "$p"; return
    fi
  done
  for p in mamba conda /opt/tljh/user/bin/mamba /opt/tljh/user/bin/conda /opt/conda/bin/mamba /opt/conda/bin/conda \
           "$HOME"/miniconda3/bin/conda "$HOME"/anaconda3/bin/conda /opt/anaconda3/bin/conda; do
    if command -v "$p" >/dev/null 2>&1; then conda=$p; break; fi
  done
  [[ -n $conda ]] || die "Python ≥ 3.11 ve conda bulunamadı (sistemde: $(python3 --version 2>&1)). Yöneticiden python3.12 isteyin veya PYTHON=/yol/python3.12 ile verin."
  say "Python 3.12 kuruluyor: $conda create -p $PY_ENV python=3.12 (birkaç dakika; ayrıntı: $SETUP_LOG)" >&2
  if ! "$conda" create -y -p "$PY_ENV" python=3.12 >>"$SETUP_LOG" 2>&1; then
    tail -n 25 "$SETUP_LOG" >&2
    die "conda ile Python 3.12 kurulamadı (kanal erişimi? ~/.condarc ile kurum aynası gerekebilir)"
  fi
  echo "$PY_ENV/bin/python"
}

pip_env() {   # venv'ler, kaynak Python'un pip ayarını (dizin, trusted-host, proxy …) PIP_* ortam değişkenleriyle alır
  # Conda ortamının kendi pip.conf'u ($CONDA_PREFIX/pip.conf) venv'e geçmez; bu yüzden ayar buradan aktarılır.
  # PIP_CONFIG_FILE verilmişse (ör. repodaki kurum pip.conf'u) o kullanılır.
  local line key val
  if [[ -z ${PIP_CONFIG_FILE:-} ]]; then
    while IFS= read -r line; do
      [[ $line == *.*=* ]] || continue
      key=${line%%=*}; val=${line#*=}; val=${val#\'}; val=${val%\'}
      key=${key#*.}; key=${key//-/_}
      export "PIP_${key^^}=$val"
    done < <("$PY" -m pip config list 2>/dev/null)
  fi
  # erişilemeyen dizinde saatlerce beklemesin (kurum pip.conf'unda timeout=180, retries=20)
  export PIP_TIMEOUT=${PIP_FORCE_TIMEOUT:-30} PIP_RETRIES=${PIP_FORCE_RETRIES:-3}
  say "pip dizini: ${PIP_INDEX_URL:-https://pypi.org/simple (varsayılan)}${PIP_CONFIG_FILE:+ (ayar: $PIP_CONFIG_FILE)}"
}

ensure_venv() {   # venv, requirements dosyası → yoksa oluştur; requirements değiştiyse yeniden kur
  local venv=$1 req=$2 stamp sum
  say "venv kontrolü: $venv"
  stamp=$venv/.requirements.sha256
  sum=$(sha256sum "$req" | cut -d' ' -f1)
  if [[ -x $venv/bin/python ]] && ! "$venv/bin/python" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    say "eski Python'lu venv siliniyor: $venv ($("$venv/bin/python" --version 2>&1))"
    rm -rf "$venv"
  fi
  if [[ ! -x $venv/bin/python ]]; then
    say "venv oluşturuluyor: $venv ($PY)"
    quiet "$PY" -m venv "$venv"
    quiet "$venv/bin/pip" install -U pip
  fi
  if [[ $(cat "$stamp" 2>/dev/null) != "$sum" ]]; then
    say "bağımlılıklar kuruluyor: $venv (birkaç dakika sürebilir; ayrıntı: $SETUP_LOG)"
    quiet "$venv/bin/pip" install -r "$req"
    echo "$sum" >"$stamp"
  fi
}

ensure_export() {
  say "export kontrolü: $KURUM_DIR"
  if [[ ! -d $KURUM_DIR/mevzuat-core || ! -d $KURUM_DIR/mevzuat-crawler ]]; then
    say "kurum/export.py → $KURUM_DIR"
    quiet "$CORE_VENV/bin/python" "$REPO/kurum/export.py" "$KURUM_DIR" --force
    rm -f "$CORE_VENV/.editable" "$CRAWLER_VENV/.editable"
  fi
}

ensure_editable() {   # venv, app dizini → komut satırı araçları (mevzuat-ai, mevzuat-crawler …)
  local venv=$1 app=$2
  if [[ ! -f $venv/.editable ]]; then
    say "paket kuruluyor: $app"
    quiet "$venv/bin/pip" install --no-deps -e "$app"
    touch "$venv/.editable"
  fi
}

urlq() { "$CORE_VENV/bin/python" -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"; }

write_envs() {    # ~/.mevzuat/mevzuat.env → iki app/.env (her setup'ta yeniden yazılır)
  local db_url redis_url redis_auth=""
  db_url="postgresql+psycopg://$(urlq "$MEVZUAT_PG_USER"):$(urlq "$MEVZUAT_PG_PASSWORD")@${PG_HOST}:${PG_PORT}/${MEVZUAT_PG_DB}"
  [[ -n ${REDIS_PASSWORD:-} ]] && redis_auth=":$(urlq "$REDIS_PASSWORD")@"
  redis_url="redis://${redis_auth}${REDIS_HOST}:${REDIS_PORT}/${REDIS_DB}"

  umask 077
  cat >"$CORE_APP/.env" <<EOF
# start.sh tarafından $CONF dosyasından üretildi — burada düzenlemeyin.
APP_ENV=local
TZ=Europe/Istanbul
LOG_LEVEL=INFO
DATABASE_URL=$db_url
DB_AUTO_CREATE=false
REDIS_URL=$redis_url
CRAWL_MODE=remote
RAW_STORAGE=gridfs
MONGO_URL=$MONGO_URL
MONGO_DB=$MONGO_DB
CRAWLER_STALE_MINUTES=30
LLM_PROVIDER=vllm
LLM_BASE_URL=$LLM_BASE_URL
LLM_MODEL=$LLM_MODEL
LLM_API_KEY=$LLM_API_KEY
LLM_VERIFY_TLS=${LLM_VERIFY_TLS:-true}
LLM_TIMEOUT_S=120
LLM_MAX_CONCURRENCY=${LLM_MAX_CONCURRENCY:-2}
LLM_ENABLE_THINKING=true
LLM_JSON_MODE=json_schema
OCR_PROVIDER=none
AUTH_MODE=disabled
CORS_ORIGINS=
EOF
  cat >"$CRAWLER_APP/.env" <<EOF
# start.sh tarafından $CONF dosyasından üretildi — burada düzenlemeyin.
TZ=Europe/Istanbul
LOG_LEVEL=INFO
CRAWL_MODE=remote
RAW_STORAGE=gridfs
MONGO_URL=$MONGO_URL
MONGO_DB=$MONGO_DB
CRAWLER_MAX_PARALLEL=3
CRAWLER_POLL_S=15
HTTPS_PROXY=${HTTPS_PROXY:-}
CA_BUNDLE=
HTTP_USER_AGENT=MevzuatTakipBot/1.0 (+Mevzuat ve Uyum Baskanligi)
COLLECT_REQUEST_DELAY_S=1.5
COLLECT_TIMEOUT_S=30
COLLECT_MAX_ATTACHMENTS=5
EOF
  umask 022
}

ensure_ca_bundle() {    # bir kez dener; başarısız olursa sistem/certifi deposu kullanılır
  [[ -f $CERT_DIR/bundle.pem || -f $CERT_DIR/.indirilemedi || -z $CA_CERT_URLS ]] && return
  mkdir -p "$CERT_DIR"
  say "kurum CA sertifikaları indiriliyor (en çok ~40 sn)"
  local u ok=1
  for u in $CA_CERT_URLS; do
    curl -fsSk --connect-timeout 10 --max-time 20 -o "$CERT_DIR/$(basename "$u")" "$u" 2>>"$SETUP_LOG" || ok=0
  done
  if [[ $ok == 1 ]]; then
    cat "$("$CORE_VENV/bin/python" -m certifi)" "$CERT_DIR"/*.crt >"$CERT_DIR/bundle.pem"
    say "kurum CA paketi: $CERT_DIR/bundle.pem"
  else
    touch "$CERT_DIR/.indirilemedi"   # her başlatmada yeniden denenmez; tekrar denemek için bu dosyayı silin
    say "kurum CA sertifikaları indirilemedi (atlandı; LLM'de SSL hatası olursa: $CERT_DIR/bundle.pem)"
  fi
}

ensure_services_reachable() {
  MONGO_URL=$MONGO_URL REDIS_URL_CHECK="redis://${REDIS_HOST}:${REDIS_PORT}/${REDIS_DB}" REDIS_PASSWORD=${REDIS_PASSWORD:-} \
  "$CORE_VENV/bin/python" - <<'EOF' || die "MongoDB/Redis erişilemiyor (yukarıdaki hata)"
import os, sys, pymongo, redis
try:
    pymongo.MongoClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=3000).admin.command("ping")
except Exception as e:
    sys.exit(f"MongoDB: {e}")
try:
    redis.Redis.from_url(os.environ["REDIS_URL_CHECK"], password=os.environ["REDIS_PASSWORD"] or None,
                         socket_timeout=3).ping()
except Exception as e:
    sys.exit(f"Redis: {e}")
EOF
}

ensure_database() {   # uygulama kullanıcısı bağlanabiliyorsa dokunmaz; yoksa yönetici hesabıyla oluşturur
  PG_HOST=$PG_HOST PG_PORT=$PG_PORT PG_ADMIN_USER=$PG_ADMIN_USER PG_ADMIN_PASSWORD=${PG_ADMIN_PASSWORD:-} \
  APP_USER=$MEVZUAT_PG_USER APP_PW=$MEVZUAT_PG_PASSWORD APP_DB=$MEVZUAT_PG_DB \
  "$CORE_VENV/bin/python" - <<'EOF' || die "PostgreSQL hazırlanamadı (yukarıdaki hata)"
import os, sys, psycopg
from psycopg import sql
e = os.environ
common = dict(host=e["PG_HOST"], port=e["PG_PORT"], connect_timeout=5)
try:
    psycopg.connect(user=e["APP_USER"], password=e["APP_PW"], dbname=e["APP_DB"], **common).close()
    sys.exit(0)
except psycopg.OperationalError as err:
    first = str(err).strip().splitlines()[-1]
if not e["PG_ADMIN_PASSWORD"]:
    sys.exit(f"Uygulama kullanıcısıyla bağlanılamadı ({first}); oluşturmak için PG_ADMIN_PASSWORD gerekli")
with psycopg.connect(user=e["PG_ADMIN_USER"], password=e["PG_ADMIN_PASSWORD"], dbname="postgres",
                     autocommit=True, **common) as c:
    if c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (e["APP_USER"],)).fetchone():
        c.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(e["APP_USER"]),
                                                                   sql.Literal(e["APP_PW"])))
    else:
        c.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(e["APP_USER"]),
                                                                    sql.Literal(e["APP_PW"])))
    if not c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (e["APP_DB"],)).fetchone():
        c.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(e["APP_DB"]),
                                                                sql.Identifier(e["APP_USER"])))
print(f"PostgreSQL: {e['APP_USER']} / {e['APP_DB']} hazır")
EOF
}

setup() {
  : >"$SETUP_LOG"
  say "ayarlar: $CONF"
  load_conf
  require_secrets
  PY=$(pick_python)
  say "python: $PY ($("$PY" --version 2>&1))"
  pip_env
  ensure_venv "$CORE_VENV" "$REPO/kurum/mevzuat-core/requirements.txt"   # export.py da bu venv'le çalışır
  ensure_export
  ensure_venv "$CRAWLER_VENV" "$CRAWLER_APP/requirements.txt"
  ensure_editable "$CORE_VENV" "$CORE_APP"
  ensure_editable "$CRAWLER_VENV" "$CRAWLER_APP"
  say ".env dosyaları yazılıyor"
  write_envs
  ensure_ca_bundle
  ca_env
  say "MongoDB / Redis erişimi"
  ensure_services_reachable
  say "PostgreSQL kullanıcı / veritabanı"
  ensure_database
  say "şema: alembic upgrade head"
  (cd "$CORE_APP" && quiet "$CORE_VENV/bin/alembic" upgrade head)
  say "birimler: mevzuat-ai units-load"
  (cd "$CORE_APP" && quiet "$CORE_VENV/bin/mevzuat-ai" units-load)
  say "kurulum tamam"
}

# ---------------------------------------------------------------- süreçler
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
  # setsid: süreç kendi grubunda (pid = grup no) başlar; stop alt süreçlerle (gunicorn/celery worker'ları) birlikte kapatır
  (cd "$dir" || exit 1
   $SETSID nohup "$@" >>"$LOG_DIR/$name.log" 2>&1 &
   echo $! >"$LOG_DIR/$name.pid")
  sleep 2
  if running "$name"; then
    echo "  $name başladı (pid $(cat "$LOG_DIR/$name.pid"), log: $LOG_DIR/$name.log)"
  else
    echo "  $name BAŞLAMADI — son log satırları:" >&2
    tail -n 20 "$LOG_DIR/$name.log" >&2
    return 1
  fi
}

start_procs() {
  ca_env
  echo "mevzuat-crawler:"
  launch crawler "$CRAWLER_APP" "$CRAWLER_VENV/bin/mevzuat-crawler" serve
  echo "mevzuat-core:"
  launch api "$CORE_APP" "$CORE_VENV/bin/gunicorn" app.api.main:app -c ../gunicorn_config.py -b "$API_BIND"
  launch worker "$CORE_APP" "$CORE_VENV/bin/celery" -A app.worker worker \
    -Q ingest,process,ai,monitor,default -c "$WORKER_CONCURRENCY" -l INFO
  launch beat "$CORE_APP" "$CORE_VENV/bin/celery" -A app.worker beat -l INFO \
    -s "$LOG_DIR/celerybeat-schedule" --pidfile=
  sleep 3
  if curl -fsS -m 5 "http://$API_BIND/readyz" >/dev/null 2>&1; then
    echo "API hazır: http://$API_BIND/  (tarayıcı: ssh -L ${API_BIND##*:}:$API_BIND <kullanıcı>@atgdevtmirpr01)"
  else
    echo "API henüz yanıt vermiyor; kontrol: $0 logs api" >&2
  fi
}

signal_proc() {   # pid, sinyal → süreç grubu varsa tümüne, yoksa yalnızca sürece
  kill -"$2" -- "-$1" 2>/dev/null || kill -"$2" "$1" 2>/dev/null || true
}

stop() {
  local name pid
  for name in "${PROCS[@]}"; do
    if running "$name"; then
      pid=$(cat "$LOG_DIR/$name.pid")
      signal_proc "$pid" TERM
      for _ in {1..30}; do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
      kill -0 "$pid" 2>/dev/null && signal_proc "$pid" KILL
      echo "  $name durdu"
    else
      echo "  $name çalışmıyor"
    fi
    rm -f "$LOG_DIR/$name.pid"
  done
}

status() {
  local name
  for name in "${PROCS[@]}"; do
    if running "$name"; then
      printf '  %-8s çalışıyor (pid %s)\n' "$name" "$(cat "$LOG_DIR/$name.pid")"
    else
      printf '  %-8s DURUYOR\n' "$name"
    fi
  done
  curl -fsS -m 5 "http://$API_BIND/readyz" 2>/dev/null && echo || echo "  readyz: yanıt yok"
}

check() {
  ca_env
  ensure_services_reachable && echo "  MongoDB / Redis: ok"
  ensure_database && echo "  PostgreSQL: ok"
  echo "LLM (mevzuat-ai llm-check):"
  (cd "$CORE_APP" && "$CORE_VENV/bin/mevzuat-ai" llm-check) || echo "  LLM testi başarısız" >&2
  echo "Crawler (mevzuat-crawler check):"
  (cd "$CRAWLER_APP" && "$CRAWLER_VENV/bin/mevzuat-crawler" check) || true
  echo "Kaynak erişimi: (cd $CRAWLER_APP && $CRAWLER_VENV/bin/mevzuat-collect check-access)"
}

update() {
  load_conf
  stop
  if [[ -d $KURUM_DIR ]]; then
    say "export yeniden üretiliyor: $KURUM_DIR"
    rm -rf "$KURUM_DIR"
  fi
  setup
  start_procs
}

if [[ ${1:-start} != logs ]]; then   # ekrandaki her şey ayrıca start.log'a
  exec > >(tee -a "$LOG_DIR/start.log") 2>&1
  echo "===== $(date '+%F %T') start.sh ${*:-start}"
fi

case ${1:-start} in
  start)   setup; start_procs ;;
  setup)   setup ;;
  update)  update ;;
  check)   load_conf; check ;;
  stop)    stop ;;
  restart) stop; load_conf; start_procs ;;
  status)  load_conf; status ;;
  logs)    tail -n 100 -f "$LOG_DIR/${2:?süreç adı: ${PROCS[*]} setup}.log" ;;
  *)       echo "Kullanım: $0 [start|setup|update|check|stop|restart|status|logs <${PROCS[*]}|setup>]" >&2; exit 2 ;;
esac
