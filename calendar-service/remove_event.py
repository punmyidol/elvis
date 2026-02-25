from pyicloud import PyiCloudService
from pyicloud.services.calendar import CalendarObject
from pyicloud.services.calendar import EventObject
from auth import authenticate
from datetime import datetime, timedelta

def remove_event(calendar_service, pguid : str) -> None:
    '''
    removes event from RoV calendar
    takes calendar_service from <<api.calendar>>, pguid as string
    '''

    # check guid of all events
    # assigns event to delete to a variable
    # deletes the event
    event = EventObject(pguid=pguid)
    calendar_service.remove_event(event)

if __name__ == "__main__":
    api = authenticate()
    calendar_service = api.calendar
    pguid = "360680A8-1346-438B-A171-4A7C0734E74E"
    remove_event(calendar_service, pguid)