from openai import AsyncOpenAI
import os

from dotenv import load_dotenv
load_dotenv()

client = AsyncOpenAI(
    base_url=os.getenv("LLM_BASE_URL", "http://localhost:11434/v1"), 
    api_key=os.getenv("API_KEY", "OLLAMA")
)

async def embed_text(text: list[str]) -> list[list[float]]:
    response = await client.embeddings.create(
        input=text,
        model=os.getenv("EMBED_MODEL", "nomic-embed-text:latest")
    )

    return [embedding.embedding for embedding in response.data]

