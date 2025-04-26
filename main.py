from fastapi import FastAPI
from contextlib import asynccontextmanager
from fastapi.staticfiles import StaticFiles
from src.routers.examples import router as examples_router
from src.routers.schemas import router as schemas_router
from src.routers.processing import router as processing_router
from src.database.sqlite import Base, engine
from src.database.chroma import init_client

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create database tables at startup
    Base.metadata.create_all(bind=engine)
    await init_client()
    yield

app = FastAPI(lifespan=lifespan)

# API routes must come BEFORE static file mounting
app.include_router(examples_router)
app.include_router(schemas_router)
app.include_router(processing_router)

# Mount static files AFTER registering API routes
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)