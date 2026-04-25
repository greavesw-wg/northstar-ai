from ai_engine.intent_router import route_message


def main() -> None:
    test_messages = [
        "My toilet is overflowing in unit 3B.",
        "I want to see a 2 bedroom apartment this weekend.",
        "The snow removal vendor did not salt the sidewalks.",
        "Please schedule the move-out inspection for unit 5A.",
        "We need to review a reasonable accommodation request.",
        "The paving contractor is asking for site access on Tuesday.",
    ]

    for message in test_messages:
        result = route_message(message)
        print("=" * 80)
        print(f"MESSAGE: {message}")
        print("-" * 80)
        print(result)
        print()


if __name__ == "__main__":
    main()