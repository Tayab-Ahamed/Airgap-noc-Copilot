.PHONY: setup data train index run demo test clean

setup:
	python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

data:
	python -m ml.make_dataset --episodes 90 --out data/train.parquet

train: data
	python -m ml.train --data data/train.parquet --out models/

index:
	python -m rag.indexer --docs rag/runbooks --out data/faiss_index

run:
	uvicorn backend.main:app --reload --port 8000

demo: train index run

test:
	PYTHONPATH=. pytest -q

clean:
	rm -rf data models __pycache__ */__pycache__
