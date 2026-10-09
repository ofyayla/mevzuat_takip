# Mevzuat Takip — geliştirme ve işletim kısayolları (depo kökünden). Servis dizinleri: mevzuat-core, mevzuat-crawler.
CORE = mevzuat-core/app
CRAWLER = mevzuat-crawler/app
LOCAL_BUILD_ARGS = --build-arg BASE_IMAGE=python:3.12-slim-bullseye --build-arg PIP_INDEX_URL=https://pypi.org/simple \
	--build-arg CA_CERT_URLS=
.PHONY: install install-crawler test test-core test-crawler lint shared-check shared-sync api-reference eval smoke \
	migrate api load-test audit-verify docker-build compose-up mongo-test

install:        ## mevzuat-core geliştirme ortamı
	cd $(CORE) && pip install -e ".[core,dev,postgres]"
install-crawler: ## mevzuat-crawler geliştirme ortamı (ayrı venv önerilir)
	cd $(CRAWLER) && pip install -e ".[dev]"
test: test-core test-crawler shared-check   ## ağ gerektirmeyen tüm testler
test-core:
	cd $(CORE) && pytest -q
test-crawler:
	cd $(CRAWLER) && pytest -q
lint:
	cd $(CORE) && ruff check .
	cd $(CRAWLER) && ruff check .
shared-check:   ## core ↔ crawler ortak dosyalar eşit mi (tools/shared.py)
	python tools/shared.py check
shared-sync:    ## ortak dosyaları core'dan crawler'a kopyala
	python tools/shared.py sync
api-reference:  ## mevzuat-core/API_REFERENCE.md'yi OpenAPI şemasından yeniden üret
	cd $(CORE) && python scripts/gen_api_reference.py
eval:           ## İK-3 değerlendirme seti (LLM gerekir)
	cd $(CORE) && mevzuat-ai eval tests/eval/relevance_v0.jsonl
smoke:          ## kaynak sitelerine erişim (canlı)
	cd $(CRAWLER) && mevzuat-collect check-access
migrate:        ## şemayı güncelle
	cd $(CORE) && alembic upgrade head
api:
	cd $(CORE) && uvicorn app.api.main:app --port 8000
load-test:      ## 1 yıllık veri hacmiyle yanıt süreleri
	cd $(CORE) && python scripts/load_test.py
audit-verify:   ## denetim izi hash zinciri
	cd $(CORE) && mevzuat-monitor audit-verify
docker-build:   ## iki imaj, kurum ağı dışında (kurumda Azure DevOps argümansız derler)
	docker build $(LOCAL_BUILD_ARGS) -t mevzuat-core ./mevzuat-core
	docker build $(LOCAL_BUILD_ARGS) -t mevzuat-crawler ./mevzuat-crawler
compose-up:     ## kurum topolojisinin yerel kopyası (crawler + mongo + api/worker/beat + redis + postgres)
	docker compose up -d --build
mongo-test:     ## Mongo entegrasyon testleri için yerel MongoDB (tests/test_crawler_mongo.py)
	docker run -d --name mevzuat-mongo-test -p 27018:27017 mongo:7
