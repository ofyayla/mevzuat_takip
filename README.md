# Mevzuat Takip Otomasyonu — geliştirme deposu

Resmî kaynaklardaki (Resmî Gazete, BDDK, SPK, TCMB, KVKK, MASAK, Ticaret, Rekabet, TKBB) yeni düzenlemeleri toplayan,
tekilleştiren, YZ ile ilgililik/önem/özet/birim önerisi üreten ve Mevzuat ve Uyum Başkanlığı portalında onaya sunan
sistem.

Kurumda iki servis ve iki repo vardır; bu depo ikisinin **tek kaynağıdır**:

| Kurum reposu | Yer | İmaj (Nexus) | Dağıtım |
|---|---|---|---|
| `mevzuat-core` | LAN Kubernetes (`artint`) | `com.albaraka.ai/mevzuat-core` | ArgoCD — `ai-uat-charts/mevzuat-core` |
| `mevzuat-crawler` | DMZ, 192.168.18.35 (RedHat/podman) | `com.albaraka.ai/mevzuat-crawler` | Podman Quadlet (`deploy/dmz`) |

DMZ'den LAN'a yalnızca MongoDB (27017) açıktır; crawler taradığını MongoDB'ye yazar, core oradan alıp işler.

Kurum repoları ve chart'lar üretilir: `backend/.venv/bin/python kurum/export.py` → `~/Downloads/mevzuat-kurum`
(ayrıntı: `kurum/README.md`).

## Depo yapısı

| Yol | İçerik |
|---|---|
| `backend/` | Kod, yapılandırma, migrasyonlar, testler — ayrıntı: `backend/README.md` |
| `Mevzuat Takip Portali.dc.html`, `support.js` | Portal arayüzü |
| `surec-haritasi.html` | Süreç haritası (`/surec`): işleme hattının durumları ve canlı sayıları |
| `kurum/` | Kurum repolarının iskeletleri (Dockerfile, Jenkinsfile, pip.conf, gunicorn, README, deploy), chart'lar, `export.py` |
| `Dockerfile`, `pip.conf`, `backend/docker/docker-compose.yml` | Yerel uçtan uca ortam (crawler + mongo + api/worker/beat + redis + postgres) |
| `docs/` | Devreye alma, işletim (runbook), mimari plan, kaynak keşif raporu |

## Yerel geliştirme

```bash
cd backend && make install && make test          # Mongo testleri: make mongo-test
make compose-up                                  # kurum topolojisinin yerel kopyası
```

## Belgeler

- Kurumda devreye alma: `docs/kurum-devreye-alma.md`
- İşletim ve olay müdahalesi: `docs/runbook.md`
- Mimari ve geliştirme planı: `docs/backend-gelistirme-plani.md`
