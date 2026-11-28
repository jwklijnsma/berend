# Quick start

```bash
ollama pull qwen2.5:14b && ollama pull nomic-embed-text
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env                     # fill in what you use

python -m berend.ingest                  # build the index (incremental; --reset to rebuild)
python -m berend.agent "Which Argo CD apps are broken and why?"   # test without chat
python -m berend.argocd --once           # test the Argo CD connection
python -m berend.main                    # run it all
pip install -r requirements-dev.txt && pytest    # 29 tests, no external services needed
```
