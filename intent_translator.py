#!/usr/bin/env python3
"""
mAxIvis Intent Translator — v1.0
=================================
Translates what you MEAN, not word-for-word.

Synthesized from Jeremy's Drive builds:
  - "Translator " (Stealth God blueprint)  -> voice-pipeline hooks (STT/TTS wiring points)
  - "Hardening PhoneticBridge"              -> dual-provider fallback, structured JSON,
                                               dialect config, validation + correction loop
  - "mAxIvis Translated charm"              -> (backend notes; nothing kept)

Pipeline:
  1. INTENT DETECT  — classify speech act + tone from the English input
  2. MEANING MAP    — translate the intent, not the words (idioms, affection, jokes)
  3. DIALECT FIT    — adapt to target dialect (default: Latin American / seseo)
  4. VALIDATE       — confidence check + correction loop on low confidence

Modes:
  - Offline heuristic engine (no keys needed, solid for common phrases)
  - LLM pipeline: Anthropic Claude primary -> Gemini 2.0 Flash fallback
    (set ANTHROPIC_API_KEY and/or GEMINI_API_KEY)

Usage:
  python3 intent_translator.py "tell her I miss her like crazy"
  python3 intent_translator.py --dialect castilian "what's up"
  echo "good night beautiful" | python3 intent_translator.py
"""

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple

VERSION = "1.0"
MIN_CONFIDENCE = 0.80  # from PhoneticBridge hardening spec


# ---------------------------------------------------------------------------
# 1. INTENT DETECTION
# ---------------------------------------------------------------------------

INTENT_PATTERNS: List[Tuple[str, List[str]]] = [
    ("affection", [r"\bi (miss|love|adore)\b", r"\bthinking of you\b", r"\bmy (love|baby|girl|queen)\b",
                   r"\bte (amo|quiero|extraño)\b", r"\bkiss\b", r"\bhug\b", r"\bcrazy about you\b"]),
    ("greeting", [r"^(hey|hi|hello|hola|good (morning|afternoon|evening|night))\b", r"\bwhat'?s up\b",
                  r"\bhow'?s it going\b", r"\bhow are you\b"]),
    ("farewell", [r"\bgood ?night\b", r"\bbye\b", r"\bsee you (later|tomorrow|soon)\b",
                  r"\btalk (later|tomorrow|soon)\b", r"\btake care\b", r"\bsweet dreams\b"]),
    ("joke_tease", [r"\blol\b", r"\bhaha\b", r"\bthat'?s (funny|hilarious)\b", r"\bjust (kidding|joking|teasing)\b",
                    r"\bstop it\b", r"\byou'?re (crazy|silly|trouble)\b"]),
    ("compliment", [r"\byou look\b", r"\bbeautiful\b", r"\bgorgeous\b", r"\bamazing\b", r"\bso (smart|funny|sweet)\b",
                    r"\bproud of you\b"]),
    ("apology", [r"\bi'?m sorry\b", r"\bforgive me\b", r"\bmy (bad|fault|mistake)\b", r"\bapologize\b"]),
    ("gratitude", [r"\bthank you\b", r"\bthanks\b", r"\bappreciate\b", r"\bgrateful\b"]),
    ("request", [r"\bcan you\b", r"\bcould you\b", r"\bplease\b", r"\bwould you mind\b", r"\bdo me a favor\b"]),
    ("question", [r"\?\s*$", r"^(what|when|where|why|how|who|which|do|does|did|are|is|can)\b"]),
    ("reassurance", [r"\bdon'?t worry\b", r"\bit'?s (ok|okay|alright|fine)\b", r"\btrust me\b",
                     r"\beverything('s| is) (gonna|going to) be\b", r"\bi'?m here\b"]),
    ("excitement", [r"\b(awesome|incredible|unbelievable)\b", r"!\s*$", r"\bguess what\b", r"\byou won'?t believe\b"]),
    ("instruction", [r"\b(open|close|turn|press|click|send|call|text|look at)\b", r"^(do|don'?t|stop|wait|listen|look)\b"]),
    ("small_talk", [r"\bhow was your day\b", r"\bwhat'?s new\b", r"\bnice (weather|day)\b", r"\bboring\b"]),
]

