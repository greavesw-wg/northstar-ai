import csv
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from config.property_config import get_property_config

from openai.types.chat import (
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)

# Load environment variables from project root .env
load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Project paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"
LOGS_DIR.mkdir(exist_ok=True)

VERSION = "NorthStar Maintenance Triage + Dispatch Brain Engine v8"

SYSTEM_PROMPT = """
You are an AI maintenance triage engine for multifamily real estate operations.

Your job:
- Read a tenant maintenance message.
- Classify the issue.
- Determine urgency.
- Route to the correct department.
- Route to the correct trade.
- Recommend the next action.
- Draft a short resident-facing reply.

Return ONLY valid JSON with these exact keys:
{
  "issue_type": "string",
  "category": "string",
  "trade": "string",
  "priority": "Emergency | Urgent | Routine",
  "dispatch_priority": 1,
  "department": "Maintenance | Leasing | Compliance | Security | Unknown",
  "recommended_action": "string",
  "eta_guidance": "string",
  "resident_reply": "string"
}
"""

TEST_REQUESTS = [
    {
        "property_name": "NorthStar Gardens",
        "building": "Building A",
        "unit_number": "2B",
        "resident_name": "Test Resident 1",
        "message": "My bathtub is overflowing and water is going onto the bathroom floor.",
    },
    {
        "property_name": "NorthStar Gardens",
        "building": "Building C",
        "unit_number": "4D",
        "resident_name": "Test Resident 2",
        "message": "The heat is not working in my apartment and it is very cold in here.",
    },
    {
        "property_name": "NorthStar Gardens",
        "building": "Building B",
        "unit_number": "1A",
        "resident_name": "Test Resident 3",
        "message": "My kitchen faucet has been dripping for three days.",
    },
    {
        "property_name": "NorthStar Gardens",
        "building": "Building D",
        "unit_number": "3C",
        "resident_name": "Test Resident 4",
        "message": "I smell something burning from the breaker panel.",
    },
    {
        "property_name": "NorthStar Gardens",
        "building": "Main Entrance",
        "unit_number": "",
        "resident_name": "Test Resident 5",
        "message": "I lost my key fob and cannot get into the building.",
    },
    {
        "property_name": "NorthStar Gardens",
        "building": "Building E",
        "unit_number": "5A",
        "resident_name": "Test Resident 6",
        "message": "There is sewage backing up into my shower drain.",
    },
    {
        "property_name": "NorthStar Towers",
        "building": "Tower East",
        "unit_number": "19D",
        "resident_name": "Tower Resident",
        "message": "The heat is not working in my apartment and it is very cold in here.",
    },
]

TECHNICIAN_ROSTER = {
    "General Maintenance": {
        "primary": "Mike Reynolds",
        "on_call": "Carlos Vega",
    },
    "Plumbing": {
        "primary": "Angela Brooks",
        "on_call": "Derrick Moss",
    },
    "HVAC": {
        "primary": "Luis Martinez",
        "on_call": "Brian Keller",
    },
    "Electrical": {
        "primary": "Eric Dalton",
        "on_call": "Shawn Turner",
    },
    "Appliance": {
        "primary": "Nina Patel",
        "on_call": "Mike Reynolds",
    },
    "Access Control": {
        "primary": "Front Office Coordinator",
        "on_call": "After-Hours Access Line",
    },
    "Pest Control": {
        "primary": "Vendor Managed",
        "on_call": "Vendor Managed",
    },
    "Life Safety": {
        "primary": "Emergency Vendor",
        "on_call": "Emergency Vendor",
    },
}

