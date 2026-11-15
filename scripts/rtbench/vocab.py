"""Synthetic vocabulary. Every name is coined; no case draws on a real store."""

ENTITIES = (
    "Orrin Mill", "Calder Works", "Brisa Yard", "Fennick Depot", "Halvard Plant", "Ivel Foundry",
    "Jessop Wharf", "Kestrel Kiln", "Lunet Forge", "Marrow Quay", "Nesbit Hall", "Oriel Dock",
    "Pellam Works", "Quarry Vane", "Rusk Mill", "Selby Reach", "Tamsin Yard", "Ulden Plant",
    "Varrow Depot", "Wensum Forge", "Yarrow Quay", "Zelda Works", "Ashby Kiln", "Brenna Dock",
    "Corvin Hall", "Dunmore Yard", "Elstow Mill", "Farrel Plant", "Gisla Wharf", "Hollin Depot",
    "Ilvane Forge", "Jorvik Reach", "Kelda Works", "Lorne Kiln", "Mabry Quay", "Norland Mill",
    "Osric Yard", "Pevensey Plant", "Ravel Dock", "Sorrel Works",
)

ATTRIBUTES = (
    ("annual output", "tonnes"), ("headcount", "staff"), ("floor area", "square metres"),
    ("budget", "thousand dollars"), ("water use", "kilolitres"), ("fleet size", "vehicles"),
    ("storage capacity", "pallets"), ("power draw", "kilowatts"), ("shift length", "minutes"),
    ("intake", "cubic metres"), ("throughput", "parcels"), ("roof span", "metres"),
)

NOTES = (
    "Team lunch moved to Friday.", "The printer on level two is fixed.",
    "Reminder: fire drill next week.", "Parking permits renew in March.",
    "The quarterly newsletter went out.", "New coffee machine in the break room.",
    "Visitor badges are at reception.", "The lift is back in service.",
)

SUPERSEDE_REASONS = (
    "double-counted a shift", "late figure from the site office", "audit correction",
    "unit conversion fixed", "updated survey",
)

RETRACT_REASONS = ("source withdrawn", "entered against the wrong site", "unverifiable")


def make_slots(rng, n) -> list:
    pairs = [(e, a) for e in ENTITIES for a in ATTRIBUTES]
    chosen = rng.sample(pairs, n)
    return [{"slot": f"k{i + 1}", "entity": e, "attribute": a, "unit": u}
            for i, (e, (a, u)) in enumerate(chosen)]


def fresh_value(rng, unit, used) -> str:
    while True:
        v = f"{rng.randint(100, 9999)} {unit}"
        if v not in used:
            used.add(v)
            return v