TONE_PATTERNS: List[Tuple[str, List[str]]] = [
    ("playful", [r"\blol\b", r"\bhaha\b", r"\bteas", r"😂", r"😉"]),
    ("tender", [r"\blove\b", r"\bmiss\b", r"\bbeautiful\b", r"❤", r"💕"]),
    ("urgent", [r"!\s*$", r"\bquick\b", r"\bhurry\b", r"\bright now\b", r"\basap\b"]),
    ("formal", [r"\bdear\b", r"\bsincerely\b", r"\bregards\b"]),
    ("casual", [r"\bgonna\b", r"\bwanna\b", r"\by'?all\b", r"\bdude\b"]),
]


def detect_intent(text: str) -> Tuple[str, str, float]:
    """Return (intent, tone, confidence). Falls back to statement/neutral."""
    low = text.lower()
    intent, tone = "statement", "neutral"
    hits = 0
    for name, patterns in INTENT_PATTERNS:
        for p in patterns:
            if re.search(p, low):
                intent, hits = name, hits + 1
                break
        if hits:
            break
    for name, patterns in TONE_PATTERNS:
        for p in patterns:
            if re.search(p, low):
                tone = name
                break
        if tone != "neutral":
            break
    confidence = 0.95 if hits else 0.55
    return intent, tone, confidence


# ---------------------------------------------------------------------------
# 2. MEANING MAP — intent-first EN -> ES (Latin American default)
# ---------------------------------------------------------------------------
# Each entry: (intent_match, english_pattern, latam_spanish, castilian_spanish|None,
#              literal_gloss, note)

PHRASE_BANK = [
    # affection
    ("affection", r"i miss you( so much| like crazy| terribly)?", "te extraño muchísimo", None,
     "I miss you very much", "escalated: 'muchísimo' carries the weight"),
    ("affection", r"i love you( so much)?", "te amo", None,
     "I love you", "'te amo' for romance; 'te quiero' for warm/family"),
    ("affection", r"thinking of you", "pensando en ti", None,
     "thinking of you", None),
    ("affection", r"good ?night( beautiful| my love| baby)?", "buenas noches, hermosa", None,
     "good night, beautiful", "kept the endearment — dropping it would lose the intent"),
    ("affection", r"sweet dreams", "dulces sueños", None, "sweet dreams", None),
    # greeting
    ("greeting", r"what'?s up", "¿qué onda?", "¿qué pasa?",
     "what's up", "LatAm casual; NOT literal 'qué hay arriba'"),
    ("greeting", r"how'?s it going", "¿cómo va todo?", None, "how's it going", None),
    ("greeting", r"how are you\??", "¿cómo estás?", None, "how are you", None),
    ("greeting", r"good morning( beautiful)?", "buenos días, hermosa", None, "good morning, beautiful", None),
    # farewell
    ("farewell", r"talk (to you )?(later|tomorrow|soon)", "hablamos luego", None,
     "we'll talk later", "idiomatic > literal"),
    ("farewell", r"take care", "cuídate", None, "take care (of yourself)", None),
    # joke / tease
    ("joke_tease", r"just kidding", "es broma", None, "it's a joke", None),
    ("joke_tease", r"you'?re crazy", "estás loca", None, "you're crazy",
     "playful register; feminine form kept (for Ivis)"),
    ("joke_tease", r"stop it", "ya párale", None, "stop it already", "LatAm playful scold"),
    # compliment
    ("compliment", r"you look beautiful", "te ves hermosa", None, "you look beautiful", None),
    ("compliment", r"i'?m (so )?proud of you", "estoy tan orgulloso de ti", None,
     "I am so proud of you", None),
    # apology
    ("apology", r"i'?m (so |really )?sorry", "lo siento mucho", None, "I'm very sorry", None),
    ("apology", r"my bad", "fue mi culpa", None, "it was my fault", "NOT literal 'mi mal'"),
    # gratitude
    ("gratitude", r"thank you( so much)?", "muchas gracias", None, "thank you very much", None),
    # reassurance
    ("reassurance", r"don'?t worry", "no te preocupes", None, "don't worry", None),
    ("reassurance", r"everything('s| is) (gonna|going to) be (ok|okay|alright|fine)",
     "todo va a estar bien", None, "everything is going to be fine", None),
    ("reassurance", r"i'?m here( for you)?", "aquí estoy", None, "here I am", "short, warm > literal"),
    # excitement
    ("excitement", r"guess what", "adivina qué", None, "guess what", None),
    # request
    ("request", r"can you (please )?call me", "¿me puedes llamar, por favor?", None,
     "can you call me, please", None),
]


