# Mevzuat Takip Otomasyonu — geliştirme deposu

Resmî kaynaklardaki (Resmî Gazete, BDDK, SPK, TCMB, KVKK, MASAK, Ticaret, Rekabet, TKBB) yeni düzenlemeleri toplayan,
tekilleştiren, YZ ile ilgililik/önem/özet/birim önerisi üreten ve Mevzuat ve Uyum Başkanlığı portalında onaya sunan
sistem.

Depo, kurumdaki servislerin kendisidir: her servis dizini bir kurum reposunun (ve chart dizininin) birebir karşılığıdır;
üretme/dışa aktarma adımı yoktur.

| Dizin | Kurum karşılığı | Yer | İmaj (Nexus) | Dağıtım |
|---|---|---|---|---|
| `mevzuat-core/` | `mevzuat-core` reposu | LAN Kubernetes (`artint`) | `com.albaraka.ai/mevzuat-core` | ArgoCD — `ai-uat-charts/mevzuat-core` |
| `mevzuat-crawler/` | `mevzuat-crawler` reposu | DMZ, 192.168.18.35 (RedHat/podman) | `com.albaraka.ai/mevzuat-crawler` | Podman Quadlet (`mevzuat-crawler/deploy/dmz`) |
| `ai-uat-charts/` | kurum chart reposu | — | — | Helm chart'ları (kurum `common` 1.0.4), `values-albaraka-{uat,dev}.yaml` |

DMZ'den LAN'a yalnızca MongoDB (27017) açıktır; crawler taradığını MongoDB'ye yazar, core oradan alıp işler.

## Depo yapısı

| Yol | İçerik |
|---|---|
| `mevzuat-core/` | Dockerfile, pip.conf, gunicorn_config.py, Jenkinsfile, README, `API_REFERENCE.md`, `deploy/` (Secret, ArgoCD, Mongo rolleri) |
| `mevzuat-core/app/` | Kod (`app/`), yapılandırma (`config/`), migrasyonlar, testler, `requirements.txt` — ayrıntı: `mevzuat-core/app/README.md` |
| `mevzuat-core/portal/` | Portal arayüzü (`Mevzuat Takip Portali.dc.html`, `support.js`) ve süreç haritası (`surec-haritasi.html`, `/surec`) |
| `mevzuat-core/docs/` | Devreye alma, işletim (runbook), mimari plan, kaynak keşif raporu ve kaynak listesi analizi |
| `mevzuat-crawler/` | Dockerfile, pip.conf, Jenkinsfile, README, `API_REFERENCE.md`, `deploy/dmz` (podman) |
| `mevzuat-crawler/app/` | Yalnızca toplama + crawler kodu ve testleri (LLM/OCR/API/Celery yok) |
| `ai-uat-charts/` | `mevzuat-core` ve `mevzuat-crawler` Helm chart'ları |
| `docker-compose.yml` | Yerel uçtan uca ortam (crawler + mongo + api/worker/beat + redis + postgres); servislerin kendi Dockerfile'larını kullanır |
| `jupyterhub/start.sh` | JupyterHub (atgdevtmirpr01) üzerinde iki servisi yerel süreç olarak kurar ve çalıştırır |
| `tools/shared.py` | core ↔ crawler ortak dosyaların eşitlenmesi/denetimi |
| `Makefile` | Test, lint, docker, compose kısayolları |

## Ortak kod

`settings.py`, `db.py`, `storage.py`, `mongo.py`, `collectors/`, `crawler/`, `config/sources.yaml`, `config/certs/`
ve ilgili testler/fixture'lar iki serviste de bulunur (servisler ayrı repolara gittiği için). **Kaynak mevzuat-core'dur:**
ortak dosya core'da değiştirilir, sonra eşitlenir:

```bash
python tools/shared.py sync      # core → crawler
python tools/shared.py check     # fark varsa 1 ile çıkar (make test içinde de çalışır)
```

## Yerel geliştirme

```bash
make install && make test          # core venv'inde; crawler testleri için ayrıca: make install-crawler
make compose-up                    # kurum topolojisinin yerel kopyası (docker-compose.yml; önce mevzuat-core/app/.env.example → .env)
make api-reference                 # uç nokta değişince mevzuat-core/API_REFERENCE.md'yi yeniden üret
```

### Compose için otomatik Git güncellemesi

Linux'ta, upstream'i tanımlı bir Git checkout'unda aşağıdaki komut uzak değişiklikleri her 30 saniyede kontrol eder.
Yeni commit varsa fast-forward alıp imajları yeniden oluşturur; başlangıçta `api` servisi çalışmıyorsa Compose
uygulamasını başlatır. Yerel değişiklikleri ezmemek için kirli çalışma ağacında güncellemeyi durdurur.

```bash
bash scripts/auto-deploy-compose.sh
```

Kontrol aralığı `AUTO_UPDATE_INTERVAL=60` gibi saniye cinsinden ayarlanabilir. Scriptin sürekli çalışması ve sunucu
yeniden başladığında tekrar açılması için bir `systemd` servisi veya süreç yöneticisi altında çalıştırın. Bu script
yalnızca kök dizindeki yerel Docker Compose dağıtımını yönetir; kurum Kubernetes dağıtımı Argo CD tarafından yönetilir.

Mongo testleri için `make mongo-test`. İki servis için ayrı sanal ortam önerilir (core `.[core,dev]`, crawler `.[dev]`);
JupyterHub'da tek komutla kurulum: `jupyterhub/start.sh`.

**Doğrulama** (kurum ağı dışında):

```bash
make docker-build                  # iki imaj (Nexus/CA/pip aynası ezilir)
helm lint ai-uat-charts/mevzuat-core -f ai-uat-charts/mevzuat-core/values-albaraka-uat.yaml
```

**requirements.txt** (`<servis>/app/requirements.txt`): Python 3.12 imajında testleri geçen sabit sürümler. Bağımlılık
değişince yeniden üretilir: servis imajını derleyip `docker run --rm <imaj> pip freeze` (proje satırı hariç).

## Belgeler

- Kurumda devreye alma: `mevzuat-core/docs/kurum-devreye-alma.md`
- İşletim ve olay müdahalesi: `mevzuat-core/docs/runbook.md`
- Mimari ve geliştirme planı: `mevzuat-core/docs/backend-gelistirme-plani.md`
