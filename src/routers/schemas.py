from fastapi import APIRouter, HTTPException, status, UploadFile, File
from typing import List, Optional
from pydantic import BaseModel
from src.database.sqlite import Schema, get_session
from src.database.chroma import add_schema_chroma, delete_schema_chroma
import pandas as pd
import json
import asyncio
from io import BytesIO

router = APIRouter(prefix="/schemas", tags=["schemas"])

class SchemaBase(BaseModel):
    type: str
    attributes: list[str]

class SchemaCreate(SchemaBase):
    pass

    def model_post_init(self, __context):
        self.type = self.type.lower().strip()
        self.attributes = [attr.lower().strip() for attr in self.attributes]
        
class SchemaUpdate(BaseModel):
    type: Optional[str] = None
    attributes: Optional[list[str]] = None

    def model_post_init(self, __context):
        if self.type:
            self.type = self.type.lower().strip()
        if self.attributes:
            self.attributes = [attr.lower().strip() for attr in self.attributes]

class SchemaResponse(SchemaBase):
    id: int
    
    class Config:
        from_attributes = True

@router.post("/", response_model=SchemaResponse, status_code=status.HTTP_201_CREATED)
async def create_schema(schema: SchemaCreate):
    with get_session() as session:
        # Check if schema type already exists
        existing = session.query(Schema).filter(Schema.type == schema.type).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Схема с типом '{schema.type}' уже существует"
            )
        
        db_schema = Schema(
            type=schema.type,
            attributes=schema.attributes
        )
        session.add(db_schema)
        session.flush()
        session.refresh(db_schema)
        
        # Add to Chroma vector database
        await add_schema_chroma(db_schema)
        
        # Create a copy of the data before closing the session
        schema_data = SchemaResponse(
            id=db_schema.id,
            type=db_schema.type,
            attributes=db_schema.attributes
        )
        
        return schema_data

@router.get("/{schema_id}", response_model=SchemaResponse)
async def read_schema(schema_id: int):
    with get_session() as session:
        db_schema = session.query(Schema).filter(Schema.id == schema_id).first()
        if db_schema is None:
            raise HTTPException(status_code=404, detail="Схема не найдена")
        
        # Create a copy of the data before closing the session
        return SchemaResponse(
            id=db_schema.id,
            type=db_schema.type,
            attributes=db_schema.attributes
        )

@router.get("/type/{type}", response_model=SchemaResponse)
def read_schema_by_type(type: str):
    with get_session() as session:
        db_schema = session.query(Schema).filter(Schema.type == type).first()
        if db_schema is None:
            raise HTTPException(status_code=404, detail="Схема не найдена")
        
        # Create a copy of the data before closing the session
        return SchemaResponse(
            id=db_schema.id,
            type=db_schema.type,
            attributes=db_schema.attributes
        )

@router.get("/", response_model=List[SchemaResponse])
def read_schemas(skip: int = 0, limit: int = 100):
    with get_session() as session:
        schemas = session.query(Schema).offset(skip).limit(limit).all()
        
        # Create a copy of the data before closing the session
        return [
            SchemaResponse(
                id=schema.id,
                type=schema.type,
                attributes=schema.attributes
            )
            for schema in schemas
        ]

@router.put("/{schema_id}", response_model=SchemaResponse)
async def update_schema(schema_id: int, schema: SchemaUpdate):
    with get_session() as session:
        db_schema = session.query(Schema).filter(Schema.id == schema_id).first()
        if db_schema is None:
            raise HTTPException(status_code=404, detail="Схема не найдена")
        
        update_data = schema.model_dump(exclude_unset=True)
        
        # If type is being changed, check for conflicts
        if "type" in update_data and update_data["type"] != db_schema.type:
            existing = session.query(Schema).filter(Schema.type == update_data["type"]).first()
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Схема с типом '{update_data['type']}' уже существует"
                )
        
        for key, value in update_data.items():
            setattr(db_schema, key, value)
        
        session.flush()
        session.refresh(db_schema)
        
        # Update in Chroma vector database
        await delete_schema_chroma(str(db_schema.id))
        await add_schema_chroma(db_schema)
        
        # Create a copy of the data before closing the session
        return SchemaResponse(
            id=db_schema.id,
            type=db_schema.type,
            attributes=db_schema.attributes
        )

@router.delete("/{schema_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schema(schema_id: int):
    with get_session() as session:
        db_schema = session.query(Schema).filter(Schema.id == schema_id).first()
        if db_schema is None:
            raise HTTPException(status_code=404, detail="Схема не найдена")
        
        # Store ID before deletion for Chroma
        schema_id_str = str(db_schema.id)
        
        session.delete(db_schema)
        
        # Delete from Chroma vector database
        await delete_schema_chroma(schema_id_str)
        
    return None

@router.post("/upload_from_xlsx", status_code=status.HTTP_201_CREATED, response_model=List[SchemaResponse])
async def upload_from_xlsx(file: UploadFile = File(...)):
    # Save the uploaded file temporarily
    contents = await file.read()
    
    df = pd.read_excel(BytesIO(contents), header=None)
    
    # Get JSON objects from the first column
    rows = df.iloc[:, 0].to_list()
    
    created_schemas = []
    chroma_tasks = []
    
    # Process each row in the dataframe
    with get_session() as session:
        for i, row in enumerate(rows):  # Added enumerate to get index
            print(f"Processing row {i}: {row}")  # Added print statement
            
            # Parse JSON string to dict
            json_data = json.loads(row)
            json_data = {k.lower().strip(): v.lower().strip() for k, v in json_data.items()}

            # Extract type from "тип" field
            schema_type = json_data.get("тип")
            if not schema_type:
                raise HTTPException(status_code=400, detail="Поле 'тип' не найдено")
            
            # Check if schema type already exists
            existing = session.query(Schema).filter(Schema.type == schema_type).first()
            if existing:
                continue  # Skip existing schemas
            
            # Create schema in database
            db_schema = Schema(
                type=schema_type,
                attributes=list(json_data.keys())
            )

            session.add(db_schema)
            session.flush()  # Flush to get the ID
            session.refresh(db_schema)
            
            # Convert to Pydantic model before appending
            created_schemas.append(SchemaResponse(
                id=db_schema.id,
                type=db_schema.type,
                attributes=db_schema.attributes
            ))
            
            # Create task for adding to Chroma
            chroma_tasks.append(add_schema_chroma(db_schema))
    
        await asyncio.gather(*chroma_tasks)
        
        return created_schemas