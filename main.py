from fastapi import FastAPI
import uvicorn

from core.database import lifespan
from api import api_router

app = FastAPI(lifespan=lifespan)
app.include_router(api_router)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
