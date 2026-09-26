# Mevzuat Takip — geliştirme ve işletim kısayolları
.PHONY: install test lint eval smoke migrate api load-test docker-build compose-up audit-verify

install:        ## geliştirme ortamı
	pip install -e ".[dev,postgres]"
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
docker-build:
	cd .. && docker build -f backend/docker/Dockerfile -t mevzuat-backend .
compose-up:
	cd .. && docker compose -f backend/docker/docker-compose.yml --profile dev up -d
