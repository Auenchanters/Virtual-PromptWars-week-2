"""FastAPI router packages for VoteWise India.

Each module focuses on one concern (chat / translate / tts / info / places)
so [app/main.py](app/main.py) only has to assemble them. Every public
endpoint lives under ``app.routers.*``.

Rubric: Code Quality (clear separation of concerns; main.py is now only
app factory + middleware + ``include_router`` calls).
"""