VENDOR_DIRECTORY = {
    "Pest Control": {
        "vendor_name": "Guardian Pest Services",
        "vendor_contact": "(800) 555-0141",
    },
    "Life Safety": {
        "vendor_name": "Rapid Response Life Safety",
        "vendor_contact": "(800) 555-0110",
    },
    "Electrical": {
        "vendor_name": "Metro Electrical Services",
        "vendor_contact": "(800) 555-0199",
    },
    "HVAC": {
        "vendor_name": "Summit HVAC Solutions",
        "vendor_contact": "(800) 555-0125",
    },
    "Plumbing": {
        "vendor_name": "Apex Plumbing Response",
        "vendor_contact": "(800) 555-0133",
    },
    "Appliance": {
        "vendor_name": "Premier Appliance Repair",
        "vendor_contact": "(800) 555-0158",
    },
    "Access Control": {
        "vendor_name": "SecureEntry Systems",
        "vendor_contact": "(800) 555-0164",
    },
}

VALID_SERVICE_DELIVERY_MODELS = {
    "In-House",
    "Vendor",
    "Specialist Vendor",
    "Union Vendor",
    "Emergency Vendor",
}


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


def normalize_trade(trade: str) -> str:
    t = normalize_text(trade)

    if any(x in t for x in ["hvac", "heating", "cooling", "air conditioning", "ac technician"]):
        return "HVAC"
    if any(x in t for x in ["plumber", "plumbing", "pipe", "drain"]):
        return "Plumbing"
    if any(x in t for x in ["electric", "electrical", "electrician", "breaker"]):
        return "Electrical"
    if any(x in t for x in ["appliance", "refrigerator", "fridge", "stove", "oven", "dishwasher"]):
        return "Appliance"
    if any(x in t for x in ["access", "key fob", "lockout", "door entry", "entry control"]):
        return "Access Control"
    if any(x in t for x in ["pest", "mice", "rodent", "roach", "insect"]):
        return "Pest Control"
    if any(x in t for x in ["life safety", "fire", "gas leak"]):
        return "Life Safety"
    if any(x in t for x in ["structural", "wall", "ceiling", "flooring"]):
        return "Structural"
    if any(x in t for x in ["general maintenance", "maintenance tech", "handyman"]):
        return "General Maintenance"

    return trade.strip() if trade.strip() else "Unknown"


def normalize_category(category: str) -> str:
    c = normalize_text(category)

    if "hvac" in c or "heating" in c or "cooling" in c or "air conditioning" in c:
        return "HVAC"

    if any(x in c for x in ["plumbing", "faucet", "drain", "overflow", "leak", "sewage"]):
        return "Plumbing"

    if any(x in c for x in ["electrical", "breaker", "spark", "burning smell"]):
        return "Electrical"

    if any(x in c for x in ["access", "lockout", "key fob"]):
        return "Access Issue"

    if any(x in c for x in ["appliance", "refrigerator", "fridge"]):
        return "Appliance"

    if any(x in c for x in ["pest", "mice", "rodent", "roach"]):
        return "Pest Issue"

    if "gas" in c:
        return "Gas Leak"

    if "flood" in c:
        return "Flooding"

    return category.strip() if category.strip() else "Unknown"


