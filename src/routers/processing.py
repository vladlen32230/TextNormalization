from fastapi import APIRouter, Body, UploadFile, File
from fastapi.responses import StreamingResponse
from src.ai.llm import determine_type, normalize_text
from src.database.sqlite import Schema, get_session
import pandas as pd
import io
import json
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
    first_column_series = df.iloc[:, 0]
    # Filter out NaN values and empty strings
    first_column_series = first_column_series.dropna()
    first_column = [str(text).strip() for text in first_column_series.tolist() if str(text).strip()]
    
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
                keys = sorted(list(keys)) # Sort keys for consistent column order
                
                rows = []
                for idx, item in sorted_data:
                    # Get the original text using the stored index
                    original_text = first_column[idx]
                    
                    # Create row dictionary including normalized data, empty column, and original text
                    row = {key: item.get(key, "") for key in keys}
                    # Add an empty column (using an empty string as key, pandas will handle it)
                    row[""] = "" 
                    # Add the original text column
                    row["Оригинал"] = original_text
                    rows.append(row)
                
                # Define column order: normalized keys, empty column, original column
                column_order = keys + ["", "Оригинал"]
                
                sheet_df = pd.DataFrame(rows, columns=column_order)
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

@router.post("/validate_normalization", response_model=dict)
async def validate_normalization(file: UploadFile = File(...)):
    """
    Endpoint that receives an XLSX file with unnormalized text in column 1 and JSON normalized version in column 2.
    It normalizes the text using existing methods and checks if the result matches the provided JSON.
    
    Returns a dictionary with validation results.
    """
    # Read the XLSX file
    contents = await file.read()
    df = pd.read_excel(io.BytesIO(contents), header=None)
    
    # Ensure the file has at least 2 columns
    if df.shape[1] < 2:
        return {"error": "The file must have at least 2 columns: unnormalized text and JSON normalized version"}
    
    # Extract valid rows and prepare data for processing
    valid_rows = []
    for index, row in df.iterrows():
        print(row)
        if pd.isna(row[0]) or pd.isna(row[1]):
            continue
            
        unnormalized_text = str(row[0]).strip()
        expected_json_str = str(row[1]).strip()
        
        # Skip empty rows
        if not unnormalized_text or not expected_json_str:
            continue
        
        try:
            # Load and normalize the expected JSON (strip and lowercase keys)
            raw_expected_json = json.loads(expected_json_str)
            expected_json = {}
            for key, value in raw_expected_json.items():
                # Strip and lowercase keys for consistent comparison
                expected_json[key.strip().lower()] = value
                
            valid_rows.append({
                "index": index,
                "unnormalized_text": unnormalized_text,
                "expected_json_str": expected_json_str,
                "raw_expected_json": raw_expected_json,
                "expected_json": expected_json
            })
        except json.JSONDecodeError:
            # Skip rows with invalid JSON
            pass
    
    # Initialize validation summary
    validation_summary = {
        "total": len(valid_rows),     # Total number of rows processed
        "matched": 0,                 # Number of rows with perfect matches
        "mismatched": 0,             # Number of rows with at least one mismatch
        "total_pairs": 0,            # Total number of key-value pairs checked
        "correct_pairs": 0,          # Number of correctly matched key-value pairs
        "incorrect_pairs": 0         # Number of incorrectly matched key-value pairs
    }
    
    # No valid rows to process
    if not valid_rows:
        return {
            "summary": validation_summary,
            "results": []
        }
    
    # Step 1: Determine types for all texts in parallel
    unnormalized_texts = [row["unnormalized_text"] for row in valid_rows]
    type_tasks = [determine_type(text) for text in unnormalized_texts]
    text_types = await asyncio.gather(*type_tasks)
    
    # Step 2: Get schemas for all determined types
    type_to_attributes = {}
    with get_session() as session:
        for text_type in set(text_types):
            if text_type.lower().strip() != "неизвестно":
                schema = session.query(Schema).filter(Schema.type == text_type).first()
                if schema:
                    type_to_attributes[text_type] = schema.attributes
    
    # Step 3: Create normalization tasks for texts with valid schemas
    normalization_tasks = []
    task_indices = []
    
    for i, (row, text_type) in enumerate(zip(valid_rows, text_types)):
        attributes = type_to_attributes.get(text_type, [])
        if text_type.lower().strip() != "неизвестно" and attributes:
            task = normalize_text(row["unnormalized_text"], text_type, attributes)
            normalization_tasks.append(task)
            task_indices.append(i)
    
    # Step 4: Execute all normalization tasks in parallel
    normalization_results = await asyncio.gather(*normalization_tasks) if normalization_tasks else []
    
    # Step 5: Process results and build response
    results = []
    
    for i, (row, text_type) in enumerate(zip(valid_rows, text_types)):
        unnormalized_text = row["unnormalized_text"]
        raw_expected_json = row["raw_expected_json"]
        expected_json = row["expected_json"]
        
        # Check if this row had a normalization task
        if i in task_indices:
            task_idx = task_indices.index(i)
            raw_actual_json = normalization_results[task_idx]
            
            # Normalize the actual JSON (strip and lowercase keys)
            actual_json = {}
            for key, value in raw_actual_json.items():
                # Strip and lowercase keys for consistent comparison
                actual_json[key.strip().lower()] = value
            
            # Compare the normalized result with the expected JSON
            is_match = True
            mismatch_details = {}
            
            # Count key-value pairs for statistics
            total_pairs = len(expected_json)
            correct_pairs = 0
            incorrect_pairs = 0
            
            for key, expected_value in expected_json.items():
                validation_summary["total_pairs"] += 1
                
                # For string values, also compare them case-insensitively
                if key not in actual_json:
                    is_match = False
                    incorrect_pairs += 1
                    mismatch_details[key] = {
                        "expected": expected_value,
                        "actual": "missing"
                    }
                elif isinstance(expected_value, str) and isinstance(actual_json[key], str):
                    # For string values, compare them case-insensitively
                    if actual_json[key].strip().lower() != expected_value.strip().lower():
                        is_match = False
                        incorrect_pairs += 1
                        mismatch_details[key] = {
                            "expected": expected_value,
                            "actual": actual_json[key]
                        }
                    else:
                        correct_pairs += 1
                elif actual_json[key] != expected_value:
                    is_match = False
                    incorrect_pairs += 1
                    mismatch_details[key] = {
                        "expected": expected_value,
                        "actual": actual_json[key]
                    }
                else:
                    correct_pairs += 1
            
            # Update validation counters
            if is_match:
                validation_summary["matched"] += 1
            else:
                validation_summary["mismatched"] += 1
                
            # Add pair statistics
            validation_summary["correct_pairs"] += correct_pairs
            validation_summary["incorrect_pairs"] += incorrect_pairs
            
            # Add result to the list
            results.append({
                "unnormalized_text": unnormalized_text,
                "expected_json": raw_expected_json,  # Return the original format for display
                "actual_json": raw_actual_json,      # Return the original format for display
                "matched": is_match,
                "total_pairs": total_pairs,
                "correct_pairs": correct_pairs,
                "incorrect_pairs": incorrect_pairs,
                "mismatch_details": mismatch_details if not is_match else {},
                "type": text_type
            })
        else:
            # Type is unknown or no schema found, mark as mismatched
            validation_summary["mismatched"] += 1
            results.append({
                "unnormalized_text": unnormalized_text,
                "expected_json": raw_expected_json,
                "actual_json": {"тип": "неизвестно"},
                "matched": False,
                "total_pairs": 0,
                "correct_pairs": 0,
                "incorrect_pairs": 0,
                "type": text_type
            })
    
    # Create output with summary and detailed results
    return {
        "summary": validation_summary,
        "results": results
    }

    # Create output with summary and detailed results
    return {
        "summary": validation_summary,
        "results": results
    }