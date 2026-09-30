// MongoDB kullanıcı ve rolleri (DMZ ↔ LAN teslim alanı). Mongo yöneticisi bir kez çalıştırır:
//   mongosh "mongodb://admin@10.155.7.195:27017/admin" --file deploy/mongo/init-users.js
// Parolalar ortam değişkeninden okunur: CRAWLER_PW=... CORE_PW=... mongosh ... --file ...
//
// En az yetki: DMZ ele geçirilse bile crawler kullanıcısı yalnızca mevzuat_crawl'a yazabilir; belge SİLEMEZ,
// başka veritabanını göremez. Ana servis kullanıcısı taranan veriyi değiştiremez; yalnızca senkron işaretini
// koyar ve tarama talebi bırakır.
const DB = "mevzuat_crawl";
const admin = db.getSiblingDB("admin");
const r = (coll, actions) => ({ resource: { db: DB, collection: coll }, actions });
const RW = ["find", "insert", "update"];

admin.createRole({
  role: "mevzuatCrawler", roles: [],
  privileges: [
    r("sources", RW), r("fetch_runs", RW), r("raw_documents", RW),
    r("raw.files", ["find", "insert"]), r("raw.chunks", ["find", "insert"]),   // GridFS: yalnızca ekleme
    r("crawl_requests", ["find", "update"]),                                   // talebi al / sonucunu yaz
    r("locks", ["find", "insert", "update", "remove"]),
    r("crawler_status", RW),
    { resource: { db: DB, collection: "" }, actions: ["createIndex", "listIndexes", "listCollections"] },
  ],
});

admin.createRole({
  role: "mevzuatCore", roles: [],
  privileges: [
    r("sources", ["find", "update"]), r("fetch_runs", ["find", "update"]),
    r("raw_documents", ["find", "update"]),                // yalnızca synced işareti
    r("raw.files", ["find"]), r("raw.chunks", ["find"]),
    r("crawl_requests", ["find", "insert"]),
    r("crawler_status", ["find"]),
    { resource: { db: DB, collection: "" }, actions: ["listCollections"] },
  ],
});

admin.createUser({ user: "mevzuat_crawler", pwd: process.env.CRAWLER_PW || passwordPrompt(), roles: ["mevzuatCrawler"] });
admin.createUser({ user: "mevzuat_core", pwd: process.env.CORE_PW || passwordPrompt(), roles: ["mevzuatCore"] });
print("mevzuatCrawler ve mevzuatCore rolleri ile kullanıcılar oluşturuldu");
