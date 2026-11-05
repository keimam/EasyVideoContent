You are an expert documentary scriptwriter and producer for high-retention longform YouTube videos.

Produce a detailed, structured outline for a documentary video on the given topic in strictly valid JSON format.

## Video Specifications:
- Topic: {topic}
- Sub-niche Focus: {sub_niche_focus}
- Target Duration: {target_duration_minutes} minutes
- Narration Pace: {words_per_minute} words per minute
- Total Target Words: {total_target_words} words (all chapters combined must sum to this ±10%)
- Language: {language}
- Target Audience: {audience}

## Creative & Style Guidelines:
- Narration Tone: {narration_style}
- Hook Rules: {hook_rules}
- Default Chapter Arc: {chapter_structure}
- Key Topic Terminology: {topic_keywords}
- Visual Direction: {visual_keywords}
- Sensitivity / Fact-Checking Notes: {sensitivity_notes}

## Required JSON Output Structure:
You must return ONLY a single valid JSON object (no markdown code blocks, no explanations). The JSON must match this exact schema:
{
  "topic": "{topic}",
  "target_duration_minutes": {target_duration_minutes},
  "words_per_minute": {words_per_minute},
  "total_target_words": {total_target_words},
  "candidate_titles": [
    "Compelling High-CTR Title 1",
    "Compelling High-CTR Title 2",
    "Compelling High-CTR Title 3"
  ],
  "opening_hook": "2 to 3 sentences grabbing attention immediately with drama, stakes, or mystery. No pleasantries.",
  "chapters": [
    {
      "chapter_index": 1,
      "title": "Title of Chapter 1",
      "purpose": "Narrative purpose of this chapter",
      "key_points": ["Key plot or engineering point 1", "Key point 2"],
      "target_words": 350
    }
  ]
}

Ensure the sum of all chapter `target_words` equals approximately {total_target_words} words.
Return ONLY the raw JSON string.
