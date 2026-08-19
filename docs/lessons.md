# Lessons

Every rule in this toolkit exists because the obvious alternative was tried
first and produced a wrong answer that looked right. These are the failure
modes, so you can recognise them in your own beat instead of rediscovering them.

## Collection

**A source that failed and a source with nothing to say look identical.**
This is the single most expensive failure, because it turns into "quiet week" in
a report someone acts on. Every collector returns an explicit state —
`ok / thin / error / baseline / rate_limited / skipped_offline` — and the state
table is printed in every report. A run where two sources errored is not a quiet
week; it is an incomplete week.

**HTTP 200 does not mean you got what you asked for.** A retired feed URL can
answer 200 with an HTML page. The parse then fails somewhere deep with a
"mismatched tag" message that tells the operator nothing. Detect the HTML and
say so: *"URL returned an HTML page, not a feed"*. Encountered while writing the
example config in this repo, on a well-run central bank site.

**Filters on general-interest outlets must match the title only.** A first pass
that matched title + summary with words like "payment" or a local currency name
kept essentially every article: those words appear in almost every body text of
a general newspaper. The filter was doing nothing and looked like it was
working. Narrow the word list and match headlines.

**Never overwrite a success with a failure.** When a query is rate-limited,
cache the results you already have, mark the remainder `deferred`, and resume
next run. Writing an empty array over yesterday's good data destroys the only
copy, and the loss is invisible.

**Strip tracking parameters, not query strings.** Some channels put the record
id in the query — app-store review ids are the classic case. Cutting everything
after `?` silently collapses every review of one app into a single item.

**A JS-rendered page is not an empty page.** Report it as `thin` with the
character count and let the operator decide, rather than recording "no change".

## Keywords

**Most hand-written keyword tables are largely dead weight.** A measured
example: of 130 terms, 68% matched nothing at all across a full corpus. The
causes were consistent — internal jargon that never appears in public text, and
terms written in a different language than the corpus. Check hit rates; a term
that never fires is not harmless, it is a false sense of coverage.

**Two tiers, not one.** Terms that carry their own attribution (product names,
programme names) can score alone. Generic terms (`refund`, `licence`, `api`)
cannot: without knowing whose refund it is, the match means nothing. Gate them
behind a brand word.

**Brand words themselves must score zero.** They appear in a large fraction of
everything you collect, so they have no discriminating power. Their job is to be
a gate, not a signal.

**Keep the keyword table and the source queries in sync.** If they drift apart,
almost everything scores zero, and the run looks like a quiet week rather than a
misconfiguration. When most events come back unassigned, suspect this first.

**Never auto-append discovered keywords.** The keyword table is the definition
of the beat. Auto-appending changes that definition silently, and later nobody
can tell which terms a human chose. Produce a ranked candidate list with
evidence and let a person pick.

**Discovery needs a shape whitelist, not a stopword blacklist.** Lowercasing and
removing stopwords produced a top-50 of ordinary English. Terms that matter have
shapes: all-caps acronyms, proper nouns, camel-cased product names, hyphenated
compounds, domain bigrams. A lone lowercase common word is almost never a
keyword. (Camel case needs its own pattern — a word-boundary rule cannot see the
break inside `TradeDepot`.)

**The gap scan is a separate pass on purpose.** Terms your own organisation
discusses but your sources do not return appear in exactly one corpus, so any
"must span N sources" rule filters them out — and those are precisely the blind
spots worth fixing. Run them as two scans, not one.

## Scoring

**Relevance must dominate.** Source and author calibrate how much to trust an
item; only relevance says whether it is about your beat. A perfectly sourced
piece about someone else's market is still noise.

**Normalise relevance at a high percentile, not the maximum.** One freak item
otherwise compresses everything else into a narrow band at the bottom.

**Engagement is only comparable within a channel.** View counts on a social
platform and thumbs-up on an app store are different units. Percentile-rank
within the tier; never compare raw numbers across channels.

**Damp categories, do not delete them.** Support replies, job posts and promo
content are individually low-value and collectively numerous — left alone, they
flood the top of the list. Multipliers keep them present and searchable while
stopping them from crowding out the two or three that matter.

**Tier-scoped rules must beat general ones regardless of file order.** A
regulator notice containing "we are sorry" should not be damped to a fifth of
its score by a support-reply rule that happens to be written above it. Otherwise
the config carries an invisible ordering constraint.

**Assign audiences by relevance, not by total score.** Ranking on the total
sends every high-authority item with no keyword match to every audience at once,
and two teams end up reading the same report. Zero relevance means no audience
owns it; it stays in the ledger, collected and searchable, addressed to nobody.

**Deduplicate twice.** Once on the canonical URL, once on author + body text.
The second pass catches the same person posting the same thing under two ids —
invisible to URL dedupe, and it is what makes one voice look like a trend.

**Reprints are not corroboration.** Four outlets running one press release is
one event with four pieces of evidence. Give a bounded bonus for corroboration
across *different* source tiers and nothing for repetition within one.

**Mark cross-run continuity.** Without it, a story that has been running for a
month re-enters the top of the report every week as if it just broke.

## Output and acceptance

**Check mechanically what you keep getting wrong by eye.** The rules worth
automating are the ones that recur: stale items promoted as news, recruitment
posts ranked as product signal, an entire channel missing, two audiences with
near-identical top lists. Each is trivial to check in code and expensive to
catch by reading.

**Two perspectives that produce the same top list are one perspective.** Check
the overlap between what each audience is actually *assigned*, not the global
ranking — the global ranking is identical by construction for everything with no
keyword match.

**Say what you truncated.** When a report shows the top N of a tier, print how
many were withheld and where the rest live. A silently capped list reads as
complete coverage.

**Empty output must be explainable.** "0 candidates" can mean nothing new
happened or that the corpus is too small to clear the thresholds. Print the
thresholds and the counts they rejected, every time.