def offline_translate(text: str, intent: str, dialect: str) -> Optional[Dict]:
    low = text.strip().lower().rstrip(".")

    def build(entry):
        _, _, latam, castilian, gloss, note = entry
        translation = castilian if (dialect == "castilian" and castilian) else latam
        return {
            "translation": translation,
            "literal_gloss": gloss,
            "confidence": 0.93,
            "note": note or "phrase-bank match",
            "engine": "offline-phrase-bank",
        }

    # Pass 1: same-intent matches (most precise)
    for entry in PHRASE_BANK:
        if entry[0] == intent and re.search(entry[1], low):
            return build(entry)
    # Pass 2: any-intent matches (intent classifier isn't perfect)
    for entry in PHRASE_BANK:
        if re.search(entry[1], low):
            return build(entry)
    return None


# ---------------------------------------------------------------------------
# 3. LLM PIPELINE — Claude primary, Gemini fallback (from PhoneticBridge)
# ---------------------------------------------------------------------------

SYSTEM_INSTRUCTION = (
    "You are an intent-first English-to-Spanish translator. Translate what the speaker "
    "MEANS, not word-for-word. Preserve the speech act (affection, joke, apology...), "
    "the tone (tender, playful, urgent), and the register (casual vs formal). "
    "Adapt idioms to natural {dialect} Spanish — never calque English idioms literally. "
    'Output ONLY strict JSON: '
    '{{"translation": "...", "literal_gloss": "word-for-word English gloss", '
    '"confidence": 0.0-1.0, "note": "one short line on the key intent decision"}}.'
)

FEW_SHOT = (
    'Input: "what\'s up" | intent: greeting | tone: casual\n'
    'Output: {"translation": "¿qué onda?", "literal_gloss": "what wave", '
    '"confidence": 0.97, "note": "LatAm casual greeting; literal would be nonsense"}\n'
    'Input: "I miss you like crazy" | intent: affection | tone: tender\n'
    'Output: {"translation": "te extraño muchísimo", "literal_gloss": "I miss you very much", '
    '"confidence": 0.95, "note": "escalated intensity to match \'like crazy\'"}\n'
    'Input: "my bad" | intent: apology | tone: casual\n'
    'Output: {"translation": "fue mi culpa", "literal_gloss": "it was my fault", '
    '"confidence": 0.96, "note": "idiom mapped to natural apology, not literal"}\n'
)


async def llm_translate(text: str, intent: str, tone: str, dialect: str) -> Dict:
    """Claude primary -> Gemini fallback. Raises RuntimeError if both fail."""
    dialect_label = "Latin American Spanish (seseo)" if dialect == "latam" else "Castilian Spanish"
    system = SYSTEM_INSTRUCTION.format(dialect=dialect_label)
    prompt = (f"{FEW_SHOT}\nInput: \"{text}\" | intent: {intent} | tone: {tone}\nOutput:")

    # --- Primary: Anthropic Claude ---
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic()
            resp = await client.messages.create(
                model="claude-3-5-sonnet-latest", max_tokens=300,
                system=system, messages=[{"role": "user", "content": prompt}])
            data = json.loads(resp.content[0].text)
            data["engine"] = "claude-primary"
            return data
        except Exception:
            pass  # fall through to Gemini

    # --- Fallback: Gemini 2.0 Flash ---
    if os.environ.get("GEMINI_API_KEY"):
        try:
            from google import genai
            from google.genai import types
            client = genai.Client()
            resp = await client.aio.models.generate_content(
                model="gemini-2.0-flash", contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system, response_mime_type="application/json",
                    temperature=0.2))
            data = json.loads(resp.text)
            data["engine"] = "gemini-fallback"
            return data
        except Exception as e:
            raise RuntimeError(f"Translation pipeline failed: {e}")

    raise RuntimeError("No LLM provider configured (set ANTHROPIC_API_KEY or GEMINI_API_KEY)")


