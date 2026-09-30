# Kurumda Devreye Alma — Kodun Taşınması ve İlk Kurulum

Bu belge kodun kurum ağına taşınmasından ilk taramanın portala düşmesine kadar olan adımları sırasıyla verir.
Sonraki günlük işletim ve olay müdahalesi için: `runbook.md`. Mimari: `backend-gelistirme-plani.md` §2.1.

```
 [Geliştirici Mac] ──(git clone veya git bundle)──▶ [Kurum GitLab] ──▶ [Jenkins] ──▶ [Nexus: 2 imaj]
                                                                                        │
                                  ┌─────────────────────────────────────────────────────┤
                                  ▼ helm upgrade --install                              ▼ podman pull / load
                     [LAN K8s: artint] api · worker · beat                 [DMZ 192.168.18.35] mevzuat-crawler
                                  └──────────────▶ MongoDB 10.155.7.195 ◀──────────────┘
```

## 0. Önce istenecekler (paralel yürütülür)

Kurulum bu talepler karşılanmadan tamamlanamaz. Kimden istendiği sağ sütunda.

| # | İhtiyaç | Değer / not | Kim |
|---|---|---|---|
| 1 | GitLab projesi | ör. `analitikcozumtasarimi/mevzuat-takip` | GitLab yöneticisi |
| 2 | Jenkins pipeline işi | GitLab reposu + `Jenkinsfile`; `jenkinsBuildForNexus`, `sonarqube8-token` kimlikleri | DevOps |
| 3 | PostgreSQL veritabanı ve kullanıcı | `mevzuat` DB, sahibi `mevzuat` kullanıcısı; `audit_event` için UPDATE/DELETE yetkisi verilmez | DBA |
| 4 | MongoDB kullanıcıları | `deploy/mongo/init-users.js` (iki kullanıcı: `mevzuat_crawler`, `mevzuat_core`) | Mongo yöneticisi |
| 5 | Redis DB numarası | `redis-master.artint.svc` paylaşımlı; boş iki numara (UAT, dev). Varsayılan 5 ve 6 | Platform ekibi |
| 6 | Firewall: DMZ → internet 443 | Allowlist: `config/sources.yaml` içindeki alan adları (`mevzuat-collect sources`) | Ağ / Bilgi Güvenliği |
| 7 | Firewall: DMZ → MongoDB 27017 | 192.168.18.35 → 10.155.7.195:27017 | Ağ |
| 8 | DMZ'ye imaj taşıma yolu | DMZ → Nexus 9099 izni **veya** LAN'dan DMZ'ye dosya kopyalama (scp / onaylı aktarım) | Ağ / Bilgi Güvenliği |
| 9 | Kubernetes erişimi | `artint` (UAT) ve `dev` namespace'lerinde helm/kubectl yetkisi | Platform ekibi |
| 10 | DNS | `mevzuat-takip.artinttest.albarakaturk.local` → ingress | Ağ |
| 11 | Keycloak client | `narui` realm'inde `mevzuat-portal` client'ı ve roller; realm açık anahtarı (PEM) | IAM |
| 12 | OCR uç noktası | Azure Document Intelligence adresi ve anahtarı | OCR servis sahibi |

## 1. Kodun kuruma taşınması

Repoda gizli değer yoktur: `backend/.env`, `secret.yaml` ve `backend/data/` git dışındadır. Taşımadan önce
geliştirme dalı `main`'e birleştirilir (Jenkins `main` → `latest` etiketi üretir).

**Yol A — Kurum PC'si GitHub'a erişebiliyorsa:**

```bash
git clone https://github.com/ofyayla/mevzuat_takip.git
cd mevzuat_takip
git remote rename origin github
git remote add origin http://<gitlab>/analitikcozumtasarimi/mevzuat-takip.git
git push -u origin --all && git push origin --tags
```

**Yol B — Erişim yoksa (tek dosya, tüm geçmiş):** Geliştirici makinesinde:

```bash
git bundle create mevzuat_takip.bundle --all
git bundle verify mevzuat_takip.bundle
```

Dosya kurumun onaylı aktarım yöntemiyle taşınır. Kurum PC'sinde:

```bash
git clone mevzuat_takip.bundle mevzuat_takip
cd mevzuat_takip
git remote set-url origin http://<gitlab>/analitikcozumtasarimi/mevzuat-takip.git
git push -u origin --all
```

Sonraki güncellemeler için artımlı paket de oluşturulabilir: `git bundle create guncelleme.bundle <son-taşınan-commit>..main`,
kurumda `git pull guncelleme.bundle main`.

## 2. İmajların üretilmesi (Jenkins)

