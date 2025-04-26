from chromadb import Client
from chromadb.api import ClientAPI
from src.database.sqlite import Example, Schema, get_session
from src.ai.embedding import embed_text
import logging
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

chroma_client: ClientAPI

async def init_client():
    global chroma_client
    chroma_client = Client()

    with get_session() as session:
        examples = session.query(Example).all()
        schemas = session.query(Schema).all()

        examples_collection = chroma_client.get_or_create_collection("examples")
        schemas_collection = chroma_client.get_or_create_collection("schemas")

        if len(examples) > 0:
            example_unnormalized_embeddings = await embed_text([example.unnormalized_text for example in examples])
            examples_collection.add(
                ids=[str(example.id) for example in examples],
                embeddings=example_unnormalized_embeddings,
                documents=[example.unnormalized_text for example in examples],
                metadatas=[{"type": example.type, "normalized_json": json.dumps(example.normalized_json, ensure_ascii=False)} for example in examples]
            )

        logger.info(f"Added {len(examples)} examples to the examples collection")

        if len(schemas) > 0:
            schema_embeddings = await embed_text([schema.type for schema in schemas])
            schemas_collection.add(
                ids=[str(schema.id) for schema in schemas],
                embeddings=schema_embeddings,
                documents=[schema.type for schema in schemas],
                metadatas=[{"attributes": str(schema.attributes)} for schema in schemas]
            )

        logger.info(f"Added {len(schemas)} schemas to the schemas collection")

async def get_examples(unnormalized_text: str, type: str) -> tuple[list[str], list[str]]:
    global chroma_client
    examples_collection = chroma_client.get_collection("examples")

    text_embeddings = await embed_text([unnormalized_text])

    results = examples_collection.query(
        query_embeddings=text_embeddings,
        where={"type": type},
        n_results=examples_collection.count(),
    )

    unnormalized_texts = results["documents"][0][:3]
    normalized_jsons = [json.loads(result["normalized_json"]) for result in results["metadatas"][0][:3]]

    return unnormalized_texts, normalized_jsons

async def get_types(unnormalized_text: str) -> list[str]:
    global chroma_client
    schemas_collection = chroma_client.get_collection("schemas")

    text_embeddings = await embed_text([unnormalized_text])

    results = schemas_collection.query(
        query_embeddings=text_embeddings,
        n_results=schemas_collection.count(),
    )

    types = results["documents"][0][:7]

    return types

async def add_example_chroma(example: Example):
    global chroma_client
    examples_collection = chroma_client.get_collection("examples")
    
    example_embedding = await embed_text([example.unnormalized_text])
    
    examples_collection.add(
        ids=[str(example.id)],
        embeddings=example_embedding,
        documents=[example.unnormalized_text],
        metadatas=[{"type": example.type, "normalized_json": json.dumps(example.normalized_json, ensure_ascii=False)}]
    )


async def add_schema_chroma(schema: Schema):
    global chroma_client
    schemas_collection = chroma_client.get_collection("schemas")
    
    schema_embedding = await embed_text([schema.type])
    
    schemas_collection.add(
        ids=[str(schema.id)],
        embeddings=schema_embedding,
        documents=[schema.type],
        metadatas=[{
            "attributes": str(schema.attributes)}
        ]
    )


async def delete_example_chroma(id: str):
    global chroma_client
    examples_collection = chroma_client.get_collection("examples")
    
    examples_collection.delete(ids=[id])


async def delete_schema_chroma(id: str):
    global chroma_client
    schemas_collection = chroma_client.get_collection("schemas")
    
    schemas_collection.delete(ids=[id])
