# Kurumda Devreye Alma

Kurum akışı: **iki uygulama reposu** (GitLab: `mevzuat-core`, `mevzuat-crawler`) + **chart reposu**
(`ai-uat-charts/mevzuat-core`, `ai-uat-charts/mevzuat-crawler`) → pipeline'lar (DevOps ekibi; Azure DevOps, repolarda
kurum örneğindeki Jenkinsfile da var) imajları Nexus'a gönderir → **ArgoCD** chart'ı senkronlayıp servisi ayağa
kaldırır. Crawler, internete çıkış yalnızca DMZ sunucusunda olduğu için varsayılan olarak DMZ'de podman ile çalışır.

```
 mevzuat-core (GitLab)       mevzuat-crawler (GitLab)     ai-uat-charts (GitLab)
   Dockerfile, pip.conf,       Dockerfile, pip.conf,        mevzuat-core/    mevzuat-crawler/
   gunicorn_config.py,         Jenkinsfile, app/,           (values-albaraka-{uat,dev}.yaml)
   Jenkinsfile, app/, portal/  deploy/dmz                         │
          │ pipeline                 │ pipeline                  ▼ ArgoCD
          ▼                          ▼
   Nexus com.albaraka.ai/mevzuat-core    ─▶ LAN K8s (artint): api · worker · beat (+ PreSync migrate)
   Nexus com.albaraka.ai/mevzuat-crawler ─▶ DMZ 192.168.18.35: podman (Quadlet)
                                   └──────────▶ MongoDB 10.155.7.195 ◀──────────┘
```

Üç klasör geliştirme deposunda hazır durur ve olduğu gibi ilgili repoya kopyalanır (§1). Sonraki günlük işletim:
`runbook.md`. Mimari: `backend-gelistirme-plani.md` §2.1.

## 0. Önce istenecekler (paralel yürütülür)

| # | İhtiyaç | Değer / not | Kim |
|---|---|---|---|
| 1 | Uygulama repoları | GitLab'da iki proje: `mevzuat-core`, `mevzuat-crawler` | GitLab yöneticisi |
| 2 | Chart reposunda klasörler | `ai-uat-charts/mevzuat-core/`, `ai-uat-charts/mevzuat-crawler/` | DevOps |
| 3 | Pipeline'lar | Her repo için bir tane; gereksinimler §2 | DevOps |
| 4 | ArgoCD Application | `mevzuat-core` UAT (ve istenirse dev); örnek: mevzuat-core `deploy/argocd-application.example.yaml` | DevOps |
| 5 | PostgreSQL veritabanı ve kullanıcı | `mevzuat` DB; `audit_event` için UPDATE/DELETE yetkisi verilmez | DBA |
| 6 | MongoDB kullanıcıları | mevzuat-core `deploy/mongo/init-users.js` (`mevzuat_crawler`, `mevzuat_core`) | Mongo yöneticisi |
| 7 | Redis DB numarası | `redis-master.artint.svc` paylaşımlı; boş numara (UAT 5, dev 6 varsayıldı) | Platform ekibi |
| 8 | Firewall: DMZ → internet 443 | Allowlist: `config/sources.yaml` alan adları (`mevzuat-collect sources`) | Ağ / Bilgi Güvenliği |
| 9 | Firewall: DMZ → MongoDB 27017 | 192.168.18.35 → 10.155.7.195:27017 | Ağ |
| 10 | DMZ'ye imaj taşıma yolu | DMZ → Nexus 9099 izni **veya** LAN'dan DMZ'ye dosya kopyalama | Ağ / Bilgi Güvenliği |
| 11 | DNS | `mevzuat-takip.artinttest.albarakaturk.local` → ingress | Ağ |
| 12 | Keycloak client | `narui` realm'inde `mevzuat-portal` client'ı ve roller; realm açık anahtarı (PEM) | IAM |
| 13 | OCR uç noktası | Azure Document Intelligence adresi ve anahtarı | OCR servis sahibi |

## 1. Repoların açılması

Geliştirme deposunun (bu repo) kökündeki servis dizinleri kurum repolarının birebir karşılığıdır; üretme adımı yoktur:

```
mevzuat-core/          → GitLab mevzuat-core reposunun kökü
mevzuat-crawler/       → GitLab mevzuat-crawler reposunun kökü
ai-uat-charts/
  mevzuat-core/        → chart reposunda ai-uat-charts/mevzuat-core
  mevzuat-crawler/     → chart reposunda ai-uat-charts/mevzuat-crawler
```

