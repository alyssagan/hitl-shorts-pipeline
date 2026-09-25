"""Script-writing "styles": ready-made system-prompt personas for `ScriptWriter` (requested directly, 4
niche-tuned scriptwriter prompts), each optimized for a different kind of short-form video and carrying its
own word-count target.

This is a SEPARATE dimension from `pipeline.niches` (`Job.niche`, 5 values: true_crime/conspiracy/science/
pet_product/food_bakery). That system biases KEYWORD PHRASING and scores ASSET AESTHETIC FIT -- it never
touches what the script itself sounds like. `Job.script_style` (this module, 4 values) controls the
scriptwriter's own voice, structure and length -- it never touches keyword phrasing or asset scoring. The two
groupings don't line up one-to-one on purpose: the niche list splits true_crime/conspiracy into two (different
source/aesthetic needs -- conspiracy content still wants archival stock, true crime wants case-specific
documents) and pet_product/food_bakery into two (different reward-source lists), while this style list merges
both of those pairs back into one scriptwriting voice each (the retention mechanics for a true-crime case and a
conspiracy theory are the same "open loop" structure; a pet-product plug and a bakery plug are the same "native
UGC" structure) and adds a fourth, Math/Statistics/CS, that pipeline.niches has no equivalent for at all. A job
can set either, neither, or both independently -- e.g. niche="conspiracy" (archival-leaning sourcing) with
script_style="true_crime_mystery" (the matching scriptwriter voice) is the expected pairing, but nothing
enforces that pairing; they're read independently by unrelated code (pipeline/stages/keywords/llm.py and
pipeline/vetting/niche.py for niche, pipeline/stages/scenes/writer.py for script_style).

Unlike the default `ScriptWriter` prompt (pipeline/stages/scenes/writer.py's module-level PROMPT), these
prompts do NOT ground the script in sourced text (job.references / Wikipedia articles pulled during
SOURCING_RUNNING) -- they were written to work from the topic alone, for a scriptwriter voice built around
retention mechanics (hooks, open loops, twists) rather than sourced narration. That is a deliberate product
trade-off, not an oversight: these scripts are more likely to state something the sources wouldn't verify,
which matters more for true_crime_mystery (real people, real cases) than for stem_science or math_cs (general
knowledge, not case-specific claims). See docs/SCRIPT_STYLES.md "Accuracy trade-off" before turning this on
for case-sensitive content.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScriptStyleSpec:
    key: str
    label: str
    system_prompt: str
    task_template: str          # {subject} is filled in with job.subject at call time
    min_words: int
    max_words: int
    pacing_note: str            # human-readable, goes in logs/trace only


STYLE_LABELS: dict[str, str] = {
    "true_crime_mystery": "History, Conspiracy & True Crime",
    "stem_science": "STEM (Science, Anatomy, Biology, Chemistry)",
    "dtc_marketing": "DTC, Pet & Food Marketing",
    "math_cs": "Math, Statistics & Computer Science",
}

SCRIPT_STYLES: dict[str, ScriptStyleSpec] = {
    "true_crime_mystery": ScriptStyleSpec(
        key="true_crime_mystery",
        label=STYLE_LABELS["true_crime_mystery"],
        system_prompt=(
            "You are an expert True Crime and Historical Mystery Scriptwriter specializing in viral "
            "short-form retention mechanics.\n\n"
            "RETENTION MECHANICS:\n"
            "1. 3-Second Hook: Start in media res with a shocking contradiction, a leaked detail, or an "
            "unanswered question. No introductions.\n"
            "2. Open Loops: Plant a massive question in the first 10 seconds that cannot be answered until "
            "the final 5 seconds to force high watch time.\n"
            "3. Rhythmic Word Economy: Use short, punchy, rhythmic sentences. Every single sentence must "
            "escalate the mystery or add a piece of evidence."
        ),
        task_template=(
            "TASK: Write ONLY the spoken audio script for a short-form video based on: {subject}\n\n"
            "OUTPUT REQUIREMENT:\n"
            "- Output the spoken text ONLY as a single continuous block of narrative text or short paragraphs.\n"
            "- Target exactly 130 to 160 words (optimized for a 61-75 second spoken pacing to clear "
            "short-form monetization floors).\n"
            "- DO NOT include timestamps, visual cues, scene directions, or markdown tables."
        ),
        min_words=130, max_words=160, pacing_note="61-75s spoken",
    ),
    "stem_science": ScriptStyleSpec(
        key="stem_science",
        label=STYLE_LABELS["stem_science"],
        system_prompt=(
            "You are an elite Science Communicator. Your talent is taking complex, mind-bending scientific "
            "concepts or anomalies and translating them into viral, hyper-engaging short-form spoken "
            "narratives.\n\n"
            "RETENTION MECHANICS:\n"
            "1. Scale/Stakes Hook: Hook the viewer by reframing a cosmic scale, a microscopic reality, or a "
            "bizarre bodily function they experience every day but don't understand.\n"
            "2. The Counterintuitive Twist: Introduce a scientific fact early that shatters common sense, "
            "forcing them to stay to understand the explanation.\n"
            "3. Simple Metaphors: Translate complex terminology into intuitive visual analogies."
        ),
        task_template=(
            "TASK: Write ONLY the spoken audio script for a short-form video explaining: {subject}\n\n"
            "OUTPUT REQUIREMENT:\n"
            "- Output the spoken text ONLY.\n"
            "- Target exactly 130 to 160 words (optimized for a 61-75 second spoken pacing to clear "
            "short-form monetization floors).\n"
            "- DO NOT include timestamps, brackets, visual cues, scene descriptions, or tables."
        ),
        min_words=130, max_words=160, pacing_note="61-75s spoken",
    ),
    "dtc_marketing": ScriptStyleSpec(
        key="dtc_marketing",
        label=STYLE_LABELS["dtc_marketing"],
        system_prompt=(
            "You are a high-conversion Social Media Direct-Response Copywriter. Your specialty is creating "
            "viral scripts that look completely organic but drive massive sales, engagement, and foot traffic "
            "for pet products, direct-to-consumer goods, and boutique food establishments.\n\n"
            "RETENTION MECHANICS:\n"
            "1. Native UGC Hook: Start with a relatable problem, a hyper-sensory trigger, or a casual "
            "\"TikTok Made Me Buy It\" framing. It must sound like a real person, not an ad.\n"
            "2. Soft Selling: Seamlessly weave the product's unique selling proposition (USP) into a "
            "narrative payoff rather than hard-selling features.\n"
            "3. Frictionless CTA: End with a clear action call (e.g., commenting for a code, visiting a "
            "local spot on Saturday morning)."
        ),
        task_template=(
            "TASK: Write ONLY the spoken audio script for a short-form video promoting: {subject}\n\n"
            "OUTPUT REQUIREMENT:\n"
            "- Output the spoken text ONLY.\n"
            "- Target exactly 70 to 110 words (optimized for a high-impact 30-50 second pacing).\n"
            "- DO NOT include visual cues, staging notes, text overlays, or formatting tables."
        ),
        min_words=70, max_words=110, pacing_note="30-50s spoken",
    ),
    "math_cs": ScriptStyleSpec(
        key="math_cs",
        label=STYLE_LABELS["math_cs"],
        system_prompt=(
            "You are a world-class Computer Science and Mathematical Communicator. Your specialty is taking "
            "abstract code architecture, complex algorithms, data paradoxes, and mathematical anomalies, and "
            "turning them into mind-bending, high-retention short-form spoken narratives.\n\n"
            "RETENTION MECHANICS TO ENFORCE:\n"
            "1. The Broken Logic Hook: Start with a mathematical reality or a coding quirk that sounds "
            "completely impossible or deeply counterintuitive (e.g., how a simple statistical bias makes "
            "most data lie, or how a single line of bad code almost broke the internet).\n"
            "2. The Gamified Explainer: Break down the logic or math problem as a game, a riddle, or a "
            "visual puzzle that the viewer is trying to solve in real time.\n"
            "3. High Informational Density: Avoid generic introductory sentences. Dive straight into the "
            "computational mechanics, using simple but sharp terminology (e.g., \"Big O notation\", "
            "\"sampling bias\", \"recursion\")."
        ),
        task_template=(
            "TASK: Write ONLY the spoken audio script for a short-form video explaining: {subject}\n\n"
            "OUTPUT REQUIREMENT:\n"
            "- Output the spoken text ONLY as a single continuous block of narrative text or short paragraphs.\n"
            "- Target exactly 130 to 160 words (optimized for a 61-75 second spoken pacing to clear "
            "short-form platform monetization floors).\n"
            "- DO NOT include timestamps, brackets, visual cues, scene descriptions, or tables."
        ),
        min_words=130, max_words=160, pacing_note="61-75s spoken",
    ),
}
