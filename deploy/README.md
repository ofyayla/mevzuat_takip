# Dağıtım

Kurum topolojisi (Albaraka Türk ağ şeması): internete çıkan tek bileşen DMZ'deki crawler'dır; LAN'a yalnızca
MongoDB (27017) üzerinden erişir. Ana servis LAN'daki Kubernetes cluster'ında çalışır.

```
 İnternet ◀─443── [DMZ] mevzuat-crawler (192.168.18.35, RedHat/podman)
                        │ 27017 (yalnızca yazma: meta veri + GridFS)
                        ▼
                  [LAN] MongoDB 10.155.7.195  (mevzuat_crawl)
                        ▲ 27017 (ingest + tarama talepleri)
                        │
       [LAN / K8s] core-api (portal + API) · core-worker · core-beat · Redis (cluster)  ──▶ PostgreSQL
                        ▲ 443                                    ──▶ vLLM, Azure DI (OCR), Keycloak
          Kullanıcılar, Albatros
```

| Klasör | İçerik |
|---|---|
| `k8s/` | Ana servis: Kustomize (api ×2, worker ×1, beat ×1 Recreate, migrate Job, Ingress, ConfigMap, Secret örneği) |
| `dmz/` | Crawler: Podman Quadlet (systemd) veya compose; ortam dosyası örneği |
| `mongo/` | En az yetkili iki Mongo kullanıcısı ve rolleri |

İmajlar (depo kökünden):

```bash
docker build -f backend/docker/Dockerfile --target core -t registry.kurum.local/mevzuat/mevzuat-core:0.2.0 .
docker build -f backend/docker/Dockerfile --target crawler -t registry.kurum.local/mevzuat/mevzuat-crawler:0.2.0 .
```

Kurulum sırası ve günlük işletim: `docs/runbook.md` §2.
