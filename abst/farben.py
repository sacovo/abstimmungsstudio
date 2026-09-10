"""Party colours.

These are identity colours: people read them as names, not as values on a
scale, which is why a party keeps its colour across every chart. They are
used by the behavior view here and shipped with the export so the slide deck
draws the same party in the same colour.

One table, two consumers. Keep it that way — a second copy in a template or
in the deck drifts within a season.

Keys are the labels that ``abst.behavior`` produces for the three scopes
(party, party group, political camp) plus the categories of a vote-to-vote
comparison.
"""

PARTEI_FARBEN: dict[str, str] = {
    # Parteien
    "GRÜNE": "#84B547",
    "SP": "#F0554D",
    "PdA/Sol.": "#BF3939",
    "GLP": "#C4C43D",
    "Lega": "#9070D4",
    "CSP": "#E3BA24",
    "EVP": "#DEAA28",
    "EDU": "#A65E42",
    "Mitte": "#D6862B",
    "SVP": "#4B8A3E",
    "FDP": "#3872B5",
    "SD": "#9D9D9D",
    "MCR": "#49A5E7",
    "FGA": "#A83232",
    "Übrige": "#B8B8B8",
    # Parteigruppen
    "Die Mitte": "#D6862B",
    "Kleine Mitteparteien (LdU, EVP, CSP)": "#DEAA28",
    "Kleine Rechtsparteien (SD, EDU, FPS, Lega, MCR)": "#A65E42",
    "FDP.Die Liberalen (inkl. LPS)": "#3872B5",
    "FDP. Die Liberalen (inkl. LPS)": "#3872B5",
    "Grüne (GPS, FGA, POCH)": "#84B547",
    "Grünliberale Partei": "#C4C43D",
    "Kleine Linksparteien (PdA/Sol.)": "#BF3939",
    "Schweizerische Volkspartei": "#4B8A3E",
    "Sozialdemokratische Partei der Schweiz": "#F0554D",
    "Übrige/Splittergruppen": "#B8B8B8",
    # Parteipolitische Lager
    "Linke und grüne Parteien (SP, PdA, Sol., POCH, FGA, GPS)": "#d62728",
    "Traditionelle bürgerliche und rechte Parteien (FDP, Mitte, SVP, LPS, SD, EDU, FPS, Lega, MCR)": "#3872B5",
    "Traditionelle bürgerliche und rechte Parteien (FDP, MItte, SVP, LPS, SD, EDU, FPS, Lega, MCR)": "#3872B5",
    "Kleine Mitteparteien und andere (LdU, EVP, CSP, GLP, andere)": "#ff7f0e",
}

# Ja, Nein, Enthaltung and the like carry no party identity — they are a
# polarity, and every surface should render them in its own colours for that.
# The deck uses the poles of its diverging ramp, this app the values below.
KATEGORIE_FARBEN: dict[str, str] = {
    "Ja (Ziel)": "#1f77b4",
    "Nein (Ziel)": "#d62728",
    "Enthaltung (Ziel)": "#7f7f7f",
    "Exit": "#cccccc",
    "Ja (Quelle)": "#1f77b4",
    "Nein (Quelle)": "#d62728",
    "Enthaltung (Quelle)": "#7f7f7f",
    "Neuwähler": "#7F7F7F",
    "neuwähler": "#7F7F7F",
    "Nichtwähler": "#D3D3D3",
    "nichtwahler": "#D3D3D3",
}

ALLE_FARBEN: dict[str, str] = {**PARTEI_FARBEN, **KATEGORIE_FARBEN}


def parteifarben_fuer(labels) -> dict[str, str]:
    """The party colours among ``labels``, in the order they were given.

    Only party identities — a vote's Ja/Nein/Enthaltung is deliberately left
    out so each surface can colour polarity in its own palette.
    """
    return {label: PARTEI_FARBEN[label] for label in labels if label in PARTEI_FARBEN}
