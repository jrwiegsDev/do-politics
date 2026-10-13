"""How the loaders identify themselves to the sites they download from."""

# Sent with every request, so each site's webmasters can see who is asking.
# Congress.gov refuses requests that carry Python's default identification.
USER_AGENT = "DoPolitics (https://github.com/jrwiegsDev/do-politics)"
