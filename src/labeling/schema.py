"""Label schema: the single source of truth for annotation sheets, LLM labeling and the classifier.

Full definitions and edge cases: docs/annotation_guideline.md.
"""

REVIEW_TYPES = ("lived_experience", "prospective", "staff_shoutout", "generic")

TOPICS = (
    "maintenance_repairs",
    "elevators_access",
    "pests_mold",
    "noise",
    "cleanliness",
    "safety_security",
    "parking",
    "staff_management",
    "leasing_process",
    "fees_deposit",
    "amenities",
    "unit_building",
    "location",
)

# Values in annotation sheets: empty cell = not mentioned.
SENTIMENTS = ("pos", "neg")
# Class indices for each topic head of the classifier.
TOPIC_CLASSES = ("none", "pos", "neg")

# One-line hints shown in the annotation sheet header comments.
TOPIC_HINTS = {
    "maintenance_repairs": "Repairs and in-unit systems (water, AC, appliances, internet): fixed fast = pos; slow / not fixed / keeps breaking = neg",
    "elevators_access": "Elevators, key fobs, door locks, garage gates (only here, not under maintenance)",
    "pests_mold": "Roaches, rats, bugs, mold, bad smells",
    "noise": "Neighbors, walls, construction, fire alarms",
    "cleanliness": "Hallways, trash, dog waste, unit dirty at move-in",
    "safety_security": "Crime, break-ins, homeless people in building, stolen packages, feeling safe",
    "parking": "Parking, towing, guest spots",
    "staff_management": "Staff attitude, management communication / responsiveness / accountability",
    "leasing_process": "Tours, application, lease signing, renewal, move-in process",
    "fees_deposit": "Rent level, extra fees, utility bills, rent increases, deposit, move-out charges",
    "amenities": "Pool, gym, study rooms, events, pet facilities",
    "unit_building": "Unit size, layout, light, views, finishes, building quality, matches advertising",
    "location": "Location, neighborhood, distance to campus / downtown",
}


def consistency_errors(review_type: str, topics: dict[str, str | None]) -> list[str]:
    """Rule violations for one labeled review (topics: topic -> 'pos' / 'neg' / None)."""
    errors = []
    if review_type not in REVIEW_TYPES:
        errors.append(f"unknown review_type {review_type!r}")
    bad = {t: v for t, v in topics.items() if v not in (None, *SENTIMENTS)}
    errors += [f"{t}: invalid value {v!r}" for t, v in bad.items()]
    mentioned = {t for t, v in topics.items() if v in SENTIMENTS}
    if review_type == "generic" and mentioned:
        errors.append(f"generic review must have no topics, has {sorted(mentioned)}")
    if review_type == "staff_shoutout":
        if "staff_management" not in mentioned:
            errors.append("staff_shoutout needs staff_management pos/neg")
        if mentioned - {"staff_management"}:
            errors.append(f"staff_shoutout may only have staff_management, has {sorted(mentioned - {'staff_management'})}")
    return errors