Dizinlerde gizli değer yoktur (`.env`, `secret.yaml`, veri dosyaları `.gitignore`'dadır). Kurum ağına onaylı yöntemle
taşınır ve her biri kendi reposuna kopyalanıp push edilir (ya da kurum git'ine erişim varsa `git subtree`):

```bash
git subtree split --prefix=mevzuat-core -b kurum/mevzuat-core      # geçmişiyle birlikte, dizin kök olur
git push https://gitlab.albarakaturk.local/<grup>/mevzuat-core.git kurum/mevzuat-core:main
# veya: mevzuat-core/ içeriğini boş bir klona kopyalayıp commit/push (git init -b main … git push -u origin main)
# mevzuat-crawler için aynı; chart'lar ai-uat-charts reposuna klasör olarak eklenir
```

**Güncelleme:** kod bu depoda değişir, değişen servis dizini ilgili repoya aktarılır. `app/settings.py`, `app/db.py`,
`app/storage.py`, `app/mongo.py`, `app/collectors/`, `app/crawler/` ve `config/sources.yaml` iki serviste de bulunur
(kaynak: mevzuat-core). Ortak dosya yalnızca birinde değişirse sözleşme (MongoDB) bozulabilir; bu yüzden
`python tools/shared.py sync` ile eşitlenir, `python tools/shared.py check` fark varsa hata verir.

## 2. Pipeline gereksinimleri (DevOps ekibine iletilecek)

| | mevzuat-core | mevzuat-crawler |
|---|---|---|
| Derleme | repo kökünde `docker build .` | repo kökünde `docker build .` |
| İmaj | `com.albaraka.ai/mevzuat-core` | `com.albaraka.ai/mevzuat-crawler` |
| Etiket | UAT `latest`, dev `dev` (values dosyalarıyla uyumlu) | aynı |
| Dağıtım | ArgoCD — `ai-uat-charts/mevzuat-core` | DMZ podman (§5); internete çıkabilen cluster varsa `ai-uat-charts/mevzuat-crawler` |
| Sonar | `mevzuat-core` (Jenkinsfile'daki ayarlar) | `mevzuat-crawler` |

- **Build argümanı gerekmez.** Dockerfile varsayılanları kurum ayarlarıdır: taban imaj
  `nexus.alb.albarakatech.com:9099/com.albaraka.python:3.12-slim-bullseye`, paketler `pip.conf`
  (`10.54.62.31:8080` aynası) ve `app/requirements.txt` (3.12'de test edilmiş sabit sürümler), kurum kök/ara CA'ları
  Nexus raw deposundan. Repolardaki `Jenkinsfile` kurum örneğiyle aynı akıştır (derle → Nexus → SonarQube).
- **Test (isteğe bağlı):** `docker run --rm -e DB_AUTO_CREATE=true -v $(pwd)/app/tests:/app/tests <imaj> sh -c "pip install pytest respx && cd /app && python -m pytest -q"`
  (Mongo testleri Mongo erişimi yoksa atlanır). Testler imaja girmez (`.dockerignore`).
- **Etiket stratejisi:** `latest` + `pullPolicy: Always` ile yeni imaj ArgoCD'de Deployment → Restart ile alınır.
  Pipeline chart reposundaki `deployment.image.tag` değerini sürüm etiketine güncellerse ArgoCD pod'ları kendiliğinden
  yeniler (önerilen).

## 3. Veri katmanı hazırlığı

1. **PostgreSQL** (DBA): `CREATE DATABASE mevzuat;` ve uygulama kullanıcısı. Şemayı uygulama kurar (PreSync migrate
   Job'ı); elle tablo oluşturulmaz.
2. **MongoDB** (Mongo yöneticisi, bir kez):

   ```bash
   # mevzuat-core reposunda:
   CRAWLER_PW='…' CORE_PW='…' mongosh "mongodb://<admin>@10.155.7.195:27017/admin" --file deploy/mongo/init-users.js
   ```

## 4. LAN — Ana servis (ArgoCD)

1. **Secret** (bir kez, ArgoCD dışında; git'e girmez):

   ```bash
   cp deploy/secret.example.yaml secret.yaml        # mevzuat-core reposu: DATABASE_URL, MONGO_URL (mevzuat_core), LLM_API_KEY, OCR_API_KEY
   kubectl -n artint apply -f secret.yaml && rm secret.yaml
   ```

2. **Values:** chart reposundaki `mevzuat-core/values-albaraka-uat.yaml` içinde TEYİT satırları doldurulur (Redis DB numarası,
   `OCR_ENDPOINT`, `KEYCLOAK_CLIENT_ID`) ve push edilir.
3. **ArgoCD senkronu** (DevOps'un tanımladığı Application): sıra
   `mevzuat-core-migrate` (PreSync, şema) → Service, Deployment'lar, Ingress. Migrate başarısız olursa
   senkron durur; ArgoCD'de Job'ın logu açılır (Job silinmez, bir sonraki senkronda yenilenir).
4. **Kontrol** (ArgoCD'de uygulama `Healthy / Synced`):

   ```bash
   curl -k https://mevzuat-takip.artinttest.albarakaturk.local/readyz     # {"status":"ok", …}
   kubectl -n artint exec deploy/mevzuat-core -- mevzuat-ai units-load   # başlangıç verisi (bir kez)
   kubectl -n artint exec deploy/mevzuat-core -- mevzuat-ai llm-check
   kubectl -n artint exec deploy/mevzuat-core -- mevzuat-process ocr-check
   ```

Bu aşamada portal açılır ama crawler çalışmadığı için izleme `crawler_down` alarmı gösterir; beklenen durumdur.

## 5. DMZ — Crawler (192.168.18.35, RedHat)

1. **İmaj:**
   - DMZ Nexus'a erişebiliyorsa:
     `podman login 10.54.62.31:9099 && podman pull 10.54.62.31:9099/com.albaraka.ai/mevzuat-crawler:latest`
   - Erişemiyorsa LAN'da dosyaya alınır, onaylı yöntemle kopyalanır:

     ```bash
     docker pull 10.54.62.31:9099/com.albaraka.ai/mevzuat-crawler:latest
     docker save -o mevzuat-crawler.tar 10.54.62.31:9099/com.albaraka.ai/mevzuat-crawler:latest
     # DMZ'de:
     sudo podman load -i mevzuat-crawler.tar
     ```

   Azure DevOps'ta DMZ'ye SSH ile bağlanan bir dağıtım adımı tanımlanırsa bu adımlar pipeline'a alınabilir.
2. **Ortam dosyası:**

   ```bash
   sudo install -d -m 700 /etc/mevzuat
   sudo install -m 600 deploy/dmz/crawler.env.example /etc/mevzuat/crawler.env   # mevzuat-crawler reposu; MONGO_URL = mevzuat_crawler
   ```

3. **Servis (RHEL 9.2+ / podman ≥ 4.4 — Quadlet):**

   ```bash
   sudo install -m 644 deploy/dmz/mevzuat-crawler.container /etc/containers/systemd/
   sudo systemctl daemon-reload && sudo systemctl start mevzuat-crawler
   journalctl -u mevzuat-crawler -f
   ```

   Daha eski RHEL'de `podman-compose -f deploy/dmz/docker-compose.crawler.yml up -d`.
4. **Kontrol ve ilk tarama:**

   ```bash
   sudo podman exec mevzuat-crawler mevzuat-collect check-access     # 9 kaynak OK (hata → runbook §4.2)
   sudo podman exec mevzuat-crawler mevzuat-crawler check             # Mongo erişimi, indeksler
   sudo podman exec mevzuat-crawler mevzuat-crawler run all           # ilk tarama: mevcut içerik BASELINE
   ```

## 6. Uçtan uca doğrulama

1. `mevzuat-crawler check`: "aktarılmayı bekleyen belge" birkaç dakika içinde 0'a iner (ingest her dakika çalışır).
2. Portal durum çubuğu / `GET /api/v1/sources/health`: `crawler_down` kapanır, kaynaklar `ok`.
3. Portaldan "şimdi tara" → talep ≤15 sn içinde DMZ'de alınır (`journalctl -u mevzuat-crawler`).
4. `kubectl -n artint exec deploy/mevzuat-core -- mevzuat-monitor audit-verify` → `ok`.
5. İlk günlerde yeni düzenlemelerin portala düştüğü ve YZ alanlarının dolduğu kontrol edilir (runbook §6).

## 7. Güncelleme akışı

```
 geliştirme deposu (tools/shared.py check) → mevzuat-core / mevzuat-crawler / ai-uat-charts dizinlerini ilgili repolara aktar
   mevzuat-core    → pipeline → Nexus (latest) → ArgoCD Restart (veya image.tag güncellenirse otomatik; migrasyon PreSync)
   mevzuat-crawler → pipeline → Nexus (latest) → DMZ: podman pull (veya save/load) → systemctl restart mevzuat-crawler
   ai-uat-charts   → push → ArgoCD senkronlar
```

Yeni kaynak sitesi: `config/sources.yaml` (iki repoda da; core izleme için okur) + crawler imajı + firewall allowlist.
Geri alma: ArgoCD'de önceki revizyona dönülür (History and Rollback). Şema geri alma gerekiyorsa önce yedek alınır
(runbook §7).

## Dev ortamı notu

`values-albaraka-dev.yaml` (iki chart'ta da) dev için ayrı Mongo veritabanı (`mevzuat_crawl_dev`) kullanır. Dev ve UAT aynı Mongo
veritabanını okursa birbirinin kayıtlarını "aktarıldı" olarak işaretleyip veri kaçırır. Bu yüzden dev için DMZ'de
`MONGO_DB=mevzuat_crawl_dev` ile ikinci bir crawler örneği gerekir; ya da dev, UAT kurulduktan sonra ihtiyaç halinde
açılır.
