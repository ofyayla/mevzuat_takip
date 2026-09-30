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
       [LAN / K8s] mevzuat-takip (portal + API) · -worker · -beat · Redis (cluster)  ──▶ PostgreSQL
                        ▲ 443                                    ──▶ vLLM, Azure DI (OCR), Keycloak
          Kullanıcılar, Albatros
```

| Klasör | İçerik |
|---|---|
| `helm/mevzuat-takip/` | Ana servis Helm chart'ı (kurum düzeni: `common` kütüphanesi, `values-albaraka-{dev,uat}.yaml`): api ×2, worker, beat ×1 Recreate, migrate hook Job, Ingress, ConfigMap |
| `helm/secret.example.yaml` | Chart dışındaki Secret örneği (gizli değerler values'a yazılmaz) |
| `dmz/` | Crawler: Podman Quadlet (systemd) veya compose; ortam dosyası örneği |
| `mongo/` | En az yetkili iki Mongo kullanıcısı ve rolleri |

İmajlar Jenkins'te derlenir (`../Jenkinsfile`: Nexus taban imajı, kurum pip aynası ve CA'ları build-arg ile) ve
Nexus'a `com.albaraka.ai/mevzuat-takip-{core,crawler}` olarak gönderilir. Yerel derleme argümansız çalışır:

```bash
docker build -f backend/docker/Dockerfile --target core -t mevzuat-core .
docker build -f backend/docker/Dockerfile --target crawler -t mevzuat-crawler .
```

Chart doğrulama (Helm kurulu değilse konteynerle):

```bash
helm lint deploy/helm/mevzuat-takip -f deploy/helm/mevzuat-takip/values-albaraka-uat.yaml
helm template mevzuat-takip deploy/helm/mevzuat-takip -n artint -f deploy/helm/mevzuat-takip/values-albaraka-uat.yaml
```

Kurum ağına taşıma ve ilk kurulum: `docs/kurum-devreye-alma.md`. Günlük işletim: `docs/runbook.md`.
