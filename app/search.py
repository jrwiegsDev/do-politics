"""Read what someone typed into a bill search box: the member's votes or Bill Search."""

from ingest.bills import bill_for

# The longest search the box accepts, in characters.
MAX_LENGTH = 200

# The words of a search as a Postgres text-search query, for SQL that has the
# words as %(words)s. websearch_to_tsquery reads them as a search box would
# (a minus sign leaves a word out, quotes keep a phrase) and trims each word
# to its stem, so 'veterans' finds 'veteran'. regexp_replace then adds :* to
# every word, making it match any title word that starts with it: 'richar'
# finds 'Richard', so a half-typed or half-remembered word still finds bills
# as the visitor types. Words like 'the' alone leave an empty query, which
# matches nothing.
TITLE_QUERY = (
    "regexp_replace(websearch_to_tsquery('english', %(words)s)::text,"
    " '(''[^'']*'')', '\\1:*', 'g')::tsquery"
)


def read_search(text: str) -> dict:
    """Return the bill type, bill number and words a search asks for.

    A bill as Congress writes it, such as 'H.R. 2102', 'HR 2102' or 'hr2102',
    gives its type and number. A number alone, such as '2102', gives only the
    number, so it matches a bill of any type. Anything else is words to look
    for in bill titles. Each part not asked for is None, and an empty search
    asks for nothing.
    """
    found = {"bill_type": None, "bill_number": None, "words": None}
    text = " ".join(text.split())
    if not text:
        return found

    if text.isdigit():
        return found | {"bill_number": int(text)}

    bill = bill_for(text)
    if bill is not None:
        bill_type, bill_number = bill
        return found | {"bill_type": bill_type, "bill_number": bill_number}

    return found | {"words": text}
