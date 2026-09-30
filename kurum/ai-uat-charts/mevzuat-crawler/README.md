# mevzuat-crawler

Kaynak siteleri (Resmî Gazete, BDDK, SPK, TCMB, KVKK, MASAK, Ticaret, Rekabet, TKBB) tarayıp sonucu MongoDB'ye
(`mevzuat_crawl`) yazan servis. Tek Deployment, **tek örnek** (Recreate); servis/port yoktur.

**Nerede çalışır:** internete 443 (`config/sources.yaml` alan adları) ve MongoDB'ye 27017 erişimi olan bir
cluster/namespace. Kurum ağ şemasında bu erişim DMZ sunucusunda (192.168.18.35) var; Kubernetes cluster'ından
internete çıkış yoksa crawler DMZ'de podman ile çalışır (mevzuat-crawler reposunda `deploy/dmz`). İkisi aynı anda
çalıştırılmaz.

| Dosya | Ortam | Mongo DB | İmaj etiketi |
|---|---|---|---|
| `values-albaraka-uat.yaml` | UAT | `mevzuat_crawl` | `latest` |
| `values-albaraka-dev.yaml` | DEV | `mevzuat_crawl_dev` | `dev` |

**Önkoşul:** `mevzuat-crawler-secrets` (dev: `mevzuat-crawler-dev-secrets`) Secret'ı: `MONGO_URL`
(`mevzuat_crawler` kullanıcısı; yalnızca `mevzuat_crawl`'a yazabilir, silemez). Örnek: mevzuat-crawler reposunda
`deploy/secret.example.yaml`.

```bash
helm lint . -f values-albaraka-uat.yaml
helm template mevzuat-crawler . -n artint -f values-albaraka-uat.yaml
```
