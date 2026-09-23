"""Explainable risk vetting for pulled assets ("rules-v1").

Nothing here approves or rejects an asset. Every asset still goes to a human.
The vetting step only (a) flags what looks risky and (b) says exactly why,
using plain rules you can read below. Each flag records:

    rule      a stable id (docs/VETTING.md lists them all)
    severity  info | low | medium | high
    message   what the concern is, in plain English
    evidence  the exact field/text that triggered the rule

The asset's overall risk is the HIGHEST severity among the flags that fired
(info flags don't raise it). "Low" means no rule fired above low. It does NOT
mean safe. It only means this checker found nothing to warn about.
"""
from __future__ import annotations

import re
from typing import Callable

from ..core.models import Asset, Flag, RightsStatus, ScoreContribution, Vetting

VERSION = "rules-v1"
MIN_SIDE = 480                 # the renderer (MoneyPrinterTurbo) skips anything smaller
_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3}

CHILD_WORDS = ["child", "children", "kid", "kids", "baby", "babies", "toddler", "infant", "boy", "girl", "teen", "teenager", "minor"]
PEOPLE_WORDS = ["man", "woman", "men", "women", "person", "people", "portrait", "face", "couple", "family", "worker", "crowd", "student", "player"]
SENSITIVE_HIGH = ["gore", "corpse", "dead body", "autopsy", "nude", "naked", "suicide", "decapitat", "massacre", "mutilat", "beheading"]
SENSITIVE_MEDIUM = ["blood", "murder", "execution", "weapon", "gun", "knife", "violence", "victim", "crime scene", "drug", "abuse", "war", "death", "killing", "prison", "torture"]


def _words(text: str, words: list[str]) -> list[str]:
    found = []
    for w in words:
        if re.search(rf"\b{re.escape(w)}", text, re.I):
            found.append(w)
    return found


def _text(a: Asset) -> str:
    cats = a.meta.get("extmetadata", {}).get("Categories", "") if isinstance(a.meta, dict) else ""
    return " | ".join(x for x in (a.title, a.description, cats) if x)


def _license_kind(a: Asset) -> str:
    s = f"{a.license} {a.license_url}".lower()
    if not s.strip():
        return "unknown"
    for stock in ("pexels", "pixabay", "unsplash"):
        if stock in s:
            return stock
    if re.search(r"public domain|\bpd\b|cc0|cc-zero|publicdomain|/zero/|pdm|no known copyright", s):
        return "pd"
    if re.search(r"[-_/ ]nc\b|noncommercial|non-commercial|by-nc", s):
        return "nc"
    if re.search(r"[-_/ ]nd\b|noderiv|no-deriv|by-nd", s):
        return "nd"
    if re.search(r"[-_/ ]sa\b|sharealike|share-alike|by-sa", s):
        return "sa"
    if "gfdl" in s or "free documentation" in s:
        return "gfdl"
    if re.search(r"cc[- ]by|attribution", s):
        return "by"
    return "unknown"


# --- individual rules: each returns a list of Flags (possibly empty) -----------------

