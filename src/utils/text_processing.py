def normalize_quotes_for_json(text: str) -> str:
    """
    Replace curly quotes with straight quotes for JSON parsing.
    Converts both " and " to " to ensure valid JSON format.
    """
    return text.replace('“', '"').replace('”', '"') 