def apply_rule_overrides(message: str, result: dict[str, Any]) -> dict[str, Any]:
    text = normalize_text(message)

    if "gas smell" in text or "smell gas" in text:
        result.update(
            {
                "category": "Gas Leak",
                "trade": "Life Safety",
                "priority": "Emergency",
                "dispatch_priority": 1,
                "department": "Maintenance",
                "recommended_action": "Treat as a gas emergency. Instruct resident to leave the unit immediately, avoid switches or flames, and contact emergency services and the gas utility. Dispatch maintenance leadership immediately.",
                "eta_guidance": "Immediate emergency response required.",
                "resident_reply": "This may be a gas emergency. Leave the unit immediately, avoid using switches or flames, and call 911 and your gas utility now. We are escalating this immediately.",
                "rule_override_applied": True,
                "matched_rule": "gas smell",
            }
        )
        return result

    if "burning smell" in text or "breaker panel" in text or "sparking" in text:
        result.update(
            {
                "category": "Electrical Issue",
                "trade": "Electrical",
                "priority": "Emergency",
                "dispatch_priority": 1,
                "department": "Maintenance",
                "recommended_action": "Treat as an electrical emergency. Dispatch maintenance or electrician immediately. If safe, instruct resident to stay away from the area and call emergency services if smoke or fire is present.",
                "eta_guidance": "Immediate response required.",
                "resident_reply": "This is being treated as an emergency. Please stay away from the affected electrical area. Call 911 right away if there is smoke or fire. We are escalating this immediately.",
                "rule_override_applied": True,
                "matched_rule": "electrical hazard",
            }
        )
        return result

    if "sewage" in text or "backing up" in text:
        result.update(
            {
                "category": "Sewage Backup",
                "trade": "Plumbing",
                "priority": "Emergency",
                "dispatch_priority": 1,
                "department": "Maintenance",
                "recommended_action": "Treat as an emergency plumbing sanitation issue. Dispatch maintenance immediately and instruct resident to avoid the affected fixtures and area.",
                "eta_guidance": "Immediate response required.",
                "resident_reply": "This is being treated as an emergency. Please avoid the affected plumbing area. We are notifying maintenance for immediate response.",
                "rule_override_applied": True,
                "matched_rule": "sewage backup",
            }
        )
        return result

    if "overflowing" in text or "overflow" in text:
        result.update(
            {
                "category": "Active Water Overflow",
                "trade": "Plumbing",
                "priority": "Urgent",
                "dispatch_priority": 2,
                "department": "Maintenance",
                "recommended_action": "Dispatch maintenance immediately to stop overflow and limit water damage. Instruct resident to shut off the water if possible and stop using the fixture.",
                "eta_guidance": "Prompt same-day response required.",
                "resident_reply": "Thank you for reporting this. Please stop using the fixture and shut off the water if you can do so safely. We are notifying maintenance now for a prompt response.",
                "rule_override_applied": True,
                "matched_rule": "overflowing fixture",
            }
        )
        return result

    if "flood" in text or "flooding" in text:
        result.update(
            {
                "category": "Flooding",
                "trade": "Plumbing",
                "priority": "Emergency",
                "dispatch_priority": 1,
                "department": "Maintenance",
                "recommended_action": "Treat as an emergency water intrusion event. Dispatch maintenance immediately to stop the source and mitigate damage.",
                "eta_guidance": "Immediate response required.",
                "resident_reply": "This is being treated as an emergency. Please stay clear of standing water near electrical devices. We are escalating maintenance immediately.",
                "rule_override_applied": True,
                "matched_rule": "flooding",
            }
        )
        return result

    heating_phrases = [
        "no heat",
        "heat is not working",
        "heater not working",
        "heating not working",
        "apartment is cold",
        "very cold in here",
    ]
    if any(phrase in text for phrase in heating_phrases):
        result.update(
            {
                "category": "Heating Outage",
                "trade": "HVAC",
                "priority": "Urgent",
                "dispatch_priority": 2,
                "department": "Maintenance",
                "recommended_action": "Dispatch HVAC or maintenance promptly. Confirm indoor conditions and outside temperature. Escalate to emergency if freezing conditions are present.",
                "eta_guidance": "Urgent same-day review required.",
                "resident_reply": "Thank you for letting us know. We are treating this as urgent and routing it to maintenance now.",
                "rule_override_applied": True,
                "matched_rule": "heating outage",
            }
        )
        return result

    if "locked out" in text:
        result.update(
            {
                "category": "Lockout",
                "trade": "Access Control",
                "priority": "Urgent",
                "dispatch_priority": 2,
                "department": "Leasing",
                "recommended_action": "Treat as resident lockout or access issue. Verify identity and follow property lockout procedure.",
                "eta_guidance": "Respond as soon as possible per office or on-call procedure.",
                "resident_reply": "Thank you. We are routing this as an access issue and will follow the property lockout procedure as quickly as possible.",
                "rule_override_applied": True,
                "matched_rule": "lockout",
            }
        )
        return result

    if "key fob" in text:
        result.update(
            {
                "category": "Access Issue",
                "trade": "Access Control",
                "priority": "Urgent",
                "dispatch_priority": 2,
                "department": "Leasing",
                "recommended_action": "Verify resident identity and assist with building access and key fob replacement or reprogramming under property policy.",
                "eta_guidance": "Prompt follow-up required.",
                "resident_reply": "Thank you. We are routing this as an access issue and will follow up as quickly as possible under property procedures.",
                "rule_override_applied": True,
                "matched_rule": "key fob",
            }
        )
        return result

    if "refrigerator" in text or "fridge" in text:
        result.update(
            {
                "category": "Appliance Failure",
                "trade": "Appliance",
                "priority": "Urgent",
                "dispatch_priority": 2,
                "department": "Maintenance",
                "recommended_action": "Dispatch maintenance to inspect refrigerator failure promptly to prevent food loss and determine repair or replacement path.",
                "eta_guidance": "Prompt same-day or next available response recommended.",
                "resident_reply": "Thank you for reporting this. We are routing this to maintenance promptly for appliance review.",
                "rule_override_applied": True,
                "matched_rule": "refrigerator failure",
            }
        )
        return result

    if "mice" in text or "mouse" in text or "rodent" in text:
        result.update(
            {
                "category": "Pest Issue",
                "trade": "Pest Control",
                "priority": "Routine",
                "dispatch_priority": 3,
                "department": "Maintenance",
                "recommended_action": "Create pest-control work order, inspect likely entry points, and schedule treatment.",
                "eta_guidance": "Schedule under standard service window.",
                "resident_reply": "Thank you for reporting this. We are documenting the pest issue and routing it for follow-up service.",
                "rule_override_applied": True,
                "matched_rule": "pest issue",
            }
        )
        return result

    result["rule_override_applied"] = False
    result["matched_rule"] = ""
    return result


