from typing import Any

DEFAULT_PROPERTY_CONFIG: dict[str, Any] = {
    "property_name": "DEFAULT",
    "internal_trades": [
        "General Maintenance",
        "Access Control",
        "Plumbing",
        "HVAC",
        "Appliance",
    ],
    "vendor_trades": [
        "Pest Control",
        "Life Safety",
    ],
    "specialist_vendor_trades": [
        "Electrical",
        "Structural",
    ],
    "union_vendor_trades": [],
    "after_hours_vendor_trades": [
        "Life Safety",
    ],
    "emergency_vendor_trades": [
        "Life Safety",
    ],
    "trade_overrides": {},
    "keywords": {
        "emergency_vendor": [
            "gas leak",
            "gas smell",
            "fire alarm panel",
            "sprinkler system",
            "life safety",
            "catastrophic",
            "major flood",
            "severe flooding",
            "sewage backup",
            "backing up",
        ],
        "specialist_vendor": [
            "boiler",
            "chiller",
            "compressor",
            "refrigerant",
            "transformer",
            "generator",
            "elevator",
            "fire panel",
            "alarm panel",
            "camera inspection",
            "jetting",
            "underground leak",
            "collapsed drain",
            "slab leak",
            "sanitary main",
            "main line",
            "water main",
            "sewer line",
            "roof system",
            "facade",
            "façade",
            "structural",
        ],
        "union_vendor": [
            "union",
            "unionized",
            "high voltage",
            "panel replacement",
            "switchgear",
            "major pipeline break",
            "pipeline break",
            "pipe burst",
            "burst pipe",
            "broken pipe",
            "major pipe break",
            "excavation",
            "asbestos",
            "lead abatement",
            "licensed electrician",
            "licensed plumber",
            "licensed contractor",
            "welding",
        ],
    },
}


PROPERTY_CONFIGS: dict[str, dict[str, Any]] = {
    "NorthStar Gardens": {
        "property_name": "NorthStar Gardens",
        "internal_trades": [
            "General Maintenance",
            "Access Control",
            "Plumbing",
            "HVAC",
            "Appliance",
        ],
        "vendor_trades": [
            "Pest Control",
            "Life Safety",
        ],
        "specialist_vendor_trades": [
            "Electrical",
            "Structural",
        ],
        "union_vendor_trades": [],
        "after_hours_vendor_trades": [
            "Life Safety",
            "Plumbing",
        ],
        "emergency_vendor_trades": [
            "Life Safety",
            "Plumbing",
        ],
        "trade_overrides": {
            "Access Control": "In-House",
        },
        "keywords": {
            "emergency_vendor": [
                "gas leak",
                "gas smell",
                "fire alarm panel",
                "sprinkler system",
                "life safety",
                "catastrophic",
                "major flood",
                "severe flooding",
                "sewage backup",
                "backing up",
            ],
            "specialist_vendor": [
                "boiler",
                "chiller",
                "compressor",
                "refrigerant",
                "transformer",
                "generator",
                "elevator",
                "fire panel",
                "alarm panel",
                "camera inspection",
                "jetting",
                "underground leak",
                "collapsed drain",
                "slab leak",
                "sanitary main",
                "main line",
                "water main",
                "sewer line",
                "roof system",
                "facade",
                "façade",
                "structural",
            ],
            "union_vendor": [
                "union",
                "unionized",
                "high voltage",
                "panel replacement",
                "switchgear",
                "major pipeline break",
                "pipeline break",
                "pipe burst",
                "burst pipe",
                "broken pipe",
                "major pipe break",
                "excavation",
                "asbestos",
                "lead abatement",
                "licensed electrician",
                "licensed plumber",
                "licensed contractor",
                "welding",
            ],
        },
    },
    "NorthStar Towers": {
        "property_name": "NorthStar Towers",
        "internal_trades": [
            "General Maintenance",
            "Access Control",
            "Appliance",
        ],
        "vendor_trades": [
            "Pest Control",
            "Life Safety",
            "Plumbing",
        ],
        "specialist_vendor_trades": [
            "Electrical",
            "HVAC",
            "Structural",
        ],
        "union_vendor_trades": [
            "Electrical",
        ],
        "after_hours_vendor_trades": [
            "Life Safety",
            "Plumbing",
            "HVAC",
            "Electrical",
        ],
        "emergency_vendor_trades": [
            "Life Safety",
            "Plumbing",
            "HVAC",
            "Electrical",
        ],
        "trade_overrides": {},
        "keywords": {
            "emergency_vendor": [
                "gas leak",
                "gas smell",
                "fire alarm panel",
                "sprinkler system",
                "life safety",
                "catastrophic",
                "major flood",
                "severe flooding",
                "sewage backup",
                "backing up",
            ],
            "specialist_vendor": [
                "boiler",
                "chiller",
                "compressor",
                "refrigerant",
                "transformer",
                "generator",
                "elevator",
                "fire panel",
                "alarm panel",
                "camera inspection",
                "jetting",
                "underground leak",
                "collapsed drain",
                "slab leak",
                "sanitary main",
                "main line",
                "water main",
                "sewer line",
                "roof system",
                "facade",
                "façade",
                "structural",
            ],
            "union_vendor": [
                "union",
                "unionized",
                "high voltage",
                "panel replacement",
                "switchgear",
                "major pipeline break",
                "pipeline break",
                "pipe burst",
                "burst pipe",
                "broken pipe",
                "major pipe break",
                "excavation",
                "asbestos",
                "lead abatement",
                "licensed electrician",
                "licensed plumber",
                "licensed contractor",
                "welding",
            ],
        },
    },
}


def get_property_config(property_name: str) -> dict[str, Any]:
    base = DEFAULT_PROPERTY_CONFIG.copy()
    property_config = PROPERTY_CONFIGS.get(property_name, {})

    merged = {
        **base,
        **property_config,
    }

    merged["internal_trades"] = list(property_config.get("internal_trades", base["internal_trades"]))
    merged["vendor_trades"] = list(property_config.get("vendor_trades", base["vendor_trades"]))
    merged["specialist_vendor_trades"] = list(
        property_config.get("specialist_vendor_trades", base["specialist_vendor_trades"])
    )
    merged["union_vendor_trades"] = list(
        property_config.get("union_vendor_trades", base["union_vendor_trades"])
    )
    merged["after_hours_vendor_trades"] = list(
        property_config.get("after_hours_vendor_trades", base["after_hours_vendor_trades"])
    )
    merged["emergency_vendor_trades"] = list(
        property_config.get("emergency_vendor_trades", base["emergency_vendor_trades"])
    )
    merged["trade_overrides"] = dict(property_config.get("trade_overrides", base["trade_overrides"]))

    base_keywords = base["keywords"]
    property_keywords = property_config.get("keywords", {})
    merged["keywords"] = {
        "emergency_vendor": list(
            property_keywords.get("emergency_vendor", base_keywords["emergency_vendor"])
        ),
        "specialist_vendor": list(
            property_keywords.get("specialist_vendor", base_keywords["specialist_vendor"])
        ),
        "union_vendor": list(
            property_keywords.get("union_vendor", base_keywords["union_vendor"])
        ),
    }

    return merged