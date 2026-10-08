"""Facts about states and territories shared by the ingestion jobs."""

# Postal abbreviation to FIPS code for everything that has a seat in Congress:
# the 50 states, DC, and the five territories. Sources name a state either way
# (congress-legislators says 'IL', the Census says '17'); our tables join on FIPS.
FIPS_BY_POSTAL = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09",
    "DE": "10", "DC": "11", "FL": "12", "GA": "13", "HI": "15", "ID": "16", "IL": "17",
    "IN": "18", "IA": "19", "KS": "20", "KY": "21", "LA": "22", "ME": "23", "MD": "24",
    "MA": "25", "MI": "26", "MN": "27", "MS": "28", "MO": "29", "MT": "30", "NE": "31",
    "NV": "32", "NH": "33", "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38",
    "OH": "39", "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46",
    "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53", "WV": "54",
    "WI": "55", "WY": "56", "AS": "60", "GU": "66", "MP": "69", "PR": "72", "VI": "78",
}

# These send one non-voting member to the House (five delegates and Puerto
# Rico's Resident Commissioner). The Census gives their single district the
# code '98'; an at-large state's single district is '00'.
NON_VOTING = {"DC", "AS", "GU", "MP", "PR", "VI"}