def ensure_dispatch_priority(result: dict[str, Any]) -> dict[str, Any]:
    mapping = {
        "Emergency": 1,
        "Urgent": 2,
        "Routine": 3,
    }
    priority = result.get("priority", "Routine")
    result["dispatch_priority"] = mapping.get(priority, 3)
    return result


def validate_result(result: dict[str, Any]) -> dict[str, Any]:
    required_keys = [
        "issue_type",
        "category",
        "trade",
        "priority",
        "dispatch_priority",
        "department",
        "recommended_action",
        "eta_guidance",
        "resident_reply",
    ]

    for key in required_keys:
        if key not in result:
            result[key] = ""

    allowed_priorities = {"Emergency", "Urgent", "Routine"}
    if result["priority"] not in allowed_priorities:
        result["priority"] = "Routine"

    allowed_departments = {"Maintenance", "Leasing", "Compliance", "Security", "Unknown"}
    if result["department"] not in allowed_departments:
        result["department"] = "Unknown"

    if not isinstance(result["dispatch_priority"], int):
        result["dispatch_priority"] = 3

    result["trade"] = normalize_trade(str(result.get("trade", "")))
    result["category"] = normalize_category(str(result.get("category", "")))

    return ensure_dispatch_priority(result)

def triage_message(payload: dict, model: str = "gpt-4.1-mini") -> dict[str, Any]:

    message = str(payload.get("message", "")).strip()
    property_name = str(payload.get("property_name", "")).strip()
    building = str(payload.get("building", "")).strip()
    unit_number = str(payload.get("unit_number", "")).strip()
    resident_name = str(payload.get("resident_name", "")).strip()

    user_context = f"""
Property: {property_name}
Building: {building}
Unit: {unit_number}
Resident: {resident_name}
Issue: {message}
"""

    response = client.chat.completions.create(
        model=model,
        messages=[
            ChatCompletionSystemMessageParam(
                role="system",
                content=str(SYSTEM_PROMPT),
            ),
            ChatCompletionUserMessageParam(
                role="user",
                content=(
                    "Triaging this maintenance request and return JSON only:\n\n"
                    f"{user_context}"
                ),
            ),
        ],
    )

    raw_text = response.choices[0].message.content.strip()
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
    result = ensure_dispatch_priority(result)
    return result