1. Jenkins'te GitLab reposuna bağlı pipeline işi oluşturulur; betik yolu `Jenkinsfile`.
2. İlk çalıştırmada kontrol edilecekler:
   - Taban imaj çekildi: `nexus.alb.albarakatech.com:9099/com.albaraka.python:3.12-slim-bullseye`
   - pip aynası (`10.54.62.31:8080`) paketleri buldu. Eksik paket varsa Nexus'taki PyPI proxy'sine eklenir.
   - Kurum CA'ları indirildi (`update-ca-certificates` satırı).
3. Sonuç: Nexus'ta `com.albaraka.ai/mevzuat-takip-core` ve `com.albaraka.ai/mevzuat-takip-crawler`, etiketler
   `0.2.0-<BUILD_NUMBER>` ve `latest`/`dev`.

Jenkins henüz hazır değilse Docker kurulu bir LAN makinesinde aynı derleme elle yapılır:

```bash
ARGS="--build-arg BASE_IMAGE=nexus.alb.albarakatech.com:9099/com.albaraka.python:3.12-slim-bullseye \
  --build-arg PIP_INDEX_URL=http://10.54.62.31:8080/repository/albaraka-python/simple --build-arg PIP_TRUSTED_HOST=10.54.62.31 \
  --build-arg CA_CERT_URLS='https://nexus.alb.albarakatech.com/repository/atg-raw-file/alb_ca/albaraka-root-ca-2042.crt https://nexus.alb.albarakatech.com/repository/atg-raw-file/alb_ca/albaraka-sub-ca-2041.crt'"
eval docker build -f backend/docker/Dockerfile --target core    $ARGS -t 10.54.62.31:9099/com.albaraka.ai/mevzuat-takip-core:latest .
eval docker build -f backend/docker/Dockerfile --target crawler $ARGS -t 10.54.62.31:9099/com.albaraka.ai/mevzuat-takip-crawler:latest .
docker login 10.54.62.31:9099 && docker push 10.54.62.31:9099/com.albaraka.ai/mevzuat-takip-core:latest \
  && docker push 10.54.62.31:9099/com.albaraka.ai/mevzuat-takip-crawler:latest
```

## 3. Veri katmanı hazırlığı

