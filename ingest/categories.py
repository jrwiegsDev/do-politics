"""Decide what kind of vote a roll call is, from its chamber, question and title.

Neither chamber labels its votes this way. The question says what was asked
("On Passage", "On the Cloture Motion"), and for two questions only the title
tells a final vote from a procedural one.
"""

LEGISLATION = "legislation"
NOMINATION = "nomination"
PROCEDURAL = "procedural"

# Final votes on a bill or resolution.
HOUSE_FINAL = {
    "On Passage",
    "On Motion to Suspend the Rules and Pass",
    "On Motion to Suspend the Rules and Pass, as Amended",
    "On Motion to Suspend the Rules and Agree",
    "On Motion to Suspend the Rules and Agree, as Amended",
    "On Motion to Concur in the Senate Amendment",
    "On Motion to Concur in the Senate Amendments",
    "On Motion to Suspend the Rules and Concur in the Senate Amendments",
    "On Motion to Suspend the Rules and Concur in Senate Adt to House Adt to Senate Adt",
    "Passage, Objections of the President To The Contrary Notwithstanding",
}

SENATE_FINAL = {
    "On Passage of the Bill",
    "On the Joint Resolution",
    "On the Resolution",
    "On the Concurrent Resolution",
}

SENATE_NOMINATION = {"On the Nomination"}

# The House uses these for real resolutions and also for "rules", which only
# set the terms for debating something else. The title tells them apart.
HOUSE_RESOLUTION = {
    "On Agreeing to the Resolution",
    "On Agreeing to the Resolution, as Amended",
}
RULE_TITLE_STARTS = (
    "Providing for consideration",
    "Providing for disposition",
    "Relating to consideration",
    "Waiving a requirement",
)

# The Senate's catch-all. It is a final vote only when the motion is to accept
# the House's version of a bill.
SENATE_MOTION = "On the Motion"
CONCUR_TITLE_START = "Motion to Concur"

HOUSE_PROCEDURAL = {
    "Call by States",
    "Call of the House",
    "Election of the Speaker",
    "On Agreeing to the Amendment",
    "On Agreeing to the Amendment, as Modified",
    "On Consideration of the Resolution",
    "On Motion to Adjourn",
    "On Motion to Commit",
    "On Motion to Commit with Instructions",
    "On Motion to Discharge",
    "On Motion to Instruct Conferees",
    "On Motion to Recommit",
    "On Motion to Reconsider",
    "On Motion to Refer",
    "On Motion to Table",
    "On Motion to Table the Motion to Discharge Committee",
    "On Ordering the Previous Question",
    "On Retaining Division A",
    "On Retaining Divisions B and C",
}

SENATE_PROCEDURAL = {
    "On Cloture on the Motion to Proceed",
    "On the Amendment",
    "On the Cloture Motion",
    "On the Decision of the Chair",
    "On the Motion to Adjourn",
    "On the Motion to Discharge",
    "On the Motion to Proceed",
    "On the Motion to Recommit",
    "On the Motion to Reconsider",
    "On the Motion to Table",
    "On the Point of Order",
}

KNOWN = {
    "house": HOUSE_FINAL | HOUSE_RESOLUTION | HOUSE_PROCEDURAL,
    "senate": SENATE_FINAL | SENATE_NOMINATION | SENATE_PROCEDURAL | {SENATE_MOTION},
}


def categorize(vote: dict) -> str:
    """Return 'legislation', 'nomination' or 'procedural' for a parsed vote.

    A question this module has never seen counts as procedural, so nothing
    is shown as a final vote by mistake. is_known() says when that happened.
    """
    question = vote["question"]
    title = vote["title"] or ""

    if vote["chamber"] == "house":
        if question in HOUSE_FINAL:
            return LEGISLATION
        if question in HOUSE_RESOLUTION and not title.startswith(RULE_TITLE_STARTS):
            return LEGISLATION
        return PROCEDURAL

    if question in SENATE_NOMINATION:
        return NOMINATION
    if question in SENATE_FINAL:
        return LEGISLATION
    if question == SENATE_MOTION and title.startswith(CONCUR_TITLE_START):
        return LEGISLATION
    return PROCEDURAL


def is_known(vote: dict) -> bool:
    """True when the vote's question is one this module has a rule for."""
    return vote["question"] in KNOWN[vote["chamber"]]
