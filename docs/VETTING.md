# How the machine judges risk (`rules-v1`)

**The vetting step never approves and never deletes.** It reads each pulled asset's metadata, fires plain rules,
and records for each one: the rule id, a severity, a plain-English message, and the exact evidence that triggered it.
A person still decides every asset at Gate 2.

An asset's overall risk is the **highest severity** among the rules that fired (`info` doesn't raise it).
**"low" does not mean safe.** It means this checker found nothing to warn about. It cannot see the picture.

Approving a **high** risk asset requires a written note, saved in the decision log next to the flags you were shown.
Assets under 480 px (`LOW_RES`) can't be approved at all, because the renderer would skip them.

| Rule | Severity | Fires when | Why it matters |
|---|---|---|---|
| `LIC_UNKNOWN` | high | No license text found | No proof you may use it |
| `LIC_NC` | high | Non-commercial license | Can't be used in monetized video |
| `LIC_ND` | high | No-derivatives license | Cropping/zooming/cutting may count as modifying |
| `LIC_SA` | medium | Share-alike | Adaptations may need the same license |
| `LIC_GFDL` | medium | GNU Free Documentation License | Awkward extra obligations for video |
| `LIC_BY` | low | Attribution required | Credit is generated in `CREDITS.md`; publish it |
| `LIC_PEXELS` | low | Pexels License | Free commercial use; link back requested; people can't be shown in a bad light |
| `LIC_PD` | info | Public domain / CC0 | No restrictions |
| `ATTR_MISSING` | medium | License needs credit but no author found | Can't comply |
| `PEOPLE_MINOR` | high | Title/description mentions a child or minor | Needs extra care and consent |
| `PEOPLE_IDENTIFIABLE` | medium | Mentions a person | Avoid implied endorsement or bad-light use |
| `CONTENT_GRAPHIC` | high | Mentions graphic content | Look at the image before approving |
| `CONTENT_SENSITIVE` | medium | Mentions violence, crime, drugs, etc. | Check it suits your topic |
| `RESTRICTIONS` | medium | Commons lists legal restrictions | Trademark, personality rights, etc. |
| `TRADEMARK` | medium | Title looks like a logo or brand | Copyright licenses don't cover trademarks |
| `LOW_RES` | high (unusable) | Short side < 480 px | Renderer skips it |
| `RES_UNKNOWN` | info | Size unknown | Resolution check skipped |
| `NO_SOURCE_URL` | medium | No URL recorded | Can't trace or credit |
| `DUPLICATE` | low | Same hash as an earlier asset | Keep one |

Rules are keyword and metadata based, so they produce false positives and miss things. That is by design:
they exist to make sure a human looks, not to replace the human. Add or change rules in
`pipeline/vetting/rules.py`; the version string is written to every decision so old decisions stay explainable.
This is a risk aid, not legal advice.
