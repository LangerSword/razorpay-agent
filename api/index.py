from fastapi import FastAPI

app = FastAPI()

@app.get("/")
async def root():
    return {"status": "ok", "message": "Common API"}

@app.get("/api/health")
async def health():
    return {"status": "ok"}
