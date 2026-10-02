# Mevzuat Takip — geliştirme ve işletim kısayolları
.PHONY: install test lint eval smoke migrate api load-test docker-build compose-up mongo-test audit-verify

install:        ## geliştirme ortamı
	pip install -e ".[core,dev,postgres]"
test:           ## ağ gerektirmeyen testler
	pytest -q
lint:
	ruff check .
eval:           ## İK-3 değerlendirme seti (LLM gerekir)
	mevzuat-ai eval tests/eval/relevance_v0.jsonl
smoke:          ## kaynak sitelerine erişim (canlı)
	mevzuat-collect check-access
migrate:        ## şemayı güncelle
	alembic upgrade head
api:
	uvicorn app.api.main:app --port 8000
load-test:      ## 1 yıllık veri hacmiyle yanıt süreleri
	python scripts/load_test.py
audit-verify:   ## denetim izi hash zinciri
	mevzuat-monitor audit-verify
LOCAL_BUILD_ARGS = --build-arg BASE_IMAGE=python:3.12-slim-bullseye --build-arg PIP_INDEX_URL=https://pypi.org/simple \
	--build-arg CA_CERT_URLS=
docker-build:   ## iki imaj, kurum ağı dışında (kurumda Azure DevOps argümansız derler)
	cd .. && docker build $(LOCAL_BUILD_ARGS) --target core -t mevzuat-core . && \
	docker build $(LOCAL_BUILD_ARGS) --target crawler -t mevzuat-crawler .
compose-up:     ## kurum topolojisinin yerel kopyası (crawler + mongo + api/worker/beat + redis + postgres)
	cd .. && docker compose -f backend/docker/docker-compose.yml up -d --build
mongo-test:     ## Mongo entegrasyon testleri için yerel MongoDB (tests/test_crawler_mongo.py)
	docker run -d --name mevzuat-mongo-test -p 27018:27017 mongo:7