# ---------------------------------------------------------------------------
# 4. VALIDATE + CORRECTION LOOP
# ---------------------------------------------------------------------------

@dataclass
class TranslationResult:
    input: str
    intent: str
    tone: str
    dialect: str
    translation: str
    literal_gloss: str
    confidence: float
    note: str
    engine: str
    corrected: bool = False


async def translate(text: str, dialect: str = "latam") -> TranslationResult:
    text = text.strip()
    if not text:
        raise ValueError("empty input")
    intent, tone, intent_conf = detect_intent(text)

    result: Optional[Dict] = offline_translate(text, intent, dialect)
    if result is None:
        try:
            result = await llm_translate(text, intent, tone, dialect)
        except RuntimeError:
            # Graceful degradation: honest literal fallback, flagged low confidence
            result = {
                "translation": f"[untranslated — no phrase-bank match and no API key] {text}",
                "literal_gloss": text,
                "confidence": 0.30,
                "note": "no confident mapping; set ANTHROPIC_API_KEY or GEMINI_API_KEY for full coverage",
                "engine": "degraded-fallback",
            }

    confidence = min(float(result.get("confidence", 0.5)), intent_conf if result.get("engine", "").startswith("offline") else 1.0)

    # Correction loop: low confidence -> retry via LLM once with explicit constraint
    corrected = False
    if confidence < MIN_CONFIDENCE and result.get("engine", "").startswith("offline"):
        try:
            retry = await llm_translate(text, intent, tone, dialect)
            retry["note"] = (retry.get("note", "") + " [correction-loop retry]").strip()
            result, corrected = retry, True
            confidence = float(retry.get("confidence", 0.5))
        except RuntimeError:
            pass

    return TranslationResult(
        input=text, intent=intent, tone=tone, dialect=dialect,
        translation=result["translation"],
        literal_gloss=result.get("literal_gloss", ""),
        confidence=round(confidence, 2),
        note=result.get("note", ""),
        engine=result.get("engine", "unknown"),
        corrected=corrected,
    )


# ---------------------------------------------------------------------------
# 5. VOICE-PIPELINE HOOKS (from Stealth God blueprint — wire your STT/TTS here)
# ---------------------------------------------------------------------------

def speak_to_her(spanish_text: str):
    """Hook: send TTS audio to Cable B (her ears). Wire eleven-flash here."""
    print(f"[VOICE -> HER / Cable B]: {spanish_text}")


def whisper_to_jeremy(note: str):
    """Hook: private alert to Jeremy's headset only. Wire Gemini Prime here."""
    print(f"[WHISPER -> JEREMY / headset]: {note}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="mAxIvis Intent Translator")
    ap.add_argument("text", nargs="?", help="English text to translate (or pipe via stdin)")
    ap.add_argument("--dialect", choices=["latam", "castilian"], default="latam")
    ap.add_argument("--speak", action="store_true", help="route translation to voice hook")
    ap.add_argument("--json", action="store_true", help="raw JSON output")
    args = ap.parse_args()

    text = args.text or (sys.stdin.read().strip() if not sys.stdin.isatty() else "")
    if not text:
        ap.error("give me some English text to translate")

    res = asyncio.run(translate(text, dialect=args.dialect))

    if args.json:
        print(json.dumps(asdict(res), ensure_ascii=False, indent=2))
        return

    print(f"\n  {res.input}")
    print(f"  intent: {res.intent}  ·  tone: {res.tone}  ·  dialect: {res.dialect}")
    print(f"  →  {res.translation}")
    if res.literal_gloss and res.literal_gloss != res.translation:
        print(f"     (literally: \"{res.literal_gloss}\")")
    print(f"  confidence: {res.confidence}  ·  engine: {res.engine}"
          + ("  ·  corrected ✓" if res.corrected else ""))
    if res.note:
        print(f"  note: {res.note}")
    if res.confidence < MIN_CONFIDENCE:
        whisper_to_jeremy(f"Low confidence ({res.confidence}) on: {res.input[:60]}")
    if args.speak:
        speak_to_her(res.translation)
    print()


if __name__ == "__main__":
    main()
