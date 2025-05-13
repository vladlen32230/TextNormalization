from openai import AsyncOpenAI
import os
from src.database.chroma import get_types, get_examples
import json
from dotenv import load_dotenv
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

type_prompt = """
/no_think
Ты должен определить тип товара по заданному тексту.

Текст:
{unnormalized_text}

Возможные типы:
{types}

Ответ должен быть либо одним из заданных типов, либо "неизвестно".
Выведи в ответе только тип, без дополнительных комментариев или лишнего текста.

Тип: 
"""

normalize_prompt = """
/no_think
Ты должен нормализовать заданный товар в json формат.
Ключи - названия атрибутов, значения - значения атрибутов.
Если атрибут неизвестен, напиши "Неизвестно".

Текст:
{unnormalized_text}

Ответ должен быть заданном формате:
```json
{attributes_examples}
```
"""

client = AsyncOpenAI(
    api_key=os.getenv("API_KEY", "Ollama"),
    base_url=os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
)

async def determine_type(unnormalized_text: str) -> str:
    types = await get_types(unnormalized_text)

    examples_type_prompt = type_prompt.format(
        unnormalized_text=unnormalized_text,
        types="\n".join(types)
    )

    response = await client.chat.completions.create(
        model=os.getenv("LLM_MODEL"),
        messages=[{"role": "user", "content": examples_type_prompt}],
        temperature=0.0
    )

    logger.info(f"\n\n" + "-" * 100 + f"\nDETERMINE TYPE PROMPT: \n{examples_type_prompt}\n\n ANSWER: \n{response.choices[0].message.content}\n" + "-" * 100 + "\n\n")

    return response.choices[0].message.content.replace("<think>", "").replace("</think>", "").strip().lower()

async def normalize_text(unnormalized_text: str, type: str, attributes: list[str]) -> dict:
    unnormalized_texts, normalized_jsons = await get_examples(unnormalized_text, type)

    attributes_examples = "{\n" + "\n".join([f"    \"{attribute}\": \"...\"" + ("," if i < len(attributes) - 1 else "") for i, attribute in enumerate(attributes)]) + "\n}"

    # Process and clean up normalized_jsons examples
    processed_jsons = []
    attributes_lower = [attr.lower().strip() for attr in attributes]
    
    for normalized_json_str in normalized_jsons:
        if not isinstance(normalized_json_str, dict):
            normalized_json = json.loads(normalized_json_str)
        else:
            normalized_json = normalized_json_str
          
        # Remove keys not in attributes list (case insensitive)
        keys_to_remove = [key for key in normalized_json if key.lower().strip() not in attributes_lower]
        for key in keys_to_remove:
            del normalized_json[key]
          
        # Add missing attributes with empty string values
        for i, attr in enumerate(attributes):
            attr_lower = attributes_lower[i]
            if not any(key.lower().strip() == attr_lower for key in normalized_json):
                normalized_json[attr] = "Неизвестно"
                  
        processed_jsons.append(json.dumps(normalized_json, ensure_ascii=False))
    
    examples_normalize_prompt = normalize_prompt.format(
        unnormalized_text=unnormalized_text,
        attributes_examples=attributes_examples
    ) + "\n\nПримеры нормализации:\n" + "\n\n".join([f"Текст: {unnormalized_text}\nНормализованный товар: {processed_json}" for unnormalized_text, processed_json in zip(unnormalized_texts, processed_jsons)])

    response = await client.chat.completions.create(
        model=os.getenv("LLM_MODEL"),
        messages=[{"role": "user", "content": examples_normalize_prompt}],
        temperature=0.0
    )

    logger.info(f"\n\n" + "-" * 100 + f"\nNORMALIZE TEXT PROMPT: \n{examples_normalize_prompt}\n\n ANSWER: \n{response.choices[0].message.content}\n" + "-" * 100 + "\n\n")

    content = response.choices[0].message.content.strip()

    # Find and remove code block markers using indexes
    start_marker = "```json"
    end_marker = "```"
    
    start_idx = content.find(start_marker)
    if start_idx != -1:
        content = content[start_idx + len(start_marker):].strip()
    
    end_idx = content.rfind(end_marker)
    if end_idx != -1:
        content = content[:end_idx].strip()

    content = content.replace("'", '"')

    try:
        return json.loads(content)
    except:
        print(f"Error parsing JSON: {content}")
        return {}
