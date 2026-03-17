# Velura BrandKit

Portable brand reference for workspace migration. This file consolidates the core brand identity currently spread across `config/brand.json`, setup docs, prompt files, and product data.

## Brand Snapshot

- Brand name: Velura
- Tagline: Skin-first beauty.
- Category: Premium cosmetics / skincare
- Positioning: Clean, premium, understated beauty with a sensorial feel
- Core voice: Confident, understated, sensorial

## Brand Essence

Velura should feel polished, modern, and quietly luxurious. The brand is not loud, clinical, or overly salesy. Messaging should emphasize texture, ritual, confidence, softness, glow, and elevated everyday use.

Key emotional territory:

- Effortless beauty
- Refined self-care
- Clean, modern femininity
- Quiet confidence
- Sensory product appeal

## Messaging Principles

- Keep copy concise and elegant.
- Favor soft-sell language over aggressive urgency.
- Make benefits feel aspirational but believable.
- Lead with appearance, texture, feel, ritual, and confidence.
- Avoid sounding clinical, gimmicky, or hard-close direct response.

Preferred CTA style:

- Soft-sell
- "Link in bio"
- "You need this"
- "Choose Velura"
- "Discover"
- "Add to Ritual"
- "Your Ritual"

## Voice And Tone

Use language that feels:

- Confident
- Understated
- Sensorial
- Modern
- Premium

Good copy cues:

- "melts into skin"
- "featherweight"
- "softens"
- "brightens"
- "luminous"
- "satin finish"
- "weightless"
- "clean beauty"
- "morning ritual"
- "night routine"

Avoid:

- Overhyped slang
- Harsh fear-mongering in brand copy
- Medical framing
- Clinical promises
- Cheap or discount-heavy language

## Visual Identity

### Color Palette

Working palette labels are included for portability and can be renamed later if needed.

| Role | Hex |
|------|-----|
| Canvas / Warm Ivory | `#FAF7F2` |
| Primary Accent / Taupe | `#B8A99A` |
| Primary Text / Espresso | `#2E2420` |
| Secondary Accent / Dusty Rose | `#C9958A` |
| Secondary Accent / Sage | `#B7C4B1` |
| Secondary Accent / Soft Gold | `#E8D5B7` |

Usage guidance:

- Use `#FAF7F2` for light backgrounds and soft negative space.
- Use `#2E2420` for primary text, logos, and contrast.
- Use `#B8A99A` as the main brand accent.
- Use `#C9958A`, `#B7C4B1`, and `#E8D5B7` sparingly for highlights, UI accents, packaging, or campaign variations.

### Typography

- Display font: `Cormorant Garamond`
- Body font: `DM Sans`
- Overlay font: `DM Sans`

Usage guidance:

- Use `Cormorant Garamond` for headlines, hero sections, and luxury editorial moments.
- Use `DM Sans` for body copy, product UI, captions, overlays, and utility text.
- For video overlays, use `DM Sans Medium` in white with a soft drop shadow.

### Text Overlay Spec

- Font: `DM Sans Medium`
- Color: `#FFFFFF`
- Effect: Soft drop shadow
- Highlight accent: `#B8A99A`

## Creative Direction

### Overall Look

Velura creative should feel:

- Clean
- Premium
- Soft-lit
- Neutral
- Beauty-forward
- Editorial but accessible

Preferred environments:

- Luxury bathroom counter
- Vanity
- Bright neutral interior
- Soft-lit skincare setting
- Natural, clean outdoor background for creator-style content when needed

### Video Defaults

- Aspect ratio: `9:16`
- Duration: `15 seconds`
- Resolution: `1080x1920` for assembly workflows

### Avatar / UGC Direction

- Diverse skin tones
- Age range: 20-30
- "Effortless clean girl" aesthetic
- Chest-up talking head framing
- Warm, casual, authentic female TTS voice
- Natural pace at `1.0x`

### Product-Centric AI Creative

In existing workflow prompts, the brand uses:

- Anthropomorphic product characters
- Pixar-style facial features
- Luxury bathroom counter scenes
- Minimal motion and subtle animation
- One product per scene
- One hard cut between Scene 1 and Scene 2

Packaging/marking note:

- The product is typically shown with the brand name `velura` in brown writing.

## Compliance And Guardrails

### AI Disclosure

Persistent disclosure text:

`AI-generated creative for demonstration purposes only`

Guidance:

- Place bottom center in video
- Approximate opacity: `50%`
- Keep visible for full video duration

### FTC / Claims Guidance

Do not use medical or treatment claims. Approved softeners used in the workflow include:

- "appears to"
- "feels like"
- "helps skin look"
- "designed to"

Avoid:

- Medical claims
- Implied treatment outcomes
- Before/after framing that suggests medical transformation

## Product Universe

### Core Products In Current Automation

- Brow Pomade
- Premium Lipstick
- Eye Cream
- Collagen Moisturizer

### Broader Catalog Categories

- Skincare
- Lips
- Eyes
- Face

### Common Product Signals Across Catalog

- Vegan
- Cruelty-free
- Paraben-free
- Clean beauty
- Texture-led benefits
- Elevated daily ritual framing

## Brand Copy Examples

Examples of phrasing already aligned to the brand:

- "Deep hydration that melts into skin."
- "Featherweight formula."
- "Full satin coverage in one stroke."
- "Softens fine lines and depuffs the under-eye area."
- "Lightweight enough for morning, rich enough for night."

## Portable Brand Prompt

Use the following summary when initializing a new workspace, AI assistant, or content system:

> Velura is a premium, skin-first beauty brand with a clean, understated, sensorial identity. The brand voice is confident, modern, and softly luxurious rather than loud or clinical. Use a warm neutral palette anchored by `#FAF7F2`, `#B8A99A`, and `#2E2420`, with `Cormorant Garamond` for display and `DM Sans` for body and overlays. Messaging should feel elegant, concise, and soft-sell, focusing on texture, ritual, glow, ease, and confidence. Creative should look clean, premium, and softly lit, often in vanity or luxury bathroom settings. For AI-generated video, maintain the disclosure "AI-generated creative for demonstration purposes only" and avoid medical or non-compliant performance claims.

## Source Notes

This BrandKit was assembled from the current project's brand config, creative templates, workflow prompts, Shopify setup guidance, and product catalog files so it can serve as a single migration-friendly reference.
