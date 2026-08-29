from strands import Agent, tool
from datetime import datetime
from zoneinfo import ZoneInfo


# Define a custom tool as a Python function using the @tool decorator
@tool
def current_datetime(timezone: str) -> str:
    """
    Gets current date and time.

    Args:
        timezone: IANA timezone name (for example, "Europe/London"), or
            "local" to use the system timezone. This argument is required.

    Returns:
        str: Current date and time.
    """

    if timezone == "local":
        now = datetime.now().astimezone()
    else:
        now = datetime.now(ZoneInfo(timezone))

    return now.isoformat(timespec="seconds")


def main() -> None:
    agent = Agent(tools=[current_datetime])

    message = """
    I have 1 request:

    1. What is the time right now in Europe/London?
    """

    agent(message)


if __name__ == "__main__":
    main()
