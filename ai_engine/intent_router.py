import json
import os
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables from project root .env
load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

VERSION = "NorthStar AI Intent Router v2"

VALID_INTENTS = {
    "maintenance",
    "leasing inquiry",
    "resident services",
    "vendor coordination",
    "inspection",
    "compliance",
    "capital project",
}

SYSTEM_PROMPT = """
You are the top-level AI Intent Router for North Star AI,
an AI Operating System for Property Management.

Your job:
- Read an incoming property operations message.
- Classify the message into exactly one operational loop.
- Write a short operational summary.
- Return ONLY valid JSON.

Valid intent values:
- maintenance
- leasing inquiry
- resident services
- vendor coordination
- inspection
- compliance
- capital project

Return ONLY valid JSON with these exact keys:
{
  "intent": "maintenance | leasing inquiry | resident services | vendor coordination | inspection | compliance | capital project",
  "confidence": "high | medium | low",
  "summary": "string"
}

Routing guidance:
- maintenance:
  repair requests, no heat, leaks, plumbing, electrical, appliance failures,
  flooding, sewage, lockout/access problems, broken fixtures, HVAC issues,
  active building or unit maintenance problems

- leasing inquiry:
  prospective tenant questions, availability, tours, pricing, rent for prospects,
  applications, pet policy, amenities, parking questions from prospects,
  move-in interest, unit availability, leasing office inquiries from non-residents

- resident services:
  current resident questions that are not maintenance work orders,
  lease renewal questions, account/service questions, move-out coordination,
  billing/service questions, community policy questions, general resident support

- vendor coordination:
  vendor scheduling, contractor no-shows, access for vendors, delivery coordination,
  landscape/snow/janitorial/vendor follow-up, service appointment coordination,
  external service provider coordination not tied to a capital project

- inspection:
  unit inspections, housekeeping inspections, move-in/move-out inspections,
  REAC/NSPIRE references, deficiency checks, inspection scheduling

- compliance:
  fair housing, legal notices, policy violations, accommodation requests,
  documentation requests, complaints with compliance implications, regulatory items

- capital project:
  roof replacement, paving, façade work, boiler replacement, renovation phases,
  large project coordination, multi-unit upgrades, construction access planning,
  contractor access for major project work

Rules:
- Choose exactly one intent.
- Do not return markdown.
- Do not explain your reasoning.
- If uncertain, choose the best fit and lower the confidence.
"""


def normalize_text(text: str) -> str:
    return text.lower().strip()


def strip_json_fences(raw_text: str) -> str:
    cleaned = raw_text.strip()

    if cleaned.startswith("```json"):
        cleaned = cleaned[len("```json"):].strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned[len("```"):].strip()

    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()

    return cleaned


