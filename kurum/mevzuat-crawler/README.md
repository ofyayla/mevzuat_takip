# mevzuat-crawler

Resmî kaynakları (Resmî Gazete, BDDK, SPK, TCMB, KVKK, MASAK, Ticaret, Rekabet, TKBB) tarayan servis. Yeni/değişen
içeriği tespit eder, sürümler ve MongoDB'ye yazar (meta veri + ham içerik GridFS). İnternete çıkan tek bileşendir;
DMZ'de (192.168.18.35) çalışır ve LAN'a yalnızca MongoDB (27017) üzerinden erişir. Taranan veriyi **mevzuat-core**
alır ve işler.

## Yapı

| Yol | İçerik |
|---|---|
| `app/` | Uygulama: `app/collectors` (kaynak adaptörleri), `app/crawler` (servis), `config/sources.yaml`, `config/certs`, `tests/`, `requirements.txt` |
| `Dockerfile`, `pip.conf` | İmaj (Nexus taban imajı, kurum pip aynası, kurum CA'ları). LLM/OCR/API/Celery yok |
| `Jenkinsfile` | Nexus'a derle-gönder + SonarQube |
| `API_REFERENCE.md` | Komutlar, ortam değişkenleri, MongoDB sözleşmesi (mevzuat-core ile ortak) |
| `deploy/dmz/` | DMZ kurulumu: Podman Quadlet (systemd), compose, ortam dosyası örneği |
| `deploy/` | Secret ve ArgoCD örnekleri (crawler cluster'da çalışacaksa) |

Chart: kurum chart reposunda `ai-uat-charts/mevzuat-crawler`. Kurum ağ şemasında internet çıkışı DMZ sunucusunda
olduğu için varsayılan kurulum podman'dır (`deploy/dmz`); chart, internete çıkabilen bir cluster için hazırdır.
**İkisi aynı anda çalıştırılmaz.**

## Neden gunicorn yok

Crawler web sunucusu değildir: tek süreçte zamanlayıcı (`config/sources.yaml` cron) ve LAN tarama talepleri çalışır.
gunicorn birden çok worker açıp zamanlayıcıyı çoğaltırdı (aynı kaynak aynı anda birkaç kez taranırdı).

## DMZ kurulumu (özet)

```bash
sudo podman load -i mevzuat-crawler.tar          # veya: podman pull 10.54.62.31:9099/com.albaraka.ai/mevzuat-crawler:latest
sudo install -d -m 700 /etc/mevzuat && sudo install -m 600 deploy/dmz/crawler.env.example /etc/mevzuat/crawler.env
sudo install -m 644 deploy/dmz/mevzuat-crawler.container /etc/containers/systemd/
sudo systemctl daemon-reload && sudo systemctl start mevzuat-crawler
sudo podman exec mevzuat-crawler mevzuat-collect check-access      # 9 kaynak OK
sudo podman exec mevzuat-crawler mevzuat-crawler check              # Mongo erişimi
sudo podman exec mevzuat-crawler mevzuat-crawler run all            # ilk tarama (mevcut içerik BASELINE)
```

Ayrıntı: mevzuat-core reposunda `docs/kurum-devreye-alma.md` §5.

## Yerel geliştirme

```bash
cd app
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest respx && pip install --no-deps -e .
pytest -q          # Mongo testleri için: docker run -d -p 27018:27017 mongo:7
mevzuat-collect probe TCMB                       # listeyi çeker, yazmaz
```

## Ortak kod

`app/settings.py`, `app/db.py`, `app/storage.py`, `app/mongo.py` ve `app/collectors/` mevzuat-core'da da bulunur
(core yerel geliştirmede aynı toplayıcıyı kullanır). **MongoDB sözleşmesi** (`app/mongo.py`,
`app/crawler/mongo_store.py`, `API_REFERENCE.md`) değişirse iki repo birlikte güncellenir.
