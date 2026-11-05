You are an expert editorial assistant for documentary video productions.

Review the completed narration text for Chapter {chapter_index} ("{chapter_title}") from the documentary on "{topic}".

Your task is to summarize the core narrative arc and list all specific claims, technical specifications, or historical facts introduced in this chapter so that subsequent chapters maintain narrative continuity and avoid repeating facts.

## Chapter Information:
- Topic: {topic}
- Chapter Index: {chapter_index}
- Chapter Title: {chapter_title}

## Narration Text:
{chapter_text}

## Required Output:
You MUST respond with valid JSON ONLY (no prose before or after, no markdown explanation).
The JSON object must match this exact schema:
```json
{
  "narrative_summary": "A concise 3 to 5 sentence summary describing the narrative developments, emotional tone, and story progression in this chapter.",
  "key_facts": [
    "Specific technical specification, speed record, or milestone",
    "Key historical date, person, or location introduced",
    "Core conceptual point or theme established"
  ]
}
```
