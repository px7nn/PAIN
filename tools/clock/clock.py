from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


from core.tool_manager import ToolManager

class Clock:
    def get_time(self, timezone_name: str | None = None) -> dict:
        if timezone_name is None or timezone_name.lower() == "system":
            tz = datetime.now().astimezone().tzinfo
        else:
            try:
                tz = ZoneInfo(timezone_name)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError(f"Unknown timezone: {timezone_name}") from exc
            
        now = datetime.now(tz)
        utc_now = datetime.now(timezone.utc)
        
        return {
            "date":       now.strftime("%Y-%m-%d"),
            "time":       now.strftime("%H:%M:%S"),
            "day":        now.strftime("%A"),
            "timezone":   timezone_name,
            "utc_offset": now.strftime("%z"),
            "iso":        now.isoformat(),
            "utc":        utc_now.isoformat()
        }
        

_clock = Clock()


def register(tool_manager: ToolManager):
    tool_manager.register(
        name = "get_current_time",
        description = (
            "Get the current date and time for a specified IANA timezone. "
            "Defaults to the system's local timezone when omitted or set to System."
        ),
        parameters = {
            "type": "object",
            "properties": {
                "timezone_name": {
                    "type": "string",
                    "description": (
                        "IANA timezone, such as Europe/London or America/New_York."
                    )
                }
            },
            "additionalProperties": False
        },
        handler= _clock.get_time
    )