1. **PostgreSQL** (DBA): `CREATE DATABASE mevzuat;` ve uygulama kullanıcısı. Şemayı uygulama kurar (Helm migrate
   hook'u); elle tablo oluşturulmaz.
2. **MongoDB** (Mongo yöneticisi, bir kez):

   ```bash
   CRAWLER_PW='…' CORE_PW='…' mongosh "mongodb://<admin>@10.155.7.195:27017/admin" --file deploy/mongo/init-users.js
   ```

   Parolalar kasaya konur. Crawler kullanıcısı yalnızca `mevzuat_crawl`'a yazabilir, silemez.

## 4. LAN — Ana servisin kurulması (Kubernetes, `artint`)

1. Gizli değerler (bir kez; repoya girmez):

   ```bash
   cp deploy/helm/secret.example.yaml secret.yaml     # DATABASE_URL, MONGO_URL (mevzuat_core), LLM_API_KEY, OCR_API_KEY
   kubectl -n artint apply -f secret.yaml && rm secret.yaml
   ```

2. `deploy/helm/mevzuat-takip/values-albaraka-uat.yaml` içindeki TEYİT satırları doldurulur: Redis DB numarası,
   `OCR_ENDPOINT`, `KEYCLOAK_CLIENT_ID`.
3. Kurulum (sabit etiketle; `latest` ile güncellemede pod'lar yenilenmez):

   ```bash
   helm upgrade --install mevzuat-takip deploy/helm/mevzuat-takip -n artint \
     -f deploy/helm/mevzuat-takip/values-albaraka-uat.yaml --set deployment.image.tag=0.2.0-<BUILD_NUMBER>
   ```

   Önce `mevzuat-takip-migrate` Job'ı şemayı kurar. Başarısız olursa sürüm uygulanmaz:
   `kubectl -n artint logs job/mevzuat-takip-migrate`.
4. Kontrol:

   ```bash
   kubectl -n artint get pods -l 'app in (mevzuat-takip-worker,mevzuat-takip-beat)'
   kubectl -n artint get deploy mevzuat-takip
   curl -k https://mevzuat-takip.artinttest.albarakaturk.local/readyz     # {"status":"ok", …}
   ```

5. Başlangıç verisi ve dış servis testleri:

   ```bash
   kubectl -n artint exec deploy/mevzuat-takip -- mevzuat-ai units-load
   kubectl -n artint exec deploy/mevzuat-takip -- mevzuat-ai llm-check
   kubectl -n artint exec deploy/mevzuat-takip -- mevzuat-process ocr-check
   ```

Bu aşamada portal açılır ama crawler çalışmadığı için izleme `crawler_down` alarmı gösterir; beklenen durumdur.

## 5. DMZ — Crawler'ın kurulması (192.168.18.35, RedHat)

1. **İmajın taşınması:**
   - DMZ Nexus'a erişebiliyorsa:
     `podman login 10.54.62.31:9099 && podman pull 10.54.62.31:9099/com.albaraka.ai/mevzuat-takip-crawler:latest`
   - Erişemiyorsa, LAN'daki bir makinede dosyaya alınıp kopyalanır:

     ```bash
     docker pull 10.54.62.31:9099/com.albaraka.ai/mevzuat-takip-crawler:latest
     docker save -o mevzuat-crawler.tar 10.54.62.31:9099/com.albaraka.ai/mevzuat-takip-crawler:latest
     # onaylı yöntemle DMZ'ye kopyalanır, DMZ'de:
     sudo podman load -i mevzuat-crawler.tar
     ```

     İmaj adı değişmediği için Quadlet dosyası olduğu gibi kullanılır; yüklü imaj varken yeniden çekmez.
2. **Ortam dosyası:**

   ```bash
   sudo install -d -m 700 /etc/mevzuat
   sudo install -m 600 deploy/dmz/crawler.env.example /etc/mevzuat/crawler.env   # MONGO_URL = mevzuat_crawler
   ```

   Çıkış SSL denetimli bir proxy üzerindense `HTTPS_PROXY` ve (imajda kurum CA'ları varsa) `CA_BUNDLE` boş bırakılır.
3. **Servis (RHEL 9.2+ / podman ≥ 4.4 — Quadlet):**

   ```bash
   sudo install -m 644 deploy/dmz/mevzuat-crawler.container /etc/containers/systemd/
   sudo systemctl daemon-reload && sudo systemctl start mevzuat-crawler
   journalctl -u mevzuat-crawler -f
   ```

   Daha eski RHEL'de `podman-compose -f deploy/dmz/docker-compose.crawler.yml up -d` kullanılır.
4. **Kontrol ve ilk tarama:**

   ```bash
   sudo podman exec mevzuat-crawler mevzuat-collect check-access     # 9 kaynak OK (hata → runbook §4.2)
   sudo podman exec mevzuat-crawler mevzuat-crawler check             # Mongo erişimi, indeksler
   sudo podman exec mevzuat-crawler mevzuat-crawler run all           # ilk tarama: mevcut içerik BASELINE
   ```

   İlk tarama kaynak başına birkaç dakika sürer (istekler arası 1,5 sn bekleme). Sonrasında crawler zamanlamaya
   göre kendiliğinden tarar.

## 6. Uçtan uca doğrulama

1. `mevzuat-crawler check`: "aktarılmayı bekleyen belge" birkaç dakika içinde 0'a iner (ingest her dakika çalışır).
2. Portal durum çubuğu veya `GET /api/v1/sources/health`: `crawler_down` kapanır, kaynaklar `ok` görünür.
3. Portaldan bir kaynak için "şimdi tara" → talep ≤15 sn içinde DMZ'de alınır (`journalctl -u mevzuat-crawler`).
4. `kubectl -n artint exec deploy/mevzuat-takip -- mevzuat-monitor audit-verify` → `ok`.
5. İlk günlerde gelen yeni düzenlemelerin portala düştüğü ve YZ alanlarının dolduğu (ilgililik, özet, birim)
   kontrol edilir. Paralel çalışma: runbook §6.

## 7. Güncelleme akışı

```
 kod değişikliği → GitLab main → Jenkins (0.2.0-<N> + latest) →
   LAN : helm upgrade … --set deployment.image.tag=0.2.0-<N>        (migrasyon hook'la otomatik)
   DMZ : podman pull (veya save/load) → systemctl restart mevzuat-crawler
```

Yalnızca kaynak listesi değiştiyse (`config/sources.yaml`, yeni site) crawler imajını güncellemek yeterlidir; yeni
alan adı için firewall allowlist'i de güncellenir. Geri alma: `helm rollback mevzuat-takip <revizyon>`. Şema geri alma
gerekiyorsa önce yedek alınır (runbook §7).

## Dev ortamı notu

`values-albaraka-dev.yaml` dev için ayrı Mongo veritabanı (`mevzuat_crawl_dev`) kullanır. Dev ve UAT aynı Mongo
veritabanını okursa birbirinin kayıtlarını "aktarıldı" olarak işaretleyip veri kaçırır. Bu yüzden dev için DMZ'de
`MONGO_DB=mevzuat_crawl_dev` ile ikinci bir crawler örneği gerekir; ya da dev, UAT kurulduktan sonra ihtiyaç halinde
açılır.
