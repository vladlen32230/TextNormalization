from fastapi import APIRouter, Body, UploadFile, File
from fastapi.responses import StreamingResponse
from src.ai.llm import determine_type, normalize_text
from src.database.sqlite import Schema, get_session
import pandas as pd
import io
import asyncio
from collections import defaultdict
from openpyxl.utils import get_column_letter


router = APIRouter(prefix="/processing", tags=["processing"])

@router.post("/normalize_text", response_model=dict)
async def normalize_text_endpoint(text: str = Body(...)):
    text = text.strip().lower()
    type = await determine_type(text)
    with get_session() as session:
        schema = session.query(Schema).filter(Schema.type == type).first()
        if type.lower().strip() == "неизвестно" or not schema:
            return {"тип": "неизвестно"}
        attributes = schema.attributes

    normalized_text = await normalize_text(text, type, attributes)
    return normalized_text

@router.post("/normalize_xlsx")
async def normalize_xlsx_endpoint(file: UploadFile = File(...)):
    # Read the XLSX file
    contents = await file.read()
    df = pd.read_excel(io.BytesIO(contents), header=None)
    
    # Extract first column texts
    first_column = df.iloc[:, 0].astype(str).tolist()
    
    # Determine types in parallel
    type_tasks = [determine_type(text) for text in first_column]
    types = await asyncio.gather(*type_tasks)
    types = [type.lower().strip() for type in types]
    
    # Group texts by type
    type_to_texts = defaultdict(list)
    for i, (text, type_name) in enumerate(zip(first_column, types)):
        type_to_texts[type_name].append((i, text.strip().lower()))
    
    # Get schemas for each type
    type_to_schema = {}
    with get_session() as session:
        for type_name in type_to_texts.keys():
            if type_name != "неизвестно":
                schema = session.query(Schema).filter(Schema.type == type_name).first()
                if schema:
                    type_to_schema[type_name] = schema.attributes
    
    # Normalize texts in parallel across all types
    all_tasks = []
    task_metadata = []
    
    for type_name, text_indices in type_to_texts.items():
        if type_name == "неизвестно":
            for idx, text in text_indices:
                task_metadata.append((type_name, idx, None, text))
        else:
            attributes = type_to_schema.get(type_name, [])
            if attributes:
                # Create normalization tasks for this type
                for idx, text in text_indices:
                    task = normalize_text(text, type_name, attributes)
                    all_tasks.append(task)
                    task_metadata.append((type_name, idx, len(all_tasks)-1, None))
    
    # Execute all normalization tasks at once
    all_results = await asyncio.gather(*all_tasks) if all_tasks else []
    
    # Organize results by type
    normalized_data = defaultdict(list)
    
    for type_name, idx, task_idx, text in task_metadata:
        if type_name == "неизвестно":
            normalized_data[type_name].append((idx, {"text": text}))
        else:
            normalized_data[type_name].append((idx, all_results[task_idx]))
    
    # Create a new Excel file with sheets for each type
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        for type_name, data in normalized_data.items():
            if data:
                # Sort by original index to maintain order
                sorted_data = sorted(data, key=lambda x: x[0])
                
                # Get all unique keys from the normalized data
                keys = set()
                for _, item in sorted_data:
                    keys.update(item.keys())
                
                # Create DataFrame with all keys as columns
                rows = []
                for _, item in sorted_data:
                    row = {key: item.get(key, "") for key in keys}
                    rows.append(row)
                
                sheet_df = pd.DataFrame(rows)
                sheet_df.to_excel(writer, sheet_name=type_name, index=False)
                
                # Auto-adjust column widths to fit content
                worksheet = writer.sheets[type_name]
                for i, column in enumerate(sheet_df.columns):
                    column_width = max(
                        sheet_df[column].astype(str).map(len).max(),
                        len(str(column))
                    )
                    # Add a little extra width for padding
                    column_width = column_width + 2
                    # Set column width (column index is i+1 because Excel is 1-indexed)
                    worksheet.column_dimensions[get_column_letter(i+1)].width = column_width
    
    # Reset pointer to the beginning of the file
    output.seek(0)
    
    # Return the XLSX file
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=normalized_data.xlsx"}
    )