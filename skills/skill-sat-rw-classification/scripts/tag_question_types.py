#!/usr/bin/env python3
"""
Rule-based SAT RW question type tagger + classification task generator.

Phase 1: Rule-based pre-classification using prompt, choices, and passage signals.
Phase 2: Generate classification_tasks.json for optional Codex/model review of low/medium confidence items.

Usage:
    python3 scripts/tag_question_types.py \
        --input questions.json \
        --output tagged_questions.json \
        --tasks classification_tasks.json \
        [--taxonomy config/sat_rw_taxonomy.json]

Outputs:
    tagged_questions.json: input JSON with classification field added
    classification_tasks.json: subset of questions needing review
"""

from __future__ import annotations

import json
import re
import sys
import argparse
from pathlib import Path
from typing import Any


SKILL_DIR = Path(__file__).resolve().parents[1]


def load_taxonomy(path: Path) -> dict:
    """Load the official SAT RW taxonomy."""
    return json.loads(path.read_text(encoding="utf-8"))


def load_questions(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "questions" in data:
        return data["questions"]
    raise ValueError("Expected JSON list or {questions: [...]}")


# ── Rule-based classifiers ──────────────────────────────────────────────

TRANSITION_WORDS = {
    "therefore", "however", "nevertheless", "nonetheless", "additionally",
    "for example", "for instance", "in contrast", "conversely", "similarly",
    "consequently", "moreover", "furthermore", "indeed", "in fact",
    "on the other hand", "as a result", "in addition", "accordingly",
    "thus", "hence", "instead", "specifically", "in particular",
    "alternatively", "besides", "likewise", "meanwhile", "otherwise",
    "rather", "still", "then", "thereafter", "accordingly",
}

PUNCTUATION_MARKERS = {";", ":", "—", "–", ",", "."}


def is_transition_choice(choice_text: str) -> bool:
    """Check if a choice text looks like a transition word/phrase."""
    ct = choice_text.strip().lower().rstrip(".,;:!")
    return ct in TRANSITION_WORDS


def is_punctuation_driven(choices: list[dict]) -> bool:
    """Check if choices differ primarily in punctuation."""
    texts = [c.get("text", "") for c in choices]
    # Extract punctuation patterns
    punct_counts = []
    for t in texts:
        punct = set(c for c in t if c in PUNCTUATION_MARKERS)
        punct_counts.append(punct)
    # If choices differ in punctuation sets, likely Boundaries
    if len(set(frozenset(p) for p in punct_counts)) > 1:
        return True
    # Also check for obvious sentence structure differences
    has_semicolon = any(";" in t for t in texts)
    has_colon = any(":" in t for t in texts)
    has_dash = any("—" in t or "–" in t for t in texts)
    return (has_semicolon or has_colon or has_dash)


def is_grammar_driven(choices: list[dict]) -> bool:
    """Check if choices differ in verb forms, agreement, pronoun case, etc."""
    texts = [c.get("text", "") for c in choices]
    # Count words per choice
    word_counts = [len(t.split()) for t in texts]
    similar_length = max(word_counts) - min(word_counts) <= 3
    # Check for verb form variation
    verb_variation = False
    pronouns = {"i", "me", "my", "mine", "myself", "we", "us", "our", "who", "whom"}
    for t in texts:
        words = set(t.lower().split())
        if words & pronouns:
            verb_variation = True
            break
    # Check for common verb patterns
    verb_patterns = [r"\b(?:is|are|was|were|has|have|had|be|been|being|do|does|did)\b",
                     r"\b\w+(?:ing|ed|s|es)\b"]
    verb_match_counts = []
    for t in texts:
        count = sum(1 for p in verb_patterns if re.search(p, t, re.IGNORECASE))
        verb_match_counts.append(count)
    verb_diff = max(verb_match_counts) - min(verb_match_counts) > 0 if verb_match_counts else False
    return (similar_length and verb_diff) or verb_variation


def has_student_notes(passage: str) -> bool:
    """Check if passage is a student notes format (Rhetorical Synthesis)."""
    return passage.strip().startswith("While researching a topic")


def has_text1_text2(passage: str) -> bool:
    """Check if passage contains Text 1 / Text 2 markers."""
    return bool(re.search(r"\bText\s+1\b", passage) and re.search(r"\bText\s+2\b", passage))


def rule_based_classify(question: dict[str, Any]) -> dict[str, Any]:
    """
    Rule-based pre-classification. Returns classification dict or None if uncertain.
    confidence: high = rule-based is sufficient, medium/low = needs review.
    """
    prompt = question.get("prompt", "")
    passage = question.get("passage", "")
    choices = question.get("choices", [])
    choice_texts = [c.get("text", "") for c in choices]
    qid = question.get("question_id", "unknown")

    evidence = {"stem_signal": "", "option_signal": "", "passage_signal": ""}

    # ── Words in Context ──
    if re.search(r"most logical and precise word or phrase", prompt, re.IGNORECASE):
        # Check if choices are transitions
        transition_count = sum(1 for ct in choice_texts if is_transition_choice(ct))
        if transition_count >= len(choice_texts) * 0.75:
            return {
                "domain": "Expression of Ideas",
                "skill": "Transitions",
                "subtype": "logical_connector",
                "confidence": "medium",
                "evidence": {
                    "stem_signal": "stem says 'word or phrase' but choices are overwhelmingly transition words",
                    "option_signal": f"{transition_count}/{len(choice_texts)} choices are logical connectors",
                    "passage_signal": "transition fitting context",
                },
                "classifier_sources": ["rule_based"],
                "model_agreement": "not_checked",
                "needs_review": True,
            }
        return {
            "domain": "Craft and Structure",
            "skill": "Words in Context",
            "subtype": "precise_word_choice",
            "confidence": "high",
            "evidence": {
                "stem_signal": "prompt: 'most logical and precise word or phrase'",
                "option_signal": "choices are vocabulary items, not connectors or punctuation",
                "passage_signal": "blank requires contextually precise vocabulary",
            },
            "classifier_sources": ["rule_based"],
            "model_agreement": "not_checked",
            "needs_review": False,
        }

    if re.search(r"what does the word\s+[‘'\"“].+?[’'\"”]\s+most nearly mean", prompt, re.IGNORECASE):
        return {
            "domain": "Craft and Structure",
            "skill": "Words in Context",
            "subtype": "word_meaning_in_context",
            "confidence": "high",
            "evidence": {
                "stem_signal": "prompt asks what a quoted word most nearly means in context",
                "option_signal": "choices are candidate contextual meanings",
                "passage_signal": "the tested word appears in the passage",
            },
            "classifier_sources": ["rule_based"],
            "model_agreement": "not_checked",
            "needs_review": False,
        }

    # ── Transitions ──
    if re.search(r"most logical transition", prompt, re.IGNORECASE):
        return {
            "domain": "Expression of Ideas",
            "skill": "Transitions",
            "subtype": "logical_connector",
            "confidence": "high",
            "evidence": {
                "stem_signal": "prompt: 'most logical transition'",
                "option_signal": "choices are logical connectors",
                "passage_signal": "blank requires logical connection between ideas",
            },
            "classifier_sources": ["rule_based"],
            "model_agreement": "not_checked",
            "needs_review": False,
        }

    # ── Standard English Conventions ──
    if re.search(r"conforms to the conventions of Standard English", prompt, re.IGNORECASE):
        punct_driven = is_punctuation_driven(choices)
        grammar_driven = is_grammar_driven(choices)

        if punct_driven and not grammar_driven:
            return {
                "domain": "Standard English Conventions",
                "skill": "Boundaries",
                "subtype": "punctuation_and_clause_structure",
                "confidence": "high",
                "evidence": {
                    "stem_signal": "prompt: 'conforms to the conventions of Standard English'",
                    "option_signal": "choices differ in punctuation / sentence boundary structure",
                    "passage_signal": "blank at clause boundary",
                },
                "classifier_sources": ["rule_based"],
                "model_agreement": "not_checked",
                "needs_review": False,
            }
        elif grammar_driven and not punct_driven:
            return {
                "domain": "Standard English Conventions",
                "skill": "Form, Structure, and Sense",
                "subtype": "verb_form_or_agreement",
                "confidence": "high",
                "evidence": {
                    "stem_signal": "prompt: 'conforms to the conventions of Standard English'",
                    "option_signal": "choices differ in verb form, agreement, or pronoun case",
                    "passage_signal": "blank requires grammatical form decision",
                },
                "classifier_sources": ["rule_based"],
                "model_agreement": "not_checked",
                "needs_review": False,
            }
        else:
            return {
                "domain": "Standard English Conventions",
                "skill": "Boundaries",
                "subtype": "mixed_convention",
                "confidence": "low",
                "evidence": {
                    "stem_signal": "prompt: 'conforms to the conventions of Standard English'",
                    "option_signal": "choices mix punctuation and grammar signals",
                    "passage_signal": "unclear from rule analysis",
                },
                "classifier_sources": ["rule_based"],
                "model_agreement": "not_checked",
                "needs_review": True,
            }

    # ── Rhetorical Synthesis without the conventional research-notes preface ──
    if re.search(r"uses information from the given sentences to", prompt, re.IGNORECASE):
        return {
            "domain": "Expression of Ideas",
            "skill": "Rhetorical Synthesis",
            "subtype": "given_sentences_synthesis",
            "confidence": "high",
            "evidence": {
                "stem_signal": "prompt asks the student to use given sentences for a stated rhetorical goal",
                "option_signal": "choices selectively synthesize the supplied facts",
                "passage_signal": "passage is a source-sentence list",
            },
            "classifier_sources": ["rule_based"],
            "model_agreement": "not_checked",
            "needs_review": False,
        }

    # ── Inferences ──
    if re.search(r"most logically completes the text", prompt, re.IGNORECASE):
        if has_student_notes(passage):
            return {
                "domain": "Expression of Ideas",
                "skill": "Rhetorical Synthesis",
                "subtype": "notes_synthesis",
                "confidence": "high",
                "evidence": {
                    "stem_signal": "prompt: 'most logically completes' with student notes",
                    "option_signal": "choices complete a synthesized statement",
                    "passage_signal": "passage starts with 'While researching a topic'",
                },
                "classifier_sources": ["rule_based"],
                "model_agreement": "not_checked",
                "needs_review": False,
            }
        return {
            "domain": "Information and Ideas",
            "skill": "Inferences",
            "subtype": "logical_completion",
            "confidence": "high",
            "evidence": {
                "stem_signal": "prompt: 'most logically completes the text' without student notes",
                "option_signal": "choices are logical conclusions drawn from text",
                "passage_signal": "text presents information requiring logical extension",
            },
            "classifier_sources": ["rule_based"],
            "model_agreement": "not_checked",
            "needs_review": False,
        }

    if re.search(r"most strongly (?:suggest|imply)|most reasonable inference", prompt, re.IGNORECASE):
        return {
            "domain": "Information and Ideas",
            "skill": "Inferences",
            "subtype": "supported_inference",
            "confidence": "high",
            "evidence": {
                "stem_signal": "prompt asks what the text most strongly suggests or implies",
                "option_signal": "choices are candidate inferences",
                "passage_signal": "answer must follow from stated evidence rather than be directly quoted",
            },
            "classifier_sources": ["rule_based"],
            "model_agreement": "not_checked",
            "needs_review": False,
        }

    # ── Rhetorical Synthesis ──
    if has_student_notes(passage) or re.search(r"student wants to", prompt, re.IGNORECASE):
        return {
            "domain": "Expression of Ideas",
            "skill": "Rhetorical Synthesis",
            "subtype": "notes_synthesis",
            "confidence": "high",
            "evidence": {
                "stem_signal": "prompt contains 'student wants to' or passage has student notes",
                "option_signal": "choices synthesize information from notes",
                "passage_signal": "passage begins with research notes",
            },
            "classifier_sources": ["rule_based"],
            "model_agreement": "not_checked",
            "needs_review": False,
        }

    # ── Command of Evidence: Quantitative ──
    if re.search(r"data from the (table|graph)|table to|graph|data in the (table|graph)", prompt, re.IGNORECASE):
        return {
            "domain": "Information and Ideas",
            "skill": "Command of Evidence: Quantitative",
            "subtype": "data_evidence",
            "confidence": "high",
            "evidence": {
                "stem_signal": "prompt references table/graph/data",
                "option_signal": "choices reference quantitative information",
                "passage_signal": "contains or references data visualizations",
            },
            "classifier_sources": ["rule_based"],
            "model_agreement": "not_checked",
            "needs_review": False,
        }

    # ── Command of Evidence: Textual ──
    if re.search(r"quotation from|most effectively illustrates|which finding|would most strongly support the|(?:information|evidence) in the text (?:best|most strongly) supports", prompt, re.IGNORECASE):
        return {
            "domain": "Information and Ideas",
            "skill": "Command of Evidence: Textual",
            "subtype": "textual_evidence",
            "confidence": "high",
            "evidence": {
                "stem_signal": "prompt asks for quotation/evidence/supporting finding",
                "option_signal": "choices are textual evidence options",
                "passage_signal": "passage presents a claim needing textual support",
            },
            "classifier_sources": ["rule_based"],
            "model_agreement": "not_checked",
            "needs_review": False,
        }

    # ── Text Structure and Purpose ──
    if re.search(r"function of the (underlined|reference)|purpose of the text|function of the underlined|overall structure of the text", prompt, re.IGNORECASE):
        return {
            "domain": "Craft and Structure",
            "skill": "Text Structure and Purpose",
            "subtype": "structural_function",
            "confidence": "high",
            "evidence": {
                "stem_signal": "prompt asks about the text's structure, purpose, or the function of a portion",
                "option_signal": "choices describe structural or rhetorical functions",
                "passage_signal": "passage has underlined portion or specific reference",
            },
            "classifier_sources": ["rule_based"],
            "model_agreement": "not_checked",
            "needs_review": False,
        }

    # ── Cross-Text Connections ──
    # This must precede broad single-text detail rules such as "based on the text".
    if has_text1_text2(passage) and re.search(r"Text 1|Text 2|both texts|the texts", prompt, re.IGNORECASE):
        return {
            "domain": "Craft and Structure",
            "skill": "Cross-Text Connections",
            "subtype": "cross_text_analysis",
            "confidence": "high",
            "evidence": {
                "stem_signal": "prompt references one or both paired texts",
                "option_signal": "choices describe a relationship or a cross-text question",
                "passage_signal": "passage contains Text 1 / Text 2 markers",
            },
            "classifier_sources": ["rule_based"],
            "model_agreement": "not_checked",
            "needs_review": False,
        }

    # ── Central Ideas and Details ──
    if re.search(r"main idea|main purpose|according to the text|based on the text\b", prompt, re.IGNORECASE):
        return {
            "domain": "Information and Ideas",
            "skill": "Central Ideas and Details",
            "subtype": "main_idea_or_detail",
            "confidence": "high",
            "evidence": {
                "stem_signal": f"prompt: '{prompt[:80]}...'",
                "option_signal": "choices describe content/claims from text",
                "passage_signal": "passage contains explicitly stated information",
            },
            "classifier_sources": ["rule_based"],
            "model_agreement": "not_checked",
            "needs_review": False,
        }

    # ── Cross-Text Connections ──
    if has_text1_text2(passage) and re.search(r"Text 1|Text 2|both texts", prompt, re.IGNORECASE):
        return {
            "domain": "Craft and Structure",
            "skill": "Cross-Text Connections",
            "subtype": "cross_text_analysis",
            "confidence": "high",
            "evidence": {
                "stem_signal": "prompt references both texts",
                "option_signal": "choices describe relationships between texts",
                "passage_signal": "passage contains Text 1 / Text 2 markers",
            },
            "classifier_sources": ["rule_based"],
            "model_agreement": "not_checked",
            "needs_review": False,
        }

    # ── Fallback: unclassifiable by rules ──
    return {
        "domain": None,
        "skill": None,
        "subtype": "unknown",
        "confidence": "low",
        "evidence": {
            "stem_signal": "no rule matched for this prompt pattern",
            "option_signal": "could not determine from choices",
            "passage_signal": "could not determine from passage",
        },
        "classifier_sources": ["rule_based"],
        "model_agreement": "not_checked",
        "needs_review": True,
    }


def tag_questions(
    questions: list[dict[str, Any]],
    taxonomy: dict,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Tag all questions with classification.
    Returns (tagged_questions, classification_tasks_for_review).
    """
    tagged = []
    tasks = []

    valid_skills = set()
    for domain in taxonomy.get("domains", []):
        for skill in domain.get("skills", []):
            valid_skills.add(skill["skill"])

    for question in questions:
        classification = rule_based_classify(question)

        # Validate domain/skill against taxonomy
        domain = classification.get("domain")
        skill = classification.get("skill")
        if domain and skill and skill not in valid_skills:
            classification["needs_review"] = True
            classification["confidence"] = "low"
            classification["warning"] = f"skill '{skill}' not in official taxonomy"

        question["classification"] = classification
        tagged.append(question)

        # Generate review task if needs review or low/medium confidence
        if classification.get("needs_review") or classification.get("confidence") in ("low", "medium"):
            tasks.append({
                "question_id": question.get("question_id", "unknown"),
                "prompt": question.get("prompt", ""),
                "passage": question.get("passage", ""),
                "choices": question.get("choices", []),
                "correct_answer": question.get("correct_answer", ""),
                "rule_based_guess": {
                    "domain": classification.get("domain"),
                    "skill": classification.get("skill"),
                },
            })

    return tagged, tasks


def main():
    parser = argparse.ArgumentParser(description="SAT RW Question Type Tagger")
    parser.add_argument("--input", required=True, type=Path, help="input questions JSON")
    parser.add_argument("--output", required=True, type=Path, help="tagged questions JSON")
    parser.add_argument("--tasks", required=True, type=Path, help="classification review tasks")
    parser.add_argument("--taxonomy", type=Path, default=None, help="taxonomy JSON path")
    args = parser.parse_args()

    taxonomy_path = args.taxonomy or SKILL_DIR / "config" / "sat_rw_taxonomy.json"
    if not taxonomy_path.exists():
        print(f"[WARN] Taxonomy not found at {taxonomy_path}, using built-in rules only.", file=sys.stderr)
        taxonomy = {"domains": []}
    else:
        taxonomy = load_taxonomy(taxonomy_path)

    if not args.input.exists():
        print(f"[ERROR] Input not found: {args.input}", file=sys.stderr)
        return 1

    questions = load_questions(args.input)
    print(f"Loaded {len(questions)} questions from {args.input}", file=sys.stderr)

    tagged, tasks = tag_questions(questions, taxonomy)

    # Stats
    by_confidence = {}
    by_skill = {}
    needs_review = 0
    for q in tagged:
        c = q.get("classification", {})
        conf = c.get("confidence", "unknown")
        by_confidence[conf] = by_confidence.get(conf, 0) + 1
        skill = c.get("skill", "unknown")
        by_skill[skill] = by_skill.get(skill, 0) + 1
        if c.get("needs_review"):
            needs_review += 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(tagged, indent=2, ensure_ascii=False), encoding="utf-8")
    args.tasks.parent.mkdir(parents=True, exist_ok=True)
    args.tasks.write_text(json.dumps(tasks, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n── Tagging Results ──", file=sys.stderr)
    print(f"  Tagged: {len(tagged)} questions", file=sys.stderr)
    print(f"  Review tasks generated: {len(tasks)}", file=sys.stderr)
    print(f"  Needs review: {needs_review}", file=sys.stderr)
    print(f"  Confidence: {json.dumps(by_confidence)}", file=sys.stderr)
    print(f"  Skills: {json.dumps(by_skill, ensure_ascii=False)}", file=sys.stderr)
    print(f"Output: {args.output}", file=sys.stderr)
    print(f"Tasks:  {args.tasks}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    exit(main())