def r_license(a: Asset, _all: list[Asset]) -> list[Flag]:
    kind = _license_kind(a)
    lic = a.license or "(none)"
    if kind == "unknown":
        return [Flag(rule="LIC_UNKNOWN", severity="high",
                     message="No usable license information was found, so there is no proof you're allowed to use this in a video.",
                     evidence=f"license field: {lic}")]
    if kind == "nc":
        return [Flag(rule="LIC_NC", severity="high",
                     message="Non-commercial license. It can't be used in a monetized video.", evidence=f"license: {lic}")]
    if kind == "nd":
        return [Flag(rule="LIC_ND", severity="high",
                     message="No-derivatives license. Cropping, zooming or cutting it into a video may count as modifying the work.",
                     evidence=f"license: {lic}")]
    if kind == "sa":
        return [Flag(rule="LIC_SA", severity="medium",
                     message="Share-alike license. If the video counts as an adaptation, it may have to be released under the same license. Get advice before monetizing.",
                     evidence=f"license: {lic}")]
    if kind == "gfdl":
        return [Flag(rule="LIC_GFDL", severity="medium",
                     message="GNU Free Documentation License has extra obligations (license text, notices) that are awkward for video.",
                     evidence=f"license: {lic}")]
    if kind == "by":
        return [Flag(rule="LIC_BY", severity="low",
                     message="Attribution required. A credit line is generated for you in CREDITS.md, so it must be published with the video.",
                     evidence=f"license: {lic}")]
    if kind == "pexels":
        return [Flag(rule="LIC_PEXELS", severity="low",
                     message="Pexels License: free for commercial use, but Pexels' API terms ask for a visible link back, and identifiable people can't be shown in a bad light.",
                     evidence=f"license: {lic}")]
    if kind == "pixabay":
        return [Flag(rule="LIC_PIXABAY", severity="low",
                     message="Pixabay Content License: free for commercial use, no credit required. You may not sell unaltered copies or imply that a person or brand endorses your video.",
                     evidence=f"license: {lic}")]
    if kind == "unsplash":
        return [Flag(rule="LIC_UNSPLASH", severity="low",
                     message="Unsplash License: free for commercial use, credit appreciated. You may not sell unaltered copies. Unsplash's API terms about downloading are unchecked for this tool (see KNOWN_LIMITATIONS).",
                     evidence=f"license: {lic}")]
    return [Flag(rule="LIC_PD", severity="info",
                 message="Public domain / CC0. No license restrictions on use.", evidence=f"license: {lic}")]


def classify_rights_status(license_text: str, license_url: str) -> tuple[RightsStatus, str]:
    """What the SOURCE'S OWN license text suggests about reuse rights -- evidence, never a legal
    conclusion (#8: "no known restrictions" is not a definitive public-domain determination, and this
    function does not pretend otherwise; it only records what was found and where). Returns
    (RightsStatus, evidence string). Deliberately coarser than `_license_kind()` above (which drives risk
    severity): CC0 and public-domain-by-law/expiry are kept apart per #8, but a restrictive license (NC/ND)
    is folded into "unresolved" here specifically because it does NOT resolve whether this project can
    reuse the item -- the full, exact license text is still preserved unmodified in Asset.license/
    license_url regardless of what this coarser field says; nothing here ever replaces reading that."""
    s = f"{license_text} {license_url}".lower()
    if not s.strip():
        return "unresolved", ""
    if re.search(r"cc0|cc-zero|/zero/", s):
        return "cc0", f"license field: {license_text or license_url}"
    if re.search(r"[-_/ ]nc\b|noncommercial|non-commercial|by-nc|[-_/ ]nd\b|noderiv|no-deriv|by-nd", s):
        return "unresolved", f"restrictive license, not clearly reusable here: {license_text or license_url}"
    if re.search(r"public domain|\bpd\b|publicdomain|pdm|no known copyright|no known restrictions|government work", s):
        return "public_domain", f"license field: {license_text or license_url}"
    if re.search(r"cc[- ]by|by-sa|attribution|gfdl|pexels license|pixabay content license|unsplash license", s):
        return "open_license", f"license field: {license_text or license_url}"
    if re.search(r"\bpaid\b|licensed footage|stock license|royalty", s):
        return "paid_license", f"license field: {license_text or license_url}"
    return "unresolved", f"license field present but not recognized: {license_text or license_url}"


PLATFORM_HOSTS = ("youtube.com", "youtu.be", "tiktok.com", "instagram.com", "facebook.com", "fb.watch", "twitter.com",
                  "x.com", "vimeo.com", "dailymotion.com", "twitch.tv", "reddit.com", "snapchat.com", "pinterest.com")


def r_platform(a: Asset, _all: list[Asset]) -> list[Flag]:
    from urllib.parse import urlparse
    host = urlparse(a.page_url or a.source_url).netloc.lower().removeprefix("www.")
    if any(host == h or host.endswith("." + h) for h in PLATFORM_HOSTS):
        return [Flag(rule="PLATFORM_SOURCE", severity="high",
                     message="Downloaded from a social or video platform. The uploader may not own the rights (re-uploads are common), and the platform's terms may forbid downloading. Approving needs your written note.",
                     evidence=f"host: {host}")]
    return []