def apply_rule_overrides(message: str, result: dict[str, Any]) -> dict[str, Any]:
    """
    Hard routing rules that override model output when the operational category
    is obvious or safety-sensitive.
    """
    text = normalize_text(message)

    maintenance_keywords = [
        "leak",
        "leaking",
        "overflow",
        "overflowing",
        "flood",
        "flooding",
        "sewage",
        "drain",
        "clog",
        "clogged",
        "toilet",
        "sink",
        "faucet",
        "bathtub",
        "shower",
        "pipe",
        "water heater",
        "hot water",
        "no heat",
        "heat is not working",
        "heater not working",
        "heating not working",
        "apartment is cold",
        "very cold in here",
        "hvac",
        "air conditioning",
        "ac not working",
        "breaker",
        "electrical",
        "spark",
        "burning smell",
        "gas smell",
        "smell gas",
        "refrigerator",
        "fridge",
        "stove",
        "oven",
        "dishwasher",
        "lockout",
        "locked out",
        "key fob",
        "cannot get into the building",
        "maintenance",
        "repair",
        "broken",
    ]

    leasing_inquiry_keywords = [
        "tour",
        "schedule a tour",
        "availability",
        "available unit",
        "2 bedroom",
        "two bedroom",
        "1 bedroom",
        "one bedroom",
        "studio apartment",
        "rent",
        "pricing",
        "application",
        "apply",
        "leasing",
        "pets allowed",
        "pet policy",
        "parking available",
        "amenities",
        "move in",
        "move-in",
        "prospective tenant",
        "available apartment",
    ]

    resident_services_keywords = [
        "renewal",
        "renew my lease",
        "lease renewal",
        "when does my lease expire",
        "billing question",
        "account question",
        "resident portal",
        "move-out coordination",
        "move out coordination",
        "community policy",
        "service question",
        "general question about my unit account",
        "current resident",
    ]

    vendor_keywords = [
        "vendor",
        "contractor",
        "landscaper",
        "landscaping",
        "snow removal",
        "janitorial",
        "delivery",
        "service provider",
        "technician arrival",
        "no-show vendor",
        "vendor access",
        "contractor access",
    ]

    inspection_keywords = [
        "inspection",
        "inspect",
        "housekeeping inspection",
        "move-out inspection",
        "move out inspection",
        "move-in inspection",
        "move in inspection",
        "reac",
        "nspire",
        "deficiency",
        "unit walk",
    ]

    compliance_keywords = [
        "fair housing",
        "accommodation",
        "reasonable accommodation",
        "regulatory",
        "compliance",
        "legal notice",
        "policy violation",
        "documentation request",
        "discrimination",
        "harassment complaint",
    ]

    capital_project_keywords = [
        "roof replacement",
        "paving",
        "asphalt",
        "capital project",
        "renovation",
        "rehab",
        "construction",
        "boiler replacement",
        "window replacement",
        "facade",
        "façade",
        "project schedule",
        "project access",
        "multi-unit upgrade",
        "paving contractor",
        "roof contractor",
        "construction access",
        "site access for contractor",
    ]

    # Priority order matters:
    # 1. maintenance
    # 2. compliance
    # 3. inspection
    # 4. capital project
    # 5. vendor coordination
    # 6. leasing inquiry
    # 7. resident services

    if any(keyword in text for keyword in maintenance_keywords):
        result.update(
            {
                "intent": "maintenance",
                "confidence": "high",
                "rule_override_applied": True,
                "matched_rule": "maintenance keyword",
            }
        )
        return result

    if any(keyword in text for keyword in compliance_keywords):
        result.update(
            {
                "intent": "compliance",
                "confidence": "high",
                "rule_override_applied": True,
                "matched_rule": "compliance keyword",
            }
        )
        return result

    if any(keyword in text for keyword in inspection_keywords):
        result.update(
            {
                "intent": "inspection",
                "confidence": "high",
                "rule_override_applied": True,
                "matched_rule": "inspection keyword",
            }
        )
        return result

    if any(keyword in text for keyword in capital_project_keywords):
        result.update(
            {
                "intent": "capital project",
                "confidence": "high",
                "rule_override_applied": True,
                "matched_rule": "capital project keyword",
            }
        )
        return result

    if any(keyword in text for keyword in vendor_keywords):
        result.update(
            {
                "intent": "vendor coordination",
                "confidence": "high",
                "rule_override_applied": True,
                "matched_rule": "vendor coordination keyword",
            }
        )
        return result

    if any(keyword in text for keyword in leasing_inquiry_keywords):
        result.update(
            {
                "intent": "leasing inquiry",
                "confidence": "high",
                "rule_override_applied": True,
                "matched_rule": "leasing inquiry keyword",
            }
        )
        return result

    if any(keyword in text for keyword in resident_services_keywords):
        result.update(
            {
                "intent": "resident services",
                "confidence": "high",
                "rule_override_applied": True,
                "matched_rule": "resident services keyword",
            }
        )
        return result

    result["rule_override_applied"] = False
    result["matched_rule"] = ""
    return result


def validate_result(result: dict[str, Any]) -> dict[str, Any]:
    required_keys = ["intent", "confidence", "summary"]

    for key in required_keys:
        if key not in result:
            result[key] = ""

    intent = str(result.get("intent", "")).strip().lower()
    if intent not in VALID_INTENTS:
        intent = "resident services"
    result["intent"] = intent

    confidence = str(result.get("confidence", "")).strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"
    result["confidence"] = confidence

    summary = str(result.get("summary", "")).strip()
    if not summary:
        summary = "No summary provided."
    result["summary"] = summary

    return result


def route_message(message: str, model: str = "gpt-4.1-mini") -> dict[str, Any]:
    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Route this property operations message:\n\n{message}",
            },
        ],
    )

    raw_text = response.output_text.strip()
    cleaned_text = strip_json_fences(raw_text)

    try:
        result = json.loads(cleaned_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Model did not return valid JSON.\n"
            f"Raw response:\n{raw_text}"
        ) from exc

    result = validate_result(result)
    result = apply_rule_overrides(message, result)
    result = validate_result(result)
    return result


def print_result(message: str, result: dict[str, Any]) -> None:
    print("=" * 80)
    print(f"INCOMING MESSAGE: {message}")
    print("-" * 80)
    print(json.dumps(result, indent=2))
    print()


def main() -> None:
    print(f"{VERSION} - Local Test Mode\n")

    test_messages = [
        "My bathtub is overflowing and water is going onto the bathroom floor.",
        "I would like to schedule a tour for a 2-bedroom apartment.",
        "The landscaper never showed up today and the grass has not been cut.",
        "Please schedule the move-out inspection for unit 5A.",
        "We need documentation related to a fair housing complaint.",
        "The roof replacement contractor needs access Monday morning.",
        "When does my lease renew?",
    ]

    for message in test_messages:
        try:
            result = route_message(message)
            print_result(message, result)
        except Exception as e:
            print("=" * 80)
            print(f"INCOMING MESSAGE: {message}")
            print("-" * 80)
            print(f"ERROR: {e}\n")


if __name__ == "__main__":
    main()