def build_ticket_id(sequence_number: int) -> str:
    year = datetime.now(UTC).year
    return f"NS-{year}-{sequence_number:05d}"


def determine_on_call_required(result: dict[str, Any]) -> bool:
    return result.get("priority") == "Emergency"


def build_dispatch_context_text(result: dict[str, Any], message: str) -> str:
    parts = [
        str(result.get("issue_type", "")),
        str(result.get("category", "")),
        str(result.get("trade", "")),
        str(result.get("recommended_action", "")),
        str(result.get("matched_rule", "")),
        message,
    ]
    return " ".join(parts).lower()


def determine_service_delivery_model(
    request: dict[str, Any], result: dict[str, Any], message: str
) -> str:
    property_config = get_property_config(request["property_name"])
    trade = result.get("trade", "Unknown")
    priority = result.get("priority", "Routine")
    dispatch_context = build_dispatch_context_text(result, message)

    trade_overrides = property_config.get("trade_overrides", {})
    if trade in trade_overrides:
        return trade_overrides[trade]

    keywords = property_config.get("keywords", {})
    emergency_vendor_keywords = keywords.get("emergency_vendor", [])
    specialist_vendor_keywords = keywords.get("specialist_vendor", [])
    union_vendor_keywords = keywords.get("union_vendor", [])

    if any(keyword in dispatch_context for keyword in emergency_vendor_keywords):
        return "Emergency Vendor"

    if any(keyword in dispatch_context for keyword in union_vendor_keywords):
        return "Union Vendor"

    if any(keyword in dispatch_context for keyword in specialist_vendor_keywords):
        return "Specialist Vendor"

    if priority == "Emergency" and trade in property_config.get("emergency_vendor_trades", []):
        return "Emergency Vendor"

    if trade in property_config.get("union_vendor_trades", []):
        return "Union Vendor"

    if trade in property_config.get("specialist_vendor_trades", []):
        return "Specialist Vendor"

    if trade in property_config.get("vendor_trades", []):
        return "Vendor"

    if trade in property_config.get("internal_trades", []):
        return "In-House"

    return "In-House"

def map_to_assigned_type(service_delivery_model: str) -> str:
    if service_delivery_model == "In-House":
        return "In-House"
    return "Outsource"

def determine_vendor_recommended(service_delivery_model: str) -> bool:
    return service_delivery_model in {
        "Vendor",
        "Specialist Vendor",
        "Union Vendor",
        "Emergency Vendor",
    }


