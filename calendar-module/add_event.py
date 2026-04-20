from pyicloud import PyiCloudService
from pyicloud.services.calendar import CalendarObject
from pyicloud.services.calendar import EventObject
from auth import authenticate
from datetime import datetime, timedelta

def add_event(calendar_service, title : str, start_date : datetime, end_date : datetime) -> None:
    '''
    adds event to RoV calendar in iCloud
    takes calendar_service from <<api.calendar>> title as string, start_date as dt, end_date as dt
    '''

    if len(title)==0:
        print("Title cannot be empty")

    # Creates new calendar for AI events only
    calendar_name = "RoV"

    existing_calendars = calendar_service.get_calendars(as_objs=True)
    ai_calendar = next((cal for cal in existing_calendars if cal.title == calendar_name), None)

    for cal in existing_calendars:
        print(cal.title)

    if ai_calendar:
        print(f"Calendar {calendar_name} exists.")
    else:
        cal = CalendarObject(title=calendar_name, share_type=0)
        cal.color = '#14A3C7'
        calendar_service.add_calendar(cal)
        print(f"Calendar {calendar_name} created.")

    calendar_guid = ai_calendar.guid

    # Creates an event
    event = EventObject(
        pguid=calendar_guid,
        title=title,
        start_date=start_date,
        end_date=end_date, 
    )

    # Add event to calendar
    calendar_service.add_event(event)
    print(f"Event added to {calendar_name}")


if __name__ == "__main__":
    api = authenticate()
    calendar_service = api.calendar
    title = "Meeting with dad"
    start_date = datetime.now()
    end_date = start_date + timedelta(hours=1)
    add_event(calendar_service, title, start_date, end_date)