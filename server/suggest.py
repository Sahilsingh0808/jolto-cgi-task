"""Vision-to-inputs: Gemini looks at the product photo and proposes form fields.

Returns a typed `SuggestedInputs` object (pydantic) consumed by the
`POST /api/suggest-inputs` endpoint and rendered into the form by app.js.

The system prompt is deliberately opinionated — it encodes the house-style
brief template the pipeline has been trained around. High temperature lets
the user "shuffle" by re-clicking and get meaningfully different takes.
"""

from __future__ import annotations

from google import genai
from google.genai import types
from pydantic import BaseModel, Field


class SuggestedInputs(BaseModel):
    product_name: str = Field(description="Short, marketable name. 3-6 words.")
    product_material: str = Field(
        description="Honest technical description: metal purity, stones, construction."
    )
    product_notes: str = Field(
        description="Distinctive visual features, comma-separated. 2-4 phrases."
    )
    brief: str = Field(description="Creative brief in the Jolto house-style markdown template.")


SYSTEM_PROMPT = """You are a senior commercial director and jewellery merchandiser writing
creative direction for a CGI jewellery advertising pipeline. You will be
shown one product photograph; produce four fields that will drive the pipeline.

Output rules:

1. product_name: 3-6 words. Evocative but grounded — never a fantasy name.
2. product_material: accurate technical description — metal purity,
   stones, construction. Use 22k / 18k / platinum / rhodium terminology
   based on visible cues (Indian traditional warm-gold → 22k; modern
   Western fine → 18k or platinum; costume → rhodium-plated).
3. product_notes: 2-4 comma-separated phrases covering distinctive
   features the downstream prompter should preserve (pattern, stone
   placement, finish, unique construction).
4. brief: markdown, exactly this template:

# <Two-word evocative title>

A 15-second hero film for the <product_name>. No people, no hands, no
faces. The piece is the protagonist.

## Feeling
<One paragraph. Mood, palette, lighting, 1-2 reference points (e.g.
Chopard vault, Cartier Clash macro, Tanishq archive, a Vogue editorial).
Be specific to what THIS piece needs.>

## Shots should reveal in this order
1. <One sentence, usually extreme macro on the most distinctive detail.>
2. <One sentence, usually pendant / hero element focus or rotation.>
3. <One sentence, usually the full-piece reveal.>

## Do not
- <3-5 constraints. Always include: no people / no on-screen text / no
  watermarks. Add piece-specific ones: if coloured gemstones exist,
  forbid recolouring them. If the metal is white/platinum, forbid a
  warm-gold cast. If it's gold, forbid a silver cast.>

## Deliverable
Three shots, slow crossfades, 16:9 hero film at 15 seconds.

Style guidelines:
- Match the language register to the piece's tier. High-jewellery gets
  "vault", "hush", "reverence". Heritage Indian gets "archival",
  "handwork", "heritage". Daily-wear gets "quiet", "elegant", "intimate".
- Never put text on-screen. Never put people in the frame.
- Preserve the jewellery type faithfully: don't turn earrings into a
  ring, don't merge a set into a single piece.
"""


def suggest_inputs(image_bytes: bytes, mime: str, api_key: str) -> SuggestedInputs:
    """Call Gemini with the product image and return a typed suggestion."""

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime),
            "Write creative direction inputs for this product.",
        ],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=SuggestedInputs,
            temperature=1.1,
        ),
    )
    return SuggestedInputs.model_validate_json(response.text or "{}")