def determine_dispatch_group(
    result: dict[str, Any], service_delivery_model: str
) -> str:
    department = result.get("department", "Unknown")
    trade = result.get("trade", "Unknown")

    if department == "Leasing":
        return "Leasing Desk"

    if service_delivery_model == "Emergency Vendor":
        if trade == "Plumbing":
            return "Plumbing Emergency Vendor Response"
        if trade == "Electrical":
            return "Electrical Emergency Vendor Response"
        if trade == "HVAC":
            return "HVAC Emergency Vendor Response"
        if trade == "Life Safety":
            return "Emergency Vendor Response"
        return "Emergency Vendor Response"

    if service_delivery_model == "Union Vendor":
        if trade == "Plumbing":
            return "Plumbing Union Vendor Response"
        if trade == "Electrical":
            return "Electrical Union Vendor Response"
        if trade == "HVAC":
            return "HVAC Union Vendor Response"
        return "Union Vendor Response"

    if service_delivery_model == "Specialist Vendor":
        if trade == "Plumbing":
            return "Plumbing Specialist Vendor Response"
        if trade == "Electrical":
            return "Electrical Specialist Vendor Response"
        if trade == "HVAC":
            return "HVAC Specialist Vendor Response"
        if trade == "Appliance":
            return "Appliance Specialist Vendor Response"
        if trade == "Access Control":
            return "Access Control Specialist Vendor Response"
        return "Specialist Vendor Response"

    if service_delivery_model == "Vendor":
        if trade == "Plumbing":
            return "Plumbing Vendor Response"
        if trade == "HVAC":
            return "HVAC Vendor Response"
        if trade == "Electrical":
            return "Electrical Vendor Response"
        if trade == "Appliance":
            return "Appliance Vendor Response"
        if trade == "Access Control":
            return "Access Control Vendor Response"
        if trade == "Pest Control":
            return "Pest Control Vendor"
        return "Vendor Response"

    if trade == "Electrical":
        return "Electrical Response"
    if trade == "HVAC":
        return "HVAC Response"
    if trade == "Plumbing":
        return "Plumbing Response"
    if trade == "Appliance":
        return "Appliance Response"
    if trade == "Access Control":
        return "Access Control Desk"
    if trade == "Pest Control":
        return "Pest Control Vendor"
    if trade == "Life Safety":
        return "Emergency Vendor Response"

    return "General Maintenance"


def determine_escalation_path(
    result: dict[str, Any], service_delivery_model: str
) -> str:
    priority = result.get("priority")
    trade = result.get("trade")

    if service_delivery_model == "Emergency Vendor":
        return "Escalate immediately to emergency vendor dispatch and notify maintenance supervisor."

    if service_delivery_model == "Union Vendor":
        return "Escalate to approved union vendor and notify maintenance supervisor for controlled dispatch."

    if service_delivery_model == "Specialist Vendor":
        return "Escalate to specialist vendor dispatch and notify maintenance supervisor for technical review."

    if service_delivery_model == "Vendor":
        return "Route to outside vendor dispatch and notify maintenance supervisor."

    if priority == "Emergency":
        return "Notify on-call supervisor immediately and dispatch emergency response."

    if trade in {"Electrical", "HVAC", "Plumbing", "Appliance"} and priority == "Urgent":
        return "Route to maintenance supervisor for same-day dispatch review."

    if result.get("department") == "Leasing":
        return "Route to leasing or front office workflow."

    return "Route through standard maintenance queue."


def determine_resident_instructions(result: dict[str, Any]) -> str:
    matched_rule = result.get("matched_rule", "")

    instructions_map = {
        "overflowing fixture": "Stop using the fixture and shut off water if safe.",
        "sewage backup": "Avoid the affected area and do not use impacted plumbing fixtures.",
        "electrical hazard": "Stay away from the area and call 911 if smoke or fire appears.",
        "gas smell": "Leave the unit immediately and call 911 and the gas utility.",
        "key fob": "Wait for identity verification and access instructions.",
        "lockout": "Wait for property staff or on-call assistance after identity verification.",
        "refrigerator failure": "Keep the refrigerator door closed as much as possible until maintenance responds.",
        "heating outage": "Use safe temporary warming measures only and await follow-up from the property team.",
    }

    return instructions_map.get(
        matched_rule,
        "Please wait for follow-up instructions from the property team.",
    )


def determine_priority_queue_position(priority: str, index: int) -> int:
    base_positions = {
        "Emergency": 1,
        "Urgent": 10,
        "Routine": 20,
    }
    return base_positions.get(priority, 20) + index - 1


def determine_sla_hours(result: dict[str, Any], service_delivery_model: str) -> int:
    priority = result.get("priority")
    trade = result.get("trade")

    if service_delivery_model == "Emergency Vendor":
        return 1

    if service_delivery_model in {"Union Vendor", "Specialist Vendor"} and priority == "Emergency":
        return 2

    if priority == "Emergency":
        return 1

    if priority == "Urgent" and trade in {"HVAC", "Plumbing", "Electrical", "Appliance"}:
        return 4

    if priority == "Urgent":
        return 8

    return 72


