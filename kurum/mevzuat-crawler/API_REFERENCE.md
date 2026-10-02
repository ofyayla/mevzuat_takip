# mevzuat-crawler — Referans

Crawler'ın HTTP API'si yoktur. Dış dünyaya açılan yüzeyi: **komutlar**, **ortam değişkenleri** ve mevzuat-core ile
paylaşılan **MongoDB sözleşmesi**.

## Komutlar

| Komut | Açıklama |
|---|---|
| `mevzuat-crawler serve` | Servis (imaj varsayılanı): zamanlama + LAN tarama talepleri, canlılık sinyali |
| `mevzuat-crawler check` | MongoDB erişimi, indeksler, aktarılmayı bekleyen kayıtlar, son sinyal |
| `mevzuat-crawler run KOD\|all [--channel K] [--max-details N]` | Elle tarama (Mongo'ya yazar; kaynak kilidine uyar) |
| `mevzuat-crawler backfill KOD --from YYYY-MM-DD --to YYYY-MM-DD` | Geçmiş dönem (Resmî Gazete gün gün) |
| `mevzuat-crawler healthcheck` | Konteyner sağlık kontrolü (sinyal dosyası taze mi) |
| `mevzuat-collect sources` | Kaynak ve kanal listesi |
| `mevzuat-collect check-access [KOD …]` | Kaynaklara erişim testi (firewall/proxy/TLS) |
| `mevzuat-collect probe KOD [--record DİZİN]` | Listeyi çeker, yazmaz; `--record` yanıtları fixture olarak kaydeder |
| `mevzuat-collect ca-fetch HOST …` | Zincirini eksik gönderen sunucunun ara sertifikasını indirip doğrular (`config/certs`) |

## Ortam değişkenleri

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `MONGO_URL` | — (zorunlu) | `mevzuat_crawler` kullanıcısı; Secret / `crawler.env` |
| `MONGO_DB` | `mevzuat_crawl` | Dev: `mevzuat_crawl_dev` |
| `CRAWLER_MAX_PARALLEL` | `3` | Aynı anda taranan kaynak |
| `CRAWLER_POLL_S` | `15` | Zamanlama/talep kontrol aralığı (sn) |
| `CRAWLER_LOCK_TTL_S` | `3600` | Kaynak kilidi süresi (çöken sürecin kilidi bu süre sonunda devralınır) |
| `HTTPS_PROXY`, `CA_BUNDLE` | — | Kurum çıkış proxy'si ve kök sertifika paketi |
| `HTTP_USER_AGENT` | `MevzuatTakipBot/1.0 …` | İsteklerde kimlik |
| `COLLECT_REQUEST_DELAY_S` | `1.5` | Host başına istekler arası bekleme |
| `COLLECT_TIMEOUT_S`, `COLLECT_MAX_ATTACHMENTS`, `COLLECT_MAX_BYTES` | `30`, `5`, 50 MB | İstek sınırları |

## MongoDB sözleşmesi (`mevzuat_crawl`)

| Koleksiyon | Yazan | Okuyan | İçerik |
|---|---|---|---|
| `sources` | crawler | core | Kaynak yapılandırması (`_id` = kaynak kodu) |
| `fetch_runs` | crawler | core | Tarama: `source_code, started_at, finished_at, status (running\|success\|partial\|failed), items_*, channels, errors, structure_alert` |
| `raw_documents` | crawler | core | Belge sürümü: `source_code, channel, external_id, version, is_latest, parent_id, role (main\|attachment\|listing), url, final_url, title, published_at, category, listing_hash, version_key, content_type, content_sha256, size_bytes, storage_key, fetch_run_id, fetched_at, is_baseline, extra` |
| `raw.files` / `raw.chunks` | crawler | core | GridFS ham içerik; dosya adı = `storage_key` (`raw/ab/<sha256>`) |
| `crawl_requests` | core | crawler | `kind (run\|backfill), source_code, params, requested_by, status (pending→running→done\|failed), result, error` |
| `locks` | crawler | — | Kaynak başına tarama kilidi (`_id` = `collect:<KOD>`) |
| `crawler_status` | crawler | core | Canlılık sinyali (`_id` = host, `at`, `running`, `next_due`); core 30 dk sinyal görmezse `crawler_down` alarmı |

**Senkron kuralı:** crawler her yazımda `synced=false` yapar ve `rev`'i artırır. core kaydı PostgreSQL'e aktarınca
`synced=true`'yu yalnızca `rev` değişmemişse koyar (arada güncellenen kayıt bir sonraki turda yeniden aktarılır).
Ham içerik silinmez; içerik değişince yeni sürüm açılır, eski sürüm `is_latest=false` olur.

**Yetki:** `mevzuat_crawler` kullanıcısı `mevzuat_crawl` dışında hiçbir veritabanını göremez ve belge silemez
(`deploy/mongo/init-users.js`, mevzuat-core reposunda).
