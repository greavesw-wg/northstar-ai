import json
from datetime import UTC, datetime

from ai_engine.intent_router import route_message
from ai_engine.maintenance_triage_engine import generate_work_order, triage_message


VERSION = "NorthStar Orchestrator v1"


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
        "building": "Leasing Office",
        "unit_number": "",
        "resident_name": "Prospect 1",
        "message": "I would like to schedule a tour for a 2-bedroom apartment.",
    },
    {
        "property_name": "NorthStar Gardens",
        "building": "Grounds",
        "unit_number": "",
        "resident_name": "Site Staff",
        "message": "The landscaper never showed up today and the grass has not been cut.",
    },
    {
        "property_name": "NorthStar Gardens",
        "building": "Building E",
        "unit_number": "5A",
        "resident_name": "Resident 5A",
        "message": "Please schedule the move-out inspection for unit 5A.",
    },
    {
        "property_name": "NorthStar Gardens",
        "building": "Management Office",
        "unit_number": "",
        "resident_name": "Property Manager",
        "message": "We need documentation related to a fair housing complaint.",
    },
    {
        "property_name": "NorthStar Gardens",
        "building": "Roof Area",
        "unit_number": "",
        "resident_name": "Project Coordinator",
        "message": "The roof replacement contractor needs access Monday morning.",
    },
    {
        "property_name": "NorthStar Gardens",
        "building": "Building C",
        "unit_number": "3C",
        "resident_name": "Resident 3C",
        "message": "When does my lease renew?",
    },
]


def build_orchestration_record(
    request: dict,
    intent_result: dict,
    loop_result: dict | None = None,
    work_order: dict | None = None,
) -> dict:
    return {
        "timestamp_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "property_name": request["property_name"],
        "building": request["building"],
        "unit_number": request["unit_number"],
        "resident_name": request["resident_name"],
        "original_message": request["message"],
        "intent_result": intent_result,
        "loop_result": loop_result,
        "work_order": work_order,
    }


def print_record(record: dict) -> None:
    print("=" * 100)
    print(f"MESSAGE: {record['original_message']}")
    print("-" * 100)
    print(json.dumps(record, indent=2))
    print()


def main() -> None:
    print(f"{VERSION} - Local Simulation Mode")
    print("Intent router is active.")
    print("Maintenance loop is fully connected.")
    print("Non-maintenance loops are classification-only for now.\n")

    for index, request in enumerate(TEST_REQUESTS, start=1):
        try:
            intent_result = route_message(request["message"])
            intent = intent_result["intent"]

            if intent == "maintenance":
                loop_result = triage_message(request["message"])
                work_order = generate_work_order(request, loop_result, index)
                record = build_orchestration_record(
                    request=request,
                    intent_result=intent_result,
                    loop_result=loop_result,
                    work_order=work_order,
                )
            else:
                record = build_orchestration_record(
                    request=request,
                    intent_result=intent_result,
                    loop_result=None,
                    work_order=None,
                )

            print_record(record)

        except Exception as e:
            print("=" * 100)
            print(f"MESSAGE: {request['message']}")
            print("-" * 100)
            print(f"ERROR: {e}\n")


if __name__ == "__main__":
    main()