def determine_estimated_cost(result: dict[str, Any], service_delivery_model: str) -> float:
    trade = result.get("trade")
    priority = result.get("priority")

    base_costs = {
        "Plumbing": 145.00,
        "HVAC": 185.00,
        "Electrical": 210.00,
        "Appliance": 135.00,
        "Access Control": 75.00,
        "Pest Control": 120.00,
        "Life Safety": 350.00,
        "General Maintenance": 95.00,
    }

    cost = base_costs.get(trade, 100.00)

    if priority == "Emergency":
        cost *= 1.50
    elif priority == "Urgent":
        cost *= 1.15

    model_multiplier = {
        "In-House": 1.00,
        "Vendor": 1.35,
        "Specialist Vendor": 1.55,
        "Union Vendor": 1.75,
        "Emergency Vendor": 1.90,
    }

    cost *= model_multiplier.get(service_delivery_model, 1.00)
    return round(cost, 2)


def determine_vendor_assignment(
    result: dict[str, Any], service_delivery_model: str
) -> tuple[str | None, str | None]:
    if service_delivery_model == "In-House":
        return None, None

    trade = result.get("trade")
    vendor = VENDOR_DIRECTORY.get(trade)

    if vendor:
        return vendor["vendor_name"], vendor["vendor_contact"]

    return None, None


def determine_technician_assignment(
    result: dict[str, Any], service_delivery_model: str
) -> tuple[str, str]:
    if service_delivery_model != "In-House":
        return "Vendor Managed", "Vendor Managed"

    trade = result.get("trade", "General Maintenance")
    priority = result.get("priority", "Routine")

    roster = TECHNICIAN_ROSTER.get(trade, TECHNICIAN_ROSTER["General Maintenance"])

    if priority == "Emergency":
        return roster["on_call"], roster["on_call"]

    return roster["primary"], roster["on_call"]


def determine_internal_notes(
    request: dict[str, Any],
    result: dict[str, Any],
    service_delivery_model: str,
    vendor_recommended: bool,
) -> str:
    return (
        f"Property={request['property_name']}; "
        f"Building={request['building']}; "
        f"Unit={request['unit_number'] or 'N/A'}; "
        f"Resident={request['resident_name']}; "
        f"Department={result['department']}; "
        f"Trade={result['trade']}; "
        f"ServiceDeliveryModel={service_delivery_model}; "
        f"VendorRecommended={vendor_recommended}; "
        f"RuleOverride={result.get('rule_override_applied', False)}; "
        f"MatchedRule={result.get('matched_rule', '')}"
    )


