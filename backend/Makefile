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
docker-build:   ## iki imaj: LAN ana servis (core) ve DMZ crawler
	cd .. && docker build -f backend/docker/Dockerfile --target core -t mevzuat-core . && \
	docker build -f backend/docker/Dockerfile --target crawler -t mevzuat-crawler .
compose-up:     ## kurum topolojisinin yerel kopyası (crawler + mongo + api/worker/beat + redis + postgres)
	cd .. && docker compose -f backend/docker/docker-compose.yml up -d --build
mongo-test:     ## Mongo entegrasyon testleri için yerel MongoDB (tests/test_crawler_mongo.py)
	docker run -d --name mevzuat-mongo-test -p 27018:27017 mongo:7
