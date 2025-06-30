from fastapi import APIRouter, HTTPException, status, UploadFile, File
from typing import List, Optional
import pandas as pd
import json
import asyncio
from pydantic import BaseModel
from io import BytesIO
from src.database.sqlite import Example, get_session
from src.database.chroma import add_example_chroma, delete_example_chroma
from src.utils.text_processing import normalize_quotes_for_json

router = APIRouter(prefix="/examples", tags=["examples"])

class ExampleBase(BaseModel):
    type: str
    unnormalized_text: str
    normalized_json: dict

class ExampleCreate(ExampleBase):
    pass

    def model_post_init(self, __context):
        self.type = self.type.lower().strip()
        self.unnormalized_text = self.unnormalized_text.lower().strip()
        self.normalized_json = {k.lower().strip(): v.lower().strip() for k, v in self.normalized_json.items()}

class ExampleUpdate(BaseModel):
    type: Optional[str] = None
    unnormalized_text: Optional[str] = None
    normalized_json: Optional[dict] = None

    def model_post_init(self, __context):
        if self.type:
            self.type = self.type.lower().strip()
        if self.unnormalized_text:
            self.unnormalized_text = self.unnormalized_text.lower().strip()
        if self.normalized_json:
            self.normalized_json = {k.lower().strip(): v.lower().strip() for k, v in self.normalized_json.items()}

class ExampleResponse(ExampleBase):
    id: int
    
    class Config:
        from_attributes = True

@router.post("/", response_model=ExampleResponse, status_code=status.HTTP_201_CREATED)
async def create_example(example: ExampleCreate):
    with get_session() as session:
        db_example = Example(
            type=example.type,
            unnormalized_text=example.unnormalized_text,
            normalized_json=example.normalized_json
        )
        session.add(db_example)
        session.flush()  # Flush to get the ID
        session.refresh(db_example)
        
        # Add to Chroma vector database
        await add_example_chroma(db_example)
        
        # Create a copy of the data before closing the session
        example_data = ExampleResponse(
            id=db_example.id,
            type=db_example.type,
            unnormalized_text=db_example.unnormalized_text,
            normalized_json=db_example.normalized_json
        )
        
        return example_data

@router.get("/{example_id}", response_model=ExampleResponse)
async def read_example(example_id: int):
    with get_session() as session:
        db_example = session.query(Example).filter(Example.id == example_id).first()
        if db_example is None:
            raise HTTPException(status_code=404, detail="Пример не найден")
        
        # Create a copy of the data before closing the session
        return ExampleResponse(
            id=db_example.id,
            type=db_example.type,
            unnormalized_text=db_example.unnormalized_text,
            normalized_json=db_example.normalized_json
        )

@router.get("/", response_model=List[ExampleResponse])
async def read_examples(skip: int = 0, limit: int = 100, type: Optional[str] = None):
    with get_session() as session:
        query = session.query(Example)
        if type:
            query = query.filter(Example.type == type)
        examples = query.offset(skip).limit(limit).all()
        
        # Create a copy of the data before closing the session
        return [
            ExampleResponse(
                id=example.id,
                type=example.type,
                unnormalized_text=example.unnormalized_text,
                normalized_json=example.normalized_json
            ) 
            for example in examples
        ]

@router.put("/{example_id}", response_model=ExampleResponse)
async def update_example(example_id: int, example: ExampleUpdate):
    with get_session() as session:
        db_example = session.query(Example).filter(Example.id == example_id).first()
        if db_example is None:
            raise HTTPException(status_code=404, detail="Пример не найден")
        
        update_data = example.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(db_example, key, value)
        
        session.flush()
        session.refresh(db_example)
        
        # Update in Chroma vector database
        await delete_example_chroma(str(db_example.id))
        await add_example_chroma(db_example)
        
        # Create a copy of the data before closing the session
        return ExampleResponse(
            id=db_example.id,
            type=db_example.type,
            unnormalized_text=db_example.unnormalized_text,
            normalized_json=db_example.normalized_json
        )

@router.delete("/{example_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_example(example_id: int):
    with get_session() as session:
        db_example = session.query(Example).filter(Example.id == example_id).first()
        if db_example is None:
            raise HTTPException(status_code=404, detail="Пример не найден")
        
        # Store ID before deletion for Chroma
        example_id_str = str(db_example.id)
        
        session.delete(db_example)
        
    # Delete from Chroma vector database
    await delete_example_chroma(example_id_str)
        
    return None

@router.post("/upload_from_xlsx", status_code=status.HTTP_201_CREATED, response_model=List[ExampleResponse])
async def upload_from_xlsx(file: UploadFile = File(...)):
    # Save the uploaded file temporarily
    contents = await file.read()
    
    df = pd.read_excel(BytesIO(contents), header=None)
    
    rows = df.iloc[:, 0].to_list()
    
    created_examples = []
    chroma_tasks = []
    
    # Process each row in the dataframe
    with get_session() as session:
        for row in rows:
            # Replace curly quotes with straight quotes for JSON parsing
            row_normalized = normalize_quotes_for_json(str(row))
            
            # Parse JSON string to dict
            json_data = json.loads(row_normalized)
            json_data = {k.lower().strip(): v.lower().strip() for k, v in json_data.items()}
            
            # Extract type from "Тип" field
            example_type = json_data.get("тип")
            if not example_type:
                raise HTTPException(status_code=400, detail="Тип не найден")
            
            # Create unnormalized text by joining all values
            unnormalized_text = " ".join(str(v) for v in json_data.values())
            
            # Create example in database
            db_example = Example(
                type=example_type,
                unnormalized_text=unnormalized_text,
                normalized_json=json_data
            )
            session.add(db_example)
            session.flush()  # Flush to get the ID
            session.refresh(db_example)
            
            # Convert to Pydantic model before appending
            created_examples.append(ExampleResponse(
                id=db_example.id,
                type=db_example.type,
                unnormalized_text=db_example.unnormalized_text,
                normalized_json=db_example.normalized_json
            ))
            
            # Create task for adding to Chroma
            chroma_tasks.append(add_example_chroma(db_example))

        await asyncio.gather(*chroma_tasks)
        
        return created_examples