def generate_work_order(
    request: dict[str, Any], triage_result: dict[str, Any], sequence_number: int
) -> dict[str, Any]:
    timestamp_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    service_delivery_model = determine_service_delivery_model(
        request, triage_result, request["message"]
    )
    if service_delivery_model not in VALID_SERVICE_DELIVERY_MODELS:
        service_delivery_model = "In-House"

    vendor_recommended = determine_vendor_recommended(service_delivery_model)
    vendor_name, vendor_contact = determine_vendor_assignment(
        triage_result, service_delivery_model
    )
    assigned_technician, on_call_technician = determine_technician_assignment(
        triage_result, service_delivery_model
    )
    assigned_type = map_to_assigned_type(service_delivery_model)


    return {
        "ticket_id": build_ticket_id(sequence_number),
        "created_at_utc": timestamp_utc,
        "property_name": request["property_name"],
        "building": request["building"],
        "unit_number": request["unit_number"],
        "resident_name": request["resident_name"],
        "issue_summary": triage_result["issue_type"],
        "original_message": request["message"],
        "category": triage_result["category"],
        "trade": triage_result["trade"],
        "priority": triage_result["priority"],
        "dispatch_priority": triage_result["dispatch_priority"],
        "department": triage_result["department"],
        "on_call_required": determine_on_call_required(triage_result),
        "service_delivery_model": service_delivery_model,
        "dispatch_group": determine_dispatch_group(triage_result, service_delivery_model),
        "assigned_technician": assigned_technician,
        "on_call_technician": on_call_technician,
        "vendor_recommended": vendor_recommended,
        "vendor_name": vendor_name,
        "vendor_contact": vendor_contact,
        "sla_hours": determine_sla_hours(triage_result, service_delivery_model),
        "estimated_cost": determine_estimated_cost(triage_result, service_delivery_model),
        "priority_queue_position": determine_priority_queue_position(
            triage_result["priority"], sequence_number
        ),
        "escalation_path": determine_escalation_path(
            triage_result, service_delivery_model
        ),
        "recommended_action": triage_result["recommended_action"],
        "eta_guidance": triage_result["eta_guidance"],
        "resident_instructions": determine_resident_instructions(triage_result),
        "resident_reply": triage_result["resident_reply"],
        "internal_notes": determine_internal_notes(
            request, triage_result, service_delivery_model, vendor_recommended
        ),
        "status": "Open - Pending Dispatch",
        "source": "AI Simulated Intake",
        "rule_override_applied": triage_result.get("rule_override_applied", False),
        "matched_rule": triage_result.get("matched_rule", ""),
        "assigned_type": assigned_type,
    }


def log_triage_event(
    request: dict[str, Any], triage_result: dict[str, Any], work_order: dict[str, Any]
) -> None:
    log_record = {
        "timestamp_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "request": request,
        "triage_result": triage_result,
        "work_order": work_order,
    }

    log_file = LOGS_DIR / "triage_events.jsonl"
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(log_record) + "\n")


def export_pretty_json(work_orders: list[dict[str, Any]]) -> None:
    output_file = LOGS_DIR / "work_orders_pretty.json"
    with output_file.open("w", encoding="utf-8") as f:
        json.dump(work_orders, f, indent=2)


def export_csv(work_orders: list[dict[str, Any]]) -> None:
    output_file = LOGS_DIR / "work_orders.csv"
    if not work_orders:
        return

    fieldnames = list(work_orders[0].keys())
    with output_file.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(work_orders)


def print_results(request: dict[str, Any], triage_result: dict[str, Any], work_order: dict[str, Any]) -> None:
    print("=" * 80)
    print(f"TENANT MESSAGE: {request['message']}")
    print("-" * 80)
    print("TRIAGE RESULT:")
    print(json.dumps(triage_result, indent=2))
    print()
    print("WORK ORDER:")
    print(json.dumps(work_order, indent=2))
    print()


def main() -> None:
    print(f"{VERSION} - Local Test Mode")
    print("No Twilio SMS is being sent.\n")

    all_work_orders: list[dict[str, Any]] = []

    for index, request in enumerate(TEST_REQUESTS, start=1):
        try:
            triage_result = triage_message(request["message"])
            work_order = generate_work_order(request, triage_result, index)
            log_triage_event(request, triage_result, work_order)
            all_work_orders.append(work_order)
            print_results(request, triage_result, work_order)
        except Exception as e:
            print("=" * 80)
            print(f"TENANT MESSAGE: {request['message']}")
            print("-" * 80)
            print(f"ERROR: {e}\n")

    export_pretty_json(all_work_orders)
    export_csv(all_work_orders)

    print("=" * 80)
    print("EXPORTS CREATED:")
    print(f"- {LOGS_DIR / 'triage_events.jsonl'}")
    print(f"- {LOGS_DIR / 'work_orders_pretty.json'}")
    print(f"- {LOGS_DIR / 'work_orders.csv'}")
    print("=" * 80)


if __name__ == "__main__":
    main()