def r_source_notes(a: Asset, _all: list[Asset]) -> list[Flag]:
    if a.source == "nasa":
        return [Flag(rule="NASA_NOTE", severity="low",
                     message="NASA media is generally not copyrighted, but it can contain third-party material, identifiable people, or NASA's protected logos. Don't imply NASA endorses your video.",
                     evidence="source: NASA Image and Video Library")]
    if a.source == "urls" and not a.license:
        return [Flag(rule="URL_LIST_NOTE", severity="info",
                     message="This came from your own URL list, so the pipeline has no license for it. Its URL and uploader are recorded so you can trace and credit it.",
                     evidence=f"requested: {a.source_url}")]
    return []


def r_attribution(a: Asset, _all: list[Asset]) -> list[Flag]:
    if _license_kind(a) in ("by", "sa", "gfdl") and not (a.author or a.attribution):
        return [Flag(rule="ATTR_MISSING", severity="medium",
                     message="This license requires crediting the author, but no author or credit text was found.",
                     evidence="author and attribution are both empty")]
    return []


def r_people(a: Asset, _all: list[Asset]) -> list[Flag]:
    t = _text(a)
    kids = _words(t, CHILD_WORDS)
    if kids:
        return [Flag(rule="PEOPLE_MINOR", severity="high",
                     message="The text suggests a child or minor is shown. Identifiable minors need extra care, and stock licenses don't cover consent for sensitive topics.",
                     evidence=f"matched: {', '.join(kids)} in \"{t[:120]}\"")]
    ppl = _words(t, PEOPLE_WORDS)
    if ppl:
        return [Flag(rule="PEOPLE_IDENTIFIABLE", severity="medium",
                     message="An identifiable person may be shown. Most licenses don't allow using someone's image in a way that implies endorsement or shows them in a bad light. Check your topic.",
                     evidence=f"matched: {', '.join(ppl)} in \"{t[:120]}\"")]
    return []


def r_sensitive(a: Asset, _all: list[Asset]) -> list[Flag]:
    t = _text(a)
    high = _words(t, SENSITIVE_HIGH)
    if high:
        return [Flag(rule="CONTENT_GRAPHIC", severity="high",
                     message="The title or description mentions graphic or sensitive content. Look at the image itself before approving.",
                     evidence=f"matched: {', '.join(high)} in \"{t[:120]}\"")]
    med = _words(t, SENSITIVE_MEDIUM)
    if med:
        return [Flag(rule="CONTENT_SENSITIVE", severity="medium",
                     message="The title or description mentions a sensitive topic. It may be fine, but check that the image suits your video.",
                     evidence=f"matched: {', '.join(med)} in \"{t[:120]}\"")]
    return []


def r_restrictions(a: Asset, _all: list[Asset]) -> list[Flag]:
    md = a.meta.get("extmetadata", {}) if isinstance(a.meta, dict) else {}
    r = (md.get("Restrictions") or "").strip()
    if r:
        return [Flag(rule="RESTRICTIONS", severity="medium",
                     message="Wikimedia Commons lists extra legal restrictions on this file (for example trademark, personality rights or insignia).",
                     evidence=f"Restrictions: {r}")]
    return []


def r_logo(a: Asset, _all: list[Asset]) -> list[Flag]:
    if re.search(r"\blogo\b|\btrademark\b|\bbrand\b", a.title, re.I):
        return [Flag(rule="TRADEMARK", severity="medium",
                     message="Looks like a logo or brand. Free copyright licenses don't cover trademarks.", evidence=f"title: {a.title}")]
    return []


def r_resolution(a: Asset, _all: list[Asset]) -> list[Flag]:
    if a.width and a.height:
        if min(a.width, a.height) < MIN_SIDE:
            return [Flag(rule="LOW_RES", severity="high",
                         message=f"Smaller than {MIN_SIDE}px on the short side, so the renderer will skip it. It can't be used.",
                         evidence=f"{a.width}x{a.height}")]
        return []
    return [Flag(rule="RES_UNKNOWN", severity="info", message="Image size is unknown, so the resolution check was skipped.", evidence="width/height missing")]


def r_traceable(a: Asset, _all: list[Asset]) -> list[Flag]:
    if not (a.page_url or a.source_url.startswith("http")):
        return [Flag(rule="NO_SOURCE_URL", severity="medium",
                     message="No web page or URL was recorded for where this came from, so it can't be traced or credited later.",
                     evidence=f"source_url: {a.source_url or '(empty)'}")]
    return []


def r_duplicate(a: Asset, all_assets: list[Asset]) -> list[Flag]:
    for other in all_assets:
        if other.id == a.id:
            return []                    # only compare with earlier assets
        if a.sha256 and other.sha256 == a.sha256:
            return [Flag(rule="DUPLICATE", severity="low",
                         message="Identical to an earlier asset. Keep just one.", evidence=f"same file as {other.id} ({other.source})")]
    return []


RULES: list[tuple[str, str, Callable[[Asset, list[Asset]], list[Flag]]]] = [
    ("LIC_*", "License type decides commercial-use risk (unknown/NC/ND high, SA medium, BY/Pexels low, PD info)", r_license),
    ("PLATFORM_SOURCE", "Downloaded from a social/video platform (uploader may not own the rights)", r_platform),
    ("*_NOTE", "Source-specific notes (NASA third-party material, URL-list files)", r_source_notes),
    ("ATTR_MISSING", "License needs a credit but no author/credit text exists", r_attribution),
    ("PEOPLE_*", "Words in the title/description suggest identifiable people or minors", r_people),
    ("CONTENT_*", "Words in the title/description suggest graphic or sensitive content", r_sensitive),
    ("RESTRICTIONS", "Commons lists legal restrictions (trademark, personality rights, ...)", r_restrictions),
    ("TRADEMARK", "Title looks like a logo or brand", r_logo),
    ("LOW_RES", "Short side under 480px; the renderer would skip it", r_resolution),
    ("NO_SOURCE_URL", "No URL recorded for where the file came from", r_traceable),
    ("DUPLICATE", "Same file hash as an earlier asset", r_duplicate),
]


# --- relevance to the topic (needs the approved keywords, so it isn't in RULES) ----------

RELEVANCE_MIN = 0.5            # default threshold; a job can override it with option `min_relevance`

# Version of the plain word-match relevance fallback (docs/SCORING_CHANGELOG.md) -- bump this and add a changelog
# entry any time `relevance()` below changes. This is a DIFFERENT thing from VERSION above (the risk-rules engine):
# this one only versions the relevance-scoring formula used when neither TF-IDF nor the LLM produced a score.
KEYWORD_MATCH_VERSION = "keyword-match-v1"
KEYWORD_MATCH_FORMULA = ("score = max over approved keywords of (keyword words found in the asset's title/description/tags/page-URL words) "
                   "/ (keyword words after removing filler words). 0.0 to 1.0. See docs/SCORING.md.")
STOPWORDS = {"a", "an", "the", "of", "in", "on", "at", "and", "or", "to", "for", "with", "by", "from", "is", "are", "was",
             "true", "crime", "facts", "fact", "about", "story", "history", "video", "footage", "photo", "photos",
             "image", "images", "picture", "pictures", "stock", "how", "why", "what", "documentary"}


def tokens(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        out.add(w)
        if len(w) > 3 and w.endswith("s"):
            out.add(w[:-1])              # cheap plural handling: "letters" also counts as "letter"
    return out


def clean_term(term: str) -> str:
    """Typed keywords sometimes carry quotes or stray punctuation. Drop them."""
    return re.sub(r"\s+", " ", term.strip().strip("\"'`\u2018\u2019\u201c\u201d ")).strip()


def asset_text(a: Asset) -> str:
    slug = re.sub(r"[-_/.]+", " ", (a.page_url or "").split("//")[-1])
    tags = a.meta.get("tags", "") if isinstance(a.meta, dict) else ""
    if isinstance(tags, list):
        tags = " ".join(str(t) for t in tags)
    return " | ".join(x for x in (_text(a), str(tags), slug) if x)


def relevance(a: Asset, topic_terms: list[str]) -> tuple[float | None, str]:
    """Best score over the keywords: the share of that keyword's meaningful words found in the asset's own text."""
    have = tokens(asset_text(a))
    best, why = None, ""
    for term in topic_terms:
        want = [w for w in re.findall(r"[a-z0-9]+", term.lower()) if w not in STOPWORDS]
        if not want:
            continue
        hit = [w for w in want if w in have]
        score = len(hit) / len(want)
        if best is None or score > best:
            best, why = score, f"matched {len(hit)} of {len(want)} words of '{term}'" + (f" ({', '.join(hit)})" if hit else "")
    return best, why


def vet_asset(a: Asset, all_assets: list[Asset], topic_terms: list[str] | None = None, min_relevance: float = RELEVANCE_MIN,
              llm_scores: dict[str, tuple[float, str]] | None = None,
              tfidf_scores_batch: dict[str, tuple[float, str]] | None = None,
              llm_version: str = "llm-semantic-v1", tfidf_version: str = "tfidf-v1") -> Vetting:
    """`llm_version`/`tfidf_version`: the caller's current version identifier for whichever scorer produced
    `llm_scores`/`tfidf_scores_batch` (docs/SCORING_CHANGELOG.md) -- recorded on the asset's `scoring_method`/
    `method_version` so a later comparison across algorithm versions is possible (docs/EVALUATION.md).
    Orchestrator._apply_vetting() passes the real, live values (pipeline/vetting/tfidf_relevance.VERSION,
    pipeline/vetting/llm_relevance.VERSION); the defaults here exist only for direct/unit-test callers that don't
    care and must be kept in sync with those two modules (tests/test_relevance_versioning.py guards this -- it
    fails loudly if they drift apart)."""
    flags: list[Flag] = []
    for _rid, _desc, fn in RULES:
        flags.extend(fn(a, all_assets))
    prev = a.vetting                              # this asset's vetting from BEFORE this call (a prior round, if any)

    tfidf_entry = tfidf_scores_batch.get(a.id) if tfidf_scores_batch else None
    llm_entry = llm_scores.get(a.id) if llm_scores else None

    contributions: list[ScoreContribution] = []
    if tfidf_entry is not None:
        t_score, t_why = tfidf_entry
        contributions.append(ScoreContribution(scoring_method="tfidf", method_version=tfidf_version, score=t_score, why=t_why))
    if llm_entry is not None:
        l_score, l_why = llm_entry
        contributions.append(ScoreContribution(scoring_method="llm-semantic", method_version=llm_version, score=l_score, why=l_why))

    rel: float | None
    scoring_method = ""
    fallback_note = ""
    if llm_entry is not None:
        rel, rel_why = llm_entry
        scoring_method = "llm-semantic"
        contributions[-1].used_for_decision = True
        note = ("TF-IDF scored this asset first; because it was borderline (within the configured band of the "
                "threshold, docs/SCORING.md), the LLM re-scored it and its judgment is authoritative by design."
                if tfidf_entry is not None else
                "Only the LLM scored this asset this round (no TF-IDF batch score was available to compare against).")
    elif prev is not None and prev.scoring_method == "llm-semantic":
        # Already had a good semantic score from an earlier round (e.g. before a "search again") and wasn't
        # re-sent to the LLM this time -- keep it (version, contributions and all) rather than silently
        # downgrading to a cruder score or losing which version actually produced it. If TF-IDF was freshly
        # recomputed this round anyway (it's cheap and runs on every pending asset), record that as an extra,
        # non-authoritative contribution rather than discarding it -- it just didn't change the outcome.
        rel, rel_why, scoring_method = prev.relevance, prev.relevance_why, prev.scoring_method
        note = prev.contribution_note + " (kept from an earlier round; not re-scored this round.)"
        contributions = list(prev.contributions) + contributions
    elif tfidf_entry is not None:
        # The deterministic local baseline (docs/SCORING.md): used for every asset the LLM wasn't asked to
        # double-check this round, either because it's off/unkeyed or because the score wasn't borderline.
        rel, rel_why = tfidf_entry
        scoring_method = "tfidf"
        contributions[-1].used_for_decision = True
        note = "Only the TF-IDF baseline scored this asset this round (not borderline enough to ask the LLM, or no LLM is configured)."
    else:
        rel, rel_why = relevance(a, topic_terms or [])
        if rel is not None:
            scoring_method = "keyword-match"
            fallback_note = ("algorithmic score unavailable for this item" if tfidf_scores_batch is not None
                              else "LLM unavailable/failed for this item" if llm_scores is not None else "")
            contributions.append(ScoreContribution(scoring_method="keyword-match", method_version=KEYWORD_MATCH_VERSION,
                                                    score=rel, why=rel_why, used_for_decision=True))
            note = (f"TF-IDF/LLM produced no score for this asset ({fallback_note}); fell back to plain keyword-word overlap."
                    if fallback_note else
                    "Relevance scoring did not run at all for this asset (no keywords/topic given, or TF-IDF wasn't computed for it); "
                    "fell back to plain keyword-word overlap.")
        else:
            note = "No approved keywords to score against -- this asset was never scored."
    method_version = {"tfidf": tfidf_version, "llm-semantic": llm_version, "keyword-match": KEYWORD_MATCH_VERSION, "": ""}[scoring_method]
    decision: str = "relevant" if rel is not None and rel >= min_relevance else "not_relevant" if rel is not None else ""
    if rel is not None and rel < min_relevance:
        flags.append(Flag(rule="RELEVANCE_LOW", severity="low",
                          message=f"Relevance score {round(rel * 100)}% is under the {round(min_relevance * 100)}% threshold, so it is probably not about your topic.",
                          evidence=rel_why or "no keyword words found"))
    top = max((_ORDER[f.severity] for f in flags), default=0)
    risk = "high" if top >= 3 else "medium" if top == 2 else "low"
    usable = not any(f.rule == "LOW_RES" for f in flags)
    fired = [f for f in flags if f.severity != "info"]
    if fired:
        because = "; ".join(f"{f.rule} ({f.severity})" for f in sorted(fired, key=lambda f: -_ORDER[f.severity]))
        summary = f"Risk {risk.upper()} because: {because}. Overall risk is the highest severity of any rule that fired."
    else:
        summary = ("Risk LOW: no rule fired above 'info'. This is not an approval. It only means the checker "
                   "found nothing to warn about. A human still decides.")
    return Vetting(risk=risk, flags=flags, method=VERSION, summary=summary, usable=usable, relevance=rel, relevance_why=rel_why,
                   scoring_method=scoring_method, method_version=method_version, scoring_fallback_note=fallback_note,
                   relevance_threshold=min_relevance, relevance_decision=decision, contribution_note=note, contributions=contributions)


def vet_all(assets: list[Asset], topic_terms: list[str] | None = None, min_relevance: float = RELEVANCE_MIN,
            llm_scores: dict[str, tuple[float, str]] | None = None,
            tfidf_scores_batch: dict[str, tuple[float, str]] | None = None,
            llm_version: str = "llm-semantic-v1", tfidf_version: str = "tfidf-v1") -> None:
    for a in assets:
        a.vetting = vet_asset(a, assets, topic_terms, min_relevance, llm_scores, tfidf_scores_batch, llm_version, tfidf_version)
        # Evidence-based rights classification (#8), recomputed every round so a license correction upstream
        # is picked up -- but NEVER once a human has actually signed off (rights_reviewer set): that is a real
        # determination and outranks a re-run of this heuristic. Selection (Asset.status) never touches this
        # at all -- only vetting does, and only up to the point a human takes it over.
        if not a.rights_reviewer:
            status, evidence = classify_rights_status(a.license, a.license_url)
            a.rights_status = status
            a.rights_evidence = evidence
