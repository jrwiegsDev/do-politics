"""How long a member has served, one line per chamber, from their terms.

congress-legislators lists a partial term (filling a vacancy, reached by
appointment or special election) as its own row, and does not always say
so. A term much shorter than its chamber's is therefore counted as partial:
it sets when service began but is not counted as a term. When a member has
partial terms in a chamber, the count says "full" so it stays honest.

Service with a gap of a year or more in one chamber is shown as separate
stretches; shorter gaps, such as a Congress convening on January 15 instead
of January 3, are not breaks.
"""

from datetime import timedelta

from ingest.congress import ordinal

# A House term is two years (730 days) and a Senate term six (2,191). A full
# term can run a few days short, when a Congress convenes on January 5 or 7
# instead of the 3rd (in the file, full terms run 705 to 735 and 2,174 to
# 2,192 days); a row a month or more short is a partial term.
FULL_TERM = {"house": timedelta(days=700), "senate": timedelta(days=2160)}
# A gap at least this long between two terms in one chamber is a break.
BREAK = timedelta(days=365)
LABELS = {"house": "House", "senate": "Senate"}


def is_full(term: dict) -> bool:
    """Return whether a term is a whole term rather than part of one."""
    return term["ends_on"] - term["starts_on"] >= FULL_TERM[term["chamber"]]


def stretches(terms: list[dict]) -> list[list[dict]]:
    """Split one chamber's terms, oldest first, where a gap is a break."""
    groups: list[list[dict]] = []
    for term in terms:
        if groups and term["starts_on"] - groups[-1][-1]["ends_on"] < BREAK:
            groups[-1].append(term)
        else:
            groups.append([term])
    return groups


def span(group: list[dict]) -> str:
    """Return a stretch's years, such as '1983-1997'."""
    return f"{group[0]['starts_on'].year}–{group[-1]['ends_on'].year}"


def count_terms(full: int, any_partial: bool) -> str:
    """Return a number of terms in words, such as '7 terms' or '1 full term'."""
    noun = "term" if full == 1 else "terms"
    return f"{full} full {noun}" if any_partial else f"{full} {noun}"


def chamber_line(terms: list[dict], current: bool) -> str:
    """Return the line for one chamber, its terms oldest first."""
    label = LABELS[terms[0]["chamber"]]
    groups = stretches(terms)
    full = sum(is_full(term) for term in terms)
    any_partial = not all(is_full(term) for term in terms)

    if not current:
        years = ", ".join(span(group) for group in groups)
        if full == 0:
            return f"{label}: part of a term ({years})"
        return f"{label}: {count_terms(full, any_partial)} ({years})"

    since = f"since {groups[-1][0]['starts_on'].year}"
    earlier = ", ".join(span(group) for group in groups[:-1])
    when = f"{since}; also {earlier}" if earlier else since
    if not is_full(terms[-1]):
        return f"{label}: filling a vacant seat ({when})"
    word = "full term" if any_partial else "term"
    return f"{label}: {ordinal(full)} {word} ({when})"


def service_lines(terms: list[dict]) -> list[str]:
    """Return one line per chamber served in, the current chamber first.

    terms are the member's terms oldest first, as official_terms keeps them;
    the last is the one being served now.
    """
    if not terms:
        return []
    current = terms[-1]["chamber"]
    lines = [chamber_line([t for t in terms if t["chamber"] == current], True)]
    other = [t for t in terms if t["chamber"] != current]
    if other:
        lines.append(chamber_line(other, False))